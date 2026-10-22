"""The desktop window, driven through its real widgets (offscreen)."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QSettings, Qt  # noqa: E402

from lmd_fixer.desktop.main_window import MainWindow  # noqa: E402
from tests.conftest import build_sample  # noqa: E402

FIXES = ["remove_rotary_table", "remove_named_sections", "remove_repeated_p_calls", "remove_dwells"]


@pytest.fixture
def window(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    settings.setValue("selected_fixes", FIXES)
    w = MainWindow(settings)
    qtbot.addWidget(w)
    return w


@pytest.fixture
def sample_file(tmp_path):
    path = tmp_path / "O1234.ptp"
    # Bytes, not write_text: text mode on Windows would turn \r\n into \r\r\n.
    path.write_bytes("\r\n".join(build_sample()).encode("utf-8"))
    return path


def ticks(w):
    tree = w.checklist.tree
    return [tree.topLevelItem(i).checkState(0) == Qt.Checked for i in range(tree.topLevelItemCount())]


def test_starts_on_the_welcome_page(window):
    assert window.central.currentWidget() is window.welcome
    assert window.steps.selected() == tuple(FIXES)
    assert not window.act_apply.isEnabled()


def test_open_lists_the_first_steps_changes(window, sample_file):
    assert window.open_file(str(sample_file))
    assert window.central.currentWidget() is window.splitter
    assert window.code.blockCount() == len(build_sample())
    assert window.panels.currentWidget() is window.checklist
    assert ticks(window) == [True] * 11
    assert str(sample_file.resolve()) in window.recent_files()


def test_toggle_undo_redo(window, sample_file):
    window.open_file(str(sample_file))
    window.checklist.move(2)
    window.checklist.toggle_selected()
    assert ticks(window)[2] is False and len(window.session.accepted_indices()) == 10
    window.undo.undo()
    assert ticks(window)[2] is True and len(window.session.accepted_indices()) == 11
    window.undo.redo()
    assert ticks(window)[2] is False
    # Clicking the tick box itself goes through the undo stack too.
    window.checklist.tree.topLevelItem(0).setCheckState(0, Qt.Unchecked)
    assert len(window.session.accepted_indices()) == 9
    window.undo.undo()
    assert len(window.session.accepted_indices()) == 10


def test_review_keys(window, sample_file):
    window.open_file(str(sample_file))
    reject_all = next(a for a in window.review_actions if a.text() == "Reject all")
    reject_all.trigger()
    assert ticks(window) == [False] * 11
    next(a for a in window.review_actions if a.text() == "Next change").trigger()
    assert window.checklist.current_position() == 1
    next(a for a in window.review_actions if a.text() == "Toggle change").trigger()
    assert ticks(window)[1] is True


def test_selecting_a_change_follows_in_the_code_and_back(window, sample_file):
    window.open_file(str(sample_file))
    change = window.checklist.changes[4]
    window.checklist.tree.setCurrentItem(window.checklist.tree.topLevelItem(4))
    assert window.code.current_line() == change.original_index
    window.code.go_to_line(window.checklist.changes[7].original_index)
    window.code.lineActivated.emit(window.checklist.changes[7].original_index)
    assert window.checklist.current_position() == 7


def test_sections_start_unticked_then_apply_skip_back(window, sample_file):
    window.open_file(str(sample_file))
    window.apply_step()
    assert window.session.fix_id == "remove_named_sections"
    assert ticks(window) == [False, False, False]
    window.skip_step()
    assert window.session.fix_id == "remove_repeated_p_calls"
    window.back_step()
    assert window.session.fix_id == "remove_named_sections"


def test_finish_and_save_syncs_o_number_and_writes_crlf(window, sample_file, tmp_path):
    window.open_file(str(sample_file))
    while not window.session.finished:
        window.apply_step()
    assert window.panels.currentWidget() is window.final
    assert window.top.currentWidget() is window.diff
    assert window.diff.model_.rowCount() > 0
    assert window.final.name.text() == "fixed_O1234.ptp"
    assert window.act_save.isEnabled()
    out = tmp_path / "O5555 fixed.ptp"
    window.save_to(str(out))
    data = out.read_bytes()
    assert b"\r\nO5555\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    assert b"P8000" not in data


def test_selection_change_mid_review_asks_first(window, sample_file, monkeypatch):
    window.open_file(str(sample_file))
    window.apply_step()
    monkeypatch.setattr(window, "confirm_restart", lambda: False)
    window.steps.list.item(0).setCheckState(Qt.Unchecked)  # untick rotary
    assert window.session.index == 1  # kept the review
    assert "remove_rotary_table" in window.steps.selected()  # tick put back
    monkeypatch.setattr(window, "confirm_restart", lambda: True)
    window.steps.list.item(0).setCheckState(Qt.Unchecked)
    assert window.session.index == 0 and window.session.fix_id == "remove_named_sections"


def test_go_back_to_a_completed_step(window, sample_file, monkeypatch):
    window.open_file(str(sample_file))
    window.apply_step()
    window.apply_step()
    monkeypatch.setattr(window, "ask", lambda title, text: True)
    window.go_to_step(0)
    assert window.session.index == 0


# -- phase 3: the bed map, map-driven steps, material database ------------------

ALL_WITH_RECIPES = ["remove_rotary_table", "remove_named_sections", "change_recipes", "remove_repeated_p_calls",
                    "remove_dwells"]


def start_of(w, name):
    return next(s for s, info in w.map.info.items() if info.name == name)


def test_map_draws_every_toolpath_and_maps_lines(window, sample_file):
    window.open_file(str(sample_file))
    assert [i.name for i in window.map.info.values()] == ["PATH_A", "PATH_B", "PATH_C"]
    a = start_of(window, "PATH_A")
    assert window.map.toolpath_at_line(a + 5) == a
    assert window.map.toolpath_at_line(0) is None  # header, before any toolpath


def test_code_cursor_outlines_its_toolpath(window, sample_file):
    window.open_file(str(sample_file))
    b = start_of(window, "PATH_B")
    window.code.lineActivated.emit(b + 3)
    assert window.map._boxes[b].focused


def test_sections_map_and_list_share_selection(window, sample_file):
    window.open_file(str(sample_file))
    window.apply_step()  # rotary -> sections
    c = start_of(window, "PATH_C")
    window.map.set_selected([c])
    window.map.selectionEdited.emit([c])
    assert [window.checklist.changes[p].label for p in window.checklist.selected_positions()] == ["PATH_C"]
    # Selecting in the list selects on the map.
    window.checklist.tree.setCurrentItem(window.checklist.tree.topLevelItem(0))
    assert window.map.selected() == [start_of(window, "PATH_A")]


def test_double_click_on_map_toggles_a_section_and_restyles(window, sample_file):
    from lmd_fixer.desktop import theme

    window.open_file(str(sample_file))
    window.apply_step()
    b = start_of(window, "PATH_B")
    window.map.toolpathActivated.emit(b)
    assert window.session.is_accepted(b)
    assert window.map._paths[b].pen().color().name() == theme.REMOVAL_GONE
    assert window.map._labels[b].text == "✕"
    window.undo.undo()
    assert not window.session.is_accepted(b)
    assert window.map._paths[b].pen().color().name() == theme.REMOVAL_KEPT


def test_recipe_step_set_undo_apply(qtbot, tmp_path, sample_file, monkeypatch):
    monkeypatch.setenv("LMD_FIXER_RECIPES", str(tmp_path / "recipes.csv"))
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    settings.setValue("selected_fixes", ["change_recipes", "remove_repeated_p_calls"])
    w = MainWindow(settings)
    qtbot.addWidget(w)
    w.open_file(str(sample_file))
    assert w.panels.currentWidget() is w.recipes
    a, c = start_of(w, "PATH_A"), start_of(w, "PATH_C")
    # Select on the map, as a box-drag would.
    w.map.set_selected([a, c])
    w.map.selectionEdited.emit([a, c])
    assert w.recipes.selected_starts() == [a, c]
    w.recipes.new_p.setValue(31)
    w.recipes.set_selected()
    assert w.session.choices.options["recipes"] == {a: 31, c: 31}
    assert w.map._labels[c].text == "P31" and w.map._labels[c].bold
    assert len(w.session.proposal().changes) == 2
    w.undo.undo()
    assert w.session.choices.options["recipes"] == {}
    w.undo.redo()
    w.recipes.select_starts([c])
    w.recipes.reset_selected()
    assert w.session.choices.options["recipes"] == {a: 31}
    w.apply_step()
    assert w.session.result().lines.count("/M98 P0031") == 1


def test_material_database_dialog(qtbot, tmp_path, monkeypatch):
    from lmd_fixer import recipes as R
    from lmd_fixer.desktop.recipe_dialog import RecipeDialog

    path = tmp_path / "recipes.csv"
    monkeypatch.setenv("LMD_FIXER_RECIPES", str(path))
    d = RecipeDialog([20, 21])
    qtbot.addWidget(d)
    assert d.model.rowCount() == 0 and d.missing_p() == [20, 21]
    d.add_missing_rows()
    assert d.model.rowCount() == 2 and d.dirty
    power = d.columns.index("Laser power (W)")
    d.model.item(0, power).setText("1000")
    assert d.save() and not d.dirty
    df = R.load_recipes(path)
    assert list(df["P"]) == [20, 21] and df["Laser power (W)"].iloc[0] == 1000
    # A bad value is reported, not saved.
    d.model.item(1, power).setText("lots")
    assert not d.save()
    assert "must be a number" in d.errors.text()
    d.dirty = False  # skip the unsaved-changes prompt on close


# -- the 3D view (data and picking; the GL widget itself isn't created) -----------

def test_3d_view_builds_deposition_and_travel(window, sample_file):
    window.open_file(str(sample_file))
    v = window.view3d
    assert v.gl is None  # only created when the 3D tab is first shown
    a = start_of(window, "PATH_A")
    segs = v.deposition[a]
    assert segs.shape == (4, 2, 3) and set(segs[..., 2].ravel()) == {0.0}  # the square, at Z0
    assert len(v.travel) > 0 and v.travel[..., 2].max() == 8.0  # retracts to Z8 are travel
    v.z_scale.setCurrentIndex(2)  # Z x5
    assert v._scaled(v.travel)[..., 2].max() == 40.0


def test_pick_segment_nearest_then_closest_to_camera():
    import numpy as np

    from lmd_fixer.desktop.view3d import pick_segment

    # Identity "camera": screen x = (x + 1) * 50, y = (1 - y) * 50, depth = z.
    mvp = np.eye(4)
    segs = np.array([
        [[-0.5, 0, 0.5], [0.5, 0, 0.5]],    # toolpath 1, further away
        [[-0.5, 0, -0.5], [0.5, 0, -0.5]],  # toolpath 2, same place on screen, nearer
        [[-0.5, 0.6, 0], [0.5, 0.6, 0]],    # toolpath 3, higher up the screen
    ])
    owners = np.array([1, 2, 3])
    assert pick_segment(segs, owners, mvp, 100, 100, 50, 50) == 2
    assert pick_segment(segs, owners, mvp, 100, 100, 50, 21) == 3
    assert pick_segment(segs, owners, mvp, 100, 100, 50, 35) is None  # nothing within 8 px


def test_selecting_in_3d_selects_on_the_map_and_in_the_list(window, sample_file):
    window.open_file(str(sample_file))
    window.apply_step()  # sections
    b = start_of(window, "PATH_B")
    window.view3d.selectionEdited.emit([b])
    assert window.map.selected() == [b]
    assert [window.checklist.changes[p].label for p in window.checklist.selected_positions()] == ["PATH_B"]
    # And the other way: the list drives the 3D selection.
    window.checklist.tree.setCurrentItem(window.checklist.tree.topLevelItem(0))
    assert window.view3d.selected() == [start_of(window, "PATH_A")]


def test_moving_to_a_step_with_fewer_changes_keeps_the_selection_valid(window, sample_file):
    """Regression: the rows selected at one step were reported against the
    next step's (shorter) change list while the list was being refilled."""
    window.open_file(str(sample_file))
    tree = window.checklist.tree
    # clear() removes rows from the end, reporting the selection after each
    # removal, so row 5 was still "selected" against the 3-row next step.
    tree.setCurrentItem(tree.topLevelItem(10))
    tree.topLevelItem(5).setSelected(True)
    window.apply_step()  # sections: only 3 rows
    assert window.checklist.selected_positions() == [0]
    assert window.map.selected() == [start_of(window, "PATH_A")]
