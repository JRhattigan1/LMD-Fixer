"""The end of the review: original against result, and saving the file."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QProcess,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QFrame,
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

CHANGED_TAGS = ("delete", "insert", "replace")


def diff_background(tag: str, column: int) -> QColor | None:
    """A diff cell's background, from the current theme (read at draw time,
    so a theme switch recolours the diff)."""
    if tag == "gap":
        return theme.DIFF_GAP_BG
    if column < 2:
        return theme.DIFF_DELETE_BG if tag in ("delete", "replace") else None
    return theme.DIFF_INSERT_BG if tag in ("insert", "replace") else None


def change_runs(rows: list[DiffRow]) -> list[tuple[int, int]]:
    """(first, last) row of each run of consecutive changed rows."""
    runs: list[tuple[int, int]] = []
    start = None
    for r, row in enumerate(rows):
        if row.tag in CHANGED_TAGS:
            if start is None:
                start = r
        elif start is not None:
            runs.append((start, r - 1))
            start = None
    if start is not None:
        runs.append((start, len(rows) - 1))
    return runs


def reveal_in_explorer(path: str) -> None:
    """Opens Explorer on `path`'s folder with the file selected."""
    QProcess.startDetached("explorer", [f"/select,{Path(path).resolve()}"])


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
            return diff_background(row.tag, col)
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


class DiffPanel(QWidget):
    """The diff, with a bar to step between changes and to show only them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = DiffView()
        self.runs: list[tuple[int, int]] = []
        self.current_run: int | None = None
        self.prev_button = QPushButton("◀ Previous change")
        self.next_button = QPushButton("Next change ▶")
        for b in (self.prev_button, self.next_button):
            b.setProperty("flat", True)
        self.prev_button.setToolTip("Previous change (Shift+N)")
        self.next_button.setToolTip("Next change (N)")
        self.prev_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self.position = QLabel()
        self.position.setProperty("role", "dim")
        self.changes_only = QCheckBox("Changes only")
        self.changes_only.setToolTip("Hide the unchanged lines around each change")
        self.changes_only.toggled.connect(self._apply_filter)
        for keys, step in (("N", 1), ("Shift+N", -1)):
            shortcut = QShortcut(QKeySequence(keys), self, context=Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda s=step: self.step(s))

        bar = QHBoxLayout()
        bar.setContentsMargins(10, 4, 10, 4)
        bar.addWidget(self.prev_button)
        bar.addWidget(self.next_button)
        bar.addWidget(self.position)
        bar.addStretch(1)
        bar.addWidget(self.changes_only)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)

    def set_rows(self, rows: list[DiffRow]) -> None:
        self.view.set_rows(rows)
        self.runs = change_runs(rows)
        self.current_run = None
        self._apply_filter()
        self._show_position()

    def step(self, direction: int) -> None:
        """Moves to the next (1) or previous (-1) change and selects it."""
        if not self.runs:
            return
        if self.current_run is None:
            target = 0 if direction > 0 else len(self.runs) - 1
        else:
            target = max(0, min(len(self.runs) - 1, self.current_run + direction))
        self.go_to(target)

    def go_to(self, run: int) -> None:
        self.current_run = run
        first, last = self.runs[run]
        model = self.view.model()
        selection = QItemSelection(model.index(first, 0), model.index(last, model.columnCount() - 1))
        self.view.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect)
        self.view.selectionModel().setCurrentIndex(model.index(first, 0), QItemSelectionModel.NoUpdate)
        self.view.scrollTo(model.index(first, 0), QAbstractItemView.PositionAtCenter)
        self._show_position()

    def _apply_filter(self) -> None:
        only = self.changes_only.isChecked()
        for r, row in enumerate(self.view.model_.rows):
            self.view.setRowHidden(r, only and row.tag not in CHANGED_TAGS)

    def _show_position(self) -> None:
        n = len(self.runs)
        if not n:
            self.position.setText("No changes")
        elif self.current_run is None:
            self.position.setText(f"{n} change{'s' if n != 1 else ''}")
        else:
            self.position.setText(f"Change {self.current_run + 1} of {n}")
        for b in (self.prev_button, self.next_button):
            b.setEnabled(bool(n))

    def refresh_theme(self) -> None:
        self.view.viewport().update()


class FinalPanel(QWidget):
    """What was done, the output name with its notes, and Save; once saved,
    where it went, with Open folder and Fix another file."""

    nameEdited = Signal(str)
    saveRequested = Signal()
    anotherFileRequested = Signal()

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

        # Shown once saved.
        self.saved_path = ""
        self.saved = QFrame()
        self.saved_label = QLabel()
        self.saved_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.open_folder = QPushButton("Open folder")
        self.open_folder.setToolTip("Show the saved file in Explorer")
        self.open_folder.clicked.connect(lambda: reveal_in_explorer(self.saved_path))
        self.another = QPushButton("Fix another file…")
        self.another.setToolTip("Open the next program, starting in this file's folder")
        self.another.clicked.connect(self.anotherFileRequested)
        saved_layout = QHBoxLayout(self.saved)
        saved_layout.setContentsMargins(0, 0, 0, 0)
        saved_layout.addWidget(self.saved_label, 1)
        saved_layout.addWidget(self.open_folder)
        saved_layout.addWidget(self.another)
        self.saved.hide()

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
        layout.addWidget(self.saved)
        layout.addStretch(1)

        self._counts = (0, 0)
        self._notes: list[tuple[str, str]] = []

    def show_result(self, n_original: int, n_final: int, summaries: list[str], name: str) -> None:
        self._counts = (n_original, n_final)
        self._render_stats()
        self.done.clear()
        self.done.addItems(summaries or ["No fixes were run."])
        self.name.setText(name)
        self.saved_path = ""
        self.saved.hide()

    def show_notes(self, notes: list[tuple[str, str]]) -> None:
        self._notes = list(notes)
        self.notes.setText("<br>".join(
            f"<span style='color:{theme.WARNING if level == 'warning' else theme.DIM}'>"
            f"{'⚠ ' if level == 'warning' else ''}{html.escape(text)}</span>"
            for level, text in self._notes
        ))

    def show_saved(self, path: str) -> None:
        self.saved_path = path
        self.saved_label.setText(f"<span style='color:{theme.SUCCESS}'>✓</span> Saved to "
                                 f"<b>{html.escape(str(Path(path)))}</b>")
        self.saved.show()

    def refresh_theme(self) -> None:
        """Re-renders the text that bakes in theme colours."""
        self._render_stats()
        self.show_notes(self._notes)
        if self.saved_path:
            self.show_saved(self.saved_path)

    def _render_stats(self) -> None:
        n_original, n_final = self._counts
        removed = n_original - n_final
        pct = f"{removed / n_original * 100:.1f}%" if n_original else "0%"
        self.stats.setText(
            f"<b>{n_original:,}</b> original lines → <b>{n_final:,}</b> final "
            f"(<span style='color:{theme.ACCENT}'>−{removed:,}, {pct}</span>)"
        )
