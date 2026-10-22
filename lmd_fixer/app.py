"""Streamlit web UI for LMD-Fixer.

Run with `lmd-fixer` (installed entry point) or
`streamlit run lmd_fixer/app.py` from a checkout.
"""

from __future__ import annotations

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
from lmd_fixer import recipes as recipe_db
from lmd_fixer.fixes import available_fixes
from lmd_fixer.fixes.change_recipes import toolpath_recipe_calls
from lmd_fixer.export import change_context, default_output_name, diff_rows, plan_output
from lmd_fixer.gcode import GCodeProgram
from lmd_fixer.toolpaths import analyse_toolpaths, group_into_parts, path_points
from lmd_fixer.pipeline import FIX_ORDER, apply_accepted_changes, run_fix
from lmd_fixer.settings import settings_path

# Only render per-change context previews when the list is small enough for
# them to be useful rather than overwhelming (and cheap enough to render).
MAX_CHANGES_WITH_CONTEXT = 40

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
    rows = [(r.left, r.right, r.tag) for r in diff_rows(original_lines, final_lines, n_context)]

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


# Not accept_*-prefixed: a display preference should outlive a state reset.
MAP_SIZE_KEY = "map_size"


MAP_SURFACE = "#272b33"
MAP_FOCUS = "#f4f1e6"


def _selected_keys(chart_state, name: str, field: str) -> list[str]:
    """Keys picked on the map, read from the chart's session state. A point
    selection arrives as a list of `{field: ...}` dicts, or `{}` when empty."""
    try:
        points = chart_state["selection"][name]
    except (KeyError, TypeError):
        return []
    return [p[field] for p in points if isinstance(p, dict) and field in p]


def _plan_view(boxes: pd.DataFrame, size: int) -> tuple[alt.Scale, alt.Scale, float, float]:
    """X/Y scales and pixel size for a true-aspect plan view of `boxes`
    (columns x0, x1, y0, y1): one millimetre is the same number of pixels on
    both axes, so a square part looks square. `size` is the long edge in
    pixels; the short axis gets whatever that scale gives it. The caller must
    render with width="content" or Streamlit stretches the width to the
    container and the aspect goes with it."""
    x_lo, x_hi = boxes["x0"].min() - 3, boxes["x1"].max() + 3
    y_lo, y_hi = boxes["y0"].min() - 3, boxes["y1"].max() + 3
    span_x, span_y = max(x_hi - x_lo, 1e-6), max(y_hi - y_lo, 1e-6)
    px_per_mm = size / max(span_x, span_y)
    return (
        alt.Scale(domain=[x_lo, x_hi], zero=False, nice=False),
        alt.Scale(domain=[y_lo, y_hi], zero=False, nice=False),
        span_x * px_per_mm,
        span_y * px_per_mm,
    )


# What stays and what goes on the section-removal map. Validated as a pair
# against the app surface (#272b33) with the dataviz skill's
# validate_palette.py (deutan ΔE 9.9). The red sits just under 3:1 on that
# surface, so it is never the only cue: a toolpath ticked for removal is also
# dashed, its box is tinted, the tooltip says so, and the checklist beside the
# map is the table view. The app's teal accent is too light to pass the
# lightness band, which is why "kept" isn't simply ACCENT.
REMOVAL_KEPT = "#1baf7a"
REMOVAL_GONE = "#d03b3b"
REMOVAL_STATUS = ["Kept", "To remove"]
REMOVAL_SELECTION = "removal_pick"
REMOVAL_HOVER = "removal_hover"


def toolpath_from_map_selection(chart_state, name: str = REMOVAL_SELECTION) -> int | None:
    """Start line of the toolpath clicked on a click-to-tick map (section
    removal, P recipe change), or None for a click on empty space."""
    picked = _selected_keys(chart_state, name, "toolpath")
    return int(picked[0]) if picked else None


def build_removal_map(
    points: list[dict], labels: dict[int, dict[str, str]], removing: set[int], size: int = 520
) -> alt.LayerChart | None:
    """Plan view of the bed for the section-removal step: every toolpath's
    deposition path, solid teal if it stays and dashed red if it's ticked for
    removal, under a box per toolpath that is the click target.

    `labels` maps a toolpath's start line to the tooltip fields shown for it
    (name, part, lines). `removing` holds the start lines ticked for removal.
    """
    if not points:
        return None
    pts = pd.DataFrame(points)[["toolpath", "run", "x", "y"]]
    pts["x"] = pts["x"].round(3)
    pts["y"] = pts["y"].round(3)
    pts["status"] = [REMOVAL_STATUS[t in removing] for t in pts["toolpath"]]
    pts["order"] = range(len(pts))

    boxes = (
        pts.groupby("toolpath", as_index=False)
        .agg(x0=("x", "min"), x1=("x", "max"), y0=("y", "min"), y1=("y", "max"))
    )
    boxes[["x0", "y0"]] -= 2
    boxes[["x1", "y1"]] += 2
    boxes["status"] = [REMOVAL_STATUS[t in removing] for t in boxes["toolpath"]]
    boxes["Click to"] = ["keep it" if t in removing else "remove it" for t in boxes["toolpath"]]
    fields = list(next(iter(labels.values()), {}))
    for name in fields:
        boxes[name] = [labels.get(t, {}).get(name, "") for t in boxes["toolpath"]]

    x_scale, y_scale, width, height = _plan_view(boxes, size)
    status_colours = alt.Scale(domain=REMOVAL_STATUS, range=[REMOVAL_KEPT, REMOVAL_GONE])
    # Same field, same (empty) title and the same legend on both channels, so
    # Vega-Lite merges them into one legend whose symbols show the dash too.
    legend = alt.Legend(title=None, orient="top", symbolType="stroke", symbolStrokeWidth=2)

    paths = (
        alt.Chart(pts)
        .mark_line(strokeWidth=2, strokeCap="round", strokeJoin="round")
        .encode(
            x=alt.X("x:Q", title="X (mm)", scale=x_scale),
            y=alt.Y("y:Q", title="Y (mm)", scale=y_scale),
            detail="run:N",
            order="order:Q",
            color=alt.Color("status:N", scale=status_colours, legend=legend),
            strokeDash=alt.StrokeDash(
                "status:N", scale=alt.Scale(domain=REMOVAL_STATUS, range=[[1, 0], [5, 3]]), legend=legend
            ),
        )
    )

    pick = alt.selection_point(name=REMOVAL_SELECTION, fields=["toolpath"], on="click")
    hover = alt.selection_point(name=REMOVAL_HOVER, fields=["toolpath"], on="pointerover", clear="pointerout")
    is_gone = alt.datum.status == REMOVAL_STATUS[1]
    targets = (
        alt.Chart(boxes)
        .mark_rect(cornerRadius=4)
        .encode(
            x=alt.X("x0:Q", scale=x_scale),
            x2="x1:Q",
            y=alt.Y("y0:Q", scale=y_scale),
            y2="y1:Q",
            fill=alt.condition(is_gone, alt.value(REMOVAL_GONE), alt.value("#ffffff")),
            fillOpacity=alt.condition(is_gone, alt.value(0.14), alt.value(0.02)),
            stroke=alt.condition(
                hover, alt.value(MAP_FOCUS),
                alt.Stroke("status:N", scale=alt.Scale(
                    domain=REMOVAL_STATUS, range=["rgba(255,255,255,0.10)", REMOVAL_GONE]
                ), legend=None),
                empty=False,
            ),
            strokeWidth=alt.condition(hover, alt.value(2), alt.value(1), empty=False),
            tooltip=[f"{f}:N" for f in fields] + ["Click to:N"],
        )
        .add_params(pick, hover)
    )

    # A cross on each toolpath going, so the state doesn't hang on the colour
    # channel: on a dense raster pad the dashes run together and read as a
    # solid line. Drawn under the boxes so a click on it still hits the box.
    boxes["cx"] = (boxes["x0"] + boxes["x1"]) / 2
    boxes["cy"] = (boxes["y0"] + boxes["y1"]) / 2
    crosses = (
        alt.Chart(boxes[boxes["status"] == REMOVAL_STATUS[1]])
        .mark_text(text="✕", fontSize=18, fontWeight="bold", color=MAP_FOCUS)
        .encode(x=alt.X("cx:Q", scale=x_scale), y=alt.Y("cy:Q", scale=y_scale))
    )

    # Boxes on top: a click anywhere in one toggles that toolpath, which is a
    # much bigger target than a 2px line.
    return (
        alt.layer(paths, crosses, targets)
        .properties(width=width, height=height)
        .properties(autosize=alt.AutoSizeParams(type="pad", contains="padding"))
    )


# Unchanged vs new P on the recipe map. Validated as a pair against the app
# surface (#272b33) with the dataviz skill's validate_palette.py: every check
# passes, deutan/protan ΔE 27. Still not the only cue — a toolpath with a new P
# is drawn thicker and its P label is bold, and ticked toolpaths are marked by
# their box outline, which no colour carries.
RECIPE_UNCHANGED = "#3987e5"
RECIPE_CHANGED = "#d95a1d"
RECIPE_STATUS = ["Unchanged", "New P"]
RECIPE_SELECTION = "recipe_pick"
RECIPE_HOVER = "recipe_hover"


def build_recipe_map(
    points: list[dict],
    labels: dict[int, dict[str, str]],
    p_label: dict[int, str],
    changed: set[int],
    ticked: set[int],
    size: int = 520,
) -> alt.LayerChart | None:
    """Plan view of the bed for the P recipe step, built like the removal map:
    every toolpath's path (blue if it keeps its P, orange and thicker if it
    gets a new one), the P it will run written in its box, and a box per
    toolpath that is the click target. A ticked toolpath's box is outlined.

    `labels` maps a toolpath's start line to its tooltip fields, `p_label` to
    the short P it will run (e.g. `P31`).
    """
    if not points:
        return None
    pts = pd.DataFrame(points)[["toolpath", "run", "x", "y"]]
    pts["x"] = pts["x"].round(3)
    pts["y"] = pts["y"].round(3)
    pts["status"] = [RECIPE_STATUS[t in changed] for t in pts["toolpath"]]
    pts["order"] = range(len(pts))

    boxes = (
        pts.groupby("toolpath", as_index=False)
        .agg(x0=("x", "min"), x1=("x", "max"), y0=("y", "min"), y1=("y", "max"))
    )
    boxes[["x0", "y0"]] -= 2
    boxes[["x1", "y1"]] += 2
    boxes["status"] = [RECIPE_STATUS[t in changed] for t in boxes["toolpath"]]
    boxes["ticked"] = [t in ticked for t in boxes["toolpath"]]
    # The box outline marks a ticked toolpath; hover overrides it below.
    boxes["edge"] = [MAP_FOCUS if t in ticked else "rgba(255,255,255,0.10)" for t in boxes["toolpath"]]
    boxes["edge_width"] = [2.5 if t in ticked else 1.0 for t in boxes["toolpath"]]
    boxes["P label"] = [p_label.get(t, "") for t in boxes["toolpath"]]
    boxes["Click to"] = ["untick it" if t in ticked else "tick it" for t in boxes["toolpath"]]
    fields = list(next(iter(labels.values()), {}))
    for name in fields:
        boxes[name] = [labels.get(t, {}).get(name, "") for t in boxes["toolpath"]]
    boxes["cx"] = (boxes["x0"] + boxes["x1"]) / 2
    boxes["cy"] = (boxes["y0"] + boxes["y1"]) / 2

    x_scale, y_scale, width, height = _plan_view(boxes, size)
    legend = alt.Legend(title=None, orient="top", symbolType="stroke", symbolStrokeWidth=3)

    paths = (
        alt.Chart(pts)
        .mark_line(strokeCap="round", strokeJoin="round")
        .encode(
            x=alt.X("x:Q", title="X (mm)", scale=x_scale),
            y=alt.Y("y:Q", title="Y (mm)", scale=y_scale),
            detail="run:N",
            order="order:Q",
            color=alt.Color(
                "status:N", scale=alt.Scale(domain=RECIPE_STATUS, range=[RECIPE_UNCHANGED, RECIPE_CHANGED]),
                legend=legend,
            ),
            strokeWidth=alt.StrokeWidth(
                "status:N", scale=alt.Scale(domain=RECIPE_STATUS, range=[1.5, 3]), legend=None
            ),
        )
    )

    # The P each toolpath will run, in text ink rather than the series colour.
    # Under the boxes, so a click on a label still hits its box.
    # A pill of surface colour behind each label: the label sits on top of the
    # toolpath's own lines, which otherwise make it hard to read.
    backdrop = (
        alt.Chart(boxes)
        .mark_point(shape="M-1.6,-0.75H1.6V0.75H-1.6Z", size=420, filled=True, color=MAP_SURFACE, opacity=0.9)
        .encode(x=alt.X("cx:Q", scale=x_scale), y=alt.Y("cy:Q", scale=y_scale))
    )

    # Font weight can't be data-driven, so new and unchanged are two layers.
    def p_text(status: str, colour: str, weight: str) -> alt.Chart:
        return (
            alt.Chart(boxes[boxes["status"] == status])
            .mark_text(fontSize=12, fontWeight=weight, color=colour)
            .encode(x=alt.X("cx:Q", scale=x_scale), y=alt.Y("cy:Q", scale=y_scale), text="P label:N")
        )

    pick = alt.selection_point(name=RECIPE_SELECTION, fields=["toolpath"], on="click")
    hover = alt.selection_point(name=RECIPE_HOVER, fields=["toolpath"], on="pointerover", clear="pointerout")
    targets = (
        alt.Chart(boxes)
        .mark_rect(cornerRadius=4)
        .encode(
            x=alt.X("x0:Q", scale=x_scale),
            x2="x1:Q",
            y=alt.Y("y0:Q", scale=y_scale),
            y2="y1:Q",
            fill=alt.value("#ffffff"),
            fillOpacity=alt.condition(alt.datum.ticked, alt.value(0.12), alt.value(0.02)),
            stroke=alt.condition(hover, alt.value(MAP_FOCUS), alt.Stroke("edge:N", scale=None), empty=False),
            strokeWidth=alt.condition(
                hover, alt.value(2.5), alt.StrokeWidth("edge_width:Q", scale=None), empty=False
            ),
            tooltip=[f"{f}:N" for f in fields] + ["Click to:N"],
        )
        .add_params(pick, hover)
    )

    return (
        alt.layer(
            paths,
            backdrop,
            p_text(RECIPE_STATUS[0], "#c2c7d1", "normal"),
            p_text(RECIPE_STATUS[1], MAP_FOCUS, "bold"),
            targets,
        )
        .properties(width=width, height=height)
        .properties(autosize=alt.AutoSizeParams(type="pad", contains="padding"))
    )


# The material database dialog. None of these keys are accept_*-prefixed: the
# dialog isn't part of a review, so a review reset mustn't touch it.
RECIPE_DB_VER = "recipe_db_ver"
RECIPE_DB_SAVED = "recipe_db_saved"
ALL_MATERIALS = "All materials"


def _materials(db: pd.DataFrame) -> list[str]:
    return sorted({str(m).strip() for m in db[recipe_db.MATERIAL_COLUMN].dropna() if str(m).strip()})


def _open_material_database() -> None:
    # Every open re-reads the file, so edits made in Excel meanwhile show up.
    st.session_state[RECIPE_DB_VER] = st.session_state.get(RECIPE_DB_VER, 0) + 1


@st.dialog("🧪 Material database", width="large")
def material_database_dialog() -> None:
    """View and edit the recipe database: what each M98 P number sets on the
    control panel. Edits only reach the file on Save, which validates first."""
    ss = st.session_state
    try:
        path = recipe_db.recipes_path()
        on_disk = recipe_db.load_recipes(path)
    except ValueError as exc:
        st.error(str(exc))
        return

    st.caption(
        "Each **P** number is an LMD recipe stored on the machine's control panel: laser power, powder flow "
        "and nozzle gas. Record them here as they're set on the machine — nothing checks this against the "
        "panel. Material and Notes are for your own reference. "
        f"Saved to `{path}`."
    )

    # The editor is fed a frame frozen for its key (feeding its own output back
    # in as `data` makes Streamlit drop edits); the version moves on each open,
    # save, or "add missing" so the editor rebuilds from the new frame.
    ver = ss.setdefault(RECIPE_DB_VER, 0)
    base_key = f"recipe_db_base_{ver}"
    if base_key not in ss:
        ss[base_key] = on_disk
    base: pd.DataFrame = ss[base_key]

    # P numbers the loaded program uses that the database doesn't describe yet.
    program = ss.get("current_program")
    if program is not None:
        known = {int(p) for p in base[recipe_db.P_COLUMN].dropna()}
        used = sorted({c.p_value for c in toolpath_recipe_calls(program) if c.p_value is not None})
        missing = [p for p in used if p not in known]
        if missing:
            col_note, col_add = st.columns([3, 1], vertical_alignment="center")
            col_note.info(
                f"This file uses {len(missing)} recipe(s) not in the database: "
                + ", ".join(recipe_db.format_p(p) for p in missing)
            )

            def add_missing(base: pd.DataFrame = base, missing: list[int] = missing, ver: int = ver) -> None:
                rows = pd.DataFrame({recipe_db.P_COLUMN: missing})
                ss[f"recipe_db_base_{ver + 1}"] = recipe_db.normalise(pd.concat([base, rows], ignore_index=True))
                ss[RECIPE_DB_VER] = ver + 1

            col_add.button("Add rows for them", on_click=add_missing, use_container_width=True)

    material = st.selectbox(
        "Material", [ALL_MATERIALS, *_materials(base)], key="recipe_db_material",
        help="Show only one material's recipes. New rows added while filtered need their Material typed in.",
    )
    mask = (
        pd.Series(True, index=base.index)
        if material == ALL_MATERIALS
        else base[recipe_db.MATERIAL_COLUMN].fillna("").str.strip().eq(material)
    )
    number = {
        col: st.column_config.NumberColumn(col, min_value=0.0, format="%g") for col in recipe_db.NUMERIC_COLUMNS
    }
    edited = st.data_editor(
        base[mask],
        key=f"recipe_db_editor_{ver}_{material}",
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        height=min(440, 38 + 35 * (int(mask.sum()) + 2)),
        disabled=[recipe_db.UPDATED_COLUMN],
        column_config={
            recipe_db.P_COLUMN: st.column_config.NumberColumn(
                "P", min_value=1, max_value=9999, step=1, format="%04d", required=True,
                help="Recipe number as set on the control panel — the P of M98 Pxxxx",
            ),
            recipe_db.MATERIAL_COLUMN: st.column_config.TextColumn("Material"),
            **number,
            recipe_db.NOTES_COLUMN: st.column_config.TextColumn("Notes", width="large"),
            recipe_db.UPDATED_COLUMN: st.column_config.TextColumn(
                "Updated", help="Set automatically when a row is added or changed"
            ),
        },
    )

    col_save, col_note = st.columns([1, 3], vertical_alignment="center")
    col_note.caption("Changes are only kept when you press Save. Close this window to discard them.")
    if col_save.button("Save", type="primary", use_container_width=True):
        merged = pd.concat([base[~mask], edited], ignore_index=True)
        errors = recipe_db.validate_recipes(merged)
        if errors:
            st.error("Not saved:\n" + "\n".join(f"- {e}" for e in errors))
            return
        try:
            recipe_db.save_recipes(recipe_db.stamp_changed(merged, on_disk), path)
        except ValueError as exc:
            st.error(str(exc))
            return
        ss[RECIPE_DB_SAVED] = f"Saved {len(merged)} recipe(s) to {path.name}."
        _open_material_database()
        st.rerun()


def render_recipe_step(program: GCodeProgram, fix_id: str, fix_index: int) -> None:
    """Custom review step for change_recipes, laid out like section removal:
    the bed map beside a checklist of toolpaths. Tick toolpaths (in the list,
    or by clicking them on the map), enter a P, and Set gives every ticked
    toolpath that P; Reset puts them back. Both untick afterwards, so the map
    then shows the result rather than the selection.

    Nothing changes until a P is set: a recipe change changes what's
    deposited. Chosen Ps live in `accept_*`-prefixed state keyed by toolpath
    start line + name, so they're cleared with the rest of the review state
    and dropped rather than misapplied if an earlier step changes the program.
    """
    ss = st.session_state
    prefix = f"accept_{fix_id}_{fix_index}"
    choice_state: dict[str, int] = ss.setdefault(f"{prefix}_choice", {})

    calls = toolpath_recipe_calls(program)
    # What each P is recorded as, for reference only: the step works the same
    # with an empty database, since the numbers are what the program uses.
    try:
        recorded = recipe_db.summaries_by_p(recipe_db.load_recipes())
    except ValueError as exc:
        st.warning(f"Material database not read: {exc}")
        recorded = {}

    def recorded_as(p: int | None) -> str:
        if p is None:
            return ""
        if p not in recorded:
            return "not in database"
        return recorded[p] or "in database, no details"

    def tp_key(call) -> str:
        return f"{call.start_index}:{call.name}"

    changeable = [c for c in calls if c.call_index is not None]
    key_of = {c.start_index: tp_key(c) for c in changeable}
    current_p = {c.start_index: c.p_value for c in changeable}
    for key in [k for k in choice_state if k not in set(key_of.values())]:
        del choice_state[key]

    def p_text(call, p: int) -> str:
        return f"P{p:0{call.p_width}d}"

    if not calls:
        st.success("No named toolpaths found — nothing to change.")
    else:
        st.info(
            "Tick the toolpaths that should run a different recipe — in the list, or by clicking them on the "
            "map — then enter the new **P** and press **Set**. Every toolpath keeps its current P until you do."
        )

        tick_key = {c.start_index: f"{prefix}_tick_{c.start_index}" for c in changeable}
        master_key = f"{prefix}_all"
        new_p_key = f"{prefix}_newp"
        need_p_key = f"{prefix}_needp"
        ticked = {start for start, k in tick_key.items() if ss.get(k)}

        # Bound as defaults: the callbacks outlive this run. Writing the tick
        # boxes' keys is allowed here because callbacks run before the
        # widgets are drawn.
        def apply_to_ticked(
            p_key: str | None,
            tick_key: dict = tick_key,
            master_key: str = master_key,
            need_p_key: str = need_p_key,
            key_of: dict = key_of,
            current_p: dict = current_p,
            choice_state: dict = choice_state,
        ) -> None:
            p = ss.get(p_key) if p_key else None
            if p_key and p is None:
                ss[need_p_key] = True
                return
            for start, k in tick_key.items():
                if not ss.get(k):
                    continue
                if p is None or int(p) == current_p[start]:
                    choice_state.pop(key_of[start], None)
                else:
                    choice_state[key_of[start]] = int(p)
                ss[k] = False
            ss[master_key] = False

        toolpaths = [tp for tp in analyse_toolpaths(program) if tp.start_index in {c.start_index for c in calls}]
        part_of = {tp.start_index: pt for pt in group_into_parts(toolpaths) for tp in pt.toolpaths}
        points = path_points(toolpaths)

        if points:
            col_map, col_list = st.columns([3, 2], gap="medium")
            # A slot, filled after the checklist so the map shows this run's ticks.
            map_slot = col_map.container()
        else:
            col_list = st.container()

        with col_list:
            col_p, col_set, col_reset = st.columns([2, 1, 1], vertical_alignment="bottom")
            col_p.number_input(
                "New P for ticked toolpaths", min_value=1, max_value=9999, step=1, value=None,
                key=new_p_key, placeholder="e.g. 21",
            )
            col_set.button(
                "Set", type="primary", use_container_width=True, key=f"{prefix}_set",
                # Not disabled for an empty P box: a typed number only reaches
                # the app when the box loses focus, which clicking Set does —
                # disabling on it would leave a dead button under the cursor.
                disabled=not ticked,
                on_click=apply_to_ticked, args=(new_p_key,),
                help="Give every ticked toolpath this P",
            )
            col_reset.button(
                "Reset", use_container_width=True, key=f"{prefix}_reset",
                disabled=not ticked or not any(key_of[s] in choice_state for s in ticked),
                on_click=apply_to_ticked, args=(None,),
                help="Put every ticked toolpath back to its original P",
            )
            if ss.pop(need_p_key, False):
                st.warning("Enter the new P first, then press Set.")
            # No value= on these: callbacks write their keys, and a keyed
            # widget given both makes Streamlit warn. They default to False.
            st.checkbox(
                "Tick all",
                key=master_key,
                on_change=_sync_children_to_master,
                args=(master_key, list(tick_key.values())),
            )
            with st.container(height=470 if points else 440, border=True):
                for c in calls:
                    part = part_of.get(c.start_index)
                    where = f" · {part.name} ({part.where})" if part and part.bounds else ""
                    if c.call_index is None:
                        st.checkbox(f"**{c.name}**{where} — no M98 call, can't be changed", value=False,
                                    disabled=True, key=f"{prefix}_nocall_{c.start_index}")
                        continue
                    new = choice_state.get(key_of[c.start_index])
                    shown = new if new is not None else c.p_value
                    p_part = (
                        f"{p_text(c, c.p_value)} → **{p_text(c, new)}**" if new is not None
                        else p_text(c, c.p_value)
                    )
                    st.checkbox(
                        f"**{c.name}**{where} — {p_part} :gray[{recorded_as(shown)}]",
                        key=tick_key[c.start_index],
                    )

        # The ticks as drawn this run — the boxes above may have changed them.
        ticked = {start for start, k in tick_key.items() if ss.get(k)}
        if points:
            # The chart's key carries a version that every click bumps, so it
            # comes back with an empty selection and the same toolpath can be
            # clicked again (see the section-removal map).
            map_ver_key = f"{prefix}_mapver"
            map_key = f"{prefix}_map_{ss.get(map_ver_key, 0)}"

            def toggle_from_map(
                map_key: str = map_key,
                tick_key: dict = tick_key,
                master_key: str = master_key,
                map_ver_key: str = map_ver_key,
            ) -> None:
                child = tick_key.get(toolpath_from_map_selection(ss.get(map_key), RECIPE_SELECTION))
                if child is None:
                    return
                ss[child] = not ss.get(child, ss.get(master_key, False))
                ss[map_ver_key] = ss.get(map_ver_key, 0) + 1

            changed = {s for s, k in key_of.items() if k in choice_state}
            final_p = {s: choice_state.get(key_of[s], current_p[s]) for s in key_of}
            labels = {
                c.start_index: {
                    "Toolpath": c.name,
                    "Part": (
                        f"{part_of[c.start_index].name} — {part_of[c.start_index].where}"
                        if c.start_index in part_of else "—"
                    ),
                    "P": (
                        "no M98 call" if c.call_index is None
                        else f"{p_text(c, c.p_value)} → {p_text(c, final_p[c.start_index])}"
                        if c.start_index in changed else p_text(c, c.p_value)
                    ),
                    "Settings": recorded_as(final_p.get(c.start_index)),
                }
                for c in calls
            }
            chart = build_recipe_map(
                points, labels, {s: f"P{p}" for s, p in final_p.items()}, changed, ticked,
                size=int(ss.get(MAP_SIZE_KEY, 520)),
            )
            with map_slot:
                st.altair_chart(
                    chart, key=map_key, on_select=toggle_from_map,
                    selection_mode=[RECIPE_SELECTION], width="content",
                )
                stacked = any(len(pt.toolpaths) > 1 for pt in group_into_parts(toolpaths))
                st.caption(
                    "Click a toolpath to tick or untick it. Each box shows the P it will run; orange, thicker "
                    "and bold = a new P."
                    + (
                        " Some toolpaths share a footprint (layers of one build), so only the top one can be "
                        "clicked there — use the list for the rest."
                        if stacked else ""
                    )
                )

    start_of = {k: s for s, k in key_of.items()}
    result = run_fix(program, fix_id, {"recipes": {start_of[k]: p for k, p in choice_state.items()}}).result

    if result.changes:
        st.write(result.summary)
        key_of_call = {c.call_index: tp_key(c) for c in calls}
        new_p_of = [choice_state[key_of_call[c.original_index]] for c in result.changes]
        with st.expander(f"Lines that change ({len(result.changes)})", expanded=False):
            st.dataframe(
                pd.DataFrame(
                    {
                        "Line": [c.original_index + 1 for c in result.changes],
                        "Toolpath": [c.label or "" for c in result.changes],
                        "Before": [c.original_text.strip() for c in result.changes],
                        "After": [(c.new_text or "").strip() for c in result.changes],
                        "New P settings": [recorded_as(p) for p in new_p_of],
                    }
                ),
                hide_index=True,
                use_container_width=True,
                height=min(300, 38 + 35 * len(result.changes)),
            )
        new_ps = sorted(set(new_p_of))
        st.warning(
            "Before running, check on the control panel that "
            + ("recipe " if len(new_ps) == 1 else "recipes ")
            + ", ".join(f"P{p:04d}" for p in new_ps)
            + (" holds" if len(new_ps) == 1 else " hold")
            + " the laser, powder and nozzle gas settings you want — the program only names the recipe number."
        )
    elif calls:
        st.caption("No P numbers changed; the file will pass through unchanged.")

    col_a, col_b, col_c, _ = st.columns([2, 1, 1, 1])
    with col_a:
        if st.button("Apply P changes →", type="primary", use_container_width=True):
            _apply_and_advance(result.program, f"[{fix_id}] {result.summary}")
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
        "Applied in a fixed order — rotary cleanup, section removal, recipes, repeated calls, dwell review."
    )
    selected_ids = []
    for fix_id in ordered_fix_ids:
        fix = fixes[fix_id]()
        # Keyed so a selection change made part-way through a review can be
        # put back if the user decides not to start over.
        checked = st.toggle(fix.label or fix_id, help=fix.description, key=FIX_TOGGLE_KEY.format(fix_id))
        if checked:
            selected_ids.append(fix_id)
    st.divider()
    # Rendered before the no-upload st.stop() below, so the database can be
    # looked after without a program loaded.
    if st.button(
        "🧪 Material database", use_container_width=True, on_click=_open_material_database,
        help="What each recipe number (M98 Pxxxx) sets on the control panel",
    ):
        material_database_dialog()
    if saved := st.session_state.pop(RECIPE_DB_SAVED, None):
        st.toast(saved)
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

    if fix_id == "change_recipes":
        # Proposes nothing until a P number is entered, so its branch must
        # stay ahead of the "no changes" branch.
        render_recipe_step(current_program, fix_id, fix_index)
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
        st.info(
            "Nothing is removed unless you tick it. Tick a section — or click it on the map — "
            "to remove that whole block."
        )

        accept_key_prefix = f"accept_{fix_id}_{fix_index}"
        key_of_change = {c.original_index: f"{accept_key_prefix}_{c.original_index}" for c in result.changes}
        child_keys = list(key_of_change.values())
        remove_all = st.checkbox(
            "Remove all sections",
            value=False,
            key=f"{accept_key_prefix}_all",
            on_change=_sync_children_to_master,
            args=(f"{accept_key_prefix}_all", child_keys),
        )

        # Where each section sits on the bed: names like `..._COPY_35` don't
        # say, and that's what decides whether it should go. Each section
        # starts at its marker line, which is also where its toolpath starts.
        section_toolpaths = [tp for tp in analyse_toolpaths(current_program) if tp.start_index in key_of_change]
        removal_parts = group_into_parts(section_toolpaths)
        part_of_section = {tp.start_index: pt for pt in removal_parts for tp in pt.toolpaths}
        points = path_points(section_toolpaths)

        if points:
            col_map, col_list = st.columns([3, 2], gap="medium")
            # A slot, filled after the checklist so the map shows this run's ticks.
            map_slot = col_map.container()
        else:
            col_list = st.container()

        accepted_indices = set()
        n_lines_selected = 0
        with col_list.container(height=520 if points else 440, border=True):
            for change in result.changes:
                n_lines = (change.end_index - change.original_index + 1) if change.end_index is not None else 1
                part = part_of_section.get(change.original_index)
                where = f" · {part.name} ({part.where})" if part and part.bounds else ""
                caption = (
                    f"**{change.label}**{where}  — lines {change.original_index + 1}-{change.end_index + 1}"
                    f" ({n_lines} lines)"
                )
                checked = st.checkbox(caption, value=remove_all, key=key_of_change[change.original_index])
                if checked:
                    accepted_indices.add(change.original_index)
                    n_lines_selected += n_lines

        if points:
            # The chart's key carries a version that every click bumps, so the
            # chart comes back with an empty selection. Otherwise clicking the
            # same toolpath twice wouldn't change the selection, the callback
            # wouldn't fire, and it could never be toggled back.
            map_ver_key = f"{accept_key_prefix}_mapver"
            map_key = f"{accept_key_prefix}_map_{st.session_state.get(map_ver_key, 0)}"

            # Bound as defaults: the callback outlives this run.
            def toggle_from_map(
                map_key: str = map_key,
                keys: dict = key_of_change,
                master: str = f"{accept_key_prefix}_all",
                map_ver_key: str = map_ver_key,
            ) -> None:
                ss = st.session_state
                child = keys.get(toolpath_from_map_selection(ss.get(map_key)))
                if child is None:
                    return
                ss[child] = not ss.get(child, ss.get(master, False))
                ss[map_ver_key] = ss.get(map_ver_key, 0) + 1

            labels = {
                c.original_index: {
                    "Section": c.label or "",
                    "Part": (
                        f"{part_of_section[c.original_index].name} — {part_of_section[c.original_index].where}"
                        if c.original_index in part_of_section else "—"
                    ),
                    "Lines": f"{c.original_index + 1}–{c.end_index + 1}",
                }
                for c in result.changes
            }
            chart = build_removal_map(
                points, labels, accepted_indices, size=int(st.session_state.get(MAP_SIZE_KEY, 520))
            )
            with map_slot:
                st.altair_chart(
                    chart,
                    key=map_key,
                    on_select=toggle_from_map,
                    selection_mode=[REMOVAL_SELECTION],
                    width="content",
                )
                stacked = any(len(pt.toolpaths) > 1 for pt in removal_parts)
                st.caption(
                    "Click a toolpath to tick or untick it for removal. Red, dashed and marked ✕ = will be removed."
                    + (
                        " Some sections share a footprint (layers of one build), so only the top one "
                        "can be clicked there — use the list for the rest."
                        if stacked else ""
                    )
                )

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
                            change_context(current_program.lines, change.original_index, end),
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
        st.session_state["output_name"] = default_output_name(uploaded.name)
        st.session_state["output_name_default"] = uploaded.name
    with st.form("output_name_form", border=False):
        st.text_input("Output file name", key="output_name")
        st.form_submit_button("Update file name")
    output = plan_output(current_program, uploaded.name, st.session_state["output_name"])
    for level, note in output.notes:
        (st.warning if level == "warning" else st.caption)(note)

    col_dl, col_back, col_restart, _ = st.columns([2, 1, 1, 1])
    with col_dl:
        st.download_button(
            "⬇ Download fixed file",
            data=output.to_bytes(),
            file_name=output.name,
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
