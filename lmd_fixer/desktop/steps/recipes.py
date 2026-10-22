"""The P recipe step: select toolpaths (here or on the map), enter a P, Set.

The fix is option-driven: the session holds `{"recipes": {toolpath start:
new P}}` and the proposal follows from it. Set/Reset are undoable.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lmd_fixer import recipes as recipe_db
from lmd_fixer.desktop import theme
from lmd_fixer.desktop.steps.common import StepHeader
from lmd_fixer.fixes.change_recipes import ToolpathRecipeCall, toolpath_recipe_calls
from lmd_fixer.review import ReviewSession

COLUMNS = ["Toolpath", "Part", "Current P", "New P", "Settings (from the material database)"]


class SetRecipesCommand(QUndoCommand):
    def __init__(self, session: ReviewSession, after: dict[int, int], refresh: Callable[[], None], text: str) -> None:
        super().__init__(text)
        self.session = session
        self.before = dict(session.choices.options.get("recipes", {}))
        self.after = after
        self.refresh = refresh

    def _set(self, recipes: dict[int, int]) -> None:
        self.session.set_options(recipes=dict(recipes))
        self.refresh()

    def redo(self) -> None:
        self._set(self.after)

    def undo(self) -> None:
        self._set(self.before)


def p_text(call: ToolpathRecipeCall, p: int) -> str:
    return f"P{p:0{call.p_width}d}"


class RecipesPanel(QWidget):
    selectionMoved = Signal(list)  # selected toolpath start lines
    choicesChanged = Signal()

    def __init__(self, undo: QUndoStack, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.undo = undo
        self.session: ReviewSession | None = None
        self.calls: list[ToolpathRecipeCall] = []
        self.recorded: dict[int, str] = {}

        self.header = StepHeader()
        self.db_note = QLabel()
        self.db_note.setProperty("role", "faint")
        self.db_note.setWordWrap(True)

        self.new_p = QSpinBox()
        self.new_p.setRange(1, 9999)
        self.new_p.setPrefix("P")
        self.new_p.setValue(20)
        self.new_p.setToolTip("The recipe number the selected toolpaths' M98 call will select")
        self.new_p.lineEdit().returnPressed.connect(self.set_selected)
        self.set_button = QPushButton("Set for selected")
        self.set_button.setProperty("primary", True)
        self.set_button.clicked.connect(self.set_selected)
        self.reset_button = QPushButton("Reset selected")
        self.reset_button.setProperty("flat", True)
        self.reset_button.setToolTip("Back to each selected toolpath's original P")
        self.reset_button.clicked.connect(self.reset_selected)
        self.count = QLabel()
        self.count.setProperty("role", "dim")

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setToolTip("Select toolpaths here or on the map (drag a box for many), type the P, "
                             "press Enter · Ctrl+Z undo")
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.header().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tree.itemSelectionChanged.connect(self._selection_changed)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.count)
        bar.addStretch(1)
        bar.addWidget(self.reset_button)
        bar.addSpacing(8)
        bar.addWidget(QLabel("New P"))
        bar.addWidget(self.new_p)
        bar.addWidget(self.set_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.header)
        layout.addWidget(self.db_note)
        layout.addLayout(bar)
        layout.addWidget(self.tree, 1)

    # -- filling -------------------------------------------------------------

    def load(self, session: ReviewSession, part_of_toolpath: dict[int, str] | None = None) -> None:
        self.session = session
        fix = session.fix
        self.header.show_fix(fix.label, fix.description)
        self.calls = toolpath_recipe_calls(session.current)
        self.reload_database()
        parts = part_of_toolpath or {}
        self.tree.blockSignals(True)  # see ChecklistPanel.load
        self.tree.clear()
        for call in self.calls:
            item = QTreeWidgetItem([call.name, parts.get(call.start_index, ""),
                                    p_text(call, call.p_value) if call.p_value is not None else "no M98 call",
                                    "", ""])
            item.setData(0, Qt.UserRole, call.start_index)
            if call.call_index is None:
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                item.setForeground(0, QColor(theme.DIM))
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        self.refresh()
        for col in range(4):
            self.tree.resizeColumnToContents(col)

    def reload_database(self) -> None:
        """Re-reads what each P is recorded as (after the database is edited)."""
        try:
            self.recorded = recipe_db.summaries_by_p(recipe_db.load_recipes())
            self.db_note.setText(
                "Settings come from the material database. Check the control panel matches before running."
            )
        except ValueError as exc:
            self.recorded = {}
            self.db_note.setText(f"Material database not read: {exc}")

    def recorded_as(self, p: int | None) -> str:
        if p is None:
            return ""
        if p not in self.recorded:
            return "not in database"
        return self.recorded[p] or "in database, no details"

    def chosen(self) -> dict[int, int]:
        return dict(self.session.choices.options.get("recipes", {})) if self.session else {}

    def final_p(self, call: ToolpathRecipeCall) -> int | None:
        return self.chosen().get(call.start_index, call.p_value)

    def refresh(self) -> None:
        if self.session is None or self.session.finished:
            return
        chosen = self.chosen()
        bold = QFont(self.tree.font())
        bold.setBold(True)
        for i, call in enumerate(self.calls):
            item = self.tree.topLevelItem(i)
            new = chosen.get(call.start_index)
            item.setText(3, p_text(call, new) if new is not None else "")
            item.setFont(3, bold)
            item.setForeground(3, QColor(theme.RECIPE_CHANGED))
            item.setText(4, self.recorded_as(self.final_p(call)))
        self.tree.resizeColumnToContents(3)
        changed = sum(1 for c in self.calls if c.start_index in chosen)
        self.count.setText(f"<b>{changed}</b> of {len(self.calls)} toolpaths get a new P")
        self.choicesChanged.emit()

    # -- edits -------------------------------------------------------------------

    def set_selected(self) -> None:
        self.new_p.interpretText()
        self._apply(self.selected_starts(), self.new_p.value())

    def reset_selected(self) -> None:
        self._apply(self.selected_starts(), None)

    def set_for(self, starts, p: int | None) -> None:
        """Gives the toolpaths at `starts` P `p` (None: back to their own)."""
        self._apply(starts, p)

    def _apply(self, starts, p: int | None) -> None:
        if self.session is None:
            return
        current = {c.start_index: c.p_value for c in self.calls if c.call_index is not None}
        starts = [s for s in starts if s in current]
        if not starts:
            return
        after = self.chosen()
        for s in starts:
            if p is None or p == current[s]:
                after.pop(s, None)
            else:
                after[s] = p
        if after == self.chosen():
            return
        what = f"{len(starts)} toolpath(s)"
        text = f"Reset {what}" if p is None else f"Set {what} to P{p:04d}"
        self.undo.push(SetRecipesCommand(self.session, after, self.refresh, text))

    # -- selection ---------------------------------------------------------------

    def selected_starts(self) -> list[int]:
        return sorted(item.data(0, Qt.UserRole) for item in self.tree.selectedItems())

    def select_starts(self, starts) -> None:
        wanted = set(starts)
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        first = None
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, Qt.UserRole) in wanted:
                item.setSelected(True)
                first = first or item
        if first is not None:
            self.tree.scrollToItem(first)
        self.tree.blockSignals(False)

    def _selection_changed(self) -> None:
        self.selectionMoved.emit(self.selected_starts())
