"""Streamlit web UI for LMD-Fixer.

Run with `lmd-fixer` (installed entry point) or
`streamlit run lmd_fixer/app.py` from a checkout.
"""

from __future__ import annotations

import difflib
import html
import sys
from pathlib import Path

# `streamlit run` executes this file as a plain script, so when running from
# a checkout (not an installed package) make the repo root importable.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from lmd_fixer.fixes import available_fixes
from lmd_fixer.gcode import GCodeProgram
from lmd_fixer.pipeline import apply_accepted_changes, run_fix

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
        }}
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
        div[data-testid="stButton"] > button {{ border-radius: 10px; }}
        div[data-testid="stDownloadButton"] > button {{ border-radius: 10px; }}

        /* file uploader card */
        section[data-testid="stFileUploaderDropzone"] {{
            border-radius: 14px;
            border: 1.5px dashed rgba(45,212,191,0.45);
            background: rgba(45,212,191,0.04);
        }}

        /* expanders as subtle cards */
        details[data-testid="stExpander"] {{
            border-radius: 10px; border: {CARD_BORDER};
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


st.set_page_config(page_title="LMD-Fixer", page_icon="🔧", layout="wide")
inject_css()
render_hero()
st.write("")

fixes = available_fixes()

# Fixed run order: rotary table cleanup, then optional named-section removal,
# then repeated M98/M325 program calls are collapsed, and only then are the
# surviving G4 X25.00 dwells (genuine P-value changes) put up for manual review.
FIX_ORDER = ["remove_rotary_table", "remove_named_sections", "remove_repeated_p_calls", "remove_dwells"]
ordered_fix_ids = [fid for fid in FIX_ORDER if fid in fixes]
ordered_fix_ids += [fid for fid in fixes if fid not in ordered_fix_ids]

with st.sidebar:
    st.markdown(f"### <span style='color:{ACCENT}'>&#9881;</span> Fixes", unsafe_allow_html=True)
    st.caption("Applied in a fixed order — rotary cleanup, section removal, repeated calls, dwell review.")
    selected_ids = []
    for fix_id in ordered_fix_ids:
        fix = fixes[fix_id]()
        checked = st.toggle(fix.label or fix_id, help=fix.description)
        if checked:
            selected_ids.append(fix_id)

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

# Reset review state whenever the file or fix selection changes.
state_key = (uploaded.name, uploaded.size, tuple(selected_ids))
if st.session_state.get("state_key") != state_key:
    st.session_state["state_key"] = state_key
    text = uploaded.getvalue().decode("utf-8", errors="replace")
    original_program = GCodeProgram.from_text(text, source_name=uploaded.name)
    st.session_state["original_program"] = original_program
    st.session_state["current_program"] = original_program.copy()
    st.session_state["fix_index"] = 0
    st.session_state["applied_summaries"] = []
    st.session_state["history"] = []
    # Drop leftover per-change checkbox state from a previous file/selection,
    # which would otherwise leak into this review wherever keys collide.
    _clear_review_widget_state()

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

    if not result.changes:
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

    col_dl, col_back, col_restart, _ = st.columns([2, 1, 1, 1])
    with col_dl:
        st.download_button(
            "⬇ Download fixed file",
            data=current_program.to_text("\r\n"),
            file_name=f"fixed_{uploaded.name}",
            mime="text/plain",
            type="primary",
            use_container_width=True,
        )
    with col_back:
        if st.button("← Back", use_container_width=True):
            _go_back()
    with col_restart:
        if st.button("Start over", use_container_width=True):
            st.session_state["fix_index"] = 0
            st.session_state["applied_summaries"] = []
            st.session_state["current_program"] = original_program.copy()
            st.session_state["history"] = []
            _clear_review_widget_state()
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
