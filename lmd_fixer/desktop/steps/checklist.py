"""The generic review: every proposed change listed with a tick box.

Used for every "checklist" fix and for section removal ("sections"), where
each row is a whole toolpath block. Each change starts ticked or not
according to the fix's `default_accept`; every edit goes through the
window's undo stack. The list's selection is shared with the bed map.

A filter box narrows the list (Tick all / Untick all then act on the rows
shown), and in checklist steps the rows can be grouped under a heading per
toolpath, ticked a toolpath at a time. Rows are kept in `_items`, indexed
by the change's position, so the rest of the panel doesn't care whether
they're top-level or under a heading.
"""

from __future__ import annotations

import bisect

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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
TOOLPATH_COLUMN = 2
PART_COLUMN = 3
CHANGE_COLUMN = 4
WHY_COLUMN = 5
OUTSIDE_TOOLPATHS = "Program header / footer"
GROUP_SETTING = "checklist/group"
KEYS = ("J/K move between changes · Space or double-click flips the selected rows · "
        "A/R tick or untick all (the rows shown, when filtered) · Ctrl+F filters · Ctrl+Z undo")
# Where a row keeps its change's position; headings hold None there and
# their children's positions under CHILDREN.
POSITION = Qt.UserRole
CHILDREN = Qt.UserRole + 1


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

    def __init__(self, undo: QUndoStack, settings: QSettings | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.undo = undo
        self.settings = settings
        self.session: ReviewSession | None = None
        self.changes: list[LineChange] = []
        self._items: list[QTreeWidgetItem] = []  # by position
        self._headings: list[QTreeWidgetItem] = []  # when grouped
        self._parts: dict[int, str] = {}
        self._toolpaths: dict[int, str] = {}
        self._sections = False
        self._filling = False

        self.header = StepHeader()
        self.accept_all = QPushButton("Tick all")
        self.reject_all = QPushButton("Untick all")
        for b in (self.accept_all, self.reject_all):
            b.setProperty("flat", True)
        self.count = QLabel()
        self.count.setProperty("role", "dim")
        self.accept_all.clicked.connect(lambda: self.set_all(True))
        self.reject_all.clicked.connect(lambda: self.set_all(False))

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter changes…")
        self.filter.setClearButtonEnabled(True)
        self.filter.setToolTip("Show only the changes whose line, toolpath, part or text contains this (Ctrl+F; Esc clears)")
        self.filter.setMaximumWidth(260)
        self.filter.textChanged.connect(self._filter_changed)
        clear = QShortcut(QKeySequence(Qt.Key_Escape), self.filter, context=Qt.WidgetShortcut)
        clear.activated.connect(self.filter.clear)

        self.group = QCheckBox("Group by toolpath")
        self.group.setToolTip("List the changes under a heading per toolpath, to tick or untick a toolpath at once")
        self.group.setChecked(self._saved_grouping())
        self.group.toggled.connect(self._grouping_toggled)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setToolTip(KEYS)
        self.tree.header().setSectionResizeMode(CHANGE_COLUMN, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.toggle_selected())

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.count)
        bar.addStretch(1)
        bar.addWidget(self.filter)
        bar.addWidget(self.group)
        bar.addWidget(self.accept_all)
        bar.addWidget(self.reject_all)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.header)
        layout.addLayout(bar)
        layout.addWidget(self.tree, 1)

    # -- filling -------------------------------------------------------------

    def load(self, session: ReviewSession, part_of_change: dict[int, str] | None = None,
             toolpath_of_change: dict[int, str] | None = None) -> None:
        """`part_of_change` maps a change's `original_index` to where on the
        bed it is (the Part column is hidden without it);
        `toolpath_of_change` to its toolpath's name, for changes that don't
        carry one in their `label`."""
        self.session = session
        fix = session.fix
        self.changes = session.proposal().changes
        self._parts = part_of_change or {}
        self._toolpaths = toolpath_of_change or {}
        self._sections = fix.review_mode == "sections"
        self.header.show_fix(fix.label, fix.description)
        self.tree.setToolTip(("Ticked sections are removed. " if self._sections else "") + KEYS)
        # Each step starts unfiltered.
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.blockSignals(False)
        # Grouping is for checklist steps: a section row already is a toolpath.
        self.group.setVisible(not self._sections and bool(self.changes))
        self.filter.setVisible(bool(self.changes))
        self._rebuild()
        self.tree.setColumnHidden(PART_COLUMN, not self._parts)
        self.tree.setColumnHidden(WHY_COLUMN, not any(c.reason for c in self.changes))
        self.refresh()
        self._fit_columns()
        if self._items:
            self.tree.setCurrentItem(self._items[0])

    def _grouped(self) -> bool:
        return self.group.isChecked() and not self._sections

    def toolpath_of(self, change: LineChange) -> str:
        return change.label or self._toolpaths.get(change.original_index, "")

    def _rebuild(self) -> None:
        """Recreates the rows, flat or under toolpath headings."""
        grouped = self._grouped()
        code = theme.code_font()
        self._filling = True
        # Silent while refilling: clear() reports the selection as it removes
        # rows, with positions that belong to the previous step's changes.
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items = []
        self._headings = []
        headings: dict[str, QTreeWidgetItem] = {}
        for pos, change in enumerate(self.changes):
            where, what = describe(change)
            # Under a heading the row needn't repeat its toolpath's name.
            toolpath = "" if grouped else self.toolpath_of(change)
            item = QTreeWidgetItem(["", where, toolpath, self._parts.get(change.original_index, ""),
                                    what, change.reason or ""])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            # Set explicitly: a row only shows a tick box once its check
            # state has been set, and refresh() skips rows already in the
            # (default) unticked state, so unticked rows had no box at all.
            item.setCheckState(0, Qt.Unchecked)
            item.setData(0, POSITION, pos)
            item.setFont(CHANGE_COLUMN, code)
            if grouped:
                name = self.toolpath_of(change) or OUTSIDE_TOOLPATHS
                heading = headings.get(name)
                if heading is None:
                    heading = self._heading(name)
                    headings[name] = heading
                    self.tree.addTopLevelItem(heading)
                heading.addChild(item)
                heading.setData(0, CHILDREN, [*heading.data(0, CHILDREN), pos])
            else:
                self.tree.addTopLevelItem(item)
            self._items.append(item)
        self.tree.setRootIsDecorated(grouped)
        if grouped:
            self.tree.expandAll()
        self.tree.blockSignals(False)
        self._filling = False
        self._apply_filter()

    def _fit_columns(self) -> None:
        """Sizes every column but the stretching Change one to its contents;
        grouped rows are indented, so the tick-box column needs re-fitting
        or the indent pushes the boxes out of sight."""
        for col in (0, 1, TOOLPATH_COLUMN, PART_COLUMN, WHY_COLUMN):
            self.tree.resizeColumnToContents(col)

    def _heading(self, name: str) -> QTreeWidgetItem:
        heading = QTreeWidgetItem(["", "", name])
        heading.setFlags(heading.flags() | Qt.ItemIsUserCheckable)
        heading.setCheckState(0, Qt.Unchecked)
        heading.setData(0, POSITION, None)
        heading.setData(0, CHILDREN, [])
        font = heading.font(TOOLPATH_COLUMN)
        font.setBold(True)
        heading.setFont(TOOLPATH_COLUMN, font)
        self._headings.append(heading)
        return heading

    def refresh(self) -> None:
        """Re-reads every tick from the session (after an edit or undo)."""
        if self.session is None or self.session.finished:
            return
        self._filling = True
        accepted = [self.session.is_accepted(c.original_index) for c in self.changes]
        for item, ok in zip(self._items, accepted):
            state = Qt.Checked if ok else Qt.Unchecked
            if item.checkState(0) != state:
                item.setCheckState(0, state)
        for heading in self._headings:
            children = heading.data(0, CHILDREN)
            ticked = sum(accepted[p] for p in children)
            state = Qt.Checked if ticked == len(children) else Qt.Unchecked if not ticked else Qt.PartiallyChecked
            if heading.checkState(0) != state:
                heading.setCheckState(0, state)
            heading.setText(CHANGE_COLUMN, f"{len(children)} change{'s' if len(children) != 1 else ''}"
                                           f" · {ticked} ticked")
        self._filling = False
        self._show_count()
        self.choicesChanged.emit()

    def _show_count(self) -> None:
        if self.session is None or self.session.finished:
            return
        if not self.changes:
            self.count.setText("Nothing to change in this step.")
            return
        ticked = [c for c in self.changes if self.session.is_accepted(c.original_index)]
        lines = sum(c.end_index - c.original_index + 1 for c in ticked)
        noun = "sections to remove" if self._sections else "changes ticked"
        text = f"<b>{len(ticked)}</b> of {len(self.changes)} {noun} · {lines:,} lines affected"
        if self.filtering():
            text += f" · <b>{len(self.shown_positions())}</b> shown"
        self.count.setText(text)

    # -- filtering -------------------------------------------------------------

    def filtering(self) -> bool:
        return bool(self.filter.text().strip())

    def focus_filter(self) -> None:
        self.filter.setFocus()
        self.filter.selectAll()

    def shown_positions(self) -> list[int]:
        return [p for p, item in enumerate(self._items) if not item.isHidden()]

    def _filter_changed(self) -> None:
        self._apply_filter()
        self._show_count()

    def _apply_filter(self) -> None:
        text = self.filter.text().strip().lower()
        for change, item in zip(self.changes, self._items):
            haystack = " ".join([self.toolpath_of(change), *(item.text(c) for c in range(1, len(COLUMNS)))]).lower()
            item.setHidden(bool(text) and text not in haystack)
        for heading in self._headings:
            heading.setHidden(all(self._items[p].isHidden() for p in heading.data(0, CHILDREN)))
        sections = self._sections
        which = " shown" if text else " all"
        self.accept_all.setText(("Remove" if sections else "Tick") + which)
        self.reject_all.setText(("Keep" if sections else "Untick") + which)
        what = "shown " if text else ""
        self.accept_all.setToolTip(f"{'Remove' if sections else 'Tick'} every {what}change (A)")
        self.reject_all.setToolTip(f"{'Keep' if sections else 'Untick'} every {what}change (R)")

    # -- grouping ----------------------------------------------------------------

    def _saved_grouping(self) -> bool:
        if self.settings is None:
            return False
        return str(self.settings.value(GROUP_SETTING, "false")).lower() == "true"

    def _grouping_toggled(self, on: bool) -> None:
        if self.settings is not None:
            self.settings.setValue(GROUP_SETTING, "true" if on else "false")
        if self.session is None or self.session.finished:
            return
        selected = self.selected_positions()
        current = self.current_position()
        self._rebuild()
        self.refresh()
        self._fit_columns()
        if current is not None:
            self.tree.setCurrentItem(self._items[current])
        self.select_positions(selected)

    # -- edits (all undoable) ---------------------------------------------------

    def _push(self, updates: dict[int, bool], text: str) -> None:
        self.undo.push(SetAcceptedCommand(self.session, updates, self.refresh, text))

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 0:
            return
        pos = item.data(0, POSITION)
        if pos is None:  # a toolpath heading: tick or untick all of it
            accepted = item.checkState(0) != Qt.Unchecked
            indices = [self.changes[p].original_index for p in item.data(0, CHILDREN)]
            self._push({i: accepted for i in indices},
                       f"{'Tick' if accepted else 'Untick'} {item.text(TOOLPATH_COLUMN)}")
            return
        change = self.changes[pos]
        accepted = item.checkState(0) == Qt.Checked
        self._push({change.original_index: accepted}, f"{'Tick' if accepted else 'Untick'} line {change.original_index + 1}")

    def toggle_selected(self) -> None:
        """Flips the selected rows: all ticked if the first one isn't, else
        all unticked."""
        positions = self.selected_positions()
        if not positions and self.current_position() is not None:
            positions = [self.current_position()]
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
        """Ticks or unticks every change, or every one shown while filtered."""
        positions = self.shown_positions() if self.filtering() else range(len(self.changes))
        updates = {self.changes[p].original_index: accepted for p in positions}
        if not updates:
            return
        verb = "Tick" if accepted else "Untick"
        self._push(updates, f"{verb} {len(updates)} shown" if self.filtering() else f"{verb} all")

    # -- selection ---------------------------------------------------------------

    def current_position(self) -> int | None:
        item = self.tree.currentItem()
        return None if item is None else item.data(0, POSITION)

    def selected_positions(self) -> list[int]:
        """The selected changes; a selected heading counts as all of its own."""
        out: set[int] = set()
        for item in self.tree.selectedItems():
            pos = item.data(0, POSITION)
            if pos is None:
                out.update(item.data(0, CHILDREN) or [])
            elif 0 <= pos < len(self.changes):
                out.add(pos)
        return sorted(out)

    def select_positions(self, positions, emit: bool = False) -> None:
        """Selects rows chosen elsewhere (the map, the code view)."""
        positions = [p for p in positions if 0 <= p < len(self._items)]
        self.tree.blockSignals(not emit)
        self.tree.clearSelection()
        for p in positions:
            self._items[p].setSelected(True)
        if positions:
            item = self._items[positions[0]]
            self.tree.setCurrentItem(item, 0, self.tree.selectionModel().SelectionFlag.NoUpdate)
            self.tree.scrollToItem(item)
        self.tree.blockSignals(False)

    def move(self, step: int) -> None:
        """Moves `step` changes down (or up, if negative) among those shown,
        skipping headings and rows hidden by the filter."""
        shown = self.shown_positions()
        if not shown:
            return
        pos = self.current_position()
        if pos is None:
            i = 0 if step > 0 else len(shown) - 1
        elif step > 0:
            i = bisect.bisect_right(shown, pos) - 1 + step  # from pos, or the last shown row before it
        else:
            i = bisect.bisect_left(shown, pos) + step
        self.tree.setCurrentItem(self._items[shown[max(0, min(len(shown) - 1, i))]])

    def _selection_changed(self) -> None:
        self.selectionMoved.emit(self.selected_positions())
