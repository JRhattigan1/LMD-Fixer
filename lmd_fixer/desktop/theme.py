"""Themes: Dark (default), Light, Vivid and Glass, in a macOS-inspired style.

Each theme is a set of colour tokens. `use(name)` copies the chosen theme's
tokens into this module's globals, so code that reads `theme.TEXT` etc. at
draw time always gets the current theme; `apply_theme` also rebuilds the Qt
palette and stylesheet. Widgets that cache colours (the syntax highlighter,
the maps) are refreshed by `MainWindow.set_theme`.

Map state colours are per theme and were validated with the dataviz skill's
validate_palette.py against that theme's CANVAS: blue/red for kept/removed
and blue/orange for unchanged/new P pass every check on all three canvases
(CVD ΔE 24–27). Green/red was dropped: on white it failed contrast, and a
darker green fell into the colour-blind warning band.

Glass follows Apple's Liquid Glass (WWDC25 "Meet Liquid Glass" and the HIG
Materials guidance), as far as Qt allows: glass only on the navigation
layer (the floating sidebar, toolbar controls, the step buttons and
segmented controls), never on content, which stays on solid cards; a
bright specular rim along the top of each glass shape and a faint outer
edge; capsule controls and concentric corners; tint kept for the primary
action alone; no glass on glass (what sits on glass uses plain fills).
Qt can't blur or lens what's behind a widget, so the glass is a
translucent white over a soft BACKDROP gradient. It sets BACKDROP and
GLASS / GLASS_EDGE, which switch on the glass rules (`_glass_rules`, in a
dark or light variant per the theme's `dark`); opaque themes leave them
empty. Glass is dark and reuses Dark's map colours, re-validated against its
CANVAS (= its CARD, so the map has no square edge inside the rounded card):
CVD ΔE 25.7 (kept/removed) and 27.4 (P), all checks pass.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

import math
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu

THEME_NAMES = ["Dark", "Light", "Vivid", "Glass"]
DEFAULT_THEME = "Dark"


@dataclass(frozen=True)
class Theme:
    name: str
    dark: bool
    # Surfaces
    WINDOW: str
    SIDEBAR: str
    CARD: str
    FIELD: str
    RAISED: str
    CANVAS: str  # map, 3D and code backgrounds
    SEPARATOR: str
    # Text
    TEXT: str
    DIM: str  # secondary
    FAINT: str  # tertiary
    # Accent
    ACCENT: str
    ACCENT_TEXT: str
    ACCENT_2: str  # second stop of the primary button (same as ACCENT unless vivid)
    SUCCESS: str
    WARNING: str  # warning text (>= 4.5:1 on CARD)
    FOCUS: str  # selection outlines on the maps
    # Code
    SYN_G: str
    SYN_M: str
    SYN_F: str
    SYN_P: str
    SYN_O: str
    SYN_COMMENT: str
    SYN_MARKER: str
    # Maps (validated pairs, see module docstring)
    NEUTRAL: str
    TRAVEL: str
    REMOVAL_KEPT: str
    REMOVAL_GONE: str
    RECIPE_UNCHANGED: str
    RECIPE_CHANGED: str
    # Change marks (alpha 0-255)
    MARK_ALPHA: int
    # Glass themes only (see module docstring); "" means an ordinary, opaque theme.
    BACKDROP: str = ""  # QSS brush behind the whole window, e.g. a qlineargradient
    GLASS: str = ""  # QSS fill of the glass shapes, mid-sheen (sidebar, cards, controls)
    GLASS_EDGE: str = ""  # their faint outer edge (the top edge is a bright rim)


THEMES = {
    "Dark": Theme(
        "Dark", True,
        WINDOW="#1e1e20", SIDEBAR="#28282b", CARD="#232326", FIELD="#2c2c2f", RAISED="#3a3a3d",
        CANVAS="#18181a", SEPARATOR="#38383b",
        TEXT="#f2f2f7", DIM="#a1a1a6", FAINT="#6e6e73",
        ACCENT="#0a84ff", ACCENT_TEXT="#ffffff", ACCENT_2="#0a84ff", SUCCESS="#30d158", WARNING="#e3b341", FOCUS="#f5f5f7",
        SYN_G="#6cb6ff", SYN_M="#d2a8ff", SYN_F="#ffb86b", SYN_P="#7ee787", SYN_O="#64d2ff",
        SYN_COMMENT="#7c7c82", SYN_MARKER="#64d2ff",
        NEUTRAL="#6c7480", TRAVEL="#8e8e93",
        REMOVAL_KEPT="#3987e5", REMOVAL_GONE="#d03b3b", RECIPE_UNCHANGED="#3987e5", RECIPE_CHANGED="#d95a1d",
        MARK_ALPHA=70,
    ),
    "Light": Theme(
        "Light", False,
        WINDOW="#f5f5f7", SIDEBAR="#ebebee", CARD="#ffffff", FIELD="#ffffff", RAISED="#ffffff",
        CANVAS="#ffffff", SEPARATOR="#d9d9de",
        TEXT="#1d1d1f", DIM="#6e6e73", FAINT="#aeaeb2",
        ACCENT="#007aff", ACCENT_TEXT="#ffffff", ACCENT_2="#007aff", SUCCESS="#28a745", WARNING="#8a5a00", FOCUS="#1d1d1f",
        SYN_G="#0b4f9e", SYN_M="#9b2393", SYN_F="#b35a00", SYN_P="#1e7d32", SYN_O="#0e6b8a",
        SYN_COMMENT="#8a8a8e", SYN_MARKER="#0e6b8a",
        NEUTRAL="#9aa1ab", TRAVEL="#aeaeb2",
        REMOVAL_KEPT="#2a78d6", REMOVAL_GONE="#d03b3b", RECIPE_UNCHANGED="#2a78d6", RECIPE_CHANGED="#d95a1d",
        MARK_ALPHA=55,
    ),
    "Vivid": Theme(
        "Vivid", True,
        WINDOW="#1a0b38", SIDEBAR="#271055", CARD="#211049", FIELD="#2e1663", RAISED="#3b1d7c",
        CANVAS="#120726", SEPARATOR="#43228a",
        TEXT="#fff4fb", DIM="#c9b6ff", FAINT="#8f7bd6",
        ACCENT="#ff2d95", ACCENT_TEXT="#ffffff", ACCENT_2="#ff9f0a", SUCCESS="#32d74b", WARNING="#ffd60a", FOCUS="#00e5ff",
        SYN_G="#00e5ff", SYN_M="#ff6ad5", SYN_F="#ffd60a", SYN_P="#9dff5c", SYN_O="#ff9f0a",
        SYN_COMMENT="#9d86e0", SYN_MARKER="#ff9f0a",
        NEUTRAL="#7b6aa8", TRAVEL="#9d86e0",
        REMOVAL_KEPT="#3987e5", REMOVAL_GONE="#d03b3b", RECIPE_UNCHANGED="#3987e5", RECIPE_CHANGED="#d95a1d",
        MARK_ALPHA=80,
    ),
    # Apple's Liquid Glass, dark: a deep, cool backdrop; glass on the controls
    # and the floating sidebar; content on solid dark cards; tint only on
    # Apply/Save. Text checked over the glass at its lightest (TEXT 9.2:1).
    "Glass": Theme(
        "Glass", True,
        WINDOW="#121620", SIDEBAR="#1c1f2a", CARD="#1c1c1e", FIELD="#2c2c2f", RAISED="#3a3a3d",
        CANVAS="#1c1c1e", SEPARATOR="#38383b",  # CANVAS = CARD: the map blends into its rounded card
        TEXT="#f2f2f7", DIM="#aeaeb2", FAINT="#6e6e73",
        ACCENT="#0a84ff", ACCENT_TEXT="#ffffff", ACCENT_2="#0a84ff", SUCCESS="#30d158", WARNING="#e3b341",
        FOCUS="#f5f5f7",
        SYN_G="#6cb6ff", SYN_M="#d2a8ff", SYN_F="#ffb86b", SYN_P="#7ee787", SYN_O="#64d2ff",
        SYN_COMMENT="#7c7c82", SYN_MARKER="#64d2ff",
        NEUTRAL="#6c7480", TRAVEL="#8e8e93",
        REMOVAL_KEPT="#3987e5", REMOVAL_GONE="#d03b3b", RECIPE_UNCHANGED="#3987e5", RECIPE_CHANGED="#d95a1d",
        MARK_ALPHA=70,
        BACKDROP=("qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0c1220, stop:0.55 #1a1c2c, "
                  "stop:1 #0e1e28)"),
        GLASS="rgba(255, 255, 255, 13)",  # the middle of the sheen
        GLASS_EDGE="rgba(255, 255, 255, 26)",
    ),
}

# Current theme's tokens, as module globals (see module docstring). Set by use().
current: Theme = THEMES[DEFAULT_THEME]
WINDOW = SIDEBAR = CARD = FIELD = RAISED = CANVAS = SEPARATOR = ""
TEXT = DIM = FAINT = ACCENT = ACCENT_TEXT = ACCENT_2 = SUCCESS = WARNING = FOCUS = ""
SYN_G = SYN_M = SYN_F = SYN_P = SYN_O = SYN_COMMENT = SYN_MARKER = ""
NEUTRAL = TRAVEL = REMOVAL_KEPT = REMOVAL_GONE = RECIPE_UNCHANGED = RECIPE_CHANGED = ""
MARK_ALPHA = 70
BACKDROP = GLASS = GLASS_EDGE = ""
# Older names still used around the app.
SURFACE = PANEL = ""
# Proposed-change marks and diff rows, derived per theme in use().
REMOVE_BG = MODIFY_BG = REJECTED_BG = REMOVE_MARK = MODIFY_MARK = QColor()
DIFF_DELETE_BG = DIFF_INSERT_BG = DIFF_GAP_BG = QColor()


def use(name: str) -> Theme:
    """Makes `name` the current theme's tokens (no Qt calls; see apply_theme)."""
    global current, SURFACE, PANEL, REMOVE_BG, MODIFY_BG, REJECTED_BG, REMOVE_MARK, MODIFY_MARK
    global DIFF_DELETE_BG, DIFF_INSERT_BG, DIFF_GAP_BG
    t = THEMES.get(name, THEMES[DEFAULT_THEME])
    current = t
    for f in fields(Theme):
        if f.name not in ("name", "dark"):
            globals()[f.name] = getattr(t, f.name)
    SURFACE = t.CANVAS
    PANEL = t.CANVAS
    a = t.MARK_ALPHA
    REMOVE_BG = QColor(248, 81, 73, a)
    MODIFY_BG = QColor(227, 179, 65, a)
    REJECTED_BG = QColor(127, 127, 127, 26)
    REMOVE_MARK = QColor("#f85149")
    MODIFY_MARK = QColor("#e3b341")
    DIFF_DELETE_BG = QColor(248, 81, 73, a // 2 + 10)
    DIFF_INSERT_BG = QColor(52, 199, 89, a // 2 + 10)
    DIFF_GAP_BG = QColor(127, 127, 127, 22)
    return t


use(DEFAULT_THEME)


def ui_font(size: float = 10, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Helvetica Neue", font.defaultFamily()])
    font.setPointSizeF(size)
    font.setBold(bold)
    return font


def code_font() -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setFamilies(["Cascadia Mono", "Consolas", font.family()])
    font.setPointSize(10)
    return font


def apply_theme(app: QApplication, name: str = DEFAULT_THEME) -> Theme:
    t = use(name)
    app.setStyle("Fusion")
    app.setFont(ui_font(10))
    p = QPalette()
    roles = {
        QPalette.Window: t.WINDOW, QPalette.WindowText: t.TEXT, QPalette.Base: t.CARD,
        QPalette.AlternateBase: t.FIELD, QPalette.Text: t.TEXT, QPalette.Button: t.RAISED,
        QPalette.ButtonText: t.TEXT, QPalette.BrightText: t.FOCUS, QPalette.Highlight: t.ACCENT,
        QPalette.HighlightedText: t.ACCENT_TEXT, QPalette.ToolTipBase: t.CARD, QPalette.ToolTipText: t.TEXT,
        QPalette.PlaceholderText: t.FAINT, QPalette.Link: t.ACCENT, QPalette.Mid: t.SEPARATOR,
        QPalette.Midlight: t.SEPARATOR, QPalette.Light: t.RAISED,
    }
    for role, colour in roles.items():
        p.setColor(role, QColor(colour))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(t.FAINT))
    app.setPalette(p)
    app.setStyleSheet(stylesheet(t, _check_image()))
    _round_popups(app)
    return t


class _PopupRounder(QObject):
    """Menus and tooltips are separate windows, square however round their
    stylesheet border: the window's corners showed behind the curve. Made
    frameless and translucent as they're first polished, only what the
    stylesheet draws shows, rounded corners included."""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Polish and obj.isWidgetType() and obj.isWindow():
            if isinstance(obj, QMenu) or obj.metaObject().className() == "QTipLabel":
                if not obj.testAttribute(Qt.WA_TranslucentBackground):
                    obj.setWindowFlags(obj.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
                    obj.setAttribute(Qt.WA_TranslucentBackground)
        return False


def _round_popups(app: QApplication) -> None:
    if getattr(app, "_popup_rounder", None) is None:
        app._popup_rounder = _PopupRounder(app)
        app.installEventFilter(app._popup_rounder)


def _check_image() -> str:
    """A white tick for checked boxes, drawn once and kept in the temp
    folder: Qt style sheets can only take an image from a file."""
    path = Path(tempfile.gettempdir()) / "lmd-fixer-ui" / "check.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        pix = QPixmap(32, 32)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#ffffff"), 4.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        tick = QPainterPath(QPointF(8, 16.5))
        tick.lineTo(13.5, 22)
        tick.lineTo(24, 10)
        p.drawPath(tick)
        p.end()
        pix.save(str(path))
    return path.as_posix()


def card_radius(t: Theme | None = None) -> int:
    """Corner radius of the cards: rounder on glass themes, as macOS 26's
    windows are."""
    return 18 if (t or current).BACKDROP else 12


def card_inset(t: Theme | None = None) -> int:
    """How far a card's edge-to-edge content (the map, the code) must sit
    inside it to clear the rounded corners: square content any closer pokes
    out past the corner's curve, which r * (1 - 1/sqrt 2) just clears; plus
    the 1 px border."""
    return math.ceil(card_radius(t) * (1 - 1 / math.sqrt(2))) + 1


def floating_sidebar(t: Theme | None = None) -> bool:
    """Whether the sidebar floats as an inset slab (glass themes) rather
    than running the window's full height."""
    return bool((t or current).BACKDROP)


def _rgba(colour: str, alpha: float) -> str:
    c = QColor(colour)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def stylesheet(t: Theme, check_image: str = "") -> str:
    sel = _rgba(t.ACCENT, 0.28 if t.dark else 0.18)
    hover = _rgba(t.TEXT, 0.06)
    primary_bg = (f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.ACCENT}, stop:1 {t.ACCENT_2})"
                  if t.ACCENT_2 != t.ACCENT else t.ACCENT)
    radius = card_radius(t)
    return f"""
    QWidget {{ color: {t.TEXT}; }}
    QMainWindow, QDialog {{ background: {t.WINDOW}; }}
    QToolTip {{ background: {t.CARD}; color: {t.TEXT}; border: 1px solid {t.SEPARATOR};
                border-radius: 6px; padding: 6px 8px; }}

    /* Toolbar and menus */
    QToolBar {{ background: {t.WINDOW}; border: none; padding: 6px 10px; spacing: 4px; }}
    QToolBar::separator {{ width: 1px; background: {t.SEPARATOR}; margin: 6px 8px; }}
    QToolButton {{ border: none; border-radius: 7px; padding: 5px 10px; color: {t.TEXT}; }}
    QToolButton:hover {{ background: {hover}; }}
    QToolButton:disabled {{ color: {t.FAINT}; }}
    QMenuBar {{ background: {t.WINDOW}; padding: 2px 6px; }}
    QMenuBar::item {{ padding: 4px 10px; border-radius: 6px; }}
    QMenuBar::item:selected {{ background: {hover}; }}
    QMenu {{ background: {t.CARD}; border: 1px solid {t.SEPARATOR}; border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {t.ACCENT}; color: {t.ACCENT_TEXT}; }}
    QMenu::separator {{ height: 1px; background: {t.SEPARATOR}; margin: 4px 8px; }}

    /* Theme switch: a segmented control */
    QToolButton[segment="true"] {{ background: {t.FIELD}; border-radius: 0; padding: 4px 12px;
                                   border: 1px solid {t.SEPARATOR}; color: {t.DIM}; }}
    QToolButton[segment="true"]:checked {{ background: {t.ACCENT}; color: {t.ACCENT_TEXT}; border-color: {t.ACCENT}; }}
    QToolButton[edge="first"] {{ border-top-left-radius: 7px; border-bottom-left-radius: 7px; }}
    QToolButton[edge="last"] {{ border-top-right-radius: 7px; border-bottom-right-radius: 7px; }}

    /* Sidebar */
    QWidget#sidebar {{ background: {t.SIDEBAR}; border-right: 1px solid {t.SEPARATOR}; }}
    QFrame#steprow {{ border-radius: 9px; background: transparent; }}
    QFrame#steprow:hover {{ background: {hover}; }}
    QFrame#steprow[current="true"] {{ background: {_rgba(t.ACCENT, 0.18)}; }}

    /* Cards */
    QFrame#card {{ background: {t.CARD}; border: 1px solid {t.SEPARATOR}; border-radius: {radius}px; }}

    /* Text roles */
    QLabel[role="title"] {{ font-size: 16pt; font-weight: 600; }}
    QLabel[role="heading"] {{ font-size: 12pt; font-weight: 600; }}
    QLabel[role="dim"] {{ color: {t.DIM}; }}
    QLabel[role="faint"] {{ color: {t.FAINT}; }}
    QLabel[role="warning"] {{ color: #ff9f0a; }}

    /* Buttons */
    QPushButton {{ background: {t.RAISED}; border: 1px solid {t.SEPARATOR}; border-radius: 8px;
                   padding: 6px 14px; color: {t.TEXT}; }}
    QPushButton:hover {{ border-color: {t.DIM}; }}
    QPushButton:pressed {{ background: {t.FIELD}; }}
    QPushButton:disabled {{ color: {t.FAINT}; background: {t.FIELD}; border-color: {t.SEPARATOR}; }}
    QPushButton[primary="true"] {{ background: {primary_bg}; color: {t.ACCENT_TEXT}; border: none;
                                   border-radius: 15px; padding: 7px 20px; font-weight: 600; }}
    QPushButton[primary="true"]:disabled {{ background: {t.FIELD}; color: {t.FAINT}; }}
    QPushButton[flat="true"] {{ background: transparent; border: none; color: {t.ACCENT}; padding: 4px 8px; }}
    QPushButton[flat="true"]:hover {{ background: {hover}; }}

    /* Fields */
    QLineEdit, QSpinBox, QComboBox {{ background: {t.FIELD}; border: 1px solid {t.SEPARATOR};
                                      border-radius: 7px; padding: 4px 8px; selection-background-color: {t.ACCENT}; }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 2px solid {t.ACCENT}; padding: 3px 7px; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{ background: {t.CARD}; border: 1px solid {t.SEPARATOR};
                                   selection-background-color: {t.ACCENT}; selection-color: {t.ACCENT_TEXT}; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator, QTreeView::indicator {{ width: 16px; height: 16px; }}
    QCheckBox::indicator:unchecked, QTreeView::indicator:unchecked {{
        border: 2px solid {t.FAINT}; border-radius: 5px; background: {t.FIELD}; }}
    QCheckBox::indicator:unchecked:hover, QTreeView::indicator:unchecked:hover {{ border-color: {t.ACCENT}; }}
    QCheckBox::indicator:checked, QTreeView::indicator:checked {{
        border: 2px solid {t.ACCENT}; border-radius: 5px; background: {t.ACCENT}; image: url({check_image}); }}

    /* Lists and tables */
    QTreeView, QTableView, QListWidget, QPlainTextEdit {{ background: {t.CARD}; border: none;
        selection-background-color: {sel}; selection-color: {t.TEXT}; outline: 0; }}
    QTreeView::item {{ padding: 4px 4px; border: none; }}
    QTreeView::item:selected {{ background: {sel}; color: {t.TEXT}; }}
    QTreeView::item:hover {{ background: {hover}; }}
    QHeaderView {{ background: transparent; }}
    QHeaderView::section {{ background: {t.CARD}; color: {t.DIM}; border: none;
        border-bottom: 1px solid {t.SEPARATOR}; padding: 6px 6px; font-weight: 600; }}
    QTableCornerButton::section {{ background: {t.CARD}; border: none; }}

    /* Tabs as a segmented control */
    QTabWidget::pane {{ border: none; }}
    QTabWidget::tab-bar {{ alignment: center; }}
    QTabBar::tab {{ background: {t.FIELD}; color: {t.DIM}; padding: 5px 18px; margin: 6px 0;
                    border: 1px solid {t.SEPARATOR}; }}
    QTabBar::tab:first {{ border-top-left-radius: 7px; border-bottom-left-radius: 7px; }}
    QTabBar::tab:last {{ border-top-right-radius: 7px; border-bottom-right-radius: 7px; }}
    QTabBar::tab:selected {{ background: {t.ACCENT}; color: {t.ACCENT_TEXT}; border-color: {t.ACCENT}; }}

    /* Splitters and scroll bars */
    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:horizontal {{ width: 8px; }}
    QSplitter::handle:vertical {{ height: 8px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle {{ background: {_rgba(t.TEXT, 0.22)}; border-radius: 4px; min-height: 24px; min-width: 24px; }}
    QScrollBar::handle:hover {{ background: {_rgba(t.TEXT, 0.38)}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QStatusBar {{ background: {t.WINDOW}; color: {t.DIM}; }}
    QStatusBar::item {{ border: none; }}
    """ + (_glass_rules(t) if t.BACKDROP else "")


def _glass_rules(t: Theme) -> str:
    """Liquid Glass, after Apple's WWDC25 guidance (see the module
    docstring). Later rules of the same selector win, so these override the
    opaque ones above."""
    edge = t.GLASS_EDGE
    if t.dark:
        # Dark glass: white at low strength, its rim a softer highlight.
        rim = _rgba("#ffffff", 0.32)  # the specular highlight along a glass shape's top edge
        lit = _rgba("#ffffff", 0.14)  # glass under the pointer catches more light
        pressed = _rgba("#ffffff", 0.04)
        faded = _rgba("#ffffff", 0.03)  # disabled glass
        row_hover = _rgba("#ffffff", 0.06)
        track = _rgba("#ffffff", 0.07)  # a plain fill, for controls sitting on solid content
        puck = "#4a4a50"  # the selected segment: solid, not tinted
        puck_edge = _rgba("#ffffff", 0.12)
        # Light catching the glass: brightest along the top, the glass's own
        # fill (GLASS) across the middle, lifting at the bottom where it
        # refracts through.
        sheen = (0.13, 0.08)
        field = _rgba("#ffffff", 0.06)
        popup = _rgba("#1e1e22", 0.96)
    else:
        rim = _rgba("#ffffff", 0.95)
        lit = _rgba("#ffffff", 0.88)
        pressed = _rgba("#ffffff", 0.45)
        faded = _rgba("#ffffff", 0.35)
        row_hover = _rgba("#ffffff", 0.6)
        track = _rgba("#000000", 0.05)
        puck = "#ffffff"
        puck_edge = _rgba("#000000", 0.10)
        sheen = (0.75, 0.6)
        field = _rgba("#ffffff", 0.7)
        popup = _rgba("#fafafc", 0.96)
    top, bottom = sheen
    sheen = (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {_rgba('#ffffff', top)}, "
             f"stop:0.45 {t.GLASS}, stop:1 {_rgba('#ffffff', bottom)})")
    r = card_radius(t)
    # Capsule radii: half a control's height. Qt ignores a radius over half
    # the height (the control then draws square), so the smaller controls
    # (fields, tabs, toolbar buttons, about 26 px tall) get their own.
    capsule = 15  # push buttons, the theme switch
    pill = 12
    return f"""
    /* Liquid Glass: the soft backdrop shows through the bars */
    QMainWindow, QDialog {{ background: {t.BACKDROP}; }}
    QToolBar, QMenuBar, QStatusBar {{ background: transparent; }}

    /* Toolbar controls: glass capsules that light up under the pointer */
    QToolButton {{ border-radius: {pill}px; padding: 5px 12px; }}
    QToolButton:hover {{ background: {sheen}; }}
    QToolBar::separator {{ background: {edge}; }}

    /* The sidebar: a floating slab of glass (MainWindow insets it from the
       window's edges; see floating_sidebar), with step rows concentric
       inside it (plain fills, not glass on glass) */
    QWidget#sidebar {{ background: {sheen}; border: 1px solid {edge}; border-top-color: {rim};
                       border-radius: {r + 4}px; }}
    QFrame#steprow {{ border-radius: {r - 6}px; }}
    QFrame#steprow:hover {{ background: {row_hover}; }}
    QFrame#steprow[current="true"] {{ background: {_rgba(t.ACCENT, 0.14)}; }}

    /* Cards are glass: the step panel, the welcome drop zone. The map and
       program card (content="true") stays solid, as content should */
    QFrame#card {{ background: {sheen}; border: 1px solid {edge}; border-top-color: {rim};
                   border-radius: {r}px; }}
    QFrame#card[content="true"] {{ background: {t.CARD}; border: 1px solid {edge}; border-top-color: {rim}; }}
    QGraphicsView {{ border: none; }}

    /* Lists sit straight on the glass; a selected or hovered row is a
       rounded pill that starts after the indent (the row's first and last
       cells round their outer corners) */
    QTreeView, QTableView, QListWidget {{ background: transparent; }}
    QTreeView {{ show-decoration-selected: 0; }}
    QTreeView::branch, QTreeView::branch:selected, QTreeView::branch:hover {{ background: transparent; }}
    QTreeView::item:first {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
    QTreeView::item:last {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
    QTreeView::item:only-one {{ border-radius: 8px; }}
    QTableView::item:selected:first {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
    QTableView::item:selected:last {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
    QListWidget::item {{ border-radius: 8px; padding: 2px 6px; }}
    QHeaderView::section, QTableCornerButton::section {{ background: transparent; border-bottom-color: {edge}; }}

    /* Buttons: glass capsules with a specular rim; only the primary action is tinted */
    QPushButton {{ background: {sheen}; border: 1px solid {edge}; border-top-color: {rim};
                   border-radius: {capsule}px; padding: 6px 16px; }}
    QPushButton:hover {{ background: {lit}; border-color: {edge}; border-top-color: {rim}; }}
    QPushButton:pressed {{ background: {pressed}; }}
    QPushButton:disabled {{ background: {faded}; color: {t.FAINT}; border-color: {edge}; }}
    QPushButton[primary="true"] {{ background: {t.ACCENT}; color: {t.ACCENT_TEXT}; border: 1px solid {t.ACCENT};
                                   border-top-color: {_rgba("#ffffff", 0.55)}; border-radius: {capsule}px; }}
    QPushButton[primary="true"]:hover {{ background: {QColor(t.ACCENT).lighter(110).name()}; }}
    QPushButton[primary="true"]:disabled {{ background: {faded}; color: {t.FAINT};
                                            border-color: {edge}; }}
    QPushButton[flat="true"] {{ background: transparent; border: none; border-radius: 12px; }}
    QPushButton[flat="true"]:hover {{ background: {track}; }}

    /* Fields and tick boxes: rounder, on a light fill */
    QLineEdit, QSpinBox, QComboBox {{ background: {field}; border-color: {edge}; border-radius: {pill}px;
                                      padding: 4px 12px; }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ padding: 3px 11px; }}
    /* Pop-ups: rounded glass panes with the rim (see _PopupRounder) */
    QMenu {{ background: {popup}; border: 1px solid {edge}; border-top-color: {rim}; border-radius: 12px; }}
    QMenu::item {{ border-radius: 7px; }}
    QToolTip {{ background: {popup}; border: 1px solid {edge}; border-top-color: {rim}; border-radius: 10px; }}

    /* Segmented controls: a glass capsule over the backdrop (the theme switch)
       or a plain track on content (Plan / 3D), with a solid white puck */
    QToolButton[segment="true"] {{ background: {sheen}; border: 1px solid {edge}; border-top-color: {rim};
                                   color: {t.DIM}; padding: 4px 14px; }}
    QToolButton[segment="true"]:checked {{ background: {puck}; color: {t.TEXT}; border: 1px solid {puck_edge};
                                           border-top-color: {rim}; font-weight: 600; }}
    QToolButton[edge="first"] {{ border-top-left-radius: {capsule}px; border-bottom-left-radius: {capsule}px; }}
    QToolButton[edge="last"] {{ border-top-right-radius: {capsule}px; border-bottom-right-radius: {capsule}px; }}
    QTabBar::tab {{ background: {track}; border: 1px solid transparent; color: {t.DIM}; padding: 5px 20px; }}
    QTabBar::tab:first {{ border-top-left-radius: {pill}px; border-bottom-left-radius: {pill}px; }}
    QTabBar::tab:last {{ border-top-right-radius: {pill}px; border-bottom-right-radius: {pill}px; }}
    QTabBar::tab:selected {{ background: {puck}; color: {t.TEXT}; border: 1px solid {puck_edge}; font-weight: 600; }}
    """
