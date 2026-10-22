"""The program, highlighted, with the current step's proposed changes marked
on the lines themselves rather than in a separate list."""

from __future__ import annotations

import re
from collections.abc import Callable

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QToolTip, QWidget

from lmd_fixer.desktop import theme
from lmd_fixer.fixes import SECTION_MARKER_RE, LineChange


def _fmt(colour: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(colour))
    if bold:
        f.setFontWeight(700)
    f.setFontItalic(italic)
    return f


class GCodeHighlighter(QSyntaxHighlighter):
    """G/M codes, feeds, recipe numbers, comments and section markers, in
    the current theme's code colours (`reload` after a theme change)."""

    PATTERNS = [
        (re.compile(r"\bG\d+(?:\.\d+)?", re.I), "SYN_G", False),
        (re.compile(r"\bM\d+", re.I), "SYN_M", False),
        (re.compile(r"\bF[-+]?(?:\d+\.?\d*|\.\d+)", re.I), "SYN_F", False),
        (re.compile(r"\bP\d+", re.I), "SYN_P", False),
        (re.compile(r"\bO\d+", re.I), "SYN_O", True),
    ]
    COMMENT_RE = re.compile(r"\([^)]*\)")

    def __init__(self, document) -> None:
        super().__init__(document)
        self.reload()

    def reload(self) -> None:
        self.RULES = [(rx, _fmt(getattr(theme, token), bold=bold)) for rx, token, bold in self.PATTERNS]
        self.COMMENT = (self.COMMENT_RE, _fmt(theme.SYN_COMMENT, italic=True))
        self.MARKER = _fmt(theme.SYN_MARKER, bold=True)
        self.SKIP = _fmt(theme.FAINT)
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        if SECTION_MARKER_RE.match(text.strip()):
            self.setFormat(0, len(text), self.MARKER)
            return
        for pattern, fmt in self.RULES:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        pattern, fmt = self.COMMENT
        for m in pattern.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), fmt)
        if text.lstrip().startswith("/"):
            i = text.index("/")
            self.setFormat(i, 1, self.SKIP)


class _Gutter(QWidget):
    def __init__(self, view: "GCodeView") -> None:
        super().__init__(view)
        self.view = view

    def sizeHint(self) -> QSize:
        return QSize(self.view.gutter_width(), 0)

    def paintEvent(self, event) -> None:
        self.view.paint_gutter(event)


BAND_MAX_LINES = 40


class GCodeView(QPlainTextEdit):
    """Read-only, line-numbered program view.

    `show_changes` marks the current step's proposals: removals red and struck
    through, modifications amber with the new text in the tooltip, rejected
    ones only faintly, and a coloured bar in the gutter beside each. Moving
    the cursor emits `lineActivated` so the rest of the window can follow.
    """

    lineActivated = Signal(int)  # 0-based line

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setFont(theme.code_font())
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.highlighter = GCodeHighlighter(self.document())
        self._gutter = _Gutter(self)
        self._changes: list[LineChange] = []
        self._accepted: Callable[[int], bool] = lambda i: True
        self._change_at_line: dict[int, int] = {}
        self._current: int | None = None
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._cursor_moved)
        self._update_gutter_width()
        self.setMouseTracking(True)

    # -- content ---------------------------------------------------------------

    def set_lines(self, lines: list[str]) -> None:
        self._changes = []
        self._change_at_line = {}
        self._current = None
        self.setPlainText("\n".join(lines))
        self.setExtraSelections([])

    def show_changes(self, changes: list[LineChange], is_accepted: Callable[[int], bool],
                     current: int | None = None) -> None:
        """Marks `changes` (indexed by position) on the lines they touch;
        `current` is the position of the one selected in the step panel."""
        self._changes = changes
        self._accepted = is_accepted
        self._current = current
        self._change_at_line = {}
        for pos, c in enumerate(changes):
            for line in range(c.original_index, c.end_index + 1):
                self._change_at_line.setdefault(line, pos)
        self._refresh_selections()
        self._gutter.update()

    def set_current_change(self, pos: int | None, scroll: bool = True) -> None:
        self._current = pos
        self._refresh_selections()
        if scroll and pos is not None and pos < len(self._changes):
            self.go_to_line(self._changes[pos].original_index)

    def _refresh_selections(self) -> None:
        selections = []
        doc = self.document()
        for pos, c in enumerate(self._changes):
            accepted = self._accepted(c.original_index)
            fmt = QTextCharFormat()
            if not accepted:
                fmt.setBackground(theme.REJECTED_BG)
            elif c.kind == "removed":
                fmt.setBackground(theme.REMOVE_BG)
                fmt.setFontStrikeOut(True)
            else:
                fmt.setBackground(theme.MODIFY_BG)
            if pos == self._current:
                bg = fmt.background().color()
                bg.setAlpha(min(255, bg.alpha() + 70))
                fmt.setBackground(bg)
            first = doc.findBlockByNumber(c.original_index)
            last = doc.findBlockByNumber(min(c.end_index, doc.blockCount() - 1))
            if not first.isValid():
                continue
            # The text itself (struck through when it's being removed)...
            sel = QTextEdit.ExtraSelection()
            sel.format = fmt
            cursor = QTextCursor(first)
            cursor.setPosition(last.position() + max(0, last.length() - 1), QTextCursor.KeepAnchor)
            sel.cursor = cursor
            selections.append(sel)
            # ...and a full-width band per line, so a short line still reads
            # as marked. Qt only draws a full-width selection for an empty
            # cursor, hence one per line; a long range (a whole section,
            # hundreds of lines) skips it, since the text selection already
            # fills those lines and thousands of bands slow every repaint.
            if c.end_index - c.original_index < BAND_MAX_LINES:
                band_fmt = QTextCharFormat()
                band_fmt.setBackground(fmt.background())
                band_fmt.setProperty(QTextFormat.FullWidthSelection, True)
                block = first
                while block.isValid() and block.blockNumber() <= c.end_index:
                    band = QTextEdit.ExtraSelection()
                    band.format = band_fmt
                    band.cursor = QTextCursor(block)
                    selections.append(band)
                    block = block.next()
        self.setExtraSelections(selections)

    def refresh_theme(self) -> None:
        self.highlighter.reload()
        self._refresh_selections()
        self._gutter.update()
        self.viewport().update()

    # -- navigation ------------------------------------------------------------

    def go_to_line(self, line: int) -> None:
        block = self.document().findBlockByNumber(line)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        self.blockSignals(True)
        self.setTextCursor(cursor)
        self.blockSignals(False)
        # Put the line a few rows from the top rather than at the very edge.
        self.centerCursor()
        self._gutter.update()

    def current_line(self) -> int:
        return self.textCursor().blockNumber()

    def _cursor_moved(self) -> None:
        self._gutter.update()
        self.lineActivated.emit(self.current_line())

    def change_at_line(self, line: int) -> int | None:
        return self._change_at_line.get(line)

    # -- tooltips ----------------------------------------------------------------

    def event(self, e) -> bool:
        if e.type() == e.Type.ToolTip:
            cursor = self.cursorForPosition(e.pos())
            pos = self._change_at_line.get(cursor.blockNumber())
            if pos is not None:
                c = self._changes[pos]
                state = "accepted" if self._accepted(c.original_index) else "rejected"
                if c.kind == "removed":
                    text = f"Removing ({state})"
                else:
                    text = f"Changing to:\n{c.new_text}\n({state})"
                if c.reason:
                    text += f"\n{c.reason}"
                QToolTip.showText(e.globalPos(), text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    # -- gutter --------------------------------------------------------------------

    def gutter_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, *_) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor(theme.CARD))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        current = self.current_line()
        width = self._gutter.width()
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                pos = self._change_at_line.get(number)
                if pos is not None:
                    c = self._changes[pos]
                    colour = theme.REMOVE_MARK if c.kind == "removed" else theme.MODIFY_MARK
                    if not self._accepted(c.original_index):
                        colour = QColor(theme.FAINT)
                    painter.fillRect(0, top, 3, bottom - top, colour)
                painter.setPen(QColor(theme.TEXT if number == current else theme.FAINT))
                painter.drawText(0, top, width - 6, height, Qt.AlignRight, str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1
