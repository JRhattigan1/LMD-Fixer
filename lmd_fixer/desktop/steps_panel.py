"""The sidebar: which fixes run, in order, and where the review is.

One row per offered fix: a status mark (done ✓, current ●, pending ○,
skipped –), the fix's name in readable type, and a switch that chooses
whether it runs. Double-click a completed step to go back to it.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lmd_fixer.desktop import theme

STATUS_TEXT = {"done": "Done", "skipped": "Skipped", "current": "Reviewing now", "pending": "Up next", "off": "Off"}


class ToggleSwitch(QAbstractButton):
    """An iOS-style on/off switch."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)

    def sizeHint(self) -> QSize:
        return QSize(38, 22)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        on = self.isChecked()
        track = QColor(theme.ACCENT if on else theme.SEPARATOR)
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.right() - d - 2 if on else r.left() + 2
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, r.top() + 2, d, d))


class StatusMark(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.status = "pending"
        self.setFixedSize(20, 20)

    def set_status(self, status: str) -> None:
        self.status = status
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(2, 2, 16, 16)
        s = self.status
        if s == "done":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SUCCESS))
            p.drawEllipse(r)
            pen = QPen(QColor("#ffffff"), 2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            path = QPainterPath()
            path.moveTo(6.5, 10.5)
            path.lineTo(9, 13)
            path.lineTo(13.5, 7.5)
            p.drawPath(path)
        elif s == "current":
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(r.adjusted(5, 5, -5, -5))
        elif s == "skipped":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.FAINT))
            p.drawEllipse(r)
            p.setPen(QPen(QColor(theme.SIDEBAR), 2))
            p.drawLine(7, 10, 13, 10)
        else:  # pending / off
            colour = QColor(theme.FAINT if s == "off" else theme.DIM)
            if s == "off":
                colour.setAlpha(110)
            p.setPen(QPen(colour, 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))


class StepRow(QFrame):
    doubleClicked = Signal()

    def __init__(self, fix_id: str, label: str, description: str) -> None:
        super().__init__()
        self.setObjectName("steprow")
        self.fix_id = fix_id
        self.mark = StatusMark()
        self.title = QLabel(label)
        self.title.setWordWrap(True)
        self.title.setFont(theme.ui_font(11))
        self.state = QLabel()
        self.state.setFont(theme.ui_font(8.5))
        self.state.setProperty("role", "faint")
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(self.title)
        text.addWidget(self.state)
        self.switch = ToggleSwitch()
        self.switch.setToolTip("Run this fix")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        layout.addWidget(self.mark, 0, Qt.AlignTop)
        layout.addLayout(text, 1)
        layout.addWidget(self.switch, 0, Qt.AlignVCenter)
        self.setToolTip(description)
        # Grow to fit a title that wraps onto a third line.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)

    def show_status(self, status: str) -> None:
        self.mark.set_status(status)
        self.state.setText(STATUS_TEXT[status])
        font = theme.ui_font(11, bold=status == "current")
        self.title.setFont(font)
        colour = theme.TEXT if status in ("current", "pending") else (theme.FAINT if status == "off" else theme.DIM)
        self.title.setStyleSheet(f"color: {colour};")
        self.setProperty("current", status == "current")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseDoubleClickEvent(self, event) -> None:
        self.doubleClicked.emit()


class StepsPanel(QWidget):
    """`selectionEdited` fires with the newly switched-on fix ids when the
    user flips a switch; `stepRequested` with a step number when they
    double-click a completed step."""

    selectionEdited = Signal(tuple)
    stepRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        heading = QLabel("Fixes")
        heading.setProperty("role", "heading")
        caption = QLabel("Run top to bottom, whatever order they're switched on in.")
        caption.setProperty("role", "dim")
        caption.setWordWrap(True)
        self.rows: list[StepRow] = []
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(2)
        holder = QWidget()
        holder.setLayout(self.rows_box)
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.note = QLabel("Double-click a finished step to go back to it.")
        self.note.setProperty("role", "faint")
        self.note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(heading)
        layout.addWidget(caption)
        layout.addSpacing(6)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.note)
        self._filling = False
        self._step_of: dict[str, int] = {}

    def set_fixes(self, fixes: list[tuple[str, str, str]], selected: tuple[str, ...]) -> None:
        """`fixes` is (id, label, description) in run order."""
        for row in self.rows:
            row.deleteLater()
        self.rows = []
        while self.rows_box.count():
            self.rows_box.takeAt(0)
        self._filling = True
        for fid, label, description in fixes:
            row = StepRow(fid, label, description)
            row.switch.setChecked(fid in selected)
            row.switch.toggled.connect(self._switched)
            row.doubleClicked.connect(lambda fid=fid: self._double_clicked(fid))
            self.rows_box.addWidget(row)
            self.rows.append(row)
            row.show_status("pending" if fid in selected else "off")
        self.rows_box.addStretch(1)
        self._filling = False

    def selected(self) -> tuple[str, ...]:
        return tuple(row.fix_id for row in self.rows if row.switch.isChecked())

    def set_selected(self, selected: tuple[str, ...]) -> None:
        self._filling = True
        for row in self.rows:
            row.switch.setChecked(row.fix_id in selected)
        self._filling = False

    def show_status(self, fix_ids: tuple[str, ...], status_of: dict[str, str]) -> None:
        """Marks each row with its step's status ("off" for fixes not running;
        with no file open, switched-on fixes show as pending)."""
        self._step_of = {fid: n for n, fid in enumerate(fix_ids)}
        for row in self.rows:
            default = "pending" if row.switch.isChecked() else "off"
            row.show_status(status_of.get(row.fix_id, default) if fix_ids else default)

    def _switched(self, _checked: bool) -> None:
        if not self._filling:
            self.selectionEdited.emit(self.selected())

    def _double_clicked(self, fid: str) -> None:
        step = self._step_of.get(fid)
        if step is not None:
            self.stepRequested.emit(step)
