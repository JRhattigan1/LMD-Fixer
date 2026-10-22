"""Pieces every step panel shares: the header, and undoable choice edits."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lmd_fixer.review import ReviewSession


class SetAcceptedCommand(QUndoCommand):
    """Accept/reject a set of changes at the current step, undoably. Restores
    the exact previous state, including "no override, use the default"."""

    def __init__(self, session: ReviewSession, updates: dict[int, bool], refresh: Callable[[], None],
                 text: str) -> None:
        super().__init__(text)
        self.session = session
        self.updates = updates
        self.before = {i: session.choices.overrides.get(i) for i in updates}
        self.refresh = refresh

    def redo(self) -> None:
        for i, accepted in self.updates.items():
            self.session.set_accepted(i, accepted)
        self.refresh()

    def undo(self) -> None:
        overrides = self.session.choices.overrides
        for i, before in self.before.items():
            if before is None:
                overrides.pop(i, None)
            else:
                overrides[i] = before
        self.refresh()


class StepHeader(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)
        self.title = QLabel()
        self.title.setProperty("role", "title")
        self.description = QLabel()
        self.description.setProperty("role", "dim")
        self.description.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.description)

    def show_fix(self, label: str, description: str) -> None:
        self.title.setText(label)
        self.description.setText(description)
