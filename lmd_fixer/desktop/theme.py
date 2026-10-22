"""Colours and the dark Qt palette, matching the browser version's look."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

SURFACE = "#272b33"  # window background (the browser app's surface)
PANEL = "#1f232a"  # text areas and lists
PANEL_ALT = "#232831"
RAISED = "#2f3540"  # buttons
TEXT = "#c2c7d1"
DIM = "#8b93a3"
ACCENT = "#2dd4bf"
PRIMARY = "#00d86c"
FOCUS = "#f4f1e6"

# Proposed-change marks in the G-code view and lists. Translucent, so the
# syntax colours still read through them.
REMOVE_BG = QColor(248, 81, 73, 70)
MODIFY_BG = QColor(227, 179, 65, 70)
REJECTED_BG = QColor(255, 255, 255, 14)
REMOVE_MARK = QColor("#f85149")
MODIFY_MARK = QColor("#e3b341")

# Bed map states, each pair validated against SURFACE with the dataviz
# skill's validate_palette.py. Never the only cue: removal is also dashed,
# tinted and labelled ✕; a new P is also thicker with a bold label.
REMOVAL_KEPT = "#1baf7a"
REMOVAL_GONE = "#d03b3b"  # deutan ΔE 9.9 against KEPT; just under 3:1 on SURFACE
RECIPE_UNCHANGED = "#3987e5"
RECIPE_CHANGED = "#d95a1d"  # protan ΔE 27 against UNCHANGED; all checks pass

# Diff view rows.
DIFF_DELETE_BG = QColor(248, 81, 73, 40)
DIFF_INSERT_BG = QColor(63, 185, 80, 36)
DIFF_GAP_BG = QColor(255, 255, 255, 8)


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    colours = {
        QPalette.Window: SURFACE,
        QPalette.WindowText: TEXT,
        QPalette.Base: PANEL,
        QPalette.AlternateBase: PANEL_ALT,
        QPalette.Text: TEXT,
        QPalette.Button: RAISED,
        QPalette.ButtonText: TEXT,
        QPalette.BrightText: FOCUS,
        QPalette.Highlight: ACCENT,
        QPalette.HighlightedText: "#10151c",
        QPalette.ToolTipBase: "#161b24",
        QPalette.ToolTipText: TEXT,
        QPalette.PlaceholderText: DIM,
        QPalette.Link: ACCENT,
    }
    for role, colour in colours.items():
        p.setColor(role, QColor(colour))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#5d6470"))
    app.setPalette(p)
    app.setStyleSheet(
        f"""
        QToolTip {{ border: 1px solid #3a4150; padding: 4px; }}
        QDockWidget::title {{ padding: 6px; background: {PANEL}; }}
        QPushButton {{ padding: 5px 12px; border-radius: 6px; border: 1px solid #3a4150; }}
        QPushButton:default, QPushButton[primary="true"] {{
            background: {PRIMARY}; color: #0c1410; border: none; font-weight: 600;
        }}
        QPushButton:disabled {{ color: #5d6470; background: {PANEL}; }}
        QLabel[role="title"] {{ font-size: 16pt; font-weight: 600; color: #e6e9ef; }}
        QLabel[role="dim"] {{ color: {DIM}; }}
        """
    )


def code_font() -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setFamilies(["Cascadia Mono", "Consolas", font.family()])
    font.setPointSize(10)
    return font
