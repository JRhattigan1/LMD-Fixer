"""Toolpaths, parts and their deposition paths, recovered from a program.
Used by the bed maps.

Three levels, and the names matter because a program holds several of each:

    part      one deposition area — a footprint on the bed. Found by grouping
              toolpaths whose XY bounding boxes overlap (`group_into_parts`),
              so the 45 coupons of a stepover trial come out as 45 parts and
              the layers of a tall build come out as one.
    toolpath  one named `(SECTION_NAME)` block (see `SECTION_MARKER_RE`); a
              program with no markers is treated as a single toolpath.
    move      one G1/G2/G3 line.

A toolpath's *measured path* is its moves made with the laser on (between
`M323` and `M322`), falling back to every feed move if it never switches the
laser on. Only the measured path counts towards a footprint or gets drawn,
so a rapid out to a tool-change position doesn't stretch the area across the
bed. `G65 ... F1000.` is a macro call, not a move, and is ignored, as are
other non-motion G codes (G4 dwell X is a time, G28 axis words are an
intermediate point).
"""

from __future__ import annotations

import bisect
import functools
import math
import re
from dataclasses import dataclass, field

from lmd_fixer.fixes import SECTION_MARKER_RE
from lmd_fixer.gcode import GCodeProgram

_COMMENT_RE = re.compile(r"\([^)]*\)")
_WORD_RE = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
_LASER_ON = 323
_LASER_OFF = 322
# G codes whose axis words aren't a feed move (dwell, reference return,
# macro call, offsets/coordinate setting).
_NON_MOTION_G = {4, 10, 28, 30, 52, 65, 92}


@dataclass
class Move:
    """One motion line. Rapids (G0) are kept for the 3D view's travel
    moves; everything else here only uses feed moves."""

    index: int
    length: float
    laser_on: bool
    # XY at the start/end of the move; None where the position isn't tracked.
    start_xy: tuple[float, float] | None = None
    end_xy: tuple[float, float] | None = None
    start_z: float | None = None
    end_z: float | None = None
    rapid: bool = False  # G0

    @property
    def start_xyz(self) -> tuple[float, float, float] | None:
        return None if self.start_xy is None or self.start_z is None else (*self.start_xy, self.start_z)

    @property
    def end_xyz(self) -> tuple[float, float, float] | None:
        return None if self.end_xy is None or self.end_z is None else (*self.end_xy, self.end_z)


@dataclass
class Toolpath:
    name: str
    start_index: int
    end_index: int  # inclusive
    length_mm: float
    measured_with_laser: bool  # False when falling back to all feed moves
    # XY footprint, None for a toolpath whose position isn't trackable.
    bounds: tuple[float, float, float, float] | None = None  # x0, x1, y0, y1
    moves: list[Move] = field(default_factory=list, repr=False)  # the measured path


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


@functools.lru_cache(maxsize=65536)
def _words(line: str) -> tuple[tuple[str, float], ...]:
    # Cached per line text: a step removes or edits a few lines, so when the
    # next program is parsed almost every line has been seen before.
    code = _COMMENT_RE.sub("", line).strip().lstrip("/").upper()
    return tuple((letter, float(value)) for letter, value in _WORD_RE.findall(code))


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


def program_moves(program: GCodeProgram) -> list[Move]:
    """Every motion line in the program, rapids included, in order.
    Read-only: the result is cached and shared."""
    return _parse(program.lines)


def _parse(lines: list[str]) -> list[Move]:
    # The UI analyses the same program on every redraw; callers must treat
    # the result as read-only.
    return _parse_cached(tuple(lines))


@functools.lru_cache(maxsize=4)
def _parse_cached(lines: tuple[str, ...]) -> list[Move]:
    moves: list[Move] = []
    motion = 0
    absolute = True
    laser_on = False
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
        axes = [a for a in ("X", "Y", "Z") if a in by_letter]
        if not axes:
            continue

        start = dict(pos)
        for a in axes:
            if absolute:
                pos[a] = by_letter[a]
            elif pos[a] is not None:
                pos[a] = pos[a] + by_letter[a]

        start_xy = (start["X"], start["Y"]) if start["X"] is not None and start["Y"] is not None else None
        end_xy = (pos["X"], pos["Y"]) if pos["X"] is not None and pos["Y"] is not None else None
        if motion == 0:
            moves.append(Move(i, 0.0, laser_on, start_xy, end_xy, start["Z"], pos["Z"], rapid=True))
            continue
        if motion not in (1, 2, 3):
            continue
        # Moves from an untracked position (e.g. straight after G28) count as
        # zero length rather than guessing.
        length = 0.0
        if motion == 1 and all(start[a] is not None and pos[a] is not None for a in axes):
            length = math.sqrt(sum((pos[a] - start[a]) ** 2 for a in axes))
        elif motion in (2, 3) and all(start[a] is not None and pos[a] is not None for a in ("X", "Y")):
            planar = _arc_length((start["X"], start["Y"]), (pos["X"], pos["Y"]), motion == 2, by_letter)
            dz = (pos["Z"] - start["Z"]) if (start["Z"] is not None and pos["Z"] is not None) else 0.0
            length = math.hypot(planar, dz)
        moves.append(Move(i, length, laser_on, start_xy, end_xy, start["Z"], pos["Z"]))
    return moves


def _toolpath_bounds(lines: list[str]) -> list[tuple[int, int, str]]:
    markers = [(i, m.group(1)) for i, line in enumerate(lines) if (m := SECTION_MARKER_RE.match(line.strip()))]
    if not markers:
        return [(0, len(lines) - 1, "(whole program)")] if lines else []
    return [
        (start, (markers[k + 1][0] - 1) if k + 1 < len(markers) else len(lines) - 1, name)
        for k, (start, name) in enumerate(markers)
    ]


def analyse_toolpaths(program: GCodeProgram) -> list[Toolpath]:
    """Every toolpath in the program, with its measured path and footprint.
    Cached per program text (the plan map and the 3D view both ask for it);
    treat the result as read-only."""
    return _analyse_cached(tuple(program.lines))


@functools.lru_cache(maxsize=4)
def _analyse_cached(lines: tuple[str, ...]) -> list[Toolpath]:
    program = GCodeProgram(lines=list(lines))
    all_moves = _parse(program.lines)
    move_indices = [m.index for m in all_moves]
    toolpaths: list[Toolpath] = []
    for start, end, name in _toolpath_bounds(program.lines):
        moves = [m for m in all_moves[bisect.bisect_left(move_indices, start):bisect.bisect_right(move_indices, end)]
                 if not m.rapid]
        measured = [m for m in moves if m.laser_on]
        with_laser = bool(measured) and sum(m.length for m in measured) > 0
        if not with_laser:
            measured = moves
        xy = [p for m in measured for p in (m.start_xy, m.end_xy) if p is not None]
        bounds = (
            (min(p[0] for p in xy), max(p[0] for p in xy), min(p[1] for p in xy), max(p[1] for p in xy))
            if xy else None
        )
        toolpaths.append(Toolpath(
            name=name, start_index=start, end_index=end, length_mm=sum(m.length for m in measured),
            measured_with_laser=with_laser, bounds=bounds, moves=measured,
        ))
    return toolpaths


def path_points(toolpaths: list[Toolpath]) -> list[dict]:
    """Each toolpath's measured path as plottable XY polylines.

    One dict per vertex: `toolpath` (start index), `run`, `x`, `y`. A new run
    starts wherever the path isn't continuous — laser switched off, or an
    untracked position — so plotted lines don't draw across rapids. Arcs are
    drawn as chords.
    """
    rows: list[dict] = []
    run = 0
    for tp in toolpaths:
        last: tuple[float, float] | None = None
        for m in tp.moves:
            if m.start_xy is None or m.end_xy is None:
                last = None
                continue
            if last != m.start_xy:
                run += 1
                rows.append({"toolpath": tp.start_index, "run": run, "x": m.start_xy[0], "y": m.start_xy[1]})
            if m.end_xy != m.start_xy:
                rows.append({"toolpath": tp.start_index, "run": run, "x": m.end_xy[0], "y": m.end_xy[1]})
            last = m.end_xy
    return rows
