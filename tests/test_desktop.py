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
    window.checklist.toggle_current()
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
    window.checklist.select_position(4)
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
