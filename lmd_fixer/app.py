"""Streamlit web UI for LMD-Fixer.

Run with `lmd-fixer` (installed entry point) or
`streamlit run lmd_fixer/app.py` from a checkout.
"""

from __future__ import annotations

import difflib
import html
import re
import sys
from pathlib import Path

# `streamlit run` executes this file as a plain script, so when running from
# a checkout (not an installed package) make the repo root importable.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import altair as alt
import pandas as pd
import streamlit as st

from lmd_fixer import __version__
from lmd_fixer.fixes import available_fixes
from lmd_fixer.fixes.adjust_section_feeds import (
    DEFAULT_CHORD_MM,
    RAMP_FEED_STEP,
    analyse_toolpaths,
    format_feed,
    group_into_parts,
    path_points,
    round_to_step,
)
from lmd_fixer.gcode import GCodeProgram
from lmd_fixer.pipeline import FIX_ORDER, apply_accepted_changes, run_fix
from lmd_fixer.settings import settings_path

# Only render per-change context previews when the list is small enough for
# them to be useful rather than overwhelming (and cheap enough to render).
MAX_CHANGES_WITH_CONTEXT = 40

# Standalone program-number header line, e.g. `O1140` (optionally `/`-blocked
# like other lines). The machine expects this to match the program's filename.
O_NUMBER_LINE_RE = re.compile(r"^(/?\s*O)(\d+)\s*$")
O_NUMBER_IN_NAME_RE = re.compile(r"O(\d+)", re.IGNORECASE)


def sync_o_number(program: GCodeProgram, output_name: str) -> tuple[GCodeProgram, str | None, str | None]:
    """Rewrites the program's `Oxxxx` header line to match the O-number found
    in `output_name`, if both are present. Returns (possibly new program,
    old number, new number); numbers are None where there's nothing to sync
    (no O-number line, or no O#### in the chosen filename)."""
    name_match = O_NUMBER_IN_NAME_RE.search(output_name)
    if not name_match:
        return program, None, None
    new_number = name_match.group(1)
    for i, line in enumerate(program.lines):
        line_match = O_NUMBER_LINE_RE.match(line.strip())
        if line_match:
            old_number = line_match.group(2)
            if old_number == new_number:
                return program, old_number, new_number
            out = program.copy()
            out.lines[i] = f"{line_match.group(1)}{new_number}"
            return out, old_number, new_number
    return program, None, None


ACCENT = "#2dd4bf"
DIM = "#8b93a3"
CARD_BG = "#161b24"
CARD_BORDER = "1px solid rgba(255,255,255,0.08)"


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        /* tighten the top of the page */
        .block-container {{ padding-top: 2.2rem; }}

        /* ---- animations ---- */
        @keyframes lmdFadeUp {{
            from {{ opacity: 0; transform: translateY(8px); }}
            to   {{ opacity: 1; transform: translateY(0); }}
        }}
        @keyframes lmdGlow {{
            0%, 100% {{ box-shadow: 0 0 0 0 rgba(0,216,108,0.35); }}
            50%      {{ box-shadow: 0 0 10px 2px rgba(0,216,108,0.25); }}
        }}
        .lmd-hero, .lmd-steps, .lmd-stats {{ animation: lmdFadeUp 0.45s ease both; }}
        .lmd-stat:nth-child(1) {{ animation: lmdFadeUp 0.45s ease 0.00s both; }}
        .lmd-stat:nth-child(2) {{ animation: lmdFadeUp 0.45s ease 0.08s both; }}
        .lmd-stat:nth-child(3) {{ animation: lmdFadeUp 0.45s ease 0.16s both; }}
        @media (prefers-reduced-motion: reduce) {{
            .lmd-hero, .lmd-steps, .lmd-stats, .lmd-stat {{ animation: none; }}
            .lmd-step.active {{ animation: none; }}
        }}

        /* app title */
        .lmd-hero h1 {{
            font-size: 2.1rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            margin: 0;
            background: linear-gradient(90deg, {ACCENT}, #7dd3fc);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
        }}
        .lmd-hero p {{ color: {DIM}; margin: 0.15rem 0 0 0; font-size: 0.95rem; }}

        /* step chips */
        .lmd-steps {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.6rem 0 0.2rem 0; }}
        .lmd-step {{
            display: inline-flex; align-items: center; gap: 0.4rem;
            padding: 0.28rem 0.8rem; border-radius: 999px;
            font-size: 0.82rem; font-weight: 500;
            border: 1px solid rgba(255,255,255,0.10);
            color: {DIM}; background: rgba(255,255,255,0.03);
            transition: color 0.25s ease, background 0.25s ease, border-color 0.25s ease;
        }}
        .lmd-step.active {{ animation: lmdGlow 2.4s ease-in-out infinite; }}
        .lmd-step.done {{
            color: {ACCENT}; border-color: rgba(45,212,191,0.35);
            background: rgba(45,212,191,0.08);
        }}
        .lmd-step.active {{
            color: #0e1117; background: {ACCENT}; border-color: {ACCENT};
            font-weight: 600;
        }}

        /* stat pills on the summary page */
        .lmd-stats {{ display: flex; gap: 0.75rem; flex-wrap: wrap; margin: 0.4rem 0 1rem 0; }}
        .lmd-stat {{
            flex: 1; min-width: 150px;
            background: {CARD_BG}; border: {CARD_BORDER}; border-radius: 14px;
            padding: 0.9rem 1.1rem;
        }}
        .lmd-stat .v {{ font-size: 1.7rem; font-weight: 700; line-height: 1.15; }}
        .lmd-stat .k {{ color: {DIM}; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.06em; }}
        .lmd-stat.accent .v {{ color: {ACCENT}; }}

        /* section/checkbox lists breathe a little */
        div[data-testid="stCheckbox"] {{ margin-bottom: 0.15rem; }}

        /* full-width primary buttons feel more app-like */
        div[data-testid="stButton"] > button,
        div[data-testid="stDownloadButton"] > button {{
            border-radius: 10px;
            transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
        }}
        div[data-testid="stButton"] > button:hover,
        div[data-testid="stDownloadButton"] > button:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 14px rgba(0,0,0,0.35);
        }}
        div[data-testid="stButton"] > button:active,
        div[data-testid="stDownloadButton"] > button:active {{
            transform: translateY(0);
        }}

        /* file uploader card */
        section[data-testid="stFileUploaderDropzone"] {{
            border-radius: 14px;
            border: 1.5px dashed rgba(45,212,191,0.45);
            background: rgba(45,212,191,0.04);
            transition: border-color 0.2s ease, background 0.2s ease;
        }}
        section[data-testid="stFileUploaderDropzone"]:hover {{
            border-color: rgba(45,212,191,0.85);
            background: rgba(45,212,191,0.08);
        }}

        /* expanders as subtle cards */
        details[data-testid="stExpander"] {{
            border-radius: 10px; border: {CARD_BORDER};
            transition: border-color 0.2s ease;
        }}
        details[data-testid="stExpander"]:hover {{
            border-color: rgba(255,255,255,0.22);
        }}

        /* checkbox rows highlight on hover so long lists are easier to track */
        div[data-testid="stCheckbox"] {{
            border-radius: 8px;
            transition: background 0.15s ease;
        }}
        div[data-testid="stCheckbox"]:hover {{
            background: rgba(255,255,255,0.04);
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_hero() -> None:
    st.markdown(
        '<div class="lmd-hero"><h1>LMD-Fixer</h1>'
        "<p>Clean up laser metal deposition G-code — review every change before it happens.</p></div>",
        unsafe_allow_html=True,
    )


def render_stepper(labels: list[str], current: int) -> None:
    """Horizontal chip stepper: done / active / pending."""
    chips = []
    for i, label in enumerate(labels):
        if i < current:
            chips.append(f'<span class="lmd-step done">&#10003; {html.escape(label)}</span>')
        elif i == current:
            chips.append(f'<span class="lmd-step active">{i + 1} &middot; {html.escape(label)}</span>')
        else:
            chips.append(f'<span class="lmd-step">{i + 1} &middot; {html.escape(label)}</span>')
    done_chip = '<span class="lmd-step done">&#10003; Done</span>' if current >= len(labels) else \
        '<span class="lmd-step">&#9873; Done</span>'
    st.markdown(f'<div class="lmd-steps">{"".join(chips)}{done_chip}</div>', unsafe_allow_html=True)


def render_stats(n_original: int, n_final: int) -> None:
    removed = n_original - n_final
    pct = f"{removed / n_original * 100:.1f}%" if n_original else "0%"
    st.markdown(
        '<div class="lmd-stats">'
        f'<div class="lmd-stat"><div class="v">{n_original:,}</div><div class="k">Original lines</div></div>'
        f'<div class="lmd-stat"><div class="v">{n_final:,}</div><div class="k">Final lines</div></div>'
        f'<div class="lmd-stat accent"><div class="v">&minus;{removed:,}</div><div class="k">Lines removed ({pct})</div></div>'
        "</div>",
        unsafe_allow_html=True,
    )


def render_side_by_side_diff(
    original_lines: list[str], final_lines: list[str], n_context: int = 3
) -> str:
    """Builds an HTML two-column diff: original on the left, final on the right,
    with removed lines highlighted red on the left and added/changed lines
    highlighted green on the right. Long runs of unchanged lines are collapsed
    to `n_context` lines either side of each change, with a marker row showing
    how many lines were hidden.
    """
    matcher = difflib.SequenceMatcher(a=original_lines, b=final_lines, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            n = i2 - i1
            if n > 2 * n_context + 1:
                for k in range(n_context):
                    rows.append((original_lines[i1 + k], final_lines[j1 + k], "equal"))
                rows.append((f"{n - 2 * n_context} unchanged lines hidden", "", "gap"))
                for k in range(n - n_context, n):
                    rows.append((original_lines[i1 + k], final_lines[j1 + k], "equal"))
            else:
                for oi, fi in zip(range(i1, i2), range(j1, j2)):
                    rows.append((original_lines[oi], final_lines[fi], "equal"))
        elif tag == "delete":
            for oi in range(i1, i2):
                rows.append((original_lines[oi], "", "delete"))
        elif tag == "insert":
            for fi in range(j1, j2):
                rows.append(("", final_lines[fi], "insert"))
        elif tag == "replace":
            left = list(range(i1, i2))
            right = list(range(j1, j2))
            for k in range(max(len(left), len(right))):
                otext = original_lines[left[k]] if k < len(left) else ""
                ftext = final_lines[right[k]] if k < len(right) else ""
                rows.append((otext, ftext, "replace"))

    row_colors = {
        "equal": ("transparent", "transparent"),
        "delete": ("rgba(248,81,73,0.16)", "transparent"),
        "insert": ("transparent", "rgba(63,185,80,0.14)"),
        "replace": ("rgba(248,81,73,0.16)", "rgba(63,185,80,0.14)"),
    }

    html_rows = []
    for otext, ftext, tag in rows:
        if tag == "gap":
            html_rows.append(
                '<tr><td colspan="2" style="background:rgba(255,255,255,0.03); color:#8b93a3; '
                'text-align:center; padding:3px 6px; font-style:italic;">'
                f"&#8943; {html.escape(otext)} &#8943;</td></tr>"
            )
            continue
        left_bg, right_bg = row_colors[tag]
        html_rows.append(
            "<tr>"
            f'<td style="background:{left_bg}; padding:1px 8px; white-space:pre;">{html.escape(otext)}</td>'
            f'<td style="background:{right_bg}; padding:1px 8px; white-space:pre;">{html.escape(ftext)}</td>'
            "</tr>"
        )

    return (
        '<div style="max-height:600px; overflow:auto; font-family:ui-monospace,Consolas,monospace; '
        'font-size:12px; border:1px solid rgba(255,255,255,0.10); border-radius:12px;">'
        '<table style="border-collapse:collapse; width:100%;">'
        '<thead style="position:sticky; top:0; background:#161b24;">'
        '<tr><th style="text-align:left; padding:6px 8px;">Original</th>'
        '<th style="text-align:left; padding:6px 8px;">Final</th></tr>'
        "</thead><tbody>" + "".join(html_rows) + "</tbody></table></div>"
    )


def render_change_context(lines: list[str], start: int, end: int, n_context: int = 3) -> str:
    """Plain-text excerpt around a change: line numbers, with the changed
    lines marked by a leading arrow."""
    lo = max(0, start - n_context)
    hi = min(len(lines) - 1, end + n_context)
    out = []
    for i in range(lo, hi + 1):
        marker = "->" if start <= i <= end else "  "
        out.append(f"{marker} {i + 1:>6}  {lines[i]}")
    return "\n".join(out)


def _sync_children_to_master(master_key: str, child_keys: list[str]) -> None:
    """on_change callback for a master 'all' checkbox: pushes its value into
    every per-change checkbox's session state (Streamlit ignores a keyed
    widget's value= once the key exists in session state, so this is the
    only way the master toggle can move already-rendered checkboxes)."""
    value = st.session_state[master_key]
    for key in child_keys:
        st.session_state[key] = value


# Session-state key of each fix's sidebar toggle. Not accept_*-prefixed: the
# chosen set of fixes must survive the review-state reset it triggers.
FIX_TOGGLE_KEY = "fix_toggle_{}"


def _clear_review_widget_state() -> None:
    """Drops all per-change checkbox state so a fresh review starts from
    each fix's defaults instead of inheriting earlier choices."""
    for key in [k for k in st.session_state if isinstance(k, str) and k.startswith("accept_")]:
        del st.session_state[key]


def _advance(summary: str) -> None:
    """Records this step's outcome and moves to the next fix. Pushes the
    pre-step program onto the history stack so 'Back' can undo it."""
    st.session_state["history"].append(st.session_state["current_program"].copy())
    st.session_state["applied_summaries"].append(summary)
    st.session_state["fix_index"] += 1
    st.rerun()


def _apply_and_advance(new_program: GCodeProgram, summary: str) -> None:
    st.session_state["history"].append(st.session_state["current_program"].copy())
    st.session_state["current_program"] = new_program
    st.session_state["applied_summaries"].append(summary)
    st.session_state["fix_index"] += 1
    st.rerun()


def _go_back() -> None:
    """Returns to the previous fix, restoring the program as it was before
    that fix was applied. Its checkboxes keep their previous state."""
    st.session_state["current_program"] = st.session_state["history"].pop()
    st.session_state["applied_summaries"].pop()
    st.session_state["fix_index"] -= 1
    st.rerun()


def _parse_split_distances(text: str) -> tuple[list[float], bool]:
    """'20, 100.5' -> ([20.0, 100.5], True). Blank -> ([], True); anything
    unparseable -> ([], False)."""
    if not text or not text.strip():
        return [], True
    try:
        return [float(t) for t in re.split(r"[,\s;]+", text.strip()) if t], True
    except ValueError:
        return [], False


# Feed ramps, low feed -> high feed. Every one is trimmed so both ends clear
# the app surface (#272b33) and validated with the dataviz skill's
# scripts/validate_palette.py in --ordinal mode. Picked in the map's display
# settings; Plasma is the default because a single hue turned out too flat to
# read at a glance.
#
# Plasma and Viridis rise steadily in lightness across the ramp, so a brighter
# section is always a faster one and the ordering survives colour-blindness.
# Rainbow does not: its green and yellow sit at the same lightness (dL 0.005)
# so two different feeds can look identical. It's offered because it's the
# most hue-varied, not because it's the most readable — don't make it the
# default.
MAP_RAMPS: dict[str, list[str]] = {
    "Plasma — purple to yellow": ["#99159f", "#c13b82", "#e06363", "#f68d45", "#fec029", "#f0f921"],
    "Viridis — blue to yellow": ["#38598c", "#287d8e", "#1fa088", "#48c16e", "#9dd93b", "#fde725"],
    "Rainbow — blue to red": ["#434eba", "#28bceb", "#59fb73", "#dbe236", "#fa7b1f", "#b41b01"],
    "Blue — single hue": ["#1c5cab", "#3987e5", "#86b6ef", "#cde2fb"],
}
MAP_RAMP_DEFAULT = "Plasma — purple to yellow"
# Not accept_*-prefixed: a display preference should outlive a state reset.
MAP_RAMP_KEY = "map_ramp"
MAP_SIZE_KEY = "map_size"


def render_map_settings() -> tuple[list[str], int]:
    """The map's display settings, in a popover beside it. Returns the chosen
    ramp and size."""
    st.session_state.setdefault(MAP_RAMP_KEY, MAP_RAMP_DEFAULT)
    st.session_state.setdefault(MAP_SIZE_KEY, 520)
    with st.popover("⚙ Display", use_container_width=False):
        st.radio(
            "Feed colours",
            list(MAP_RAMPS),
            key=MAP_RAMP_KEY,
            captions=[
                "Brightness rises with feed — recommended",
                "Brightness rises with feed; best for colour-blind viewers",
                "Most hue variety, but green and yellow share a brightness",
                "One hue; subtle, hard to read small differences",
            ],
        )
        # Capped at 900: the map shares a row with the editor, and a chart
        # wider than its column gets clipped rather than shrunk.
        st.slider("Map size (px)", min_value=320, max_value=900, step=40, key=MAP_SIZE_KEY)
    return MAP_RAMPS[st.session_state[MAP_RAMP_KEY]], int(st.session_state[MAP_SIZE_KEY])


# Like the map's display settings, this outlives a review-state reset: chord
# length is a property of the machine and the material, set once, not a
# per-file review choice. It only has any effect on sections given a ramp.
RAMP_CHORD_KEY = "ramp_chord_mm"


def render_ramp_settings() -> float:
    """Settings for ramped sections, in a popover beside the feed editor.
    Returns the chord length long moves are split to."""
    st.session_state.setdefault(RAMP_CHORD_KEY, DEFAULT_CHORD_MM)
    with st.popover("⚙ Ramp settings", use_container_width=True):
        st.number_input(
            "Split long moves into chords of (mm)",
            min_value=0.1,
            max_value=50.0,
            step=0.5,
            format="%g",
            key=RAMP_CHORD_KEY,
            help=(
                "A ramp can only change feed where there's a move to put an F word on. "
                "Curves are already short chords, but a straight can be one long move, so "
                "moves longer than this are split. Smaller = smoother ramp, more lines."
            ),
        )
        st.caption(
            f"Ramped feeds are rounded to the nearest {RAMP_FEED_STEP:g} mm/min, so moves that "
            "land on the same value share one F word. Only plain XY moves are split — a Z "
            "plunge or a rotary move is never divided."
        )
    return float(st.session_state[RAMP_CHORD_KEY])


ALL_LAYERS = "All layers"
MAP_SURFACE = "#272b33"
MAP_MARKER = "#c3c2b7"
MAP_SELECTION = "toolpath_pick"
MAP_SECTION_SELECTION = "section_pick"
MAP_FOCUS = "#f4f1e6"


def _selected_keys(chart_state, name: str, field: str) -> list[str]:
    """Keys picked on the map, read from the chart's session state. A point
    selection arrives as a list of `{field: ...}` dicts, or `{}` when empty."""
    try:
        points = chart_state["selection"][name]
    except (KeyError, TypeError):
        return []
    return [p[field] for p in points if isinstance(p, dict) and field in p]


def _selected_toolpath_keys(chart_state) -> list[str]:
    """Toolpath keys picked by clicking a toolpath's box on the map."""
    return _selected_keys(chart_state, MAP_SELECTION, "key")


def cursor_from_selection(chart_state, known_keys) -> tuple[str, int] | None:
    """The section the map is pointing at, or None for "nothing selected".

    Only meaningful inside the chart's `on_select` callback, where an empty
    selection means the user clicked empty space. Read outside it, an empty
    selection can also be a chart that re-rendered.
    """
    chosen = [k for k in _selected_section_keys(chart_state) if k.rsplit("|", 1)[0] in known_keys]
    if not chosen:
        return None
    key, number = chosen[0].rsplit("|", 1)
    return (key, int(number))


def _selected_section_keys(chart_state) -> list[str]:
    """`"<toolpath key>|<section number>"` for each section picked by clicking the
    path itself on the map."""
    return _selected_keys(chart_state, MAP_SECTION_SELECTION, "pkey")


def densify_ramped_path(points: list[dict], ramp_for_section: dict) -> list[dict]:
    """Adds plotting vertices inside ramped sections so the feed gradient is
    actually visible.

    A line mark is one flat colour per segment, so a section is only drawn as
    a gradient if it has vertices to break it up. Curves arrive as ~0.5 mm
    chords and have plenty, but the CAM writes a straight as one or two very
    long moves — 7 of the 16 straights in a test toolpath come through as a
    single segment, which can only ever be one colour. The G-code is right in
    those sections; the preview just had nowhere to show it.

    Each segment of a ramped section is split into as many pieces as the feed
    has `RAMP_FEED_STEP` steps across it, which is exactly the number of
    distinct colours it can show — no more, so the added points stay cheap.
    Untouched sections are passed through as they are.
    """
    if not ramp_for_section:
        return points

    out: list[dict] = []
    previous: dict | None = None
    for row in points:
        key = (row["toolpath"], row["section"])
        pair = ramp_for_section.get(key)
        same_run = (
            previous is not None
            and previous["run"] == row["run"]
            and (previous["toolpath"], previous["section"]) == key
        )
        if pair and same_run:
            start_feed, end_feed = pair
            span = end_feed - start_feed
            feed_from = round_to_step(start_feed + span * previous["t"])
            feed_to = round_to_step(start_feed + span * row["t"])
            steps = int(abs(feed_to - feed_from) / RAMP_FEED_STEP)
            for k in range(1, steps):
                f = k / steps
                out.append({
                    **row,
                    "x": previous["x"] + (row["x"] - previous["x"]) * f,
                    "y": previous["y"] + (row["y"] - previous["y"]) * f,
                    "t": previous["t"] + (row["t"] - previous["t"]) * f,
                })
        out.append(row)
        previous = row
    return out


def build_toolpath_map(
    toolpaths,
    points: list[dict],
    feed_for_section: dict,
    overridden: set,
    focus: str | None = None,
    size: int = 520,
    ramp: list[str] | None = None,
    ramp_for_section: dict | None = None,
) -> alt.LayerChart | None:
    """Plan (XY) view of every toolpath's deposition path, coloured by the
    feed each section will run at, with a dot where each later section begins.

    Colour is per *vertex*, not per section, so a section given a ramp is drawn
    with the feed shading along its length — the same thing the machine will
    do. `ramp_for_section` maps a section to its (start, end) feed; sections
    absent from it are flat-coloured from `feed_for_section` as before.

    Two click targets, because a 2px line is too thin to hit: each toolpath's
    bounding box selects the whole toolpath, and a fat transparent copy of the
    path on top selects the single section under the cursor. Shift-click adds,
    clicking empty space clears.

    `focus` is the pkey of the section being edited right now. It's drawn from
    the data rather than from the Vega selection, because the section editor's
    Prev/Next buttons move it too and a chart selection can't be written back
    into the browser from here.
    """
    if not points:
        return None
    key_of = {tp.start_index: f"{tp.start_index}:{tp.name}" for tp in toolpaths}
    section_of = {(tp.start_index, sec.number): (tp, sec) for tp in toolpaths for sec in tp.sections}
    ramp_for_section = ramp_for_section or {}
    pts = pd.DataFrame(densify_ramped_path(points, ramp_for_section))
    pts["key"] = pts["toolpath"].map(key_of)
    pts["pkey"] = [f"{k}|{p}" for k, p in zip(pts["key"], pts["section"])]

    def vertex_feed(t: int, p: int, along: float) -> float | None:
        """The feed at one vertex — interpolated where the section ramps, so
        the drawn colour follows the feed the machine will actually run."""
        pair = ramp_for_section.get((t, p))
        if pair:
            start_feed, end_feed = pair
            return round_to_step(start_feed + (end_feed - start_feed) * float(along))
        return feed_for_section.get((t, p))

    if "t" not in pts:
        pts["t"] = 0.0
    # Rounded before it's serialised: the source files carry 3 dp, and full
    # float repr on tens of thousands of vertices is pure payload.
    pts["x"] = pts["x"].round(3)
    pts["y"] = pts["y"].round(3)
    pts["feed"] = [round(vertex_feed(t, p, a) or 0, 2) or None
                   for t, p, a in zip(pts["toolpath"], pts["section"], pts["t"])]
    pts["changed"] = [(t, p) in overridden for t, p in zip(pts["toolpath"], pts["section"])]
    pts["order"] = range(len(pts))
    # Only the geometry and the fields Vega actually encodes stay per vertex.
    # Everything the tooltip shows is per *section*, so it lives in a frame of
    # one row per section and is joined on in the browser — the difference
    # between carrying five strings on ~11,000 rows and on ~1,300.
    pts = pts[["toolpath", "section", "run", "x", "y", "key", "pkey", "feed", "changed", "order"]]

    def feed_label(t: int, p: int) -> str:
        pair = ramp_for_section.get((t, p))
        if pair:
            return f"{format_feed(pair[0])} → {format_feed(pair[1])} (ramp)"
        value = feed_for_section.get((t, p))
        return "—" if value is None else format_feed(value)

    descriptions = []
    for (t, p), (tp, sec) in section_of.items():
        kind = {"line": "straight", "arc": "arc"}.get(sec.kind, "")
        descriptions.append({
            "pkey": f"{key_of[t]}|{p}",
            "Toolpath": tp.name,
            "Section": f"Section {sec.number}/{len(tp.sections)}" + (f" · {kind}" if kind else ""),
            "Layer": (
                f"{sec.layer}" + (f" (Z{sec.layer_z:g})" if sec.layer_z is not None else "")
                if sec.layer else "—"
            ),
            "Section length (mm)": round(sec.to_mm - sec.from_mm, 2),
            "Feed": feed_label(t, p),
            "Change": "new" if (t, p) in overridden else "unchanged",
        })
    tooltip_fields = ["Toolpath", "Section", "Section length (mm)", "Feed", "Change"]
    # Only worth a tooltip row where something is actually split by layer —
    # and the column is dropped rather than just left out of the tooltip,
    # since `section_desc` is serialised into the spec whole.
    if any(d["Layer"] != "—" for d in descriptions):
        tooltip_fields.insert(2, "Layer")
    else:
        for d in descriptions:
            del d["Layer"]
    section_desc = pd.DataFrame(descriptions)

    boxes = []
    for tp in toolpaths:
        tp_pts = pts[pts["toolpath"] == tp.start_index]
        if tp_pts.empty:
            continue
        def describe_feed(sec) -> str:
            pair = ramp_for_section.get((tp.start_index, sec.number))
            if pair:
                return f"{sec.number}: F{format_feed(pair[0])}→F{format_feed(pair[1])} (ramp)"
            value = feed_for_section[(tp.start_index, sec.number)]
            suffix = " (new)" if (tp.start_index, sec.number) in overridden else ""
            return f"{sec.number}: F{format_feed(value)}{suffix}"

        sections_desc = " · ".join(
            describe_feed(sec)
            for sec in tp.sections
            if not sec.is_empty
            and (
                (tp.start_index, sec.number) in ramp_for_section
                or feed_for_section.get((tp.start_index, sec.number)) is not None
            )
        )
        boxes.append(
            {
                "key": key_of[tp.start_index],
                "Toolpath": tp.name,
                "Length (mm)": round(tp.length_mm, 1),
                "Sections": sections_desc or "—",
                "x0": tp_pts["x"].min() - 2, "x1": tp_pts["x"].max() + 2,
                "y0": tp_pts["y"].min() - 2, "y1": tp_pts["y"].max() + 2,
            }
        )
    boxes_df = pd.DataFrame(boxes)

    # True-aspect plan view: one millimetre is the same number of pixels on
    # both axes, so a square part looks square. `size` is the long edge in
    # pixels; the short axis gets whatever that scale gives it. The caller must
    # render this with use_container_width=False or Streamlit stretches the
    # width to the container and the aspect goes with it.
    x_lo, x_hi = boxes_df["x0"].min() - 3, boxes_df["x1"].max() + 3
    y_lo, y_hi = boxes_df["y0"].min() - 3, boxes_df["y1"].max() + 3
    span_x, span_y = max(x_hi - x_lo, 1e-6), max(y_hi - y_lo, 1e-6)
    px_per_mm = size / max(span_x, span_y)
    width, height = span_x * px_per_mm, span_y * px_per_mm
    x_enc = alt.X("x:Q", title="X (mm)", scale=alt.Scale(domain=[x_lo, x_hi], zero=False, nice=False))
    y_enc = alt.Y("y:Q", title="Y (mm)", scale=alt.Scale(domain=[y_lo, y_hi], zero=False, nice=False))

    pick = alt.selection_point(name=MAP_SELECTION, fields=["key"], on="click")
    pick_section = alt.selection_point(name=MAP_SECTION_SELECTION, fields=["pkey"], on="click")
    # Empty selections match everything, so the intersection means "whatever is
    # narrowed down right now" whether the user picked a toolpath, a section, or
    # nothing at all.
    in_focus = pick & pick_section

    # Colour by the feed each section will actually run at. Sections that were
    # given a new feed are also drawn thicker, so "what did I change" stays
    # readable without spending the colour channel on it.
    ramp = ramp or MAP_RAMPS[MAP_RAMP_DEFAULT]
    feeds = pts["feed"].dropna()
    if feeds.nunique() > 1:
        lo, hi = float(feeds.min()), float(feeds.max())
        stops = len(ramp)
        color = alt.Color(
            "feed:Q",
            title="Feed",
            scale=alt.Scale(domain=[lo + (hi - lo) * i / (stops - 1) for i in range(stops)], range=ramp),
            legend=alt.Legend(title=["Feed", "(mm/min)"], orient="right", gradientLength=160),
        )
    else:
        color = alt.value(ramp[len(ramp) // 2])

    # The layers drawn from `pts` take it from the enclosing
    # `alt.layer(..., data=pts)` rather than each holding it. Altair already
    # hashes identical frames into one entry of the spec's `datasets`, so this
    # is about saying once where the vertices come from, not about size — what
    # the payload actually responds to is the columns on `pts` (above).
    paths = (
        alt.Chart()
        .mark_line(strokeCap="round", strokeJoin="round")
        .encode(
            x=x_enc, y=y_enc, detail="run:N", order="order:Q", color=color,
            strokeWidth=alt.condition(alt.datum.changed, alt.value(3.5), alt.value(2)),
            opacity=alt.condition(in_focus, alt.value(1.0), alt.value(0.2)),
        )
    )

    # Fat invisible copy of the path: the click target for a single section, and
    # the highlight once one is picked.
    section_targets = (
        alt.Chart()
        .mark_line(strokeWidth=12, strokeCap="round", strokeJoin="round")
        .encode(
            x=x_enc, y=y_enc, detail="run:N", order="order:Q",
            stroke=alt.value(MAP_FOCUS),
            opacity=alt.condition(pick_section, alt.value(0.30), alt.value(0.001), empty=False),
            tooltip=[f"{f}:{'Q' if f.endswith('(mm)') else 'N'}" for f in tooltip_fields],
        )
        # The tooltip text is joined on here rather than carried on every
        # vertex — one row per section instead of one per point.
        .transform_lookup(
            lookup="pkey",
            from_=alt.LookupData(section_desc, key="pkey", fields=tooltip_fields),
        )
        .add_params(pick_section)
    )

    starts = pts[pts["section"] > 1].groupby(["toolpath", "section"], as_index=False).first()
    split_marks = (
        alt.Chart(starts)
        .mark_point(filled=True, size=70, color=MAP_MARKER, stroke=MAP_SURFACE, strokeWidth=2, opacity=1)
        .encode(x=x_enc, y=y_enc)
    )

    targets = (
        alt.Chart(boxes_df)
        .mark_rect(fill="#ffffff", fillOpacity=0.02, cornerRadius=4)
        .encode(
            x=alt.X("x0:Q", scale=alt.Scale(domain=[x_lo, x_hi], zero=False, nice=False)),
            x2="x1:Q",
            y=alt.Y("y0:Q", scale=alt.Scale(domain=[y_lo, y_hi], zero=False, nice=False)),
            y2="y1:Q",
            stroke=alt.condition(pick, alt.value(ACCENT), alt.value("rgba(255,255,255,0.10)"), empty=False),
            strokeWidth=alt.condition(pick, alt.value(2), alt.value(1), empty=False),
            tooltip=["Toolpath:N", "Length (mm):Q", "Sections:N"],
        )
        .add_params(pick)
    )

    layers = [paths, split_marks, targets, section_targets]

    focus_pts = pts[pts["pkey"] == focus] if focus else pts.iloc[0:0]
    if not focus_pts.empty:
        # Under the fat click target, so it reads as a glow around the section.
        # Filtered in the browser off the shared dataset, so the focused
        # section's vertices aren't sent a second time.
        layers.insert(1, (
            alt.Chart()
            .mark_line(strokeWidth=9, strokeCap="round", strokeJoin="round",
                       color=MAP_FOCUS, opacity=0.55)
            .encode(x=x_enc, y=y_enc, detail="run:N", order="order:Q")
            .transform_filter(alt.datum.pkey == focus)
        ))
        # Two rows, so this one keeps its own data.
        ends = focus_pts.iloc[[0, -1]]
        layers.append(
            alt.Chart(ends)
            .mark_point(filled=True, size=60, color=MAP_FOCUS, stroke=MAP_SURFACE, strokeWidth=2, opacity=1)
            .encode(x=x_enc, y=y_enc)
        )

    # Click targets sit on top so a click anywhere in a box selects it; the
    # section targets sit higher still, so a click on the path itself picks
    # the section rather than the whole toolpath.
    #
    # autosize must be set here, not left to Streamlit: it injects
    # `{"type": "fit"}` whenever the spec doesn't carry one, and `fit` rescales
    # the view to the container, which silently throws away the width/height
    # computed above and squashes the plan view. `pad` keeps the content size.
    return (
        alt.layer(*layers, data=pts)
        .properties(width=width, height=height)
        .properties(autosize=alt.AutoSizeParams(type="pad", contains="padding"))
    )


def render_feed_sections_step(program: GCodeProgram, fix_id: str, fix_index: int) -> None:
    """Custom review step for adjust_section_feeds: the user divides toolpaths
    into sections and types a feed per section, rather than accepting/rejecting
    proposed changes. The resulting F-word edits depend on each other (a
    restated feed at a section boundary is what stops an override leaking), so
    they're previewed and applied all-or-nothing, not per line.

    A section takes one feed, or two — `feeds_state` holds the start and
    `ramps_state` the optional end, and with both the section ramps between
    them. Ramping can *add* lines (a long straight is split into chords so the
    feed has somewhere to step), which is why the chord length lives in a
    settings popover rather than being fixed.

    Choices live in `accept_*`-prefixed session state so they're cleared with
    the rest of the review state. They're keyed by toolpath name + start line
    so that if an earlier step changes the program, stale overrides are
    dropped rather than landing on a different toolpath. Each data_editor is
    fed a base frame that's frozen for its key (feeding edited output back in
    as `data` makes Streamlit drop edits); the key changes whenever the table
    structure changes or a bulk action rewrites values.

    The toolpath map is drawn last (into a slot reserved at the top) so it
    reflects this run's table edits; its selection is read from session state
    up front, and scopes the tables and bulk edits to the picked toolpaths.
    """
    ss = st.session_state
    prefix = f"accept_{fix_id}_{fix_index}"
    sections_state: dict[str, int] = ss.setdefault(f"{prefix}_parts", {})
    splits_state: dict[str, str] = ss.setdefault(f"{prefix}_splits", {})
    geo_state: dict[str, bool] = ss.setdefault(f"{prefix}_geo", {})
    # Layers cut *as well as* whatever division is chosen above, rather than
    # instead of it, so a stacked build can be divided per straight per layer.
    layer_state: dict[str, bool] = ss.setdefault(f"{prefix}_layers", {})
    feeds_state: dict[tuple[str, str, int], float] = ss.setdefault(f"{prefix}_feeds", {})
    # The end feed of a ramped section, keyed exactly like `feeds_state` — which
    # holds the start feed. A section ramps only when it has both; an end feed
    # on its own is meaningless and is ignored by the fix.
    ramps_state: dict[tuple[str, str, int], float] = ss.setdefault(f"{prefix}_ramps", {})
    ss.setdefault(f"{prefix}_ver", 0)

    base_toolpaths = analyse_toolpaths(program)
    if not base_toolpaths:
        st.success("No toolpaths found — nothing to adjust.")
        return

    def tp_key(tp) -> str:
        return f"{tp.start_index}:{tp.name}"

    layered = {tp_key(tp): tp.n_layers > 1 for tp in base_toolpaths}

    def spec_for(key: str) -> str:
        # Layers compose with the rest, so they prefix the spec rather than
        # replacing it — toggling them has to invalidate the feed keys too,
        # since it renumbers every section in the toolpath.
        base = "geo" if geo_state.get(key) else None
        if base is None:
            distances, ok = _parse_split_distances(splits_state.get(key, ""))
            base = (
                "at " + ",".join(f"{d:g}" for d in sorted(set(distances)))
                if ok and distances
                else f"n{sections_state.get(key, 1)}"
            )
        return ("L+" if layer_state.get(key) and layered.get(key) else "") + base

    start_by_key = {tp_key(tp): tp.start_index for tp in base_toolpaths}

    def split_options() -> tuple[dict, dict, set, set]:
        """The current division state as `analyse_toolpaths` arguments."""
        return (
            {start_by_key[k]: n for k, n in sections_state.items() if k in start_by_key},
            {start_by_key[k]: _parse_split_distances(t)[0] for k, t in splits_state.items() if k in start_by_key},
            {start_by_key[k] for k, on in geo_state.items() if on and k in start_by_key},
            {start_by_key[k] for k, on in layer_state.items() if on and k in start_by_key},
        )

    n_layered = sum(1 for tp in base_toolpaths if tp.n_layers > 1)
    st.info(
        "**Parts** are the deposition areas on the bed, each holding one or more **toolpaths**, and each "
        "toolpath is divided into **sections** that can run at their own feed. Tick **Lines & arcs** to "
        "divide where the path stops going straight and starts curving. A section can hold one feed, or "
        "**ramp** between a start and an end feed along its length. "
        "Blank feed = leave unchanged. Split points snap to the nearest move."
        + (
            f"  \n{n_layered} toolpath(s) here build more than one **layer** inside a single block — "
            "tick **Layers** to give each layer its own sections."
            if n_layered
            else ""
        )
    )

    map_key = f"{prefix}_map"
    known_keys = {tp_key(tp) for tp in base_toolpaths}
    picked = [k for k in _selected_toolpath_keys(ss.get(map_key)) if k in known_keys]

    # The section being edited. The Prev/Next buttons move it too, so it's
    # held here rather than read off the chart — a chart selection can't be
    # written back to the browser from Python.
    #
    # It's maintained in the chart's `on_select` callback, which only runs when
    # the user actually changes the selection. Reading the chart's state each
    # run instead can't tell a click on empty space from a re-render that
    # dropped the selection, so clearing had to be ignored — which left no way
    # to deselect by clicking the map.
    cursor_key = f"{prefix}_cursor"

    def on_map_select() -> None:
        ss[cursor_key] = cursor_from_selection(ss.get(map_key), known_keys)

    cursor = ss.get(cursor_key)
    if cursor and cursor[0] not in known_keys:
        cursor = ss[cursor_key] = None

    # Editing a section means working inside its toolpath, so it scopes the
    # tables on its own.
    if cursor:
        picked = [cursor[0]]
    # Deposition areas. Every toolpath belongs to exactly one, so this is
    # also the top level of the naming: part > toolpath > section.
    parts = group_into_parts(base_toolpaths)
    part_of_key = {tp_key(tp): pt for pt in parts for tp in pt.toolpaths}
    part_name_of = {k: pt.name for k, pt in part_of_key.items()}

    # A part filter for programs with more than one deposition area, plus the
    # way back out to the whole bed. The cursor overrides the filter, so
    # clicking a section on the map always wins.
    part_key = f"{prefix}_part"
    # Layers of one build sit on top of each other in XY, so the map can only
    # show a single layer at a time — this picks which. It's a view filter,
    # so it's rendered beside the map, but the key is needed here for reset.
    layer_key = f"{prefix}_layerview"
    options = ["All parts"] + [f"{pt.name} — {pt.where}" for pt in parts]

    def reset_view() -> None:
        ss[cursor_key] = None
        ss[part_key] = "All parts"
        ss[layer_key] = ALL_LAYERS

    col_part, col_reset = st.columns([3, 1], vertical_alignment="bottom")
    if len(parts) > 1:
        chosen = col_part.selectbox(
            "Part", options, key=part_key,
            help="One deposition area. Narrows the map and the tables to that area.",
        )
        if chosen != "All parts" and not cursor:
            wanted = parts[options.index(chosen) - 1]
            picked = [tp_key(tp) for tp in wanted.toolpaths]
    col_reset.button(
        "⤢ Whole program",
        use_container_width=True,
        help="Clear the section and part selection and show every part on the map",
        disabled=not cursor and ss.get(part_key, "All parts") == "All parts",
        on_click=reset_view,
        key=f"{prefix}_resetview",
    )

    visible = [tp for tp in base_toolpaths if tp_key(tp) in picked] or base_toolpaths
    visible_keys = {tp_key(tp) for tp in visible}

    ver = ss[f"{prefix}_ver"]

    divided = any(geo_state.values()) or any(layer_state.values()) or any(
        v > 1 for v in sections_state.values()
    ) or any(splits_state.values())
    step1 = st.expander("1 · Divide toolpaths into sections", expanded=not divided)
    step1.caption(
        "Sections = equal-length split. Or enter distances in mm (e.g. `20, 150`) to split there instead. "
        "**Lines & arcs** beats both: one section per straight run and one per curve, split at every corner. "
        "**Layers** stacks on top of whichever of those is chosen, cutting at every layer change as well. "
        "Edit any cell directly."
    )

    # The bulk controls worth keeping: a file holds ~45 near-identical
    # toolpaths and ticking each row's box by hand would be miserable.
    all_key = f"{prefix}_geo_all"
    all_layer_key = f"{prefix}_layer_all"

    def sync_geometry_to_all() -> None:
        for tp in base_toolpaths:
            geo_state[tp_key(tp)] = ss[all_key]
        ss[f"{prefix}_ver"] += 1

    def sync_layers_to_all() -> None:
        for tp in base_toolpaths:
            if tp.n_layers > 1:
                layer_state[tp_key(tp)] = ss[all_layer_key]
        ss[f"{prefix}_ver"] += 1

    col_geo_all, col_layer_all = step1.columns(2)
    col_geo_all.checkbox(
        "Split every toolpath into lines & arcs",
        value=all(geo_state.get(tp_key(tp)) for tp in base_toolpaths),
        key=all_key,
        on_change=sync_geometry_to_all,
    )
    if n_layered:
        col_layer_all.checkbox(
            "Split every toolpath into layers",
            value=all(layer_state.get(tp_key(tp)) for tp in base_toolpaths if tp.n_layers > 1),
            key=all_layer_key,
            on_change=sync_layers_to_all,
            help="Only affects the toolpaths that actually build more than one layer.",
        )
    tp_sig = f"{ver}_{hash(tuple(tp_key(tp) for tp in visible))}"
    tp_base_key = f"{prefix}_tpbase_{tp_sig}"
    if tp_base_key not in ss:
        ss[tp_base_key] = pd.DataFrame(
            {
                "key": [tp_key(tp) for tp in visible],
                "Part": [part_name_of.get(tp_key(tp), "—") for tp in visible],
                "Toolpath": [tp.name for tp in visible],
                "Lines": [f"{tp.start_index + 1}–{tp.end_index + 1}" for tp in visible],
                "Length (mm)": [
                    f"{tp.length_mm:.1f}" if tp.measured_with_laser else f"{tp.length_mm:.1f} (no laser-on)"
                    for tp in visible
                ],
                "Feed": [", ".join(format_feed(f) for f in tp.original_feeds) or "—" for tp in visible],
                "Z levels": [
                    f"{tp.n_layers} @ {', '.join(f'{z:g}' for z in tp.layer_zs[:4])}"
                    + ("…" if len(tp.layer_zs) > 4 else "")
                    if tp.n_layers > 1
                    else "1"
                    for tp in visible
                ],
                "Layers": [bool(layer_state.get(tp_key(tp))) for tp in visible],
                "Lines & arcs": [bool(geo_state.get(tp_key(tp))) for tp in visible],
                "Sections": [sections_state.get(tp_key(tp), 1) for tp in visible],
                "Split at (mm)": [splits_state.get(tp_key(tp), "") for tp in visible],
            }
        )
    edited_tp = step1.data_editor(
        ss[tp_base_key],
        key=f"{prefix}_tp_{tp_sig}",
        hide_index=True,
        use_container_width=True,
        height=min(300, 38 + 35 * len(visible)),
        column_order=(["Part", "Toolpath", "Lines", "Length (mm)", "Feed"]
                      + (["Z levels", "Layers"] if n_layered else [])
                      + ["Lines & arcs", "Sections", "Split at (mm)"]),
        disabled=["Part", "Toolpath", "Lines", "Length (mm)", "Feed", "Z levels"],
        column_config={
            "Z levels": st.column_config.TextColumn(
                help="Layers found in this toolpath, by the Z they deposit at"
            ),
            "Layers": st.column_config.CheckboxColumn(
                help="Also cut at every layer change — combines with the columns to its right"
            ),
            "Lines & arcs": st.column_config.CheckboxColumn(
                help="Split by the geometry itself; overrides Sections and Split at"
            ),
            "Sections": st.column_config.NumberColumn(min_value=1, max_value=50, step=1, required=True),
            "Split at (mm)": st.column_config.TextColumn(help="Comma-separated distances; overrides Sections"),
        },
    )

    bad_splits = []
    for row in edited_tp.to_dict("records"):
        key = row["key"]
        geo_state[key] = bool(row["Lines & arcs"])
        # Ticking Layers on a toolpath that only has one does nothing in the
        # fix, so don't record it either — it would only churn the spec key.
        layer_state[key] = bool(row.get("Layers")) and layered.get(key, False)
        sections_state[key] = 1 if pd.isna(row["Sections"]) else max(1, int(row["Sections"]))
        text = row["Split at (mm)"] if isinstance(row["Split at (mm)"], str) else ""
        splits_state[key] = text
        if not _parse_split_distances(text)[1]:
            bad_splits.append(row["Toolpath"])
    if bad_splits:
        step1.warning(f"Couldn't read split distances for: {', '.join(bad_splits)} — using Sections instead.")

    sections_opt, splits_opt, geo_opt, layers_opt = split_options()
    toolpaths = analyse_toolpaths(program, sections_opt, splits_opt, geo_opt, layers_opt)

    # The working view: map on the left, section editor on the right, so a
    # section can be picked and re-fed without scrolling between the two.
    # Both are slots — the map has to render after the tables to show this
    # run's edits, and the editor after the section table for the same reason,
    # but they belong side by side on screen.
    st.markdown("**2 · Pick a section and set its feed**")
    col_map, col_edit = st.columns([3, 2], gap="medium")
    map_slot = col_map.container()
    editor_slot = col_edit.container()

    # The section editor works on whatever is picked, and Prev/Next/Jump walk
    # the toolpath from there, so a whole path can be gone through section by
    # section without hunting for its row in the table.
    cursor_tp = next((tp for tp in toolpaths if tp_key(tp) == cursor[0]), None) if cursor else None
    sections = [p for p in cursor_tp.sections if not p.is_empty] if cursor_tp else []
    numbers = [p.number for p in sections]
    if cursor and cursor[1] not in numbers:
        # The split changed under the cursor; fall back to the first section.
        cursor = ss[cursor_key] = (cursor[0], numbers[0]) if numbers else None

    # Navigation runs as a widget callback rather than an `if button(): ...`
    # branch followed by st.rerun(): the click already reruns the script once,
    # and forcing a second one is what made stepping through sections redraw
    # the page twice.
    def go_to(number: int | None, tp: str | None = None) -> None:
        target = tp or (cursor[0] if cursor else None)
        ss[cursor_key] = (target, number) if target and number else None

    def render_section_editor() -> None:
        """Drawn into a slot above the table, but *after* it, so a feed
        typed into a table cell shows up here in the same rerun."""
        if cursor and cursor_tp is not None:
            sec = sections[numbers.index(cursor[1])]
            pos = numbers.index(cursor[1])
            spec = spec_for(cursor[0])
            section_key = (cursor[0], spec, sec.number)
            kind = {"line": "straight", "arc": "arc"}.get(sec.kind, "section")

            with st.container(border=True):
                col_title, col_close = st.columns([6, 1], vertical_alignment="center")
                in_layer = (
                    f" · layer {sec.layer}"
                    + (f" (Z{sec.layer_z:g})" if sec.layer_z is not None else "")
                    if sec.layer
                    else ""
                )
                col_title.markdown(
                    f"**Editing section {sec.number}/{len(cursor_tp.sections)} · {kind}{in_layer}** — "
                    f"{part_name_of.get(cursor[0], '—')} · {cursor_tp.name}"
                )
                col_close.button(
                    "✕", use_container_width=True, help="Stop editing this section",
                    on_click=go_to, args=(None,), key=f"{prefix}_close_{ver}_{sec.number}",
                )
                st.caption(
                    f"Lines {sec.start_index + 1}–{sec.end_index + 1} · "
                    f"{sec.from_mm:.1f}–{sec.to_mm:.1f} mm along the path ({sec.to_mm - sec.from_mm:.1f} mm long) · "
                    f"original feed {', '.join(format_feed(f) for f in sec.original_feeds) or '—'}"
                )

                # The value goes in the key: a keyed widget ignores `value=`
                # once it exists, so a feed set from the table would never
                # reach the box otherwise.
                widget = f"{prefix}_secfeed_{ver}_{cursor[0]}_{spec}_{sec.number}_{feeds_state.get(section_key)}"

                # Bound as defaults, not captured: this callback outlives the run
                # that made it, and the names would otherwise be whatever the rest
                # of the step left in them.
                def commit_section_feed(widget: str = widget, key: tuple = section_key) -> None:
                    value = ss.get(widget)
                    if value is None:
                        feeds_state.pop(key, None)
                    else:
                        feeds_state[key] = float(value)
                    ss[f"{prefix}_ver"] += 1

                # Stacked, not spread across one row: this sits in the narrow
                # column beside the map.
                col_feed, col_clear = st.columns([2, 1], vertical_alignment="bottom")
                col_feed.number_input(
                    "Feed for this section (mm/min)",
                    min_value=1.0,
                    step=10.0,
                    format="%g",
                    value=feeds_state.get(section_key),
                    key=widget,
                    on_change=commit_section_feed,
                    placeholder="unchanged",
                )

                def clear_section_feed(key: tuple = section_key) -> None:
                    feeds_state.pop(key, None)
                    ramps_state.pop(key, None)
                    ss[f"{prefix}_ver"] += 1

                col_clear.button(
                    "Clear", use_container_width=True, help="Leave this section's feed alone",
                    on_click=clear_section_feed, key=f"{prefix}_clear_{ver}_{sec.number}",
                )
                set_feed = feeds_state.get(section_key)

                # The end of a ramp. Filling it in turns the box above into the
                # *start* feed and the section ramps between the two; leaving it
                # empty keeps the single-feed behaviour. Disabled until there's
                # a start feed to ramp from, since one end alone means nothing.
                end_widget = (
                    f"{prefix}_secend_{ver}_{cursor[0]}_{spec}_{sec.number}_{ramps_state.get(section_key)}"
                )

                def commit_section_end(widget: str = end_widget, key: tuple = section_key) -> None:
                    value = ss.get(widget)
                    if value is None:
                        ramps_state.pop(key, None)
                    else:
                        ramps_state[key] = float(value)
                    ss[f"{prefix}_ver"] += 1

                col_end, col_settings = st.columns([2, 1], vertical_alignment="bottom")
                col_end.number_input(
                    "Ramp to (mm/min)",
                    min_value=1.0,
                    step=10.0,
                    format="%g",
                    value=ramps_state.get(section_key),
                    key=end_widget,
                    on_change=commit_section_end,
                    placeholder="no ramp",
                    disabled=set_feed is None,
                    help=(
                        "Leave empty for one feed across the whole section. Set it and the feed "
                        "ramps from the value above at the section's start to this at its end."
                    ),
                )
                with col_settings:
                    render_ramp_settings()

                set_end = ramps_state.get(section_key) if set_feed is not None else None
                if set_end is not None:
                    span = sec.to_mm - sec.from_mm
                    st.caption(
                        f"Ramping F{format_feed(set_feed)} → F{format_feed(set_end)} over "
                        f"{span:.1f} mm, in steps of {RAMP_FEED_STEP:g}."
                    )

                def apply_to(
                    keys: list,
                    value: float | None = set_feed,
                    end: float | None = set_end,
                ) -> None:
                    for key in keys:
                        feeds_state[key] = float(value)
                        # Each section ramps over its own length, not as one
                        # ramp shared across the toolpath.
                        if end is None:
                            ramps_state.pop(key, None)
                        else:
                            ramps_state[key] = float(end)
                    ss[f"{prefix}_ver"] += 1

                same_kind = [o for o in sections if o.kind == sec.kind]
                # Once a toolpath is split by layer, the layer is the natural
                # unit to work in — correcting one layer's height is the whole
                # point of varying feed between them — so that gets its own
                # button rather than only the whole-toolpath sweep. It only
                # earns its place where the layer holds more than this one
                # section and isn't simply the whole toolpath again.
                in_this_layer = [o for o in same_kind if o.layer == sec.layer]
                # Without geometry every section is the same "kind", so the
                # sweep used to be pointless and was disabled. On a layered
                # toolpath it isn't: it sets a base feed across every layer.
                sweep_useful = bool(sec.kind) or (bool(sec.layer) and len(same_kind) > 1)
                st.button(
                    f"Apply to every {kind} in this toolpath"
                    + (f" ({len(same_kind)})" if sec.layer else ""),
                    use_container_width=True,
                    disabled=set_feed is None or not sweep_useful,
                    on_click=apply_to,
                    args=([(cursor[0], spec, o.number) for o in same_kind],),
                    key=f"{prefix}_samekind_{ver}_{sec.number}",
                )
                if sec.layer and 1 < len(in_this_layer) < len(same_kind):
                    st.button(
                        f"Apply to every {kind} in layer {sec.layer} ({len(in_this_layer)})",
                        use_container_width=True,
                        disabled=set_feed is None,
                        on_click=apply_to,
                        args=([(cursor[0], spec, o.number) for o in in_this_layer],),
                        key=f"{prefix}_samelayer_{ver}_{sec.number}",
                    )

                col_prev, col_next = st.columns(2, vertical_alignment="bottom")
                col_prev.button("← Prev", use_container_width=True, disabled=pos == 0,
                                on_click=go_to, args=(numbers[max(pos - 1, 0)],),
                                key=f"{prefix}_prev_{ver}_{sec.number}")
                col_next.button("Next →", use_container_width=True, disabled=pos >= len(numbers) - 1,
                                on_click=go_to, args=(numbers[min(pos + 1, len(numbers) - 1)],),
                                key=f"{prefix}_next_{ver}_{sec.number}")

                def describe_section(number: int) -> str:
                    other = sections[numbers.index(number)]
                    bits = [f"{number}/{len(cursor_tp.sections)}"]
                    if other.layer:
                        bits.append(f"L{other.layer}")
                    bits.append({"line": "straight", "arc": "arc"}.get(other.kind, "section"))
                    bits.append(f"{other.to_mm - other.from_mm:.1f} mm")
                    set_to = feeds_state.get((cursor[0], spec, number))
                    ramp_to = ramps_state.get((cursor[0], spec, number))
                    if set_to and ramp_to:
                        bits.append(f"F{format_feed(set_to)}→F{format_feed(ramp_to)}")
                    else:
                        bits.append(f"F{format_feed(set_to)}" if set_to else "—")
                    return " · ".join(bits)

                # Built now and looked up by the widget, rather than formatted
                # on demand: a format_func closing over `feeds_state` would
                # re-label the options from whatever the dict holds *later*,
                # after the edits further down this run have changed it, and
                # the labels would no longer match the ones the widget was
                # drawn with.
                section_labels = {number: describe_section(number) for number in numbers}

                def section_label(number: int, labels: dict = section_labels) -> str:
                    return labels.get(number, str(number))

                jump_key = f"{prefix}_jump_{ver}_{cursor[0]}_{sec.number}"

                def jump_to_section(widget: str = jump_key) -> None:
                    go_to(ss[widget])

                st.selectbox(
                    "Jump to section",
                    options=numbers,
                    index=pos,
                    format_func=section_label,
                    key=jump_key,
                    on_change=jump_to_section,
                )
                st.caption(f"Section {pos + 1} of {len(numbers)} in this toolpath.")
        else:
            st.info(
                "Click a section on the map to edit its feed here, then step through the toolpath "
                "with Prev/Next. Or type feeds straight into the table below.",
                icon="👈",
            )
            if len(visible) == 1 and any(not p.is_empty for p in visible[0].sections):
                first = next(p for p in visible[0].sections if not p.is_empty)
                st.button(
                    "Edit this toolpath section by section",
                    on_click=go_to, args=(first.number, tp_key(visible[0])),
                    key=f"{prefix}_startedit_{ver}",
                )

    def section_rows() -> list[dict]:
        out = []
        for tp in toolpaths:
            key = tp_key(tp)
            if key not in visible_keys:
                continue
            spec = spec_for(key)
            for sec in tp.sections:
                out.append(
                    {
                        "key": key,
                        "spec": spec,
                        "section": sec.number,
                        "Part": part_name_of.get(key, "—"),
                        "Toolpath": tp.name,
                        "▸": "▸" if cursor == (key, sec.number) else "",
                        "Section": f"{sec.number}/{len(tp.sections)}",
                        "Layer": (
                            f"{sec.layer} (Z{sec.layer_z:g})" if sec.layer and sec.layer_z is not None
                            else str(sec.layer) if sec.layer else "—"
                        ),
                        "Type": {"line": "straight", "arc": "arc"}.get(sec.kind, "—"),
                        "Lines": "(empty)" if sec.is_empty else f"{sec.start_index + 1}–{sec.end_index + 1}",
                        "Distance (mm)": f"{sec.from_mm:.1f}–{sec.to_mm:.1f}",
                        "Original feed": ", ".join(format_feed(f) for f in sec.original_feeds) or "—",
                        "New feed": feeds_state.get((key, spec, sec.number)),
                        "Ramp to": ramps_state.get((key, spec, sec.number)),
                    }
                )
        return out

    rows = section_rows()
    n_set = sum(1 for r in rows if r["New feed"] is not None)
    table = st.expander(
        f"All sections ({len(rows)}" + (f", {n_set} with a new feed)" if n_set else ")"),
        expanded=bool(n_set) or len(rows) <= 40,
    )
    table.caption(
        "Type a feed into **New feed** on any row — same as setting it in the editor. "
        "Fill **Ramp to** as well and the section ramps from one to the other. "
        "Clear the cell to leave that section alone. ▸ marks the section being edited."
    )
    sections_sig = f"{ver}_{cursor}_{hash(tuple((r['key'], r['spec'], r['section']) for r in rows))}"
    sections_base_key = f"{prefix}_ptbase_{sections_sig}"
    if sections_base_key not in ss:
        df = pd.DataFrame(rows)
        df["New feed"] = df["New feed"].astype("float64")
        df["Ramp to"] = df["Ramp to"].astype("float64")
        ss[sections_base_key] = df
    edited_sections = table.data_editor(
        ss[sections_base_key],
        key=f"{prefix}_pt_{sections_sig}",
        hide_index=True,
        use_container_width=True,
        height=min(320, 38 + 35 * len(rows)),
        column_order=(["▸", "Part", "Toolpath", "Section"]
                      + (["Layer"] if any(r["Layer"] != "—" for r in rows) else [])
                      + ["Type", "Lines", "Distance (mm)", "Original feed", "New feed", "Ramp to"]),
        disabled=["▸", "Part", "Toolpath", "Section", "Layer", "Type", "Lines", "Distance (mm)",
                  "Original feed"],
        column_config={
            "▸": st.column_config.TextColumn("", width="small", help="The section being edited"),
            "Layer": st.column_config.TextColumn(help="Which layer this section builds, and its Z"),
            "New feed": st.column_config.NumberColumn(
                "New feed (mm/min)", min_value=1.0, step=10.0, format="%g", help="Blank = unchanged"
            ),
            "Ramp to": st.column_config.NumberColumn(
                "Ramp to (mm/min)", min_value=1.0, step=10.0, format="%g",
                help="Blank = one feed across the section; set it to ramp from New feed to this",
            ),
        },
    )
    for row in edited_sections.to_dict("records"):
        feed_key = (row["key"], row["spec"], int(row["section"]))
        value = row["New feed"]
        if pd.isna(value):
            feeds_state.pop(feed_key, None)
        else:
            feeds_state[feed_key] = float(value)
        end = row["Ramp to"]
        if pd.isna(end):
            ramps_state.pop(feed_key, None)
        else:
            ramps_state[feed_key] = float(end)

    with editor_slot:
        render_section_editor()

    feeds_opt: dict[tuple[int, int], float] = {}
    ramps_opt: dict[tuple[int, int], tuple[float, float]] = {}
    for tp in toolpaths:
        key = tp_key(tp)
        for sec in tp.sections:
            value = feeds_state.get((key, spec_for(key), sec.number))
            if value is None or sec.is_empty:
                continue
            feeds_opt[(tp.start_index, sec.number)] = value
            end = ramps_state.get((key, spec_for(key), sec.number))
            if end is not None:
                ramps_opt[(tp.start_index, sec.number)] = (value, end)

    chord_mm = float(ss.get(RAMP_CHORD_KEY, DEFAULT_CHORD_MM))
    result = run_fix(
        program,
        fix_id,
        {
            "sections": sections_opt,
            "splits": splits_opt,
            "geometry": geo_opt,
            "layers": layers_opt,
            "feeds": feeds_opt,
            "ramps": ramps_opt,
            "chord_mm": chord_mm,
        },
    ).result

    # The map doubles as picker and result view, and goes into the slot beside
    # the editor. Rendered here, after the tables, so it shows this run's
    # edits. Only the toolpaths in scope are drawn: with nothing picked that's
    # every part, which for a coupon array is the useful overview of the bed;
    # picking one zooms to it, and for a stacked build it's the only way to
    # see a single layer at all.
    plotted = [tp for tp in toolpaths if tp_key(tp) in visible_keys]
    feed_for_section = {
        (tp.start_index, sec.number): feeds_opt.get(
            (tp.start_index, sec.number), sec.original_feeds[0] if sec.original_feeds else None
        )
        for tp in plotted
        for sec in tp.sections
    }
    with map_slot:
        # Layers of one build occupy the same footprint, so drawing them all
        # stacks them into an unreadable pile — and for a stacked build this
        # filter is the only way to see a single layer at all.
        layer_numbers = sorted({sec.layer for tp in plotted for sec in tp.sections if sec.layer})
        z_of_layer = {sec.layer: sec.layer_z for tp in plotted for sec in tp.sections if sec.layer}
        chosen_layer = 0
        col_ramp, col_layer = st.columns([1, 2], vertical_alignment="bottom")
        with col_ramp:
            ramp, map_size = render_map_settings()
        if len(layer_numbers) > 1:
            def layer_label(n: int) -> str:
                if n == ALL_LAYERS:
                    return ALL_LAYERS
                z = z_of_layer.get(n)
                return f"Layer {n}" + (f" — Z{z:g}" if z is not None else "")

            picked_layer = col_layer.selectbox(
                "Show layer", [ALL_LAYERS, *layer_numbers], key=layer_key,
                format_func=layer_label,
                help="Layers of one build sit on top of each other — show one at a time to see it.",
            )
            chosen_layer = 0 if picked_layer == ALL_LAYERS else int(picked_layer)

        points = path_points(program, plotted)
        if chosen_layer:
            in_layer = {
                (tp.start_index, sec.number)
                for tp in plotted
                for sec in tp.sections
                if sec.layer == chosen_layer
            }
            points = [r for r in points if (r["toolpath"], r["section"]) in in_layer]

        plotted_starts = {tp.start_index for tp in plotted}
        chart = build_toolpath_map(
            plotted,
            points,
            feed_for_section,
            {k for k in feeds_opt if k[0] in plotted_starts},
            focus=f"{cursor[0]}|{cursor[1]}" if cursor else None,
            size=map_size,
            ramp=ramp,
            ramp_for_section={k: v for k, v in ramps_opt.items() if k[0] in plotted_starts},
        )
        if chart is None:
            st.caption("No plottable XY path found.")
        else:
            # width="content" keeps the chart at the size it was built at;
            # letting Streamlit stretch it to the container squashes the plan
            # view.
            st.altair_chart(
                chart,
                key=map_key,
                on_select=on_map_select,
                selection_mode=[MAP_SELECTION, MAP_SECTION_SELECTION],
                width="content",
            )
            scope_note = ""
            if picked:
                names = [tp.name for tp in visible]
                shown = ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
                scope_note = f" Showing **{shown}** — *Whole program* goes back to every part."
            st.caption(
                "Click the path to pick a section, or the space around a toolpath to pick the whole toolpath. "
                "Colour is the feed each section will run at — a ramped section shades along its length; "
                "a section with a new feed is drawn thicker. "
                "Dots mark where each section after the first begins." + scope_note
            )

    st.markdown("**3 · Preview**")
    if result.changes:
        st.write(result.summary)
        # Collapsed by default: it's the line-by-line audit, not the thing you
        # work from — the map above is the working preview.
        def after_text(change) -> str:
            """One cell per change. A ramped move can expand into many chords,
            so show the first and last rather than a wall of text."""
            parts = (change.new_text or "").split("\n")
            if len(parts) == 1:
                return parts[0].strip()
            return f"{parts[0].strip()}  …  {parts[-1].strip()}   ({len(parts)} chords)"

        with st.expander(f"Lines that change ({len(result.changes)})", expanded=False):
            st.dataframe(
                pd.DataFrame(
                    {
                        "Line": [c.original_index + 1 for c in result.changes],
                        "Toolpath": [c.label or "" for c in result.changes],
                        "Before": [c.original_text.strip() for c in result.changes],
                        "After": [after_text(c) for c in result.changes],
                        "Why": [c.reason or "" for c in result.changes],
                    }
                ),
                hide_index=True,
                use_container_width=True,
                height=min(300, 38 + 35 * len(result.changes)),
            )
            st.caption(
                "Applied together: the restated feeds are what stop an override carrying on into the next section."
            )
    else:
        st.caption("No feed overrides set; the file will pass through unchanged.")

    col_a, col_b, col_c, _ = st.columns([2, 1, 1, 1])
    with col_a:
        if st.button("Apply feed changes →", type="primary", use_container_width=True):
            ramp_note = f", {len(ramps_opt)} ramped" if ramps_opt else ""
            _apply_and_advance(
                result.program,
                f"[{fix_id}] Set {len(feeds_opt)} section feed(s){ramp_note}; "
                f"{len(result.changes)} line(s) changed.",
            )
    with col_b:
        if st.button("Skip fix", use_container_width=True):
            _advance(f"[{fix_id}] Skipped.")
    with col_c:
        if fix_index > 0 and st.button("← Back", use_container_width=True):
            _go_back()


st.set_page_config(page_title="LMD-Fixer", page_icon="🔧", layout="wide")
inject_css()
render_hero()
st.write("")

fixes = available_fixes()

ordered_fix_ids = [fid for fid in FIX_ORDER if fid in fixes]
ordered_fix_ids += [fid for fid in fixes if fid not in ordered_fix_ids]

with st.sidebar:
    st.markdown(f"### <span style='color:{ACCENT}'>&#9881;</span> Fixes", unsafe_allow_html=True)
    st.caption(
        "Applied in a fixed order — rotary cleanup, section removal, repeated calls, dwell review, feed adjustment."
    )
    selected_ids = []
    for fix_id in ordered_fix_ids:
        fix = fixes[fix_id]()
        # Keyed so a selection change made part-way through a review can be
        # put back if the user decides not to start over.
        checked = st.toggle(fix.label or fix_id, help=fix.description, key=FIX_TOGGLE_KEY.format(fix_id))
        if checked:
            selected_ids.append(fix_id)
    # Says which file to edit, since a packaged build reads one beside the .exe
    # rather than the copy in the package.
    st.caption(
        f"v{__version__} · fixes switched on/off in `{settings_path().name}`",
        help=f"Edit {settings_path()} and refresh the page to change which fixes are offered.",
    )

uploaded = st.file_uploader(
    "G-code file", type=["ptp", "nc", "txt", "gcode"], label_visibility="collapsed"
)

if uploaded is None:
    st.markdown(
        f"""
        <div style="background:{CARD_BG}; border:{CARD_BORDER}; border-radius:14px;
                    padding:1.2rem 1.4rem; color:{DIM}; line-height:1.7;">
        <b style="color:#e6e9ef;">How it works</b><br>
        1&nbsp;&middot;&nbsp; Drop a <code>.ptp</code> / <code>.nc</code> / <code>.gcode</code> file above<br>
        2&nbsp;&middot;&nbsp; Switch on the fixes you want in the sidebar<br>
        3&nbsp;&middot;&nbsp; Review each proposed change — nothing is removed without your say-so<br>
        4&nbsp;&middot;&nbsp; Check the final diff and download the cleaned file
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

def _start_review(uploaded_file, selection: tuple[str, ...]) -> None:
    """Loads the file and begins a fresh review, discarding anything already
    applied. Drops leftover per-change checkbox state too, which would
    otherwise leak into this review wherever line indices collide."""
    text = uploaded_file.getvalue().decode("utf-8", errors="replace")
    program = GCodeProgram.from_text(text, source_name=uploaded_file.name)
    st.session_state["file_key"] = (uploaded_file.name, uploaded_file.size)
    st.session_state["selection"] = selection
    st.session_state["original_program"] = program
    st.session_state["current_program"] = program.copy()
    st.session_state["fix_index"] = 0
    st.session_state["applied_summaries"] = []
    st.session_state["history"] = []
    _clear_review_widget_state()


file_key = (uploaded.name, uploaded.size)
selection = tuple(selected_ids)
steps_done = st.session_state.get("fix_index", 0)

if st.session_state.get("file_key") != file_key:
    # A different file: there's nothing worth keeping, so no need to ask.
    _start_review(uploaded, selection)
elif st.session_state.get("selection") != selection:
    # Changing which fixes run restarts the review, because the program has
    # already been through the ones behind us. That's fine before the first
    # step, but past that it throws away applied work — so ask first, and
    # change nothing until the answer comes back.
    if steps_done == 0:
        _start_review(uploaded, selection)
    else:
        previous = st.session_state["selection"]
        st.warning(
            f"**You're {steps_done} step(s) into this review.** Changing which fixes run starts it "
            "over from the original file — everything applied so far would be undone."
        )
        # `previous` can name a fix since switched off in fix_settings.toml,
        # so look labels up in the full registry, not just the enabled fixes.
        all_fixes = available_fixes(include_disabled=True)
        st.caption(
            f"Was: {', '.join(all_fixes[f]().label or f for f in previous) or 'nothing'}  \n"
            f"Now: {', '.join(fixes[f]().label or f for f in selection) or 'nothing'}"
        )

        def _undo_selection_change(wanted: tuple[str, ...] = previous) -> None:
            for fid in ordered_fix_ids:
                st.session_state[FIX_TOGGLE_KEY.format(fid)] = fid in wanted

        col_keep, col_restart, _ = st.columns([1, 1, 2])
        col_keep.button(
            "Keep my review", use_container_width=True, type="primary",
            help="Put the sidebar back the way it was and carry on where you left off",
            on_click=_undo_selection_change,
        )
        if col_restart.button(
            "Start over with this selection", use_container_width=True,
            help="Discard everything applied so far and review the original file again",
        ):
            _start_review(uploaded, selection)
            st.rerun()
        st.stop()

current_program: GCodeProgram = st.session_state["current_program"]
original_program: GCodeProgram = st.session_state["original_program"]
fix_index: int = st.session_state["fix_index"]

if selected_ids:
    with st.sidebar:
        st.divider()
        st.markdown("### Progress")
        for i, fid in enumerate(selected_ids):
            label = fixes[fid]().label or fid
            if i < fix_index:
                st.markdown(
                    f"<span style='color:{ACCENT}'>&#10003;</span> <span style='color:{DIM}'>{label}</span>",
                    unsafe_allow_html=True,
                )
            elif i == fix_index:
                st.markdown(f"<b>&#9654; {label}</b>", unsafe_allow_html=True)
            else:
                st.markdown(f"<span style='color:{DIM}'>&#9675; {label}</span>", unsafe_allow_html=True)
        st.caption(
            f"{uploaded.name}\n\n{len(original_program.lines):,} lines uploaded &middot; "
            f"{len(current_program.lines):,} now"
        )

if not selected_ids:
    st.info("Switch on one or more fixes in the sidebar to start the review.")
    with st.expander(f"Preview: {uploaded.name} ({len(current_program.lines):,} lines)", expanded=False):
        st.text_area("current", current_program.to_text("\n"), height=400, label_visibility="collapsed")
    st.stop()

step_labels = [fixes[fid]().label or fid for fid in selected_ids]
render_stepper(step_labels, fix_index)
st.write("")

if fix_index < len(selected_ids):
    fix_id = selected_ids[fix_index]
    fix = fixes[fix_id]()

    st.subheader(fix.label)
    st.caption(fix.description)

    run_result = run_fix(current_program, fix_id)
    result = run_result.result

    if fix_id == "adjust_section_feeds":
        # Proposes nothing until the user configures parts/feeds, so it gets
        # its own editor instead of the "no changes" / checklist branches.
        render_feed_sections_step(current_program, fix_id, fix_index)
    elif not result.changes:
        st.success("No changes proposed by this fix — nothing to review.")
        col_a, col_b, _ = st.columns([1, 1, 2])
        with col_a:
            if st.button("Continue →", type="primary", use_container_width=True):
                _advance(f"[{fix_id}] {result.summary}")
        with col_b:
            if fix_index > 0 and st.button("← Back", use_container_width=True):
                _go_back()
    elif fix_id == "remove_named_sections":
        # Section removal is opt-in per section (defaults to keeping everything),
        # unlike the other fixes which default to applying every proposed change.
        st.write(result.summary)
        st.info("Nothing is removed unless you tick it. Tick a section to remove that whole block.")

        accept_key_prefix = f"accept_{fix_id}_{fix_index}"
        child_keys = [f"{accept_key_prefix}_{c.original_index}" for c in result.changes]
        remove_all = st.checkbox(
            "Remove all sections",
            value=False,
            key=f"{accept_key_prefix}_all",
            on_change=_sync_children_to_master,
            args=(f"{accept_key_prefix}_all", child_keys),
        )

        accepted_indices = set()
        n_lines_selected = 0
        with st.container(height=440, border=True):
            for change in result.changes:
                n_lines = (change.end_index - change.original_index + 1) if change.end_index is not None else 1
                caption = (
                    f"**{change.label}**  — lines {change.original_index + 1}-{change.end_index + 1}"
                    f" ({n_lines} lines)"
                )
                checked = st.checkbox(caption, value=remove_all, key=f"{accept_key_prefix}_{change.original_index}")
                if checked:
                    accepted_indices.add(change.original_index)
                    n_lines_selected += n_lines

        if accepted_indices:
            st.warning(
                f"{len(accepted_indices)} section(s) selected — **{n_lines_selected:,} lines** will be removed."
            )
        else:
            st.caption("No sections selected; the file will pass through unchanged.")

        col_a, col_b, col_c, _ = st.columns([2, 1, 1, 1])
        with col_a:
            if st.button("Apply and continue →", type="primary", use_container_width=True):
                new_program = apply_accepted_changes(current_program, result, accepted_indices)
                _apply_and_advance(
                    new_program,
                    f"[{fix_id}] Removed {len(accepted_indices)} of {len(result.changes)} section(s).",
                )
        with col_b:
            if st.button("Skip fix", use_container_width=True):
                _advance(f"[{fix_id}] Skipped.")
        with col_c:
            if fix_index > 0 and st.button("← Back", use_container_width=True):
                _go_back()
    else:
        st.write(result.summary)

        accept_key_prefix = f"accept_{fix_id}_{fix_index}"
        child_keys = [f"{accept_key_prefix}_{c.original_index}" for c in result.changes]
        select_all = st.checkbox(
            "Accept all",
            value=True,
            key=f"{accept_key_prefix}_all",
            on_change=_sync_children_to_master,
            args=(f"{accept_key_prefix}_all", child_keys),
        )

        show_context = len(result.changes) <= MAX_CHANGES_WITH_CONTEXT

        accepted_indices = set()
        with st.container(height=440, border=True):
            for change in result.changes:
                default = select_all
                where = f" in {change.label}" if change.label else ""
                end = change.end_index if change.end_index is not None else change.original_index
                n_lines = end - change.original_index + 1
                if n_lines > 1:
                    loc = f"Lines {change.original_index + 1}-{end + 1}{where}"
                    what = f"`{change.original_text.strip()}` + {n_lines - 1} following line(s)"
                else:
                    loc = f"Line {change.original_index + 1}{where}"
                    what = f"`{change.original_text.strip()}`"
                if change.kind == "removed":
                    reason = f" ({change.reason})" if change.reason else ""
                    keep_word = "these lines" if n_lines > 1 else "this line"
                    caption = f"{loc}: REMOVING {what}{reason} — uncheck to keep {keep_word} instead"
                else:
                    caption = (
                        f"{loc}: CHANGING {what} "
                        f"->  `{(change.new_text or '').strip()}` — uncheck to leave unchanged"
                    )
                checked = st.checkbox(caption, value=default, key=f"{accept_key_prefix}_{change.original_index}")
                if checked:
                    accepted_indices.add(change.original_index)
                if show_context:
                    with st.expander("Show surrounding lines", expanded=False):
                        st.code(
                            render_change_context(current_program.lines, change.original_index, end),
                            language=None,
                        )

        st.caption(f"{len(accepted_indices)} of {len(result.changes)} change(s) selected.")

        col_a, col_b, col_c, _ = st.columns([2, 1, 1, 1])
        with col_a:
            if st.button("Apply accepted changes →", type="primary", use_container_width=True):
                new_program = apply_accepted_changes(current_program, result, accepted_indices)
                _apply_and_advance(
                    new_program,
                    f"[{fix_id}] Applied {len(accepted_indices)} of {len(result.changes)} proposed change(s).",
                )
        with col_b:
            if st.button("Skip fix", use_container_width=True):
                _advance(f"[{fix_id}] Skipped.")
        with col_c:
            if fix_index > 0 and st.button("← Back", use_container_width=True):
                _go_back()
else:
    st.subheader("Review complete")
    render_stats(len(original_program.lines), len(current_program.lines))

    default_name = st.session_state.get("output_name_default")
    if default_name != uploaded.name or "output_name" not in st.session_state:
        st.session_state["output_name"] = f"fixed_{uploaded.name}"
        st.session_state["output_name_default"] = uploaded.name
    with st.form("output_name_form", border=False):
        st.text_input("Output file name", key="output_name")
        st.form_submit_button("Update file name")
    output_name = st.session_state["output_name"]
    fallback_name = f"fixed_{uploaded.name}"
    if not output_name.strip():
        st.warning(f"Output file name is empty — falling back to '{fallback_name}'.")
    final_name = output_name.strip() or fallback_name

    upload_suffix = Path(uploaded.name).suffix.lower()
    final_suffix = Path(final_name).suffix.lower()
    if upload_suffix and not final_suffix:
        final_name += upload_suffix
        st.caption(f"No file extension given — appended '{upload_suffix}' to match the uploaded file.")
    elif upload_suffix and final_suffix != upload_suffix:
        st.warning(
            f"File extension changed from '{upload_suffix}' to '{final_suffix}' — "
            "the machine controller may expect the original extension."
        )

    name_matches = O_NUMBER_IN_NAME_RE.findall(final_name)
    if len(name_matches) > 1:
        st.warning(
            f"Multiple O-numbers found in the file name ({', '.join('O' + n for n in name_matches)}) — "
            f"using the first one, O{name_matches[0]}."
        )
    download_program, old_o_number, new_o_number = sync_o_number(current_program, final_name)
    if old_o_number and new_o_number and old_o_number != new_o_number:
        st.caption(f"Program number: O{old_o_number} → **O{new_o_number}** (to match the file name).")
    elif old_o_number and new_o_number:
        st.caption(f"Program number already matches the file name (O{new_o_number}).")
    elif name_matches:
        st.caption("No O-number header line found in this program — file name left as the only reference.")

    col_dl, col_back, col_restart, _ = st.columns([2, 1, 1, 1])
    with col_dl:
        st.download_button(
            "⬇ Download fixed file",
            data=download_program.to_text("\r\n"),
            file_name=final_name,
            mime="text/plain",
            type="primary",
            use_container_width=True,
        )
    with col_back:
        if st.button("← Back", use_container_width=True):
            _go_back()
    with col_restart:
        if st.button("Start over", use_container_width=True):
            # Deliberately keep per-change checkbox state (accept_*) so the
            # user's review choices carry over into the redo, rather than
            # forcing every fix to be re-reviewed from scratch.
            st.session_state["fix_index"] = 0
            st.session_state["applied_summaries"] = []
            st.session_state["current_program"] = original_program.copy()
            st.session_state["history"] = []
            st.rerun()

    if st.session_state["applied_summaries"]:
        with st.expander("What was done", expanded=True):
            for s in st.session_state["applied_summaries"]:
                st.markdown(f"- {s}")

    st.subheader("Original vs. fixed")
    st.caption(
        "Red = removed from the original. Green = added/changed in the final version. "
        "Long unchanged runs are collapsed."
    )
    st.markdown(
        render_side_by_side_diff(original_program.lines, current_program.lines),
        unsafe_allow_html=True,
    )
