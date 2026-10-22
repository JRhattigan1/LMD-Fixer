"""Editor for the material/recipe database (`lmd_fixer.recipes`).

Cells are edited as text and only checked on Save, by the same
`validate_recipes` the file format uses, so a half-typed row never blocks
editing. Nothing reaches the file until Save.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from lmd_fixer import recipes as R
from lmd_fixer.desktop import theme

ALL_MATERIALS = "All materials"


def _cell(value) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class RecipeDialog(QDialog):
    saved = Signal()

    def __init__(self, used_p: list[int] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Material database")
        self.resize(1050, 560)
        self.used_p = sorted(set(used_p or []))
        self.path = R.recipes_path()
        self.dirty = False
        self._loading = False

        intro = QLabel(
            "Each <b>P</b> number is an LMD recipe stored on the machine's control panel: laser power, powder "
            "flow and nozzle gas. Record them as they're set on the machine — nothing checks this against the "
            "panel. Material and Notes are for your own reference."
        )
        intro.setWordWrap(True)
        where = QLabel(f"Saved to {self.path}")
        where.setProperty("role", "dim")
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.model = QStandardItemModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        # No column sorting: it would compare numbers as text (100 before 20),
        # and the file is kept in P order anyway.

        self.material = QComboBox()
        self.material.currentTextChanged.connect(self._filter)
        add = QPushButton("Add recipe")
        add.clicked.connect(self.add_row)
        delete = QPushButton("Delete selected")
        delete.clicked.connect(self.delete_selected)
        self.add_missing = QPushButton()
        self.add_missing.clicked.connect(self.add_missing_rows)

        self.errors = QLabel()
        self.errors.setWordWrap(True)
        self.errors.setStyleSheet("color: #f0a35e;")
        save = QPushButton("Save")
        save.setProperty("primary", True)
        save.setDefault(True)
        save.clicked.connect(self.save)
        close = QPushButton("Close")
        close.clicked.connect(self.close)

        tools = QHBoxLayout()
        tools.addWidget(QLabel("Show"))
        tools.addWidget(self.material)
        tools.addSpacing(12)
        tools.addWidget(add)
        tools.addWidget(delete)
        tools.addWidget(self.add_missing)
        tools.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.errors, 1)
        buttons.addWidget(save)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(where)
        layout.addLayout(tools)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)

        self.model.itemChanged.connect(self._edited)
        self.load()

    # -- loading -------------------------------------------------------------------

    def load(self) -> None:
        try:
            self.on_disk = R.load_recipes(self.path)
        except ValueError as exc:
            QMessageBox.critical(self, "Couldn't read the material database", str(exc))
            self.on_disk = R.empty_recipes()
        self.columns = list(self.on_disk.columns)
        # Not blockSignals: the filter proxy in front of the model has to see
        # the rows arrive, or the table stays empty.
        self._loading = True
        self.model.clear()
        self.model.setHorizontalHeaderLabels(self.columns)
        for _, row in self.on_disk.iterrows():
            self._append({c: _cell(row[c]) for c in self.columns})
        self._loading = False
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        notes = self.columns.index(R.NOTES_COLUMN)
        self.table.horizontalHeader().setSectionResizeMode(notes, QHeaderView.Stretch)
        self.dirty = False
        self._refresh_tools()

    def _append(self, values: dict[str, str]) -> None:
        items = []
        for col in self.columns:
            item = QStandardItem(values.get(col, ""))
            if col == R.UPDATED_COLUMN:
                item.setEditable(False)
                item.setForeground(QColor(theme.DIM))
            items.append(item)
        self.model.appendRow(items)

    def _refresh_tools(self) -> None:
        current = self.material.currentText() or ALL_MATERIALS
        materials = sorted({m for m in self._column_values(R.MATERIAL_COLUMN) if m})
        self.material.blockSignals(True)
        self.material.clear()
        self.material.addItems([ALL_MATERIALS, *materials])
        self.material.setCurrentText(current if current in materials else ALL_MATERIALS)
        self.material.blockSignals(False)
        self._filter(self.material.currentText())
        missing = self.missing_p()
        self.add_missing.setVisible(bool(missing))
        self.add_missing.setText(f"Add rows for {len(missing)} P number(s) this file uses")
        self.add_missing.setToolTip(", ".join(R.format_p(p) for p in missing))

    def _column_values(self, column: str) -> list[str]:
        c = self.columns.index(column)
        return [self.model.item(r, c).text().strip() for r in range(self.model.rowCount())]

    def missing_p(self) -> list[int]:
        known = set()
        for text in self._column_values(R.P_COLUMN):
            try:
                known.add(int(float(text)))
            except ValueError:
                pass
        return [p for p in self.used_p if p not in known]

    def _filter(self, material: str) -> None:
        if material == ALL_MATERIALS or not material:
            self.proxy.setFilterFixedString("")
        else:
            self.proxy.setFilterKeyColumn(self.columns.index(R.MATERIAL_COLUMN))
            self.proxy.setFilterRegularExpression(f"^{re.escape(material)}$")

    # -- editing -------------------------------------------------------------------

    def _edited(self, _item) -> None:
        if self._loading:
            return
        self.dirty = True
        self.errors.clear()

    def add_row(self) -> None:
        values = {}
        if self.material.currentText() not in ("", ALL_MATERIALS):
            values[R.MATERIAL_COLUMN] = self.material.currentText()
        self._append(values)
        self.dirty = True
        index = self.proxy.mapFromSource(self.model.index(self.model.rowCount() - 1, 0))
        self.table.scrollTo(index)
        self.table.edit(index)

    def add_missing_rows(self) -> None:
        for p in self.missing_p():
            self._append({R.P_COLUMN: str(p)})
        self.dirty = True
        self._refresh_tools()

    def delete_selected(self) -> None:
        rows = sorted({self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()},
                      reverse=True)
        for r in rows:
            self.model.removeRow(r)
        if rows:
            self.dirty = True

    # -- saving --------------------------------------------------------------------

    def table_frame(self) -> pd.DataFrame:
        data = {c: [] for c in self.columns}
        for r in range(self.model.rowCount()):
            for i, c in enumerate(self.columns):
                text = self.model.item(r, i).text().strip()
                data[c].append(text or None)
        df = pd.DataFrame(data, columns=self.columns)
        # Numbers stay text if they don't parse, so validation can name them.
        for col in [R.P_COLUMN, *R.NUMERIC_COLUMNS]:
            parsed = pd.to_numeric(df[col], errors="coerce")
            df[col] = [p if pd.notna(p) or v is None else v for p, v in zip(parsed, df[col])]
        return df

    def save(self) -> bool:
        df = self.table_frame()
        errors = R.validate_recipes(df)
        if errors:
            self.errors.setText("Not saved: " + " ".join(errors))
            return False
        try:
            R.save_recipes(R.stamp_changed(df, self.on_disk), self.path)
        except ValueError as exc:
            self.errors.setText(str(exc))
            return False
        self.load()
        self.errors.setText(f"Saved {self.model.rowCount()} recipe(s) to {Path(self.path).name}.")
        self.saved.emit()
        return True

    def closeEvent(self, event) -> None:
        if self.dirty:
            answer = QMessageBox.question(
                self, "Unsaved changes", "Save the changes to the material database?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if answer == QMessageBox.Cancel or (answer == QMessageBox.Save and not self.save()):
                event.ignore()
                return
        super().closeEvent(event)

