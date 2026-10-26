"""The workspace: the fixes sidebar on the left; on the right, the map and
the program (or the final diff) on one card and the current step's review
on another, with the step's buttons in a bar beneath."""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QEventLoop, QSettings, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lmd_fixer import __version__
from lmd_fixer.desktop import theme
from lmd_fixer.desktop.bed_map import BedMapView, ToolpathStyle
from lmd_fixer.desktop.final_view import DiffPanel, FinalPanel
from lmd_fixer.desktop.recipe_dialog import RecipeDialog
from lmd_fixer.desktop.gcode_view import GCodeView
from lmd_fixer.desktop.steps.checklist import ChecklistPanel
from lmd_fixer.desktop.steps.recipes import RecipesPanel
from lmd_fixer.desktop.steps_panel import StepsPanel
from lmd_fixer.desktop.spacemouse import SpaceMouse
from lmd_fixer.desktop.view3d import View3D
from lmd_fixer.export import default_output_name, diff_rows
from lmd_fixer.fixes import available_fixes
from lmd_fixer.fixes.change_recipes import toolpath_recipe_calls
from lmd_fixer.gcode import GCodeProgram
from lmd_fixer.review import ReviewSession, ordered_fix_ids
from lmd_fixer.settings import settings_path

# Step keys are ignored for this long after slow work ends: presses typed
# while it ran are delivered then, and shouldn't apply the next step too.
QUIET_AFTER_WORK_S = 0.25
FILE_FILTER = "G-code programs (*.ptp *.nc *.gcode *.txt);;All files (*)"
MAX_RECENT = 8
# Review modes the checklist panel handles; "sections" rows are whole
# toolpath blocks, so the map can tick them too.
CHECKLIST_MODES = {"checklist", "sections"}


def card(widget: QWidget, margin: int = 1) -> QFrame:
    """`widget` on a rounded card."""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.addWidget(widget)
    return frame


class Welcome(QWidget):
    """Shown until a file is open: a drop target, Open, and recent files."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window_ = window
        title = QLabel("LMD Fixer")
        title.setFont(theme.ui_font(28, bold=True))
        subtitle = QLabel("Clean up laser deposition programs, reviewing every change before it's made.")
        subtitle.setProperty("role", "dim")
        subtitle.setFont(theme.ui_font(12))

        drop = QFrame()
        drop.setObjectName("card")
        drop.setMinimumSize(460, 170)
        drop_layout = QVBoxLayout(drop)
        drop_layout.setSpacing(10)
        hint = QLabel("Drop a .ptp program here")
        hint.setFont(theme.ui_font(13, bold=True))
        or_label = QLabel("or")
        or_label.setProperty("role", "faint")
        open_button = QPushButton("Choose a file…")
        open_button.setProperty("primary", True)
        open_button.clicked.connect(window.open_dialog)
        drop_layout.addStretch(1)
        for w in (hint, or_label, open_button):
            drop_layout.addWidget(w, 0, Qt.AlignHCenter)
        drop_layout.addStretch(1)

        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(0)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(title, 0, Qt.AlignHCenter)
        layout.addWidget(subtitle, 0, Qt.AlignHCenter)
        layout.addSpacing(26)
        layout.addWidget(drop, 0, Qt.AlignHCenter)
        layout.addSpacing(22)
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
            button.setProperty("flat", True)
            button.setToolTip(path)
            button.clicked.connect(lambda _=False, p=path: self.window_.open_file(p))
            self.recent_box.addWidget(button, 0, Qt.AlignHCenter)


SHORTCUTS = [
    ("Ctrl+O", "Open a program"),
    ("Ctrl+S", "Save the fixed file"),
    ("Ctrl+Enter", "Apply the current step"),
    ("Alt+←", "Back one step"),
    ("J / K", "Next / previous change"),
    ("Space", "Tick or untick the selected changes"),
    ("A / R", "Tick / untick every change"),
    ("Ctrl+Z / Ctrl+Y", "Undo / redo"),
    ("Ctrl+F", "Filter the list of changes (Esc clears it)"),
    ("N / Shift+N", "Next / previous change in the final comparison"),
    ("Ctrl+M", "Material database"),
    ("F", "Fit the plan view (when it has focus)"),
    ("Mouse", "Plan: wheel zooms, right/middle-drag pans, drag a box to select"),
    ("Mouse", "3D: drag orbits, Ctrl/middle-drag pans, wheel zooms, click selects"),
    ("SpaceMouse", "Moves the plan or 3D view; button 1 fits, button 2 shows/hides travel"),
]


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings()
        self.session: ReviewSession | None = None
        self.path = ""
        self.output_name = ""
        self._busy = False  # slow work running; see _working
        self._quiet_until = 0.0  # time.monotonic() before which step keys are ignored
        self._saved = False  # the finished review has been saved
        self.undo = QUndoStack(self)
        self.setWindowTitle("LMD Fixer")
        self.setAcceptDrops(True)
        self.resize(1400, 900)

        # Central area: welcome page, or the workspace once a file is open.
        self.central = QStackedWidget()
        self.welcome = Welcome(self)
        self.map = BedMapView()
        self.code = GCodeView()
        self.diff = DiffPanel()
        self.map_and_code = QSplitter(Qt.Horizontal)
        self.map_and_code.setObjectName("map_and_code")
        # Plan view and 3D share styles and selection; see _show_selection.
        self.view3d = View3D()
        self.map_tabs = QTabWidget()
        self.map_tabs.setObjectName("map_tabs")
        self.map_tabs.addTab(self.map, "Plan")
        self.map_tabs.addTab(self.view3d, "3D")
        self.map_and_code.addWidget(self.map_tabs)
        self.map_and_code.addWidget(self.code)
        self.map_and_code.setHandleWidth(1)
        self.map_and_code.setStretchFactor(0, 1)
        self.map_and_code.setStretchFactor(1, 1)
        self.map_and_code.setSizes([600, 600])  # until the saved layout says otherwise
        self.top = QStackedWidget()
        self.top.addWidget(self.map_and_code)
        self.top.addWidget(self.diff)
        # Inset to clear the rounded corners (re-set per theme in set_theme).
        self.top_card = card(self.top, margin=theme.card_inset())
        self.top_card.setProperty("content", True)  # solid even on glass themes

        self.checklist = ChecklistPanel(self.undo, self.settings)
        self.recipes = RecipesPanel(self.undo)
        self.final = FinalPanel()
        self.panels = QStackedWidget()
        for panel in (self.checklist, self.recipes, self.final):
            self.panels.addWidget(panel)

        # The step's buttons, in a bar beneath the review card.
        self.step_label = QLabel()
        self.step_label.setProperty("role", "dim")
        self.apply_button = QPushButton("Apply step")
        self.apply_button.setProperty("primary", True)
        self.apply_button.setToolTip("Apply the ticked changes and move on (Ctrl+Enter)")
        self.skip_button = QPushButton("Skip")
        self.skip_button.setToolTip("Move on without changing anything")
        self.back_button = QPushButton("Back")
        self.back_button.setToolTip("Undo the previous step (Alt+←)")
        nav = QHBoxLayout()
        nav.setContentsMargins(4, 0, 0, 0)
        nav.addWidget(self.step_label)
        nav.addStretch(1)
        nav.addWidget(self.back_button)
        nav.addWidget(self.skip_button)
        nav.addSpacing(4)
        nav.addWidget(self.apply_button)
        panels_card = card(self.panels, margin=14)
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)
        bottom_layout.addWidget(panels_card, 1)
        bottom_layout.addLayout(nav)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.addWidget(self.top_card)
        self.splitter.addWidget(bottom)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setHandleWidth(12)
        self.central.addWidget(self.welcome)
        self.central.addWidget(self.splitter)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(14, 6, 14, 10)
        content_layout.addWidget(self.central)

        # Sidebar beside the content.
        self.steps = StepsPanel()
        self.steps.setMinimumWidth(270)
        self.steps.setMaximumWidth(340)
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.steps)
        root_layout.addWidget(content, 1)
        self.root_layout = root_layout
        self._place_sidebar()
        self.setCentralWidget(root)

        self.status = QLabel()
        self.statusBar().addPermanentWidget(self.status)
        self.statusBar().showMessage(f"LMD Fixer {__version__}")
        self.statusBar().setToolTip(f"Which fixes are offered is set in {settings_path()}")

        self.spacemouse = SpaceMouse(self)
        self.spacemouse.moved.connect(self._space_moved)
        self.spacemouse.buttonPressed.connect(self._space_button)

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
        # Step moves from the keyboard, menus and buttons go through
        # _from_user, which drops input queued during slow work.
        self.act_apply = action("Apply step", ["Ctrl+Return", "Ctrl+Enter"], self._from_user(self.apply_step))
        self.act_skip = action("Skip step", None, self._from_user(self.skip_step))
        self.act_back = action("Back", "Alt+Left", self._from_user(self.back_step))
        self.act_restart = action("Start over", None, self.restart)
        self.act_database = action("Material database…", "Ctrl+M", self.open_material_database)
        self.act_fit = action("Fit bed map", None, lambda: self.map.fit())
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
            action("Toggle change", "Space", self.checklist.toggle_selected),
            action("Accept all", "A", lambda: self.checklist.set_all(True)),
            action("Reject all", "R", lambda: self.checklist.set_all(False)),
            action("Filter changes", "Ctrl+F", self.checklist.focus_filter),
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
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.act_fit)
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction(self.act_database)
        self._fill_recent_menu()

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("Keyboard and mouse…", self.show_shortcuts)

        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setContextMenuPolicy(Qt.PreventContextMenu)
        # Plain Undo/Redo in the toolbar (the stack's own actions rename
        # themselves "Undo Tick 2 change(s)" and make the bar jump about);
        # what they'd undo is in the tooltip.
        self.tb_undo = QAction("Undo", self)
        self.tb_undo.triggered.connect(self.undo.undo)
        self.tb_redo = QAction("Redo", self)
        self.tb_redo.triggered.connect(self.undo.redo)
        self.undo.canUndoChanged.connect(self.tb_undo.setEnabled)
        self.undo.canRedoChanged.connect(self.tb_redo.setEnabled)
        self.undo.undoTextChanged.connect(lambda t: self.tb_undo.setToolTip(f"Undo {t} (Ctrl+Z)" if t else "Undo"))
        self.undo.redoTextChanged.connect(lambda t: self.tb_redo.setToolTip(f"Redo {t} (Ctrl+Y)" if t else "Redo"))
        self.tb_undo.setEnabled(False)
        self.tb_redo.setEnabled(False)
        for a in (self.act_open, self.act_save, None, self.tb_undo, self.tb_redo, None, self.act_database):
            if a is None:
                toolbar.addSeparator()
            else:
                toolbar.addAction(a)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        self.theme_buttons = QButtonGroup(self)
        self.theme_buttons.setExclusive(True)
        # One container, no spacing, so the segments join into one control
        # (the toolbar would space them apart).
        segments = QWidget()
        segments_layout = QHBoxLayout(segments)
        segments_layout.setContentsMargins(0, 0, 0, 0)
        segments_layout.setSpacing(0)
        for i, name in enumerate(theme.THEME_NAMES):
            b = QToolButton()
            b.setText(name)
            b.setCheckable(True)
            b.setProperty("segment", "true")
            b.setToolTip(f"{name} appearance")
            if i == 0:
                b.setProperty("edge", "first")
            elif i == len(theme.THEME_NAMES) - 1:
                b.setProperty("edge", "last")
            self.theme_buttons.addButton(b, i)
            segments_layout.addWidget(b)
        toolbar.addWidget(segments)
        current = self.settings.value("theme", theme.DEFAULT_THEME)
        if current not in theme.THEME_NAMES:
            current = theme.DEFAULT_THEME
        self.theme_buttons.button(theme.THEME_NAMES.index(current)).setChecked(True)
        self.theme_buttons.idClicked.connect(lambda i: self.set_theme(theme.THEME_NAMES[i]))

    def _wire(self) -> None:
        self.apply_button.clicked.connect(self.act_apply.trigger)
        self.skip_button.clicked.connect(self.act_skip.trigger)
        self.back_button.clicked.connect(self.act_back.trigger)
        self.steps.selectionEdited.connect(self.selection_edited)
        self.steps.stepRequested.connect(self.go_to_step)
        self.checklist.selectionMoved.connect(self._list_selection_moved)
        self.checklist.choicesChanged.connect(self._choices_changed)
        self.recipes.selectionMoved.connect(self._list_selection_moved)
        self.recipes.choicesChanged.connect(self._choices_changed)
        self.map.selectionEdited.connect(self._map_selection_edited)
        self.map.toolpathActivated.connect(self._map_activated)
        self.view3d.selectionEdited.connect(self._map_selection_edited)
        self.view3d.toolpathActivated.connect(self._map_activated)
        self.code.lineActivated.connect(self._code_line_activated)
        self.final.nameEdited.connect(self._name_edited)
        self.final.saveRequested.connect(self.save_dialog)
        self.final.anotherFileRequested.connect(self.open_another)

    def _load_fix_list(self) -> None:
        offered = available_fixes()
        order = ordered_fix_ids(offered)
        saved = self.settings.value("selected_fixes")
        selected = tuple(f for f in (saved if isinstance(saved, list) else order) if f in offered)
        self.steps.set_fixes([(f, offered[f].label or f, offered[f].description) for f in order], selected)

    def _restore_layout(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        for key, splitter in (("window/split_rows", self.splitter), ("window/split_top", self.map_and_code)):
            saved = self.settings.value(key)
            if saved is not None:
                splitter.restoreState(saved)

    # -- appearance ---------------------------------------------------------------------

    def set_theme(self, name: str) -> None:
        """Switches the whole app to theme `name` and remembers it."""
        theme.apply_theme(QApplication.instance(), name)
        self.settings.setValue("theme", name)
        button = self.theme_buttons.button(theme.THEME_NAMES.index(theme.current.name))
        if not button.isChecked():
            button.setChecked(True)
        inset = theme.card_inset()
        self.top_card.layout().setContentsMargins(inset, inset, inset, inset)
        self._place_sidebar()
        # Widgets that paint with cached theme colours.
        self.code.refresh_theme()
        self.map.refresh_theme()
        self.view3d.refresh_theme()
        self.diff.refresh_theme()
        self.final.refresh_theme()
        self.steps.show_status(*self._step_statuses())
        self._choices_changed()

    def _place_sidebar(self) -> None:
        """Insets the sidebar from the window's edges when the theme floats
        it (a glass slab), so its border and contents move together."""
        margins = (10, 2, 0, 10) if theme.floating_sidebar() else (0, 0, 0, 0)
        self.root_layout.setContentsMargins(*margins)

    def _readable_summaries(self) -> list[str]:
        """Each step's summary, led by the fix's name rather than its id."""
        out = []
        for fid, text in zip(self.session.fix_ids, self.session.summaries):
            prefix = f"[{fid}] "
            rest = text[len(prefix):] if text.startswith(prefix) else text
            out.append(f"{self.session.fix_label(fid)} — {rest}")
        return out

    def _step_statuses(self) -> tuple[tuple[str, ...], dict[str, str]]:
        if self.session is None:
            return (), {}
        return self.session.fix_ids, {fid: self.session.step_status(i) for i, fid in enumerate(self.session.fix_ids)}

    def show_shortcuts(self) -> None:
        rows = "".join(
            f"<tr><td style='padding:4px 18px 4px 0'><b>{k}</b></td><td style='padding:4px 0'>{v}</td></tr>"
            for k, v in SHORTCUTS
        )
        QMessageBox.information(self, "Keyboard and mouse", f"<table>{rows}</table>")

    # -- SpaceMouse -------------------------------------------------------------------

    def start_spacemouse(self) -> None:
        """Opens a SpaceMouse if one is plugged in. Called by `main()`, not
        the constructor, so tests never grab a real device."""
        if self.spacemouse.start():
            self.statusBar().showMessage(f"{self.spacemouse.name} connected — moves the plan and 3D views", 8000)

    def _space_moved(self, motion) -> None:
        """Moves whichever view is showing, while this window is active."""
        if not self.isActiveWindow() or self.top.currentWidget() is not self.map_and_code:
            return
        if self.map_tabs.currentWidget() is self.view3d:
            self.view3d.apply_motion(motion)
        else:
            self.map.apply_motion(motion)

    def _space_button(self, button: int) -> None:
        if not self.isActiveWindow():
            return
        if button == 0:
            (self.view3d.fit if self.map_tabs.currentWidget() is self.view3d else self.map.fit)()
        elif button == 1 and self.map_tabs.currentWidget() is self.view3d:
            self.view3d.toggle_travel()

    def closeEvent(self, event) -> None:
        self.spacemouse.stop()
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/split_rows", self.splitter.saveState())
        self.settings.setValue("window/split_top", self.map_and_code.saveState())
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

    def open_dialog(self, start: str = "") -> None:
        """Asks for a program to open, starting in `start` (by default the
        most recent file's folder)."""
        if not start and self.recent_files():
            start = str(Path(self.recent_files()[0]).parent)
        path, _ = QFileDialog.getOpenFileName(self, "Open a program", start, FILE_FILTER)
        if path:
            self.open_file(path)

    def open_another(self) -> None:
        """Fix another file: Open, starting where the last one was saved."""
        self.open_dialog(str(Path(self.final.saved_path).parent) if self.final.saved_path else "")

    def open_file(self, path: str) -> bool:
        # A finished review that's been saved is done with: no need to ask.
        saved = self._saved and self.session is not None and self.session.finished
        if self.session is not None and self.session.index > 0 and not saved and not self.ask(
            "Open another file?", "The current review has applied steps. Open a new file and discard it?"
        ):
            return False
        try:
            program = GCodeProgram.from_file(path)
        except OSError as exc:
            self.show_error("Couldn't open the file", str(exc))
            return False
        program.source_name = Path(path).name
        with self._working(f"Opening {program.source_name}…"):
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

    @contextmanager
    def _working(self, message: str):
        """A wait cursor and `message` in the status bar while slow work runs
        (a step on a 100k-line program takes seconds), with `_busy` set so
        step moves queued meanwhile are dropped (see `_from_user`)."""
        self._busy = True
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.statusBar().showMessage(message)
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)  # paint them first
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()
            self._busy = False
            self._quiet_until = time.monotonic() + QUIET_AFTER_WORK_S

    def _from_user(self, slot):
        """`slot`, ignored while slow work runs and just after, when key
        presses typed during it are delivered: a second Ctrl+Enter pressed
        while waiting mustn't apply the next step too. (Tests call the step
        methods directly.)"""
        def guarded(*_) -> None:
            if not self._busy and time.monotonic() >= self._quiet_until:
                slot()
        return guarded

    def apply_step(self) -> None:
        if self.session and not self.session.finished:
            with self._working(f"Applying {self.session.fix_label(self.session.fix_id)}…"):
                self.session.apply()
                self._update()

    def skip_step(self) -> None:
        if self.session and not self.session.finished:
            with self._working("Skipping…"):
                self.session.skip()
                self._update()

    def back_step(self) -> None:
        if self.session and self.session.index > 0:
            with self._working("Going back…"):
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
            with self._working("Going back…"):
                self.session.back_to(step)
                self._update()

    def restart(self) -> None:
        if self.session:
            with self._working("Starting over…"):
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
        with self._working("Starting the review over…"):
            self.session.set_selection(selected)
            self._update()

    # -- questions (methods, so tests can answer them) ---------------------------

    def show_error(self, title: str, text: str) -> None:
        """Errors the user should see. A method, so the self-test (which has
        no one to click OK) can send them to its report instead."""
        QMessageBox.critical(self, title, text)

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
        self._saved = False  # any step move or new file means unsaved work
        session = self.session
        if session is None:
            self.central.setCurrentWidget(self.welcome)
            self.steps.show_status((), {})
            self._enable(has_file=False)
            return
        self.central.setCurrentWidget(self.splitter)
        self.steps.show_status(*self._step_statuses())
        n = len(session.fix_ids)
        self.step_label.setText(
            "Review complete" if session.finished else f"Step {session.index + 1} of {n}"
        )
        self.status.setText(
            f"{session.original.source_name} · {len(session.original.lines):,} lines uploaded · "
            f"{len(session.current.lines):,} now"
        )

        if session.finished:
            # The code view is hidden now, but a theme change still repaints
            # it: forget the last step's changes, whose acceptance it would
            # ask a finished session (which has no current fix) about.
            self.code.show_changes([], lambda _: True)
            self.top.setCurrentWidget(self.diff)
            self.diff.set_rows(diff_rows(session.original.lines, session.current.lines))
            self.panels.setCurrentWidget(self.final)
            self.final.show_result(len(session.original.lines), len(session.current.lines),
                                   self._readable_summaries(), self.output_name)
            self._name_edited(self.output_name)
        else:
            self.top.setCurrentWidget(self.map_and_code)
            self.code.set_lines(session.current.lines)
            self.map.load(session.current)
            self.view3d.load(session.current)
            mode = session.fix.review_mode
            if mode == "recipes":
                self.panels.setCurrentWidget(self.recipes)
                self.recipes.load(session, self._part_of_toolpaths())
            else:
                self.panels.setCurrentWidget(self.checklist)
                self.checklist.load(session, self._part_of_changes(session), self._toolpath_of_changes(session))
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
        for widget in (self.apply_button, self.skip_button):
            widget.setVisible(reviewing)
        self.back_button.setVisible(has_file)

    # -- linking the map, the code and the step list -------------------------------

    def _part_of_toolpaths(self) -> dict[int, str]:
        return {s: f"{i.part} ({i.where})" if i.where else i.part for s, i in self.map.info.items()}

    def _part_of_changes(self, session: ReviewSession) -> dict[int, str]:
        parts = self._part_of_toolpaths()
        out = {}
        for change in session.proposal().changes:
            start = self.map.toolpath_at_line(change.original_index)
            if start is not None:
                out[change.original_index] = parts[start]
        return out

    def _toolpath_of_changes(self, session: ReviewSession) -> dict[int, str]:
        """The toolpath each change is in (some fixes, like rotary removal,
        don't name it on the change itself)."""
        out = {}
        for change in session.proposal().changes:
            start = self.map.toolpath_at_line(change.original_index)
            if start is not None:
                out[change.original_index] = self.map.info[start].name
        return out

    def _mode(self) -> str | None:
        if self.session is None or self.session.finished:
            return None
        return self.session.fix.review_mode

    def _choices_changed(self) -> None:
        """Re-marks the code and restyles the map after any choice edit."""
        session = self.session
        if session is None or session.finished:
            return
        mode = session.fix.review_mode
        changes = session.proposal().changes
        current = self.checklist.current_position() if mode in CHECKLIST_MODES else None
        self.code.show_changes(changes, session.is_accepted, current)
        styles, tips = {}, {}
        if mode == "sections":
            for change in changes:
                gone = session.is_accepted(change.original_index)
                styles[change.original_index] = ToolpathStyle(
                    colour=theme.REMOVAL_GONE if gone else theme.REMOVAL_KEPT,
                    width=2.0, dashed=gone, label="✕" if gone else "", label_bold=gone,
                    tint=QColor(208, 59, 59, 34) if gone else None,
                )
                tips[change.original_index] = "Will be removed" if gone else "Kept"
        elif mode == "recipes":
            chosen = self.recipes.chosen()
            for call in self.recipes.calls:
                new = chosen.get(call.start_index)
                p = new if new is not None else call.p_value
                if p is None:
                    continue
                styles[call.start_index] = ToolpathStyle(
                    colour=theme.RECIPE_CHANGED if new is not None else theme.RECIPE_UNCHANGED,
                    width=3.0 if new is not None else 1.5,
                    label=f"P{p}", label_bold=new is not None,
                )
                was = f"P{call.p_value:0{call.p_width}d} → " if new is not None else ""
                tips[call.start_index] = f"{was}P{p:0{call.p_width}d} · {self.recipes.recorded_as(p)}"
        elif mode == "checklist":
            # How many ticked changes each toolpath holds, as a label rather
            # than a colour, so no unvalidated colour pair is introduced.
            ticked: dict[int, int] = {}
            for c in changes:
                start = self.map.toolpath_at_line(c.original_index)
                if start is not None and session.is_accepted(c.original_index):
                    ticked[start] = ticked.get(start, 0) + 1
            for start, n in ticked.items():
                styles[start] = ToolpathStyle(label=f"{n} ✓")
                tips[start] = f"{n} ticked change(s) in this toolpath"
        self.map.set_styles(styles, tips)
        self.view3d.set_styles(styles)

    def _starts_for_positions(self, positions) -> list[int]:
        changes = self.checklist.changes
        starts = {self.map.toolpath_at_line(changes[p].original_index) for p in positions}
        return sorted(starts - {None})

    def _list_selection_moved(self, selection: list) -> None:
        """The step list's selection moved: follow it on the map and in the code."""
        mode = self._mode()
        if mode in CHECKLIST_MODES:
            self._show_selection(self._starts_for_positions(selection))
            if selection:
                self.code.set_current_change(selection[0])
        elif mode == "recipes":
            self._show_selection(selection)
            if selection:
                self.code.go_to_line(selection[0])

    def _show_selection(self, starts) -> None:
        """Shows the selected toolpaths in both the plan view and 3D."""
        self.map.set_selected(starts)
        self.view3d.set_selected(starts)

    def _map_selection_edited(self, starts: list) -> None:
        """Toolpaths picked on the plan map or in 3D: select them in the
        other view and in the list, and show the first in the code."""
        self._show_selection(starts)
        mode = self._mode()
        if mode in CHECKLIST_MODES:
            wanted = set(starts)
            positions = [p for p, c in enumerate(self.checklist.changes)
                         if self.map.toolpath_at_line(c.original_index) in wanted]
            self.checklist.select_positions(positions)
            if positions:
                self.code.set_current_change(positions[0])
        elif mode == "recipes":
            self.recipes.select_starts(starts)
            if starts:
                self.code.go_to_line(starts[0])

    def _map_activated(self, start: int) -> None:
        """Double-click on the map: toggle that toolpath's changes."""
        mode = self._mode()
        if mode in CHECKLIST_MODES:
            info = self.map.info[start]
            indices = [c.original_index for c in self.checklist.changes
                       if info.start <= c.original_index <= info.end]
            if indices:
                self.checklist.set_indices(indices, not self.session.is_accepted(indices[0]))
        elif mode == "recipes":
            self.recipes.select_starts([start])
            self._show_selection([start])
            self.recipes.new_p.setFocus()
            self.recipes.new_p.selectAll()

    def _code_line_activated(self, line: int) -> None:
        self.map.set_focus(self.map.toolpath_at_line(line))
        pos = self.code.change_at_line(line)
        if pos is not None and self.panels.currentWidget() is self.checklist:
            self.checklist.select_positions([pos])
            self._show_selection(self._starts_for_positions([pos]))

    # -- material database -----------------------------------------------------------

    def open_material_database(self) -> None:
        used = []
        if self.session is not None:
            used = [c.p_value for c in toolpath_recipe_calls(self.session.current) if c.p_value is not None]
        dialog = RecipeDialog(used, self)
        dialog.saved.connect(self._database_saved)
        dialog.exec()

    def _database_saved(self) -> None:
        if self._mode() == "recipes":
            self.recipes.reload_database()
            self.recipes.refresh()

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
            self.show_error("Couldn't save the file", str(exc))
            return
        self.output_name = Path(path).name
        self.final.name.setText(self.output_name)
        self.final.show_notes(plan.notes)
        self.final.show_saved(path)
        self._saved = True
        self.statusBar().showMessage(f"Saved {path}", 8000)
