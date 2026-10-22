"""The generic review: every proposed change listed with a tick box.

Used for every "checklist" fix, and for "sections" until that step gets its
map. Each change starts ticked or not according to the fix's
`default_accept`; every toggle goes through the window's undo stack.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lmd_fixer.desktop import theme
from lmd_fixer.desktop.steps.common import SetAcceptedCommand, StepHeader
from lmd_fixer.fixes import LineChange
from lmd_fixer.review import ReviewSession

COLUMNS = ["", "Line", "Toolpath", "Change", "Why"]


def describe(change: LineChange) -> tuple[str, str]:
    """(line label, what happens) for one change."""
    n = change.end_index - change.original_index + 1
    where = f"{change.original_index + 1}" if n == 1 else f"{change.original_index + 1}–{change.end_index + 1}"
    text = change.original_text.strip()
    if change.kind == "removed":
        what = f"remove  {text}" + (f"  + {n - 1} more line(s)" if n > 1 else "")
    else:
        new = (change.new_text or "").split("\n")
        after = new[0].strip() + (f"  … ({len(new)} lines)" if len(new) > 1 else "")
        what = f"{text}  →  {after}"
    return where, what


class ChecklistPanel(QWidget):
    """Lists the current step's changes. `changeSelected` fires with the
    change's position when the selection moves, so the code view can follow;
    `choicesChanged` after any accept/reject edit."""

    changeSelected = Signal(int)
    choicesChanged = Signal()

    def __init__(self, undo: QUndoStack, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.undo = undo
        self.session: ReviewSession | None = None
        self.changes: list[LineChange] = []
        self._filling = False

        self.header = StepHeader()
        self.summary = QLabel()
        self.accept_all = QPushButton("Accept all")
        self.reject_all = QPushButton("Reject all")
        self.count = QLabel()
        self.count.setProperty("role", "dim")
        self.accept_all.clicked.connect(lambda: self.set_all(True))
        self.reject_all.clicked.connect(lambda: self.set_all(False))
        self.accept_all.setToolTip("Tick every change (A)")
        self.reject_all.setToolTip("Untick every change (R)")

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.setFont(theme.code_font())
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(self._current_changed)

        bar = QHBoxLayout()
        bar.addWidget(self.accept_all)
        bar.addWidget(self.reject_all)
        bar.addSpacing(12)
        bar.addWidget(self.count)
        bar.addStretch(1)
        hint = QLabel("J/K move · Space toggles · A/R accept/reject all · Ctrl+Z undo")
        hint.setProperty("role", "dim")
        bar.addWidget(hint)

        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(self.summary)
        layout.addLayout(bar)
        layout.addWidget(self.tree, 1)

    # -- filling -------------------------------------------------------------

    def load(self, session: ReviewSession) -> None:
        self.session = session
        fix = session.fix
        result = session.proposal()
        self.changes = result.changes
        self.header.show_fix(fix.label, fix.description)
        self.summary.setText(result.summary)
        self._filling = True
        self.tree.clear()
        for pos, change in enumerate(self.changes):
            where, what = describe(change)
            item = QTreeWidgetItem(["", where, change.label or "", what, change.reason or ""])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setData(0, Qt.UserRole, pos)
            self.tree.addTopLevelItem(item)
        self._filling = False
        self.refresh()
        for col in (0, 1, 2, 4):
            self.tree.resizeColumnToContents(col)
        if self.changes:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def refresh(self) -> None:
        """Re-reads every tick from the session (after an edit or undo)."""
        if self.session is None or self.session.finished:
            return
        self._filling = True
        for pos, change in enumerate(self.changes):
            item = self.tree.topLevelItem(pos)
            state = Qt.Checked if self.session.is_accepted(change.original_index) else Qt.Unchecked
            if item.checkState(0) != state:
                item.setCheckState(0, state)
        self._filling = False
        n = len(self.session.accepted_indices()) if self.changes else 0
        self.count.setText(f"{n} of {len(self.changes)} selected")
        self.choicesChanged.emit()

    # -- edits (all undoable) ---------------------------------------------------

    def _push(self, updates: dict[int, bool], text: str) -> None:
        self.undo.push(SetAcceptedCommand(self.session, updates, self.refresh, text))

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 0:
            return
        change = self.changes[item.data(0, Qt.UserRole)]
        accepted = item.checkState(0) == Qt.Checked
        self._push({change.original_index: accepted}, f"{'Accept' if accepted else 'Reject'} line {change.original_index + 1}")

    def toggle_current(self) -> None:
        pos = self.current_position()
        if pos is None:
            return
        change = self.changes[pos]
        accepted = not self.session.is_accepted(change.original_index)
        self._push({change.original_index: accepted}, f"{'Accept' if accepted else 'Reject'} line {change.original_index + 1}")

    def set_all(self, accepted: bool) -> None:
        if not self.changes:
            return
        self._push({c.original_index: accepted for c in self.changes}, "Accept all" if accepted else "Reject all")

    # -- selection ---------------------------------------------------------------

    def current_position(self) -> int | None:
        item = self.tree.currentItem()
        return None if item is None else item.data(0, Qt.UserRole)

    def select_position(self, pos: int) -> None:
        item = self.tree.topLevelItem(pos)
        if item is not None and item is not self.tree.currentItem():
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)

    def move(self, step: int) -> None:
        pos = self.current_position()
        target = 0 if pos is None else max(0, min(len(self.changes) - 1, pos + step))
        if self.changes:
            self.select_position(target)

    def _current_changed(self, current: QTreeWidgetItem | None, _previous) -> None:
        if current is not None:
            self.changeSelected.emit(current.data(0, Qt.UserRole))
