"""Plan view of the bed: every toolpath's deposition path, with a box per
toolpath as the thing you click.

Scene units are millimetres with Y up, as on the machine. Lines and labels
keep their on-screen size at any zoom (cosmetic pens; labels ignore the view
transform). Interaction is the usual desktop kind: wheel zooms about the
cursor, middle- or right-drag pans, click/Ctrl-click/rubber-band select,
double-click activates, F fits the bed.

Selection on the map is the same selection as in the step's list: the window
mirrors one into the other. Steps style toolpaths with `set_styles`.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)

from lmd_fixer.desktop import theme
from lmd_fixer.gcode import GCodeProgram
from lmd_fixer.toolpaths import analyse_toolpaths, group_into_parts, path_points



def _alpha(colour: str, alpha: int) -> QColor:
    c = QColor(colour)
    c.setAlpha(alpha)
    return c

BOX_MARGIN_MM = 2.0
GRID_MM = 50.0


@dataclass
class ToolpathStyle:
    colour: str = ""  # "" = the theme's neutral colour
    width: float = 1.5
    dashed: bool = False
    label: str = ""
    label_bold: bool = False
    tint: QColor | None = None  # box fill, e.g. a toolpath being removed


@dataclass
class ToolpathInfo:
    start: int
    end: int
    name: str
    part: str
    where: str


class _Label(QGraphicsItem):
    """Text on a surface-coloured pill, constant size on screen, so it reads
    over the toolpath's own lines at any zoom."""

    def __init__(self) -> None:
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.text = ""
        self.bold = False
        self._rect = QRectF()

    def set_text(self, text: str, bold: bool) -> None:
        self.prepareGeometryChange()
        self.text, self.bold = text, bold
        if text:
            m = QFontMetricsF(self._font())
            w, h = m.horizontalAdvance(text) + 8, m.height() + 2
            self._rect = QRectF(-w / 2, -h / 2, w, h)
        else:
            self._rect = QRectF()
        self.update()

    def _font(self) -> QFont:
        f = QFont()
        f.setPointSizeF(8.5)
        f.setBold(self.bold)
        return f

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if not self.text:
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(_alpha(theme.CANVAS, 225))
        painter.drawRoundedRect(self._rect, 4, 4)
        painter.setFont(self._font())
        painter.setPen(QColor(theme.FOCUS if self.bold else theme.TEXT))
        painter.drawText(self._rect, Qt.AlignCenter, self.text)


class _Box(QGraphicsRectItem):
    """A toolpath's click target. Draws its own selection/focus/hover
    outline rather than Qt's dashed default."""

    def __init__(self, rect: QRectF, start: int) -> None:
        super().__init__(rect)
        self.start = start
        self.focused = False
        self.hovered = False
        self.tint: QColor | None = None
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        selected = self.isSelected()
        if self.tint is not None:
            painter.fillRect(self.rect(), self.tint)
        if selected:
            painter.fillRect(self.rect(), _alpha(theme.TEXT, 30))
        if selected or self.focused or self.hovered:
            colour = QColor(theme.FOCUS) if (selected or self.focused) else _alpha(theme.TEXT, 110)
            pen = QPen(colour, 2.5 if selected else 1.5)
            if self.focused and not selected:
                pen.setStyle(Qt.DashLine)
        else:
            pen = QPen(_alpha(theme.TEXT, 30), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(self.rect(), 1.5, 1.5)

    def hoverEnterEvent(self, event) -> None:
        self.hovered = True
        self.update()

    def hoverLeaveEvent(self, event) -> None:
        self.hovered = False
        self.update()


class BedMapView(QGraphicsView):
    selectionEdited = Signal(list)  # start lines the user selected on the map
    toolpathActivated = Signal(int)  # double-clicked toolpath's start line

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setBackgroundBrush(QColor(theme.CANVAS))
        self.setFocusPolicy(Qt.StrongFocus)
        # Panning is by dragging, so scroll bars would only take room.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scene().selectionChanged.connect(self._scene_selection_changed)
        self.info: dict[int, ToolpathInfo] = {}
        self._paths: dict[int, QGraphicsPathItem] = {}
        self._boxes: dict[int, _Box] = {}
        self._labels: dict[int, _Label] = {}
        self._starts: list[int] = []
        self._syncing = False
        self._pan_from: QPointF | None = None
        self._focus: int | None = None
        self._tooltips: dict[int, str] = {}
        # Re-fit on resize until the user zooms or pans themselves.
        self._auto_fit = True

    # -- content -----------------------------------------------------------------

    def load(self, program: GCodeProgram) -> None:
        """Draws every toolpath of `program`, unstyled."""
        self._syncing = True
        self.scene().clear()
        self._paths.clear()
        self._boxes.clear()
        self._labels.clear()
        self.info.clear()
        self._focus = None
        toolpaths = analyse_toolpaths(program)
        part_of = {tp.start_index: pt for pt in group_into_parts(toolpaths) for tp in pt.toolpaths}
        runs: dict[int, dict[int, list[QPointF]]] = defaultdict(lambda: defaultdict(list))
        for row in path_points(toolpaths):
            runs[row["toolpath"]][row["run"]].append(QPointF(row["x"], row["y"]))
        for tp in toolpaths:
            part = part_of.get(tp.start_index)
            self.info[tp.start_index] = ToolpathInfo(
                tp.start_index, tp.end_index, tp.name,
                part.name if part else "—", part.where if part else "",
            )
            if not runs.get(tp.start_index):
                continue
            path = QPainterPath()
            for points in runs[tp.start_index].values():
                path.moveTo(points[0])
                for p in points[1:]:
                    path.lineTo(p)
            item = QGraphicsPathItem(path)
            item.setAcceptedMouseButtons(Qt.NoButton)
            self.scene().addItem(item)
            self._paths[tp.start_index] = item
            box = _Box(path.boundingRect().adjusted(-BOX_MARGIN_MM, -BOX_MARGIN_MM, BOX_MARGIN_MM, BOX_MARGIN_MM),
                       tp.start_index)
            self.scene().addItem(box)
            self._boxes[tp.start_index] = box
            label = _Label()
            label.setPos(box.rect().center())
            label.setZValue(5)
            self.scene().addItem(label)
            self._labels[tp.start_index] = label
        self._starts = sorted(self.info)
        self._syncing = False
        self.set_styles({})
        self.fit()

    def refresh_theme(self) -> None:
        self.setBackgroundBrush(QColor(theme.CANVAS))
        for item in (*self._boxes.values(), *self._labels.values()):
            item.update()
        self.viewport().update()

    def has_paths(self) -> bool:
        return bool(self._boxes)

    def set_styles(self, styles: dict[int, ToolpathStyle], tooltips: dict[int, str] | None = None) -> None:
        """Styles each toolpath; any not in `styles` is drawn neutral."""
        self._tooltips = tooltips or {}
        default = ToolpathStyle()
        for start, item in self._paths.items():
            style = styles.get(start, default)
            pen = QPen(QColor(style.colour or theme.NEUTRAL), style.width)
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            if style.dashed:
                pen.setDashPattern([4, 3])
            item.setPen(pen)
            item.setZValue(2 if style.width > 1.5 else 1)
            self._labels[start].set_text(style.label, style.label_bold)
            box = self._boxes[start]
            box.tint = style.tint
            box.setToolTip(self._tooltip(start))
            box.update()

    def _tooltip(self, start: int) -> str:
        info = self.info[start]
        lines = [f"<b>{info.name}</b>", f"{info.part} · {info.where}" if info.where else info.part,
                 f"lines {info.start + 1}–{info.end + 1}"]
        if self._tooltips.get(start):
            lines.append(self._tooltips[start])
        return "<br>".join(lines)

    # -- selection and focus ------------------------------------------------------

    def selected(self) -> list[int]:
        return sorted(b.start for b in self._boxes.values() if b.isSelected())

    def set_selected(self, starts) -> None:
        """Mirrors a selection made elsewhere, without echoing it back."""
        wanted = set(starts)
        self._syncing = True
        for start, box in self._boxes.items():
            box.setSelected(start in wanted)
        self._syncing = False

    def _scene_selection_changed(self) -> None:
        if not self._syncing:
            self.selectionEdited.emit(self.selected())

    def set_focus(self, start: int | None, reveal: bool = False) -> None:
        """Outlines the toolpath the code cursor is in."""
        if start == self._focus:
            return
        for s in (self._focus, start):
            if s in self._boxes:
                self._boxes[s].focused = s == start
                self._boxes[s].update()
        self._focus = start
        if reveal and start in self._boxes:
            self.ensureVisible(self._boxes[start], 40, 40)

    def toolpath_at_line(self, line: int) -> int | None:
        """Start line of the toolpath containing program line `line`."""
        i = bisect.bisect_right(self._starts, line) - 1
        if i < 0:
            return None
        start = self._starts[i]
        return start if line <= self.info[start].end else None

    # -- view control ----------------------------------------------------------------

    def fit(self) -> None:
        self._auto_fit = True
        rect = self.scene().itemsBoundingRect()
        self.resetTransform()
        self.scale(1, -1)  # machine Y points up
        if not rect.isEmpty():
            self.fitInView(rect.adjusted(-6, -6, 6, 6), Qt.KeepAspectRatio)

    def apply_motion(self, m) -> None:
        """Pans (slide) and zooms (push/pull) from a SpaceMouse `Motion`."""
        self._auto_fit = False
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() + int(m.tx * 14))
        v.setValue(v.value() + int(m.ty * 14))
        factor = 1 - m.tz * 0.035
        zoom = abs(self.transform().m11())
        if 0.05 < zoom * factor < 200:
            self.scale(factor, factor)

    def wheelEvent(self, event) -> None:
        factor = 1.15 ** (event.angleDelta().y() / 120)
        zoom = abs(self.transform().m11())
        if 0.05 < zoom * factor < 200:
            self._auto_fit = False
            self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:
        if event.button() in (Qt.MiddleButton, Qt.RightButton):
            self._auto_fit = False
            self._pan_from = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_from is not None:
            delta = event.position() - self._pan_from
            self._pan_from = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_from is not None and event.button() in (Qt.MiddleButton, Qt.RightButton):
            self._pan_from = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._auto_fit:
            self.fit()

    def mouseDoubleClickEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        if isinstance(item, _Box):
            self.toolpathActivated.emit(item.start)
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_F:
            self.fit()
            return
        super().keyPressEvent(event)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawBackground(painter, rect)
        pen = QPen(_alpha(theme.TEXT, 16), 0)
        painter.setPen(pen)
        left = int(rect.left() // GRID_MM) * GRID_MM
        top = int(rect.top() // GRID_MM) * GRID_MM
        x = left
        while x < rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += GRID_MM
        y = top
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += GRID_MM
