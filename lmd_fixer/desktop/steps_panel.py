"""The left-hand list: which fixes run, in order, and where the review is."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from lmd_fixer.desktop import theme

STATUS_MARK = {"done": "✓", "skipped": "↷", "current": "▶", "pending": "○", "off": " "}


class StepsPanel(QWidget):
    """One row per offered fix, in run order. The tick box chooses whether
    the fix runs; the mark shows its status in the current review.

    `selectionEdited` fires with the newly ticked fix ids when the user
    changes a tick; `stepRequested` with a step number when they
    double-click a completed step to go back to it.
    """

    selectionEdited = Signal(tuple)
    stepRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.itemChanged.connect(self._item_changed)
        self.list.itemDoubleClicked.connect(self._double_clicked)
        self.note = QLabel("Fixes run in this order, whatever order they're ticked in. "
                           "Double-click a completed step to go back to it.")
        self.note.setWordWrap(True)
        self.note.setProperty("role", "dim")
        layout = QVBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.note)
        self._filling = False
        self._ids: list[str] = []
        self._step_of: dict[str, int] = {}

    def set_fixes(self, fixes: list[tuple[str, str, str]], selected: tuple[str, ...]) -> None:
        """`fixes` is (id, label, description) in run order."""
        self._filling = True
        self.list.clear()
        self._ids = [fid for fid, _, _ in fixes]
        for fid, label, description in fixes:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, fid)
            item.setToolTip(description)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if fid in selected else Qt.Unchecked)
            self.list.addItem(item)
        self._filling = False

    def selected(self) -> tuple[str, ...]:
        return tuple(
            self.list.item(i).data(Qt.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.Checked
        )

    def set_selected(self, selected: tuple[str, ...]) -> None:
        self._filling = True
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in selected else Qt.Unchecked)
        self._filling = False

    def show_status(self, fix_ids: tuple[str, ...], status_of: dict[str, str]) -> None:
        """Marks each row with its step's status ("off" for fixes not running)."""
        self._step_of = {fid: n for n, fid in enumerate(fix_ids)}
        self._filling = True
        for i in range(self.list.count()):
            item = self.list.item(i)
            fid = item.data(Qt.UserRole)
            status = status_of.get(fid, "off")
            label = item.text().split("  ", 1)[-1]
            item.setText(f"{STATUS_MARK[status]}  {label}")
            font = item.font()
            font.setBold(status == "current")
            item.setFont(font)
            item.setForeground(QColor(theme.TEXT if status in ("current", "pending") else theme.DIM))
        self._filling = False

    def _item_changed(self, item: QListWidgetItem) -> None:
        if not self._filling:
            self.selectionEdited.emit(self.selected())

    def _double_clicked(self, item: QListWidgetItem) -> None:
        step = self._step_of.get(item.data(Qt.UserRole))
        if step is not None:
            self.stepRequested.emit(step)
