"""The end of the review: original against result, and saving the file."""

from __future__ import annotations

import html

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from lmd_fixer.desktop import theme
from lmd_fixer.export import DiffRow

_LEFT_BG = {"delete": theme.DIFF_DELETE_BG, "replace": theme.DIFF_DELETE_BG}
_RIGHT_BG = {"insert": theme.DIFF_INSERT_BG, "replace": theme.DIFF_INSERT_BG}


class DiffModel(QAbstractTableModel):
    HEADERS = ["#", "Original", "#", "Final"]

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[DiffRow] = []

    def set_rows(self, rows: list[DiffRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 4

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        row = self.rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if row.tag == "gap":
                return f"⋯ {row.left} ⋯" if col == 1 else ""
            return [row.left_no or "", row.left, row.right_no or "", row.right][col]
        if role == Qt.BackgroundRole:
            if row.tag == "gap":
                return theme.DIFF_GAP_BG
            return (_LEFT_BG if col < 2 else _RIGHT_BG).get(row.tag)
        if role == Qt.ForegroundRole and (col in (0, 2) or row.tag == "gap"):
            return QColor(theme.DIM)
        if role == Qt.TextAlignmentRole and col in (0, 2):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class DiffView(QTableView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model_ = DiffModel()
        self.setModel(self.model_)
        self.setFont(theme.code_font())
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 4)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QTableView.SelectRows)
        h = self.horizontalHeader()
        for col in (0, 2):
            h.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        for col in (1, 3):
            h.setSectionResizeMode(col, QHeaderView.Stretch)

    def set_rows(self, rows: list[DiffRow]) -> None:
        self.model_.set_rows(rows)
        self.clearSpans()
        for r, row in enumerate(rows):
            if row.tag == "gap":
                self.setSpan(r, 1, 1, 3)


class FinalPanel(QWidget):
    """What was done, the output name with its notes, and Save."""

    nameEdited = Signal(str)
    saveRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title = QLabel("Review complete")
        title.setProperty("role", "title")
        self.stats = QLabel()
        self.done = QListWidget()
        self.done.setMaximumHeight(130)
        self.name = QLineEdit()
        self.name.textEdited.connect(self.nameEdited)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.save = QPushButton("Save fixed file…")
        self.save.setProperty("primary", True)
        self.save.clicked.connect(self.saveRequested)

        form = QFormLayout()
        form.addRow("Output file name", self.name)
        buttons = QHBoxLayout()
        buttons.addWidget(self.save)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.stats)
        layout.addWidget(self.done)
        layout.addLayout(form)
        layout.addWidget(self.notes)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def show_result(self, n_original: int, n_final: int, summaries: list[str], name: str) -> None:
        removed = n_original - n_final
        pct = f"{removed / n_original * 100:.1f}%" if n_original else "0%"
        self.stats.setText(
            f"<b>{n_original:,}</b> original lines → <b>{n_final:,}</b> final "
            f"(<span style='color:{theme.ACCENT}'>−{removed:,}, {pct}</span>)"
        )
        self.done.clear()
        self.done.addItems(summaries or ["No fixes were run."])
        self.name.setText(name)

    def show_notes(self, notes: list[tuple[str, str]]) -> None:
        self.notes.setText("<br>".join(
            f"<span style='color:{'#e3b341' if level == 'warning' else theme.DIM}'>"
            f"{'⚠ ' if level == 'warning' else ''}{html.escape(text)}</span>"
            for level, text in notes
        ))
