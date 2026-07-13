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
MAX_CHANGES_WITH_CONTEXT = 30


def render_side_by_side_diff(original_lines: list[str], final_lines: list[str]) -> str:
    """Builds an HTML two-column diff: original on the left, final on the right,
    with removed lines highlighted red on the left and added/changed lines
    highlighted green on the right. Unchanged lines are shown plainly on both sides.
    """
    matcher = difflib.SequenceMatcher(a=original_lines, b=final_lines, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
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
        "delete": ("#5a1e1e", "transparent"),
        "insert": ("transparent", "#1e4620"),
        "replace": ("#5a1e1e", "#1e4620"),
    }

    html_rows = []
    for otext, ftext, tag in rows:
        left_bg, right_bg = row_colors[tag]
        html_rows.append(
            "<tr>"
            f'<td style="background:{left_bg}; padding:1px 6px; white-space:pre;">{html.escape(otext)}</td>'
            f'<td style="background:{right_bg}; padding:1px 6px; white-space:pre;">{html.escape(ftext)}</td>'
            "</tr>"
        )

    return (
        '<div style="max-height:600px; overflow:auto; font-family:monospace; font-size:12px; border:1px solid #444;">'
        '<table style="border-collapse:collapse; width:100%;">'
        '<thead style="position:sticky; top:0; background:#222;">'
        '<tr><th style="text-align:left; padding:4px 6px;">Original</th>'
        '<th style="text-align:left; padding:4px 6px;">Final</th></tr>'
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


def _go_back() -> None:
    """Returns to the previous fix, restoring the program as it was before
    that fix was applied. Its checkboxes keep their previous state."""
    st.session_state["current_program"] = st.session_state["history"].pop()
    st.session_state["applied_summaries"].pop()
    st.session_state["fix_index"] -= 1
    st.rerun()


st.set_page_config(page_title="LMD-Fixer", page_icon=":wrench:", layout="wide")
st.title("LMD-Fixer")
st.caption("Upload laser metal deposition G-code, choose fixes, review each change, download the corrected file.")

uploaded = st.file_uploader("G-code file", type=["ptp", "nc", "txt", "gcode"])

fixes = available_fixes()

# Fixed run order: rotary table cleanup, then optional named-section removal,
# then repeated M98/M325 program calls are collapsed, and only then are the
# surviving G4 X25.00 dwells (genuine P-value changes) put up for manual review.
FIX_ORDER = ["remove_rotary_table", "remove_named_sections", "remove_repeated_p_calls", "flag_dwells"]
ordered_fix_ids = [fid for fid in FIX_ORDER if fid in fixes]
ordered_fix_ids += [fid for fid in fixes if fid not in ordered_fix_ids]

st.sidebar.header("Fixes")
st.sidebar.caption(
    "Applied in a fixed order: rotary cleanup -> section removal -> repeated program calls -> dwell review."
)
selected_ids = []
for fix_id in ordered_fix_ids:
    fix = fixes[fix_id]()
    checked = st.sidebar.checkbox(fix.label or fix_id, help=fix.description)
    if checked:
        selected_ids.append(fix_id)

if uploaded is None:
    st.info("Upload a .ptp/.nc/.gcode file to begin.")
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

# Sidebar: pipeline progress for the current review session.
if selected_ids:
    st.sidebar.divider()
    st.sidebar.subheader("Progress")
    for i, fid in enumerate(selected_ids):
        label = fixes[fid]().label or fid
        if i < fix_index:
            st.sidebar.markdown(f":white_check_mark: ~~{label}~~")
        elif i == fix_index:
            st.sidebar.markdown(f":arrow_forward: **{label}**")
        else:
            st.sidebar.markdown(f":black_small_square: {label}")
    st.sidebar.caption(
        f"{uploaded.name} — {len(original_program.lines)} lines uploaded, "
        f"{len(current_program.lines)} lines currently."
    )

if not selected_ids:
    st.info("Select one or more fixes from the sidebar.")
    st.subheader("Current file")
    st.caption(f"{uploaded.name} — {len(current_program.lines)} lines")
    st.text_area("current", current_program.to_text("\n"), height=400, label_visibility="collapsed")
    st.stop()

if fix_index < len(selected_ids):
    fix_id = selected_ids[fix_index]
    fix_cls = fixes[fix_id]
    fix = fix_cls()

    st.progress(fix_index / len(selected_ids), text=f"Step {fix_index + 1} of {len(selected_ids)}")
    st.subheader(f"Review: {fix.label}")
    st.caption(fix.description)

    run_result = run_fix(current_program, fix_id)
    result = run_result.result

    if not result.changes:
        st.success("No changes proposed by this fix — nothing to review.")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Continue", type="primary"):
                _advance(f"[{fix_id}] {result.summary}")
        with col_b:
            if fix_index > 0 and st.button("Back to previous fix"):
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
        with st.container(height=440):
            for change in result.changes:
                n_lines = (change.end_index - change.original_index + 1) if change.end_index is not None else 1
                caption = f"**{change.label}**  — lines {change.original_index + 1}-{change.end_index + 1} ({n_lines} lines)"
                checked = st.checkbox(caption, value=remove_all, key=f"{accept_key_prefix}_{change.original_index}")
                if checked:
                    accepted_indices.add(change.original_index)
                    n_lines_selected += n_lines

        if accepted_indices:
            st.warning(
                f"{len(accepted_indices)} section(s) selected — **{n_lines_selected} lines** will be removed."
            )
        else:
            st.caption("No sections selected; the file will pass through unchanged.")

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if st.button("Apply and continue", type="primary"):
                new_program = apply_accepted_changes(current_program, result, accepted_indices)
                summary = f"[{fix_id}] Removed {len(accepted_indices)} of {len(result.changes)} section(s)."
                st.session_state["history"].append(st.session_state["current_program"].copy())
                st.session_state["current_program"] = new_program
                st.session_state["applied_summaries"].append(summary)
                st.session_state["fix_index"] += 1
                st.rerun()
        with col_b:
            if st.button("Skip this fix entirely"):
                _advance(f"[{fix_id}] Skipped.")
        with col_c:
            if fix_index > 0 and st.button("Back to previous fix"):
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
        with st.container(height=440):
            for change in result.changes:
                default = select_all
                where = f" in {change.label}" if change.label else ""
                if change.kind == "removed":
                    reason = f" ({change.new_text})" if change.new_text else ""
                    caption = (
                        f"Line {change.original_index + 1}{where}: REMOVING `{change.original_text.strip()}`{reason} "
                        "— uncheck to keep this line instead"
                    )
                elif change.kind == "modified":
                    caption = (
                        f"Line {change.original_index + 1}{where}: CHANGING `{change.original_text.strip()}` "
                        f"->  `{(change.new_text or '').strip()}` — uncheck to leave unchanged"
                    )
                else:
                    caption = (
                        f"Line {change.original_index + 1}{where}: FLAGGING `{change.original_text.strip()}` "
                        "— uncheck to leave unflagged"
                    )
                checked = st.checkbox(caption, value=default, key=f"{accept_key_prefix}_{change.original_index}")
                if checked:
                    accepted_indices.add(change.original_index)
                if show_context:
                    end = change.end_index if change.end_index is not None else change.original_index
                    with st.expander("Show surrounding lines", expanded=False):
                        st.code(
                            render_change_context(current_program.lines, change.original_index, end),
                            language=None,
                        )

        st.caption(f"{len(accepted_indices)} of {len(result.changes)} change(s) selected.")

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if st.button("Apply accepted changes and continue", type="primary"):
                new_program = apply_accepted_changes(current_program, result, accepted_indices)
                summary = f"[{fix_id}] Applied {len(accepted_indices)} of {len(result.changes)} proposed change(s)."
                st.session_state["history"].append(st.session_state["current_program"].copy())
                st.session_state["current_program"] = new_program
                st.session_state["applied_summaries"].append(summary)
                st.session_state["fix_index"] += 1
                st.rerun()
        with col_b:
            if st.button("Skip this fix entirely"):
                _advance(f"[{fix_id}] Skipped.")
        with col_c:
            if fix_index > 0 and st.button("Back to previous fix"):
                _go_back()
else:
    st.progress(1.0, text="All steps reviewed")
    st.success("All selected fixes reviewed.")

    n_original = len(original_program.lines)
    n_final = len(current_program.lines)
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Original lines", n_original)
    col_b.metric("Final lines", n_final)
    col_c.metric("Lines removed", n_original - n_final)

    st.download_button(
        "Download fixed file",
        data=current_program.to_text("\r\n"),
        file_name=f"fixed_{uploaded.name}",
        mime="text/plain",
        type="primary",
    )

    if st.session_state["applied_summaries"]:
        st.subheader("Summary")
        for s in st.session_state["applied_summaries"]:
            st.write(f"- {s}")

    st.subheader("Final review: original vs. fixed")
    st.caption("Red = removed from the original. Green = added/changed in the final version.")
    st.markdown(
        render_side_by_side_diff(original_program.lines, current_program.lines),
        unsafe_allow_html=True,
    )

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Back to previous fix"):
            _go_back()
    with col_b:
        if st.button("Start over"):
            st.session_state["fix_index"] = 0
            st.session_state["applied_summaries"] = []
            st.session_state["current_program"] = original_program.copy()
            st.session_state["history"] = []
            _clear_review_widget_state()
            st.rerun()
