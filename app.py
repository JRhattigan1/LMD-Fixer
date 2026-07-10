"""Streamlit web UI for LMD-Fixer.

Run with: streamlit run app.py
"""

from __future__ import annotations

import difflib
import html

import streamlit as st

from lmd_fixer.fixes import available_fixes
from lmd_fixer.gcode import GCodeProgram
from lmd_fixer.pipeline import apply_accepted_changes, run_fix

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


st.set_page_config(page_title="LMD-Fixer", layout="wide")
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

current_program: GCodeProgram = st.session_state["current_program"]
fix_index: int = st.session_state["fix_index"]

if not selected_ids:
    st.info("Select one or more fixes from the sidebar.")
    st.subheader("Current file")
    st.text_area("current", current_program.to_text("\n"), height=400, label_visibility="collapsed")
    st.stop()

if fix_index < len(selected_ids):
    fix_id = selected_ids[fix_index]
    fix_cls = fixes[fix_id]
    fix = fix_cls()

    st.subheader(f"Review: {fix.label} ({fix_index + 1} of {len(selected_ids)})")
    st.caption(fix.description)

    run_result = run_fix(current_program, fix_id)
    result = run_result.result

    if not result.changes:
        st.success("No changes proposed by this fix.")
        if st.button("Continue"):
            st.session_state["applied_summaries"].append(f"[{fix_id}] {result.summary}")
            st.session_state["fix_index"] += 1
            st.rerun()
    elif fix_id == "remove_named_sections":
        # Section removal is opt-in per section (defaults to keeping everything),
        # unlike the other fixes which default to applying every proposed change.
        st.write(result.summary)

        accept_key_prefix = f"accept_{fix_id}_{fix_index}"
        remove_all = st.checkbox("Remove all sections", value=False, key=f"{accept_key_prefix}_all")

        accepted_indices = set()
        for change in result.changes:
            n_lines = (change.end_index - change.original_index + 1) if change.end_index is not None else 1
            caption = f"{change.label}  (lines {change.original_index + 1}-{change.end_index + 1}, {n_lines} lines) — check to remove this section"
            checked = st.checkbox(caption, value=remove_all, key=f"{accept_key_prefix}_{change.original_index}")
            if checked:
                accepted_indices.add(change.original_index)

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Apply and continue", type="primary"):
                new_program = apply_accepted_changes(current_program, result, accepted_indices)
                st.session_state["current_program"] = new_program
                st.session_state["applied_summaries"].append(
                    f"[{fix_id}] Removed {len(accepted_indices)} of {len(result.changes)} section(s)."
                )
                st.session_state["fix_index"] += 1
                st.rerun()
        with col_b:
            if st.button("Skip this fix entirely"):
                st.session_state["applied_summaries"].append(f"[{fix_id}] Skipped.")
                st.session_state["fix_index"] += 1
                st.rerun()
    else:
        st.write(result.summary)

        if st.button(f"Accept all {len(result.changes)} change(s) and continue", type="primary"):
            all_indices = {c.original_index for c in result.changes}
            new_program = apply_accepted_changes(current_program, result, all_indices)
            st.session_state["current_program"] = new_program
            st.session_state["applied_summaries"].append(
                f"[{fix_id}] Applied all {len(result.changes)} proposed change(s)."
            )
            st.session_state["fix_index"] += 1
            st.rerun()

        st.divider()

        accept_key_prefix = f"accept_{fix_id}_{fix_index}"
        select_all = st.checkbox("Accept all", value=True, key=f"{accept_key_prefix}_all")

        accepted_indices = set()
        for change in result.changes:
            default = select_all
            if change.kind == "removed":
                reason = f" ({change.new_text})" if change.new_text else ""
                caption = (
                    f"Line {change.original_index + 1}: REMOVING `{change.original_text.strip()}`{reason} "
                    "— uncheck to keep this line instead"
                )
            elif change.kind == "modified":
                caption = (
                    f"Line {change.original_index + 1}: CHANGING `{change.original_text.strip()}` "
                    f"->  `{(change.new_text or '').strip()}` — uncheck to leave unchanged"
                )
            else:
                caption = (
                    f"Line {change.original_index + 1}: FLAGGING `{change.original_text.strip()}` "
                    "— uncheck to leave unflagged"
                )
            checked = st.checkbox(caption, value=default, key=f"{accept_key_prefix}_{change.original_index}")
            if checked:
                accepted_indices.add(change.original_index)

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Apply accepted changes and continue", type="primary"):
                new_program = apply_accepted_changes(current_program, result, accepted_indices)
                st.session_state["current_program"] = new_program
                st.session_state["applied_summaries"].append(
                    f"[{fix_id}] Applied {len(accepted_indices)} of {len(result.changes)} proposed change(s)."
                )
                st.session_state["fix_index"] += 1
                st.rerun()
        with col_b:
            if st.button("Skip this fix entirely"):
                st.session_state["applied_summaries"].append(f"[{fix_id}] Skipped.")
                st.session_state["fix_index"] += 1
                st.rerun()
else:
    st.success("All selected fixes reviewed.")

    if st.session_state["applied_summaries"]:
        st.subheader("Summary")
        for s in st.session_state["applied_summaries"]:
            st.write(f"- {s}")

    original_program: GCodeProgram = st.session_state["original_program"]

    st.subheader("Final review: original vs. fixed")
    st.caption("Red = removed from the original. Green = added/changed in the final version.")
    st.markdown(
        render_side_by_side_diff(original_program.lines, current_program.lines),
        unsafe_allow_html=True,
    )

    st.download_button(
        "Download fixed file",
        data=current_program.to_text("\r\n"),
        file_name=f"fixed_{uploaded.name}",
        mime="text/plain",
    )

    if st.button("Start over"):
        st.session_state["fix_index"] = 0
        st.session_state["applied_summaries"] = []
        st.session_state["current_program"] = original_program.copy()
        st.rerun()
