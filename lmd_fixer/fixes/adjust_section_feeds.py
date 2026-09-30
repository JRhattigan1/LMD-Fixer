"""Divides each named toolpath into sections and sets a feed rate per section.

Three levels, and the names matter because a program holds several of each:

    part      one deposition area — a footprint on the bed. Found by grouping
              toolpaths whose XY bounding boxes overlap (`group_into_parts`),
              so the 45 coupons of a stepover trial come out as 45 parts and
              the layers of a tall build come out as one.
    toolpath  one named `(SECTION_NAME)` block (see `SECTION_MARKER_RE`); a
              program with no markers is treated as a single toolpath.
    section   a stretch of one toolpath that gets its own feed.

Each toolpath is split into sections by *deposition length* — the path length
travelled with the laser on (between `M323` and `M322`), falling back to all
feed-move length if a toolpath never switches the laser on. A split is N equal
sections, explicit distances in mm from the start of the toolpath, or the
geometry itself (`segment_by_geometry`, one section per straight run and one
per arc). Split points snap to move boundaries: a move belongs to the section
containing its midpoint, and moves are never broken in two.

Feed rate is modal. The source files set F once per toolpath (on the plunge,
e.g. `G1 Z0.0 F500.`) and every later move inherits it, so a changed feed
would leak into whatever follows. This fix therefore works out, for every
feed move (G1/G2/G3), the feed it *should* run at — the section's override, or
the original program's modal feed otherwise — and writes an F word onto any
move where the output's modal feed would differ. That also restates the
original feed at the start of the next section/toolpath when needed.

`G65 ... F1000.` is a macro argument, not a feed, and is ignored, as are
other non-motion G codes (G4 dwell X is a time, G28 axis words are an
intermediate point).

A section can instead be given a *ramp*: a start feed and an end feed, with
every move in between fed at the linearly interpolated value for its own
position along the section. Two things make that work on real files. Feeds are
snapped to `RAMP_FEED_STEP`, so a run of moves that rounds to the same value
writes one F word rather than dozens of near-identical ones. And long moves are
cut into chords of about `chord_mm`, because the CAM emits curves as ~0.5 mm
chords but straights as one or two very long moves — without splitting, a ramp
across a 26 mm straight could only step once. Only plain XY G1 moves are split;
a Z plunge, a rotary move or a real G2/G3 arc is left whole.

Options (all optional; with none, no changes are proposed):
    sections: {toolpath_start_index: int}          N equal sections (default 1)
    splits:   {toolpath_start_index: list[float]}  split distances in mm;
                                                   overrides `sections` when set
    geometry: {toolpath_start_index} or            split into straight/arc
              {toolpath_start_index: bool}         runs; overrides both above
    layers:   {toolpath_start_index} or            also cut at every layer
              {toolpath_start_index: bool}         boundary; composes with the
                                                   division above rather than
                                                   replacing it
    feeds:    {(toolpath_start_index, section_no): float}  1-based section number
    ramps:    {(toolpath_start_index, section_no): (start_feed, end_feed)}
                                                   overrides `feeds` for that section
    chord_mm: float   length to cut long moves to when ramping (DEFAULT_CHORD_MM)
    feed_step: float  rounding step for ramped feeds (RAMP_FEED_STEP)
"""

from __future__ import annotations

import bisect
import functools
import math
import re
from dataclasses import dataclass, field

from lmd_fixer.fixes import SECTION_MARKER_RE, Fix, FixResult, LineChange, register
from lmd_fixer.gcode import GCodeProgram

_COMMENT_RE = re.compile(r"\([^)]*\)")
_WORD_RE = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
# F word in the raw line, skipping over parenthesised comments.
_F_WORD_RE = re.compile(r"(\([^)]*\))|(F\s*[-+]?(?:\d+\.?\d*|\.\d+))", re.IGNORECASE)
_LASER_ON = 323
_LASER_OFF = 322
# G codes whose axis words aren't a feed move (dwell, reference return,
# macro call, offsets/coordinate setting).
_NON_MOTION_G = {4, 10, 28, 30, 52, 65, 92}


@dataclass
class _Move:
    index: int
    length: float
    laser_on: bool
    # XY at the start/end of the move; None where the position isn't tracked.
    start_xy: tuple[float, float] | None = None
    end_xy: tuple[float, float] | None = None
    curved: bool = False  # a real G2/G3 arc, as opposed to a G1 line
    # Z the move ends at — the deposition plane for a laser-on move, and what
    # layers are recovered from. None where Z isn't tracked.
    z: float | None = None


@dataclass
class _Parsed:
    """Per-line facts about the program needed to split and re-feed it."""

    moves: list[_Move] = field(default_factory=list)
    # Line index -> original modal feed in effect for that feed move.
    original_feed: dict[int, float] = field(default_factory=dict)
    # Line index -> F value written on that line, for any line setting modal F.
    explicit_feed: dict[int, float] = field(default_factory=dict)


@dataclass
class ToolpathSection:
    number: int  # 1-based
    start_index: int
    end_index: int  # inclusive; < start_index for an empty section
    from_mm: float
    to_mm: float
    original_feeds: list[float]
    kind: str = ""  # "line"/"arc" when split by geometry, else ""
    layer: int = 0  # 1-based layer this section sits in; 0 when not split by layer
    layer_z: float | None = None

    @property
    def is_empty(self) -> bool:
        return self.end_index < self.start_index


@dataclass
class Toolpath:
    name: str
    start_index: int
    end_index: int  # inclusive
    length_mm: float
    measured_with_laser: bool  # False when falling back to all feed-move length
    original_feeds: list[float]
    sections: list[ToolpathSection] = field(default_factory=list)
    # XY footprint, None for a toolpath whose position isn't trackable.
    bounds: tuple[float, float, float, float] | None = None  # x0, x1, y0, y1
    # Layers found in this toolpath's measured path, counted whether or not
    # the toolpath is actually being divided by them — the UI offers the split
    # only where there is more than one.
    n_layers: int = 1
    layer_zs: list[float] = field(default_factory=list)


@dataclass
class Part:
    """One deposition area: the toolpaths that share a footprint on the bed."""

    number: int  # 1-based, ordered by position on the bed
    toolpaths: list[Toolpath]
    bounds: tuple[float, float, float, float] | None  # x0, x1, y0, y1

    @property
    def name(self) -> str:
        return f"Part {self.number}"

    @property
    def where(self) -> str:
        if not self.bounds:
            return "position unknown"
        x0, x1, y0, y1 = self.bounds
        return f"X {x0:g}–{x1:g}, Y {y0:g}–{y1:g}"


def group_into_parts(toolpaths: list[Toolpath], gap_mm: float = 0.0) -> list[Part]:
    """Groups toolpaths into deposition areas by overlapping XY footprint.

    Layers of one build sit on top of each other and come out as one part;
    coupons laid out across the bed come out as one part each. `gap_mm`
    widens each footprint before testing, for paths that abut rather than
    overlap. Toolpaths with no trackable position go into a part of their own,
    in program order, rather than being silently merged.
    """
    groups: list[tuple[list[float] | None, list[Toolpath]]] = []
    for tp in toolpaths:
        if tp.bounds is None:
            groups.append((None, [tp]))
            continue
        x0, x1, y0, y1 = tp.bounds
        box = [x0 - gap_mm, x1 + gap_mm, y0 - gap_mm, y1 + gap_mm]
        for other, members in groups:
            if other is None:
                continue
            if not (box[1] < other[0] or other[1] < box[0] or box[3] < other[2] or other[3] < box[2]):
                other[0], other[1] = min(other[0], box[0]), max(other[1], box[1])
                other[2], other[3] = min(other[2], box[2]), max(other[3], box[3])
                members.append(tp)
                break
        else:
            groups.append((box, [tp]))

    # Bed order: bottom-left first, by row then column, so part numbers follow
    # how the coupons are laid out rather than where they fall in the file.
    def sort_key(group):
        box, members = group
        return (0, round(box[2], 3), round(box[0], 3)) if box else (1, members[0].start_index, 0)

    parts = []
    for n, (box, members) in enumerate(sorted(groups, key=sort_key), start=1):
        bounds = None
        if box:
            bounds = (box[0] + gap_mm, box[1] - gap_mm, box[2] + gap_mm, box[3] - gap_mm)
        parts.append(Part(number=n, toolpaths=members, bounds=bounds))
    return parts


def _words(line: str) -> list[tuple[str, float]]:
    code = _COMMENT_RE.sub("", line).strip().lstrip("/").upper()
    return [(letter, float(value)) for letter, value in _WORD_RE.findall(code)]


def _arc_length(start: tuple[float, float], end: tuple[float, float], clockwise: bool, words: dict) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    chord = math.hypot(dx, dy)
    if "R" in words:
        r = abs(words["R"])
        if r == 0 or chord > 2 * r:
            return chord
        sweep = 2 * math.asin(chord / (2 * r))
        if words["R"] < 0:
            sweep = 2 * math.pi - sweep
        return r * sweep
    cx, cy = start[0] + words.get("I", 0.0), start[1] + words.get("J", 0.0)
    r = math.hypot(start[0] - cx, start[1] - cy)
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    a1 = math.atan2(end[1] - cy, end[0] - cx)
    sweep = (a0 - a1) if clockwise else (a1 - a0)
    sweep %= 2 * math.pi
    if sweep == 0:
        sweep = 2 * math.pi  # start == end with a centre offset: full circle
    return r * sweep


def _parse(lines: list[str]) -> _Parsed:
    # The UI analyses, plots and applies the same program on every rerun.
    # Callers must treat the result as read-only.
    return _parse_cached(tuple(lines))


@functools.lru_cache(maxsize=4)
def _parse_cached(lines: tuple[str, ...]) -> _Parsed:
    parsed = _Parsed()
    motion = 0
    absolute = True
    laser_on = False
    feed: float | None = None
    pos: dict[str, float | None] = {"X": None, "Y": None, "Z": None}

    for i, line in enumerate(lines):
        words = _words(line)
        if not words:
            continue
        g_codes = [int(v) for letter, v in words if letter == "G" and v == int(v)]
        m_codes = [int(v) for letter, v in words if letter == "M"]
        if _LASER_ON in m_codes:
            laser_on = True
        if _LASER_OFF in m_codes:
            laser_on = False
        if 90 in g_codes:
            absolute = True
        if 91 in g_codes:
            absolute = False

        if any(g in _NON_MOTION_G for g in g_codes):
            # Reference returns / offsets leave the axes they name at a
            # position we can't track in work coordinates.
            if any(g in (28, 30, 92, 52, 10) for g in g_codes):
                for letter, _ in words:
                    if letter in pos:
                        pos[letter] = None
            continue

        for g in g_codes:
            if g in (0, 1, 2, 3):
                motion = g
        by_letter = {letter: v for letter, v in words}
        if "F" in by_letter:
            feed = by_letter["F"]
            parsed.explicit_feed[i] = feed

        axes = [a for a in ("X", "Y", "Z") if a in by_letter]
        if not axes:
            continue

        start = dict(pos)
        for a in axes:
            if absolute:
                pos[a] = by_letter[a]
            elif pos[a] is not None:
                pos[a] = pos[a] + by_letter[a]

        if motion not in (1, 2, 3):
            continue
        if feed is not None:
            parsed.original_feed[i] = feed

        # Moves from an untracked position (e.g. straight after G28) count as
        # zero length rather than guessing.
        length = 0.0
        if motion == 1 and all(start[a] is not None and pos[a] is not None for a in axes):
            length = math.sqrt(sum((pos[a] - start[a]) ** 2 for a in axes))
        elif motion in (2, 3) and all(start[a] is not None and pos[a] is not None for a in ("X", "Y")):
            planar = _arc_length((start["X"], start["Y"]), (pos["X"], pos["Y"]), motion == 2, by_letter)
            dz = (pos["Z"] - start["Z"]) if (start["Z"] is not None and pos["Z"] is not None) else 0.0
            length = math.hypot(planar, dz)
        start_xy = (start["X"], start["Y"]) if start["X"] is not None and start["Y"] is not None else None
        end_xy = (pos["X"], pos["Y"]) if pos["X"] is not None and pos["Y"] is not None else None
        parsed.moves.append(_Move(i, length, laser_on, start_xy, end_xy, motion in (2, 3), pos["Z"]))

    return parsed


def _toolpath_bounds(lines: list[str]) -> list[tuple[int, int, str]]:
    markers = [(i, m.group(1)) for i, line in enumerate(lines) if (m := SECTION_MARKER_RE.match(line.strip()))]
    if not markers:
        return [(0, len(lines) - 1, "(whole program)")] if lines else []
    return [
        (start, (markers[k + 1][0] - 1) if k + 1 < len(markers) else len(lines) - 1, name)
        for k, (start, name) in enumerate(markers)
    ]


# Geometry segmentation defaults. A junction whose local radius is below
# ARC_RADIUS_MM is "turning"; runs shorter than MIN_SEGMENT_MM are absorbed
# into a neighbour so a stray vertex doesn't become its own section.
ARC_RADIUS_MM = 10.0
MIN_SEGMENT_MM = 1.0
# A junction turning by more than this, between moves long enough to be real
# straight stretches, is a corner — whatever the radius test makes of it.
#
# Both halves are needed, and the second is not obvious. The radius test
# scales with move length, so on its own it misses a corner between *long*
# moves: a 90° turn between two 28 mm moves measures as a 40 mm radius and
# slips straight through. But angle alone is worse — genuine arcs in
# `O1140 - Original.ptp` contain junctions turning up to 46.6° (mean 11.4°),
# so a bare angle rule shreds real curves. What separates the two is the
# length of what runs into the junction: a curve arrives as ~0.5 mm chords,
# while a polygon corner sits between multi-millimetre straights.
CORNER_DEG = 30.0
CORNER_MIN_MOVE_MM = 2.0
# Two moves count as joined when their ends coincide to within this. Values
# propagate through `_parse` unchanged, so connected moves match exactly; the
# tolerance is only insurance.
JOIN_TOL_MM = 1e-6


def segment_by_geometry(
    moves: list[_Move],
    max_radius: float = ARC_RADIUS_MM,
    min_length: float = MIN_SEGMENT_MM,
    corner_deg: float = CORNER_DEG,
    corner_min_move: float = CORNER_MIN_MOVE_MM,
) -> list[tuple[int, int, str]]:
    """Splits a run of moves into straight and curved stretches.

    Returns `(first, last, kind)` triples of positions in `moves`, covering it
    in order; `kind` is `"line"` or `"arc"`.

    CAM output for these parts contains no G2/G3 — every curve arrives as a
    fan of short G1 chords — so curvature has to be recovered from the
    geometry. At each junction between two moves the local radius is
    `mean(length) / turn_angle`; below `max_radius` the path is turning there.
    A lone turning junction between two straight stretches is a *corner*, not
    an arc, so it only ends the straight run; an arc needs at least two
    consecutive turning junctions bending the same way. Real G2/G3 moves are
    taken as arcs without measuring.

    Two things stop this reading a path that isn't there. A junction only
    counts where the moves actually meet: `moves` is the *measured* path, with
    the rapids between strokes already dropped, so a raster pad arrives as a
    set of parallel strokes that never join. Taking those for one continuous
    straight is what collapsed every pad of a 17-pad sweep into a single
    section. And a turn past `corner_deg` is a corner whatever its moves
    measure, because a corner is defined by its angle and not by the length of
    what runs into it.
    """
    n = len(moves)
    if n == 0:
        return []

    def direction(m: _Move) -> float | None:
        if m.start_xy is None or m.end_xy is None or m.length == 0:
            return None
        dx, dy = m.end_xy[0] - m.start_xy[0], m.end_xy[1] - m.start_xy[1]
        return math.atan2(dy, dx) if (dx or dy) else None

    dirs = [direction(m) for m in moves]
    # Where the path actually continues from one move into the next. A gap
    # here is a pen-up: no turn is measured across it and nothing is ever
    # merged over it.
    joined_at = [
        moves[j].end_xy is not None
        and moves[j + 1].start_xy is not None
        and math.dist(moves[j].end_xy, moves[j + 1].start_xy) <= JOIN_TOL_MM
        for j in range(n - 1)
    ]

    # turn[j] is the signed direction change at the junction of move j and j+1.
    corner_rad = math.radians(corner_deg)
    turn: list[float | None] = []
    turning: list[bool] = []
    corner: list[bool] = []
    for j in range(n - 1):
        a, b = dirs[j], dirs[j + 1]
        if a is None or b is None or not joined_at[j]:
            turn.append(None)
            turning.append(False)
            corner.append(False)
            continue
        t = (b - a + math.pi) % (2 * math.pi) - math.pi
        span = (moves[j].length + moves[j + 1].length) / 2
        turn.append(t)
        turning.append(bool(span) and abs(t) / span > 1.0 / max_radius)
        corner.append(
            abs(t) > corner_rad
            and max(moves[j].length, moves[j + 1].length) >= corner_min_move
        )

    def sustained(j: int) -> bool:
        """True where this junction bends the same way as a neighbour."""
        if not turning[j]:
            return False
        return any(
            turning[k] and turn[k] is not None and turn[j] is not None and turn[k] * turn[j] > 0
            for k in (j - 1, j + 1)
            if 0 <= k < n - 1
        )

    # A corner is never part of an arc, however its neighbours behave. Without
    # this a polygon reads as a curve: the four 90° turns of a rectangle drawn
    # with 8 mm sides are each "turning" (radius 5.1 mm) and all bend the same
    # way, so `sustained` called the whole thing one arc and no corner split it.
    arc_junction = [sustained(j) and not corner[j] for j in range(n - 1)]
    kinds = [
        "arc"
        if moves[i].curved or any(arc_junction[j] for j in (i - 1, i) if 0 <= j < n - 1)
        else "line"
        for i in range(n)
    ]

    # A corner splits the straight run it sits in. Junctions *inside* an arc
    # turn too, so only a turning junction that isn't part of an arc counts.
    # A gap in the path always splits: the two sides are different strokes.
    breaks = {
        j for j in range(n - 1)
        if not joined_at[j]
        or ((turning[j] or corner[j]) and not arc_junction[j])
        or kinds[j] != kinds[j + 1]
    }
    runs: list[list] = []
    start = 0
    for j in range(n):
        if j == n - 1 or j in breaks:
            runs.append([start, j, kinds[start]])
            start = j + 1

    def run_length(run: list) -> float:
        return sum(moves[i].length for i in range(run[0], run[1] + 1))

    # Absorb slivers, then re-join neighbours that now match. A sliver is only
    # ever folded into a run the path actually reaches it from — a short
    # stroke standing on its own is a section in its own right, not a piece of
    # whatever happens to sit next to it in the file.
    while len(runs) > 1:
        for r, run in enumerate(runs):
            if run_length(run) >= min_length:
                continue
            left = runs[r - 1] if r > 0 and joined_at[run[0] - 1] else None
            right = runs[r + 1] if r + 1 < len(runs) and joined_at[run[1]] else None
            if left is None and right is None:
                continue
            target = left if right is None else right if left is None else (
                left if run_length(left) >= run_length(right) else right
            )
            target[0], target[1] = min(target[0], run[0]), max(target[1], run[1])
            runs.pop(r)
            break
        else:
            break
    joined: list[list] = []
    for run in runs:
        if (
            joined
            and joined[-1][2] == run[2] == "arc"
            and joined[-1][1] + 1 == run[0]
            and joined_at[joined[-1][1]]
        ):
            joined[-1][1] = run[1]
        else:
            joined.append(run)
    return [(a, b, k) for a, b, k in joined]


# A Z change smaller than this is the same deposition plane — noise, or a
# path that isn't perfectly flat — rather than a new layer.
LAYER_STEP_MM = 0.05


def segment_by_layer(moves: list[_Move], min_step: float = LAYER_STEP_MM) -> list[tuple[int, int, float | None]]:
    """Splits a run of moves into layers by the Z they deposit at.

    Returns `(first, last, z)` triples of positions in `moves`, covering it in
    order. A toolpath marker does not mean a layer: in a real parameter-sweep
    file one `(PAD2_1.2MM_1.28Z_4LAYERS)` block holds all four of its layers,
    at Z 0.0, 1.28, 2.56 and 3.84. Layers are therefore recovered from Z
    rather than from the markers.

    Each move is compared against the Z the *current* layer started at, so a
    path that is merely not perfectly flat stays one layer. A path whose Z
    climbs continuously — a helix, rather than the flat layers these files
    hold — has no layers to find and will come back cut every `min_step` of
    climb; that division is meaningless, so the caller shows the layer count
    and lets the operator decide whether to use it.
    """
    runs: list[list] = []
    for i, m in enumerate(moves):
        same = runs and (
            m.z is None or runs[-1][2] is None or abs(m.z - runs[-1][2]) <= min_step
        )
        if same:
            runs[-1][1] = i
        else:
            runs.append([i, i, m.z])
    return [(a, b, z) for a, b, z in runs]


def _unique(values: list[float]) -> list[float]:
    return list(dict.fromkeys(values))


def format_feed(value: float) -> str:
    """Fanuc-style: integers keep a trailing decimal point (`650.`)."""
    return f"{int(value)}." if value == int(value) else f"{value:g}"


def analyse_toolpaths(
    program: GCodeProgram,
    sections: dict[int, int] | None = None,
    splits: dict[int, list[float]] | None = None,
    geometry: set[int] | dict[int, bool] | None = None,
    layers: set[int] | dict[int, bool] | None = None,
) -> list[Toolpath]:
    """Finds each toolpath and divides it into sections (see module docstring)."""
    return _analyse(
        program.lines, _parse(program.lines), sections or {}, splits or {}, geometry or set(), layers or set()
    )


def _analyse(
    lines: list[str],
    parsed: _Parsed,
    sections: dict[int, int],
    splits: dict[int, list[float]],
    geometry: set[int] | dict[int, bool] = frozenset(),
    layers: set[int] | dict[int, bool] = frozenset(),
) -> list[Toolpath]:
    toolpaths: list[Toolpath] = []
    move_indices = [m.index for m in parsed.moves]

    for start, end, name in _toolpath_bounds(lines):
        moves = parsed.moves[bisect.bisect_left(move_indices, start):bisect.bisect_right(move_indices, end)]
        measured = [m for m in moves if m.laser_on]
        with_laser = bool(measured) and sum(m.length for m in measured) > 0
        if not with_laser:
            measured = moves
        total = sum(m.length for m in measured)
        # Footprint from the measured path, so a rapid out to a tool-change
        # position doesn't stretch the area across the bed.
        xy = [p for m in measured for p in (m.start_xy, m.end_xy) if p is not None]
        bounds = (
            (min(p[0] for p in xy), max(p[0] for p in xy), min(p[1] for p in xy), max(p[1] for p in xy))
            if xy else None
        )
        # Distance along the measured path at the start of each of its moves,
        # so a cut on a move boundary can be named as a distance.
        offset: list[float] = []
        travelled = 0.0
        for m in measured:
            offset.append(travelled)
            travelled += m.length

        layer_runs = segment_by_layer(measured)
        tp = Toolpath(
            name=name,
            start_index=start,
            end_index=end,
            length_mm=total,
            measured_with_laser=with_laser,
            original_feeds=_unique([parsed.original_feed[m.index] for m in moves if m.index in parsed.original_feed]),
            bounds=bounds,
            n_layers=len(layer_runs),
            layer_zs=[z for _, _, z in layer_runs if z is not None],
        )

        def enabled(which) -> bool:
            return start in which and (not isinstance(which, dict) or which[start])

        # Kind and layer are carried per *move* rather than per section, so
        # that layer cuts can be merged into whatever division was chosen and
        # each resulting section can still say which run and which layer it
        # came from. Reading them positionally off the run list only works
        # while the sections and the runs are the same list.
        kind_of_move: list[str] = [""] * len(measured)
        layer_of_move: list[int] = [0] * len(measured)
        z_of_move: list[float | None] = [None] * len(measured)

        cuts: list[float] = []
        if enabled(geometry):
            # Cuts land exactly on move boundaries, so the midpoint rule below
            # reproduces the runs move for move.
            for first, last, kind in segment_by_geometry(measured):
                if first:
                    cuts.append(offset[first])
                for i in range(first, last + 1):
                    kind_of_move[i] = kind
        elif start in splits and splits[start]:
            cuts = list(splits[start])
        else:
            n = max(1, int(sections.get(start, 1)))
            cuts = [total * k / n for k in range(1, n)]

        # Layers compose with the division above rather than replacing it:
        # ticking both gives one section per straight/arc *within* each layer,
        # which is what a stacked build needs. Layer cuts land on move
        # boundaries for the same reason geometry cuts do.
        if enabled(layers) and len(layer_runs) > 1:
            for number, (first, last, z) in enumerate(layer_runs, start=1):
                if first:
                    cuts.append(offset[first])
                for i in range(first, last + 1):
                    layer_of_move[i] = number
                    z_of_move[i] = z
        cuts = sorted({c for c in cuts if 0 < c < total})
        edges = [0.0, *cuts, total]

        # First measured move belonging to each section (by midpoint distance),
        # as a line index for the section bounds and as a position in
        # `measured` for the per-move facts above.
        first_move_of_section: list[int | None] = [None] * (len(edges) - 1)
        first_pos_of_section: list[int | None] = [None] * (len(edges) - 1)
        for i, m in enumerate(measured):
            mid = offset[i] + m.length / 2
            p = bisect.bisect_right(cuts, mid)
            if first_move_of_section[p] is None:
                first_move_of_section[p] = m.index
                first_pos_of_section[p] = i

        # Section p starts at its first move (section 1 at the toolpath
        # marker) and runs until the next non-empty section starts.
        starts: list[int | None] = [start] + first_move_of_section[1:]
        for p in range(len(edges) - 1):
            at = first_pos_of_section[p]
            kind = kind_of_move[at] if at is not None else ""
            layer = layer_of_move[at] if at is not None else 0
            z = z_of_move[at] if at is not None else None
            if starts[p] is None:
                tp.sections.append(
                    ToolpathSection(p + 1, start, start - 1, edges[p], edges[p + 1], [], kind, layer, z)
                )
                continue
            nxt = next((s for s in starts[p + 1:] if s is not None), end + 1)
            p_start, p_end = starts[p], nxt - 1
            feeds = _unique(
                [parsed.original_feed[m.index] for m in moves if p_start <= m.index <= p_end and m.index in parsed.original_feed]
            )
            tp.sections.append(
                ToolpathSection(p + 1, p_start, p_end, edges[p], edges[p + 1], feeds, kind, layer, z)
            )
        toolpaths.append(tp)

    return toolpaths


def path_points(program: GCodeProgram, toolpaths: list[Toolpath]) -> list[dict]:
    """The measured path of each toolpath section as plottable XY polylines.

    One dict per vertex: `toolpath` (start index), `section`, `run`, `x`, `y`,
    and `t` — how far along its own section the vertex sits, 0 at the start and
    1 at the end, so a ramped section can be coloured by the feed it actually
    reaches at that point rather than by one value for the whole section.
    A new run starts whenever the path isn't continuous — laser switched off,
    untracked position, or a new section — so plotted lines don't draw across
    rapids. Uses the same moves as the length measurement (laser-on, or all
    feed moves for a toolpath without laser codes); arcs are drawn as chords.
    """
    parsed = _parse(program.lines)
    move_indices = [m.index for m in parsed.moves]
    rows: list[dict] = []
    run = 0
    for tp in toolpaths:
        for section in tp.sections:
            if section.is_empty:
                continue
            lo = bisect.bisect_left(move_indices, section.start_index)
            hi = bisect.bisect_right(move_indices, section.end_index)
            last: tuple[float, float] | None = None
            # Measured along the section's own plotted moves, which is what a
            # ramp is interpolated over.
            span = sum(
                m.length for m in parsed.moves[lo:hi]
                if not (tp.measured_with_laser and not m.laser_on)
            )
            travelled = 0.0
            for m in parsed.moves[lo:hi]:
                if (tp.measured_with_laser and not m.laser_on) or m.start_xy is None or m.end_xy is None:
                    last = None
                    continue
                t0 = travelled / span if span > 0 else 0.0
                t1 = (travelled + m.length) / span if span > 0 else 1.0
                if last != m.start_xy:
                    run += 1
                    rows.append({"toolpath": tp.start_index, "section": section.number, "run": run,
                                 "x": m.start_xy[0], "y": m.start_xy[1], "t": t0})
                if m.end_xy != m.start_xy:
                    rows.append({"toolpath": tp.start_index, "section": section.number, "run": run,
                                 "x": m.end_xy[0], "y": m.end_xy[1], "t": t1})
                last = m.end_xy
                travelled += m.length
    return rows


def _set_feed_word(line: str, value: float) -> str:
    """Replaces the line's F word (outside comments), or appends one before
    any trailing comment."""
    new_word = f"F{format_feed(value)}"
    replaced = False

    def sub(match: re.Match) -> str:
        nonlocal replaced
        if match.group(1) or replaced:
            return match.group(0)
        replaced = True
        return new_word

    out = _F_WORD_RE.sub(sub, line)
    if replaced:
        return out
    comment = re.search(r"\s*\(.*\)\s*$", line)
    if comment:
        return f"{line[:comment.start()].rstrip()} {new_word}{line[comment.start():]}"
    return f"{line.rstrip()} {new_word}"


# A ramped section interpolates its feed along the path. Values are snapped to
# RAMP_FEED_STEP so neighbouring moves that land on the same value need no
# repeated F word — a 500->250 ramp becomes ~11 readable steps, not 86 near
# identical ones.
RAMP_FEED_STEP = 25.0
# The CAM emits curves as ~0.5 mm chords but straights as one or two very long
# moves, so a ramp across a straight would only step once or twice. Moves
# longer than this are cut into chords of about this length to give the ramp
# somewhere to land. Set per-run from the UI's ramp settings.
DEFAULT_CHORD_MM = 2.0

_AXIS_WORD_RE = re.compile(r"(\([^)]*\))|([XY])\s*([-+]?(?:\d+\.?\d*|\.\d+))", re.IGNORECASE)
_TRAILING_COMMENT_RE = re.compile(r"\s*\(.*\)\s*$")


def round_to_step(value: float, step: float = RAMP_FEED_STEP) -> float:
    """Snaps a feed to the nearest `step`, never below one whole step."""
    if step <= 0:
        return value
    return max(step, round(value / step) * step)


def _format_axis(value: float) -> str:
    """Matches the source files' axis style: `X8.266`, `X8.0` — always a
    decimal point, no trailing zero padding."""
    text = f"{value:.3f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def ramp_feed_at(section: ToolpathSection, start_feed: float, end_feed: float, distance: float,
                 step: float = RAMP_FEED_STEP) -> float:
    """The ramped feed at `distance` mm along the toolpath, for a section
    running from `start_feed` at its start to `end_feed` at its end."""
    span = section.to_mm - section.from_mm
    t = 0.0 if span <= 0 else min(1.0, max(0.0, (distance - section.from_mm) / span))
    return round_to_step(start_feed + (end_feed - start_feed) * t, step)


def _set_axis_words(line: str, x: float, y: float) -> str:
    """Rewrites the line's X and Y values (outside comments), leaving every
    other word — G code, comment — as it was."""
    def sub(match: re.Match) -> str:
        if match.group(1):
            return match.group(0)
        letter = match.group(2).upper()
        return f"{letter}{_format_axis(x if letter == 'X' else y)}"

    return _AXIS_WORD_RE.sub(sub, line)


def _subdividable(line: str, move: _Move, chord_mm: float) -> bool:
    """True when a move can safely be cut into shorter chords: a straight
    G1 in the XY plane, with both ends known and no other axis along for the
    ride (a Z plunge or a rotary move must not be split)."""
    if move.curved or chord_mm <= 0 or move.length <= chord_mm:
        return False
    if move.start_xy is None or move.end_xy is None:
        return False
    letters = {letter for letter, _ in _words(line)}
    return bool(letters & {"X", "Y"}) and not (letters & {"Z", "A", "B", "C", "U", "V", "W"})


def _ramp_move_lines(
    line: str, move: _Move, section: ToolpathSection, start_feed: float, end_feed: float,
    from_mm: float, chord_mm: float, modal: float | None, step: float = RAMP_FEED_STEP,
) -> tuple[list[str], float | None]:
    """The replacement line(s) for one move inside a ramped section.

    A long straight is cut into ~`chord_mm` chords so the feed has somewhere to
    step; anything else keeps its single line. Each chord is fed at the ramp
    value for its own midpoint, and an F word is written only where the modal
    feed would otherwise be wrong — so a run of chords that round to the same
    value writes it once.
    """
    n = 1
    if _subdividable(line, move, chord_mm):
        n = max(1, math.ceil(move.length / chord_mm))

    out: list[str] = []
    (x0, y0), (x1, y1) = move.start_xy or (0.0, 0.0), move.end_xy or (0.0, 0.0)
    for k in range(n):
        feed = ramp_feed_at(section, start_feed, end_feed, from_mm + move.length * (k + 0.5) / n, step)
        if n == 1:
            text = line
        else:
            f = (k + 1) / n
            text = _set_axis_words(line, x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)
            if k:
                # The comment belongs to the move, not to each of its chords.
                text = _TRAILING_COMMENT_RE.sub("", text)
        if feed != modal:
            text = _set_feed_word(text, feed)
            modal = feed
        out.append(text)
    return out, modal


@register
class AdjustSectionFeeds(Fix):
    id = "adjust_section_feeds"
    label = "Adjust feed rate per toolpath section"
    description = (
        "Divides each named toolpath into sections (equal deposition length, at set distances, or "
        "by straights and arcs) and lets you set a feed rate for each — a single value, or a ramp "
        "between a start and an end feed. F words are added/rewritten on feed moves only, "
        "and the original feed is restated wherever a change would otherwise carry over."
    )

    def apply(self, program: GCodeProgram, **options) -> FixResult:
        feeds: dict[tuple[int, int], float] = {k: v for k, v in (options.get("feeds") or {}).items() if v}
        # A ramp needs both ends; one alone is just a constant feed.
        ramps: dict[tuple[int, int], tuple[float, float]] = {
            k: (float(v[0]), float(v[1]))
            for k, v in (options.get("ramps") or {}).items()
            if v and v[0] and v[1]
        }
        chord_mm = float(options.get("chord_mm") or DEFAULT_CHORD_MM)
        step = float(options.get("feed_step") or RAMP_FEED_STEP)
        out = program.copy()
        parsed = _parse(program.lines)
        toolpaths = _analyse(
            program.lines,
            parsed,
            options.get("sections") or {},
            options.get("splits") or {},
            options.get("geometry") or set(),
            options.get("layers") or set(),
        )
        move_indices = [m.index for m in parsed.moves]  # ascending
        move_by_index = {m.index: m for m in parsed.moves}

        # Distance along each toolpath's measured path at the start of every
        # move, on the same measure `_analyse` used for the section edges, so a
        # ramp lines up with the from_mm/to_mm the UI shows.
        dist_at: dict[int, float] = {}
        for tp in toolpaths:
            lo = bisect.bisect_left(move_indices, tp.start_index)
            hi = bisect.bisect_right(move_indices, tp.end_index)
            moves = parsed.moves[lo:hi]
            measured = [m for m in moves if m.laser_on]
            if not (measured and sum(m.length for m in measured) > 0):
                measured = moves
            travelled = 0.0
            for m in measured:
                dist_at[m.index] = travelled
                travelled += m.length

        # Line index -> (override feed, toolpath, section) for feed moves in
        # sections given a single feed, and the same for ramped sections.
        override_at: dict[int, tuple[float, Toolpath, ToolpathSection]] = {}
        ramp_at: dict[int, tuple[Toolpath, ToolpathSection, float, float]] = {}
        n_overridden = n_ramped = 0
        for tp in toolpaths:
            for section in tp.sections:
                if section.is_empty:
                    continue
                key = (tp.start_index, section.number)
                lo = bisect.bisect_left(move_indices, section.start_index)
                hi = bisect.bisect_right(move_indices, section.end_index)
                if key in ramps:
                    n_ramped += 1
                    start_feed, end_feed = ramps[key]
                    for idx in move_indices[lo:hi]:
                        ramp_at[idx] = (tp, section, start_feed, end_feed)
                    continue
                value = feeds.get(key)
                if value is None:
                    continue
                n_overridden += 1
                for idx in move_indices[lo:hi]:
                    override_at[idx] = (value, tp, section)

        changes: list[LineChange] = []
        modal: float | None = None
        move_set = set(move_indices)
        new_lines: list[str] = []
        n_added = 0
        for i, line in enumerate(program.lines):
            if i not in move_set:
                if i in parsed.explicit_feed:
                    modal = parsed.explicit_feed[i]
                new_lines.append(line)
                continue

            if i in ramp_at and i in dist_at:
                tp, at, start_feed, end_feed = ramp_at[i]
                replacement, modal = _ramp_move_lines(
                    line, move_by_index[i], at, start_feed, end_feed, dist_at[i], chord_mm, modal, step
                )
                new_lines.extend(replacement)
                if replacement != [line]:
                    n_added += len(replacement) - 1
                    detail = f" over {len(replacement)} chords" if len(replacement) > 1 else ""
                    changes.append(
                        LineChange(
                            kind="modified",
                            original_index=i,
                            original_text=line,
                            new_text="\n".join(replacement),
                            label=tp.name,
                            reason=(
                                f"section {at.number}/{len(tp.sections)}: ramp "
                                f"F{format_feed(start_feed)}->F{format_feed(end_feed)}{detail}"
                            ),
                        )
                    )
                continue

            if i in override_at:
                desired, tp, at = override_at[i]
                reason = f"section {at.number}/{len(tp.sections)}: F{format_feed(desired)}"
            else:
                desired = parsed.original_feed.get(i)
                tp, reason = None, "restores original feed"
            if desired is None:
                new_lines.append(line)
                continue
            has_f = i in parsed.explicit_feed
            if (has_f and parsed.explicit_feed[i] == desired) or (not has_f and modal == desired):
                modal = desired
                new_lines.append(line)
                continue
            new_line = _set_feed_word(line, desired)
            new_lines.append(new_line)
            modal = desired
            changes.append(
                LineChange(
                    kind="modified",
                    original_index=i,
                    original_text=line,
                    new_text=new_line,
                    label=tp.name if tp else next((t.name for t in toolpaths if t.start_index <= i <= t.end_index), None),
                    reason=reason,
                )
            )

        out.lines = new_lines
        bits = []
        if n_overridden:
            bits.append(f"{n_overridden} section feed override(s)")
        if n_ramped:
            bits.append(f"{n_ramped} ramped section(s)")
        summary = (
            f"Set {' and '.join(bits) or 'no feed overrides'}; "
            f"{len(changes)} line(s) get a new or restated F word."
        )
        if n_added:
            summary += f" {n_added} line(s) added by splitting long moves into ~{chord_mm:g} mm chords."
        return FixResult(program=out, summary=summary, changes=changes)
