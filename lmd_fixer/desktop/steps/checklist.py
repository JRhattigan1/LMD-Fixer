"""The generic review: every proposed change listed with a tick box.

Used for every "checklist" fix and for section removal ("sections"), where
each row is a whole toolpath block. Each change starts ticked or not
according to the fix's `default_accept`; every edit goes through the
window's undo stack. The list's selection is shared with the bed map.
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

COLUMNS = ["", "Line", "Toolpath", "Part", "Change", "Why"]
CHANGE_COLUMN = 4


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
    """Lists the current step's changes.

    `selectionMoved` fires with the selected rows' positions when the user
    moves the selection (so the code view and map can follow);
    `choicesChanged` after any accept/reject edit, including undo.
    """

    selectionMoved = Signal(list)
    choicesChanged = Signal()

    def __init__(self, undo: QUndoStack, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.undo = undo
        self.session: ReviewSession | None = None
        self.changes: list[LineChange] = []
        self._filling = False

        self.header = StepHeader()
        self.summary = QLabel()
        self.accept_all = QPushButton("Tick all")
        self.reject_all = QPushButton("Untick all")
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
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.header().setSectionResizeMode(CHANGE_COLUMN, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.setFont(theme.code_font())
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.toggle_selected())

        bar = QHBoxLayout()
        bar.addWidget(self.accept_all)
        bar.addWidget(self.reject_all)
        bar.addSpacing(12)
        bar.addWidget(self.count)
        bar.addStretch(1)
        self.hint = QLabel()
        self.hint.setProperty("role", "dim")
        bar.addWidget(self.hint)

        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(self.summary)
        layout.addLayout(bar)
        layout.addWidget(self.tree, 1)

    # -- filling -------------------------------------------------------------

    def load(self, session: ReviewSession, part_of_change: dict[int, str] | None = None) -> None:
        """`part_of_change` maps a change's `original_index` to where on the
        bed it is; the Part column is hidden without it."""
        self.session = session
        fix = session.fix
        result = session.proposal()
        self.changes = result.changes
        self.header.show_fix(fix.label, fix.description)
        self.summary.setText(result.summary)
        sections = fix.review_mode == "sections"
        self.accept_all.setText("Remove all" if sections else "Tick all")
        self.reject_all.setText("Keep all" if sections else "Untick all")
        self.hint.setText(
            ("Ticked = removed. " if sections else "")
            + "J/K move · Space or double-click toggles the selected · A/R all · Ctrl+Z undo"
        )
        parts = part_of_change or {}
        self._filling = True
        self.tree.clear()
        for pos, change in enumerate(self.changes):
            where, what = describe(change)
            item = QTreeWidgetItem(["", where, change.label or "", parts.get(change.original_index, ""),
                                    what, change.reason or ""])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setData(0, Qt.UserRole, pos)
            self.tree.addTopLevelItem(item)
        self._filling = False
        self.tree.setColumnHidden(3, not parts)
        self.refresh()
        for col in (0, 1, 2, 3, 5):
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
        removed_lines = sum(c.end_index - c.original_index + 1 for c in self.changes
                            if self.session.is_accepted(c.original_index))
        noun = "section(s) to remove" if self.session.fix.review_mode == "sections" else "change(s) ticked"
        self.count.setText(f"{n} of {len(self.changes)} {noun} · {removed_lines:,} line(s) affected")
        self.choicesChanged.emit()

    # -- edits (all undoable) ---------------------------------------------------

    def _push(self, updates: dict[int, bool], text: str) -> None:
        self.undo.push(SetAcceptedCommand(self.session, updates, self.refresh, text))

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 0:
            return
        change = self.changes[item.data(0, Qt.UserRole)]
        accepted = item.checkState(0) == Qt.Checked
        self._push({change.original_index: accepted}, f"{'Tick' if accepted else 'Untick'} line {change.original_index + 1}")

    def toggle_selected(self) -> None:
        """Flips the selected rows: all ticked if the first one isn't, else
        all unticked."""
        positions = self.selected_positions() or ([self.current_position()] if self.current_position() is not None else [])
        if not positions:
            return
        indices = [self.changes[p].original_index for p in positions]
        accepted = not self.session.is_accepted(indices[0])
        what = f"line {indices[0] + 1}" if len(indices) == 1 else f"{len(indices)} changes"
        self._push({i: accepted for i in indices}, f"{'Tick' if accepted else 'Untick'} {what}")

    def set_indices(self, indices, accepted: bool) -> None:
        """Ticks or unticks the changes starting at `indices`."""
        known = {c.original_index for c in self.changes}
        updates = {i: accepted for i in indices if i in known}
        if updates:
            self._push(updates, f"{'Tick' if accepted else 'Untick'} {len(updates)} change(s)")

    def set_all(self, accepted: bool) -> None:
        if not self.changes:
            return
        self._push({c.original_index: accepted for c in self.changes}, "Tick all" if accepted else "Untick all")

    # -- selection ---------------------------------------------------------------

    def current_position(self) -> int | None:
        item = self.tree.currentItem()
        return None if item is None else item.data(0, Qt.UserRole)

    def selected_positions(self) -> list[int]:
        return sorted(item.data(0, Qt.UserRole) for item in self.tree.selectedItems())

    def select_positions(self, positions, emit: bool = False) -> None:
        """Selects rows chosen elsewhere (the map, the code view)."""
        positions = [p for p in positions if 0 <= p < len(self.changes)]
        self.tree.blockSignals(not emit)
        self.tree.clearSelection()
        for p in positions:
            self.tree.topLevelItem(p).setSelected(True)
        if positions:
            item = self.tree.topLevelItem(positions[0])
            self.tree.setCurrentItem(item, 0, self.tree.selectionModel().SelectionFlag.NoUpdate)
            self.tree.scrollToItem(item)
        self.tree.blockSignals(False)

    def move(self, step: int) -> None:
        if not self.changes:
            return
        pos = self.current_position()
        target = 0 if pos is None else max(0, min(len(self.changes) - 1, pos + step))
        self.tree.setCurrentItem(self.tree.topLevelItem(target))

    def _selection_changed(self) -> None:
        self.selectionMoved.emit(self.selected_positions())
