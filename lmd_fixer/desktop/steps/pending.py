"""Stand-in for a step whose review UI isn't in the desktop app yet."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lmd_fixer.desktop.steps.common import StepHeader
from lmd_fixer.review import ReviewSession


class PendingPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.header = StepHeader()
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(self.note)
        layout.addStretch(1)

    def load(self, session: ReviewSession) -> None:
        fix = session.fix
        self.header.show_fix(fix.label, fix.description)
        self.note.setText(
            "This step's review isn't in the desktop app yet. Skip it to carry on — "
            "applying it now would change nothing, since it only acts on choices made in its own review."
        )
