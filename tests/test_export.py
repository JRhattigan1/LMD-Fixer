from lmd_fixer.export import change_context, diff_rows, machine_bytes, plan_output, sync_o_number
from lmd_fixer.gcode import GCodeProgram


def program(*lines):
    return GCodeProgram(lines=list(lines))


def test_sync_o_number():
    p = program("%", "O1140", "G90")
    out, old, new = sync_o_number(p, "O1150 fixed.ptp")
    assert (old, new) == ("1140", "1150") and out.lines[1] == "O1150" and p.lines[1] == "O1140"
    assert sync_o_number(p, "O1140.ptp")[1:] == ("1140", "1140")
    assert sync_o_number(p, "fixed.ptp") == (p, None, None)
    assert sync_o_number(program("G90"), "O1150.ptp")[1:] == (None, None)


def test_sync_keeps_block_skip_slash():
    out, _, _ = sync_o_number(program("/O1140"), "O2000.ptp")
    assert out.lines == ["/O2000"]


def test_plan_output_names_and_notes():
    p = program("O1140")
    assert plan_output(p, "O1140.ptp", "").name == "fixed_O1140.ptp"
    plan = plan_output(p, "O1140.ptp", "O1150")
    assert plan.name == "O1150.ptp" and plan.program.lines == ["O1150"]
    assert ("info", "No file extension given — appended '.ptp' to match the uploaded file.") in plan.notes
    assert any(level == "warning" and "extension changed" in n for level, n in plan_output(p, "O1140.ptp", "O1140.nc").notes)
    assert any("Multiple O-numbers" in n for _, n in plan_output(p, "O1140.ptp", "O1150_O1160.ptp").notes)


def test_machine_bytes_are_crlf():
    assert machine_bytes(program("%", "O1", "%")) == b"%\r\nO1\r\n%"


def test_diff_rows_collapse_long_unchanged_runs():
    original = [f"L{i}" for i in range(20)]
    final = original[:10] + original[11:]
    rows = diff_rows(original, final, n_context=3)
    tags = [r.tag for r in rows]
    assert tags.count("delete") == 1 and tags.count("gap") == 2
    deleted = next(r for r in rows if r.tag == "delete")
    assert (deleted.left, deleted.left_no, deleted.right_no) == ("L10", 11, None)
    assert rows[0].tag == "equal" and rows[0].left_no == 1


def test_change_context_marks_changed_lines():
    text = change_context([f"L{i}" for i in range(10)], 4, 5, n_context=1)
    assert text.splitlines() == ["        4  L3", "->      5  L4", "->      6  L5", "        7  L6"]
