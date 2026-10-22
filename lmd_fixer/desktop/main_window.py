"""The workspace: steps on the left, the program (or the final diff) in the
middle, and the current step's review below it."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lmd_fixer import __version__
from lmd_fixer.desktop import theme
from lmd_fixer.desktop.final_view import DiffView, FinalPanel
from lmd_fixer.desktop.gcode_view import GCodeView
from lmd_fixer.desktop.steps.checklist import ChecklistPanel
from lmd_fixer.desktop.steps.pending import PendingPanel
from lmd_fixer.desktop.steps_panel import StepsPanel
from lmd_fixer.export import default_output_name, diff_rows
from lmd_fixer.fixes import available_fixes
from lmd_fixer.gcode import GCodeProgram
from lmd_fixer.review import ReviewSession, ordered_fix_ids
from lmd_fixer.settings import settings_path

FILE_FILTER = "G-code programs (*.ptp *.nc *.gcode *.txt);;All files (*)"
MAX_RECENT = 8
# Review modes the checklist panel handles. "sections" uses it until the
# section-removal step gets its map.
CHECKLIST_MODES = {"checklist", "sections"}


class Welcome(QWidget):
    """Shown until a file is open: what to do, and recent files."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window_ = window
        title = QLabel("LMD Fixer")
        title.setProperty("role", "title")
        title.setStyleSheet(f"font-size: 24pt; color: {theme.ACCENT};")
        blurb = QLabel(
            "Open a .ptp program — or drop it on this window — then review each proposed change "
            "before it happens.\nTick the fixes to run in the Steps list on the left."
        )
        blurb.setProperty("role", "dim")
        open_button = QPushButton("Open a program…")
        open_button.setProperty("primary", True)
        open_button.clicked.connect(window.open_dialog)
        self.recent_box = QVBoxLayout()
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(title, 0, Qt.AlignHCenter)
        layout.addWidget(blurb, 0, Qt.AlignHCenter)
        layout.addSpacing(12)
        layout.addWidget(open_button, 0, Qt.AlignHCenter)
        layout.addSpacing(18)
        layout.addLayout(self.recent_box)
        layout.addStretch(2)

    def show_recent(self, paths: list[str]) -> None:
        while self.recent_box.count():
            widget = self.recent_box.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        if not paths:
            return
        heading = QLabel("Recent")
        heading.setProperty("role", "dim")
        self.recent_box.addWidget(heading, 0, Qt.AlignHCenter)
        for path in paths:
            button = QPushButton(Path(path).name)
            button.setToolTip(path)
            button.setFlat(True)
            button.clicked.connect(lambda _=False, p=path: self.window_.open_file(p))
            self.recent_box.addWidget(button, 0, Qt.AlignHCenter)


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings()
        self.session: ReviewSession | None = None
        self.path = ""
        self.output_name = ""
        self.undo = QUndoStack(self)
        self.setWindowTitle("LMD Fixer")
        self.setAcceptDrops(True)
        self.resize(1400, 900)

        # Central area: welcome page, or the workspace once a file is open.
        self.central = QStackedWidget()
        self.welcome = Welcome(self)
        self.code = GCodeView()
        self.diff = DiffView()
        self.top = QStackedWidget()
        self.top.addWidget(self.code)
        self.top.addWidget(self.diff)

        self.checklist = ChecklistPanel(self.undo)
        self.pending = PendingPanel()
        self.final = FinalPanel()
        self.panels = QStackedWidget()
        for panel in (self.checklist, self.pending, self.final):
            self.panels.addWidget(panel)

        nav = QHBoxLayout()
        self.apply_button = QPushButton("Apply step  (Ctrl+Enter)")
        self.apply_button.setProperty("primary", True)
        self.skip_button = QPushButton("Skip")
        self.back_button = QPushButton("← Back")
        nav.addWidget(self.apply_button)
        nav.addWidget(self.skip_button)
        nav.addWidget(self.back_button)
        nav.addStretch(1)
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.addWidget(self.panels, 1)
        bottom_layout.addLayout(nav)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.addWidget(self.top)
        self.splitter.addWidget(bottom)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.central.addWidget(self.welcome)
        self.central.addWidget(self.splitter)
        self.setCentralWidget(self.central)

        # Steps dock.
        self.steps = StepsPanel()
        dock = QDockWidget("Steps", self)
        dock.setObjectName("steps")
        dock.setWidget(self.steps)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        dock.setMinimumWidth(330)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

        self.status = QLabel()
        self.statusBar().addPermanentWidget(self.status)
        self.statusBar().showMessage(f"v{__version__} · fixes offered per {settings_path()}")

        self._build_actions()
        self._wire()
        self._load_fix_list()
        self.welcome.show_recent(self.recent_files())
        self._restore_layout()
        self._update()

    # -- setup ---------------------------------------------------------------

    def _build_actions(self) -> None:
        def action(text, shortcut, slot, tip=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcuts([QKeySequence(s) for s in ([shortcut] if isinstance(shortcut, str) else shortcut)])
            a.triggered.connect(slot)
            if tip:
                a.setToolTip(tip)
            self.addAction(a)
            return a

        self.act_open = action("Open…", "Ctrl+O", self.open_dialog)
        self.act_save = action("Save fixed file…", "Ctrl+S", self.save_dialog)
        self.act_apply = action("Apply step", ["Ctrl+Return", "Ctrl+Enter"], self.apply_step)
        self.act_skip = action("Skip step", None, self.skip_step)
        self.act_back = action("Back", "Alt+Left", self.back_step)
        self.act_restart = action("Start over", None, self.restart)
        self.act_undo = self.undo.createUndoAction(self, "Undo")
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_redo = self.undo.createRedoAction(self, "Redo")
        self.act_redo.setShortcuts([QKeySequence.Redo, QKeySequence("Ctrl+Y")])
        self.addAction(self.act_undo)
        self.addAction(self.act_redo)
        # Review keys, live only while a checklist step is showing.
        self.review_actions = [
            action("Next change", "J", lambda: self.checklist.move(1)),
            action("Previous change", "K", lambda: self.checklist.move(-1)),
            action("Toggle change", "Space", self.checklist.toggle_current),
            action("Accept all", "A", lambda: self.checklist.set_all(True)),
            action("Reject all", "R", lambda: self.checklist.set_all(False)),
        ]

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.act_open)
        self.recent_menu = file_menu.addMenu("Open recent")
        file_menu.addAction(self.act_save)
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.close)
        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(self.act_undo)
        edit_menu.addAction(self.act_redo)
        review_menu = self.menuBar().addMenu("&Review")
        for a in (self.act_apply, self.act_skip, self.act_back, self.act_restart):
            review_menu.addAction(a)
        review_menu.addSeparator()
        for a in self.review_actions:
            review_menu.addAction(a)
        self._fill_recent_menu()

        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        for a in (self.act_open, self.act_save, None, self.act_undo, self.act_redo, None,
                  self.act_back, self.act_skip, self.act_apply):
            if a is None:
                toolbar.addSeparator()
            else:
                toolbar.addAction(a)

    def _wire(self) -> None:
        self.apply_button.clicked.connect(self.apply_step)
        self.skip_button.clicked.connect(self.skip_step)
        self.back_button.clicked.connect(self.back_step)
        self.steps.selectionEdited.connect(self.selection_edited)
        self.steps.stepRequested.connect(self.go_to_step)
        self.checklist.changeSelected.connect(lambda pos: self.code.set_current_change(pos))
        self.checklist.choicesChanged.connect(self._mark_code)
        self.code.lineActivated.connect(self._code_line_activated)
        self.final.nameEdited.connect(self._name_edited)
        self.final.saveRequested.connect(self.save_dialog)

    def _load_fix_list(self) -> None:
        offered = available_fixes()
        order = ordered_fix_ids(offered)
        saved = self.settings.value("selected_fixes")
        selected = tuple(f for f in (saved if isinstance(saved, list) else order) if f in offered)
        self.steps.set_fixes([(f, offered[f].label or f, offered[f].description) for f in order], selected)

    def _restore_layout(self) -> None:
        geometry = self.settings.value("window/geometry")
        state = self.settings.value("window/state")
        if geometry is not None:
            self.restoreGeometry(geometry)
        if state is not None:
            self.restoreState(state)

    def closeEvent(self, event) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        super().closeEvent(event)

    # -- files -----------------------------------------------------------------

    def recent_files(self) -> list[str]:
        value = self.settings.value("recent_files") or []
        paths = value if isinstance(value, list) else [value]
        return [p for p in paths if Path(p).exists()]

    def _remember(self, path: str) -> None:
        paths = [path] + [p for p in self.recent_files() if p != path]
        self.settings.setValue("recent_files", paths[:MAX_RECENT])
        self._fill_recent_menu()
        self.welcome.show_recent(self.recent_files())

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        for path in self.recent_files():
            self.recent_menu.addAction(Path(path).name, lambda p=path: self.open_file(p)).setToolTip(path)
        self.recent_menu.setEnabled(bool(self.recent_files()))

    def open_dialog(self) -> None:
        start = str(Path(self.recent_files()[0]).parent) if self.recent_files() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open a program", start, FILE_FILTER)
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> bool:
        if self.session is not None and self.session.index > 0 and not self.ask(
            "Open another file?", "The current review has applied steps. Open a new file and discard it?"
        ):
            return False
        try:
            program = GCodeProgram.from_file(path)
        except OSError as exc:
            QMessageBox.critical(self, "Couldn't open the file", str(exc))
            return False
        program.source_name = Path(path).name
        self.path = path
        self.session = ReviewSession(program, self.steps.selected())
        self.output_name = default_output_name(program.source_name)
        self._remember(str(Path(path).resolve()))
        self.setWindowTitle(f"{program.source_name} — LMD Fixer")
        self._update()
        return True

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.open_file(urls[0])

    # -- review steps ----------------------------------------------------------

    def apply_step(self) -> None:
        if self.session and not self.session.finished:
            self.session.apply()
            self._update()

    def skip_step(self) -> None:
        if self.session and not self.session.finished:
            self.session.skip()
            self._update()

    def back_step(self) -> None:
        if self.session and self.session.index > 0:
            self.session.back()
            self._update()

    def go_to_step(self, step: int) -> None:
        if not self.session or step >= self.session.index:
            return
        if self.ask(
            "Go back?",
            f"Go back to “{self.session.fix_label(self.session.fix_ids[step])}”? "
            "The steps after it are undone; your choices in them are kept.",
        ):
            self.session.back_to(step)
            self._update()

    def restart(self) -> None:
        if self.session:
            self.session.restart(keep_choices=True)
            self._update()

    def selection_edited(self, selected: tuple[str, ...]) -> None:
        self.settings.setValue("selected_fixes", list(selected))
        if self.session is None:
            return
        if self.session.selection_change_discards_work(selected) and not self.confirm_restart():
            self.steps.set_selected(self.session.fix_ids)
            self.settings.setValue("selected_fixes", list(self.session.fix_ids))
            return
        self.session.set_selection(selected)
        self._update()

    # -- questions (methods, so tests can answer them) ---------------------------

    def ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def confirm_restart(self) -> bool:
        """Whether to start over after a selection change that would undo
        applied steps. "Keep my review" is the default."""
        box = QMessageBox(self)
        box.setWindowTitle("Start the review over?")
        box.setText(
            f"You're {self.session.index} step(s) into this review. Changing which fixes run starts "
            "it over from the original file — everything applied so far would be undone."
        )
        keep = box.addButton("Keep my review", QMessageBox.RejectRole)
        box.addButton("Start over with this selection", QMessageBox.AcceptRole)
        box.setDefaultButton(keep)
        box.exec()
        return box.clickedButton() is not keep

    # -- refreshing the view -----------------------------------------------------

    def _update(self) -> None:
        """Brings every part of the window in line with the session."""
        self.undo.clear()  # undo covers edits within the current step
        session = self.session
        if session is None:
            self.central.setCurrentWidget(self.welcome)
            self.steps.show_status((), {})
            self._enable(has_file=False)
            return
        self.central.setCurrentWidget(self.splitter)
        statuses = {fid: session.step_status(i) for i, fid in enumerate(session.fix_ids)}
        self.steps.show_status(session.fix_ids, statuses)
        self.status.setText(
            f"{session.original.source_name} · {len(session.original.lines):,} lines uploaded · "
            f"{len(session.current.lines):,} now"
        )

        if session.finished:
            self.top.setCurrentWidget(self.diff)
            self.diff.set_rows(diff_rows(session.original.lines, session.current.lines))
            self.panels.setCurrentWidget(self.final)
            self.final.show_result(len(session.original.lines), len(session.current.lines),
                                   session.summaries, self.output_name)
            self._name_edited(self.output_name)
        else:
            self.top.setCurrentWidget(self.code)
            self.code.set_lines(session.current.lines)
            if session.fix.review_mode in CHECKLIST_MODES:
                self.panels.setCurrentWidget(self.checklist)
                self.checklist.load(session)
            else:
                self.panels.setCurrentWidget(self.pending)
                self.pending.load(session)
                self.code.show_changes([], lambda i: True)
        self._enable(has_file=True)

    def _enable(self, has_file: bool) -> None:
        session = self.session
        reviewing = has_file and session is not None and not session.finished
        checklist = reviewing and self.panels.currentWidget() is self.checklist
        for a in self.review_actions:
            a.setEnabled(checklist)
        self.act_apply.setEnabled(reviewing)
        self.act_skip.setEnabled(reviewing)
        self.apply_button.setEnabled(reviewing)
        self.skip_button.setEnabled(reviewing)
        can_back = has_file and session is not None and session.index > 0
        self.act_back.setEnabled(can_back)
        self.back_button.setEnabled(can_back)
        self.act_restart.setEnabled(can_back)
        self.act_save.setEnabled(has_file and session is not None and session.finished)
        for widget in (self.apply_button, self.skip_button, self.back_button):
            widget.setVisible(has_file)

    def _mark_code(self) -> None:
        if self.session and not self.session.finished:
            self.code.show_changes(self.checklist.changes, self.session.is_accepted,
                                   self.checklist.current_position())

    def _code_line_activated(self, line: int) -> None:
        pos = self.code.change_at_line(line)
        if pos is not None and self.panels.currentWidget() is self.checklist:
            self.checklist.select_position(pos)

    # -- saving ------------------------------------------------------------------

    def _name_edited(self, name: str) -> None:
        self.output_name = name
        if self.session:
            self.final.show_notes(self.session.plan_output(name).notes)

    def save_dialog(self) -> None:
        if not (self.session and self.session.finished):
            return
        suggested = self.session.plan_output(self.output_name).name
        start = str(Path(self.path).with_name(suggested))
        path, _ = QFileDialog.getSaveFileName(self, "Save fixed file", start, FILE_FILTER)
        if not path:
            return
        self.save_to(path)

    def save_to(self, path: str) -> None:
        """Writes the result to `path`; the O-number is synced to the name
        actually chosen, which the dialog may have changed."""
        plan = self.session.plan_output(Path(path).name)
        try:
            Path(path).write_bytes(plan.to_bytes())
        except OSError as exc:
            QMessageBox.critical(self, "Couldn't save the file", str(exc))
            return
        self.output_name = Path(path).name
        self.final.name.setText(self.output_name)
        self.final.show_notes(plan.notes)
        self.statusBar().showMessage(f"Saved {path}", 8000)
