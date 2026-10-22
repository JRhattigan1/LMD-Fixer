import pytest

from lmd_fixer.fixes.change_recipes import toolpath_recipe_calls
from lmd_fixer.review import ReviewSession, ordered_fix_ids

ALL = ["remove_rotary_table", "remove_named_sections", "change_recipes", "remove_repeated_p_calls", "remove_dwells"]


def test_fixes_run_in_fix_order_whatever_order_given():
    assert ordered_fix_ids(["remove_dwells", "remove_rotary_table", "remove_dwells"]) == (
        "remove_rotary_table",
        "remove_dwells",
    )


def test_unknown_fix_is_rejected(sample_program):
    with pytest.raises(KeyError):
        ReviewSession(sample_program, ["no_such_fix"])


def test_defaults_come_from_the_fix(sample_program):
    session = ReviewSession(sample_program, ["remove_rotary_table", "remove_named_sections"])
    assert session.accepted_indices() == {c.original_index for c in session.proposal().changes}
    session.apply()
    assert session.fix_id == "remove_named_sections"
    assert session.accepted_indices() == set()  # section removal starts on keep


def test_apply_skip_and_summaries(sample_program):
    session = ReviewSession(sample_program, ["remove_rotary_table", "remove_named_sections"])
    first = session.proposal()
    session.set_accepted(first.changes[0].original_index, False)
    assert session.apply() == "[remove_rotary_table] Applied 10 of 11 proposed change(s)."
    assert session.skip() == "[remove_named_sections] Skipped."
    assert session.finished and session.fix is None
    assert [session.step_status(i) for i in range(2)] == ["done", "skipped"]
    assert sum("P8000" in l for l in session.result().lines) == 1
    with pytest.raises(RuntimeError):
        session.apply()


def test_preview_matches_what_apply_does(sample_program):
    session = ReviewSession(sample_program, ["remove_rotary_table"])
    preview = session.preview()
    session.apply()
    assert session.result().lines == preview.lines


def test_back_restores_the_program_and_keeps_choices(sample_program):
    session = ReviewSession(sample_program, ["remove_rotary_table", "remove_repeated_p_calls"])
    session.set_all_accepted(False)
    session.set_accepted(session.proposal().changes[0].original_index, True)
    session.apply()
    after_first = session.current
    session.apply()
    session.back()
    assert session.current is after_first and session.fix_id == "remove_repeated_p_calls"
    session.back_to(0)
    assert session.current.lines == sample_program.lines
    assert len(session.accepted_indices()) == 1  # the choice survived going back
    with pytest.raises(IndexError):
        session.back_to(1)


def test_restart_keeps_choices_unless_told_not_to(sample_program):
    session = ReviewSession(sample_program, ["remove_rotary_table"])
    session.set_all_accepted(False)
    session.apply()
    session.restart()
    assert session.index == 0 and session.accepted_indices() == set()
    session.restart(keep_choices=False)
    assert len(session.accepted_indices()) == 11


def test_selection_change(sample_program):
    session = ReviewSession(sample_program, ["remove_rotary_table"])
    session.set_all_accepted(False)
    assert not session.selection_change_discards_work(ALL)  # nothing applied yet
    session.apply()
    assert not session.selection_change_discards_work(["remove_rotary_table"])  # same selection
    assert session.selection_change_discards_work(ALL)
    session.set_selection(ALL)
    assert session.index == 0 and session.fix_ids == tuple(ALL)
    assert len(session.accepted_indices()) == 11  # choices forgotten


def test_option_driven_step_applies_all_its_changes(sample_program):
    session = ReviewSession(sample_program, ["change_recipes", "remove_repeated_p_calls"])
    assert session.proposal().changes == []
    path_c = next(c for c in toolpath_recipe_calls(session.current) if c.name == "PATH_C")
    session.set_options(recipes={path_c.start_index: 20})
    assert len(session.proposal().changes) == 1
    assert session.apply() == "[change_recipes] Changed the recipe of 1 toolpath(s)."
    assert len(session.proposal().changes) == 2  # PATH_B and now PATH_C repeat P20


def test_full_review_with_defaults(sample_program):
    session = ReviewSession(sample_program, ALL)
    while not session.finished:
        session.apply()
    out = session.result().lines
    assert out.count("/M98 P0020") == 1 and out.count("/M98 P0021") == 1
    assert not any("/G4 X25.00" == l for l in out)  # both remaining dwells accepted
    assert "(PATH_B)" in out  # sections kept by default


def test_plan_output_uses_the_file_name_not_the_path(sample_program):
    sample_program.source_name = r"C:\somewhere\O1234.ptp"
    plan = ReviewSession(sample_program, []).plan_output("")
    assert plan.name == "fixed_O1234.ptp"
