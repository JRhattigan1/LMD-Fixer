from lmd_fixer.fixes import LineChange, FixResult, section_names
from lmd_fixer.fixes.change_recipes import toolpath_recipe_calls
from lmd_fixer.gcode import GCodeProgram
from lmd_fixer.pipeline import apply_accepted_changes, run_fix


def full_accept(program, fix_id, options=None):
    result = run_fix(program, fix_id, options).result
    return result, apply_accepted_changes(program, result, {c.original_index for c in result.changes})


def test_rotary_removes_every_g65_m337_and_axis_zero(sample_program):
    result, out = full_accept(sample_program, "remove_rotary_table")
    # 3 per toolpath (G65, M337, A0) plus the footer's G65 + M337.
    assert len(result.changes) == 11
    assert not any("P8000" in l or l == "M337" or l == "G90 G0 A0.0" for l in out.lines)


def test_named_sections_proposes_one_range_per_block_and_spares_the_footer(sample_program):
    result = run_fix(sample_program, "remove_named_sections").result
    assert [c.label for c in result.changes] == ["PATH_A", "PATH_B", "PATH_C"]
    last = result.changes[-1]
    assert sample_program.lines[last.end_index] == "G0 Z8."  # footer excluded
    assert all(c.end_index > c.original_index for c in result.changes)


def test_repeated_p_call_collapses_as_one_group(sample_program):
    result, out = full_accept(sample_program, "remove_repeated_p_calls")
    assert len(result.changes) == 1
    (change,) = result.changes
    assert change.label == "PATH_B" and change.end_index - change.original_index == 2
    assert len(out.lines) == len(sample_program.lines) - 3


def test_dwells_name_the_p_they_follow(sample_program):
    _, program = full_accept(sample_program, "remove_repeated_p_calls")
    result = run_fix(program, "remove_dwells").result
    assert [c.reason for c in result.changes] == ["after M98 P0020", "after M98 P0021"]


def test_dwell_not_attributed_across_other_commands():
    program = GCodeProgram(lines=["/M98 P0020", "/M325", "G90", "/G4 X25.00"])
    (change,) = run_fix(program, "remove_dwells").result.changes
    assert change.reason == "after M98 unknown P value"


def test_change_recipes_keeps_slash_and_padding(sample_program):
    calls = toolpath_recipe_calls(sample_program)
    path_c = next(c for c in calls if c.name == "PATH_C")
    result, out = full_accept(sample_program, "change_recipes", {"recipes": {path_c.start_index: 7}})
    assert [(c.original_text, c.new_text) for c in result.changes] == [("/M98 P0021", "/M98 P0007")]
    assert out.lines[path_c.call_index] == "/M98 P0007"


def test_change_recipes_proposes_nothing_without_options(sample_program):
    assert run_fix(sample_program, "change_recipes").result.changes == []


def test_changing_a_recipe_feeds_into_repeated_call_collapse(sample_program):
    path_c = next(c for c in toolpath_recipe_calls(sample_program) if c.name == "PATH_C")
    _, out = full_accept(sample_program, "change_recipes", {"recipes": {path_c.start_index: 20}})
    result = run_fix(out, "remove_repeated_p_calls").result
    assert [c.label for c in result.changes] == ["PATH_B", "PATH_C"]


def test_apply_accepted_changes_splits_multiline_replacements():
    program = GCodeProgram(lines=["A", "B", "C"])
    result = FixResult(program=program, changes=[
        LineChange(kind="modified", original_index=1, original_text="B", new_text="B1\nB2"),
        LineChange(kind="removed", original_index=2, original_text="C"),
    ])
    assert apply_accepted_changes(program, result, {1, 2}).lines == ["A", "B1", "B2"]
    assert apply_accepted_changes(program, result, {1}).lines == ["A", "B1", "B2", "C"]


def test_section_names_tracks_the_enclosing_block(sample_program):
    names = section_names(sample_program.lines)
    assert names[0] is None
    assert names[sample_program.lines.index("(PATH_B)") + 1] == "PATH_B"


def test_toolpaths_parts_and_paths(sample_program):
    from lmd_fixer.toolpaths import analyse_toolpaths, group_into_parts, path_points

    toolpaths = analyse_toolpaths(sample_program)
    assert [tp.name for tp in toolpaths] == ["PATH_A", "PATH_B", "PATH_C"]
    a = toolpaths[0]
    assert a.measured_with_laser and a.length_mm == 40 and a.bounds == (0, 10, 5, 15)
    assert [p.name for p in group_into_parts(toolpaths)] == ["Part 1", "Part 2", "Part 3"]
    points = [p for p in path_points(toolpaths) if p["toolpath"] == a.start_index]
    # The laser-on square only: the rapid in and the plunge aren't drawn.
    assert [(p["x"], p["y"]) for p in points] == [(0, 5), (0, 15), (10, 15), (10, 5), (0, 5)]
