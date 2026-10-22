"""3D view of the program: deposition paths at their real Z, and optionally
the travel moves between them.

Drag to orbit, Ctrl-drag or middle-drag to pan, wheel to zoom; a click
(press and release without dragging) selects the toolpath under the cursor,
Ctrl-click adds or removes it, double-click toggles it like on the plan map.
It takes the same `ToolpathStyle`s and selection as the plan map, so it
always shows the same state.

Picking is done in screen space: every deposition segment is projected with
the camera's own matrices and the nearest one within a few pixels wins,
the one nearest the camera among near-ties, so the top layer of a stack is
picked first. The GL widget is only created the first time the view is
shown, so nothing touches OpenGL until someone looks at it.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMatrix4x4, QVector3D
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lmd_fixer.desktop import theme
from lmd_fixer.desktop.bed_map import NEUTRAL, ToolpathStyle
from lmd_fixer.gcode import GCodeProgram
from lmd_fixer.toolpaths import analyse_toolpaths, program_moves

# SpaceMouse response per poll tick at full deflection. Axes (3Dconnexion's
# raw HID frame): x right, y towards the user, z down; rotations about those.
SPACE_ORBIT_DEG = 2.5
SPACE_PAN_PX = 14.0
SPACE_ZOOM = 0.035
CLICK_SLOP_PX = 4  # movement below this between press and release is a click
PICK_TOL_PX = 8
TRAVEL_RGBA = (0.62, 0.66, 0.72, 0.35)
Z_SCALES = [1, 2, 5, 10]


def rgba(colour: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    c = QColor(colour)
    return (c.redF(), c.greenF(), c.blueF(), alpha)


def matrix_to_array(m: QMatrix4x4) -> np.ndarray:
    """QMatrix4x4 (stored column-major) as a row-major numpy array."""
    return np.array(m.data(), dtype=float).reshape(4, 4).T


def project(points: np.ndarray, mvp: np.ndarray, width: float, height: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Screen x/y (pixels, y down), depth (NDC z) and an in-front mask for
    (N, 3) world points."""
    h = np.c_[points, np.ones(len(points))]
    clip = h @ mvp.T
    w = clip[:, 3]
    front = w > 1e-9
    safe_w = np.where(front, w, 1.0)
    ndc = clip[:, :3] / safe_w[:, None]
    xy = np.c_[(ndc[:, 0] + 1) * 0.5 * width, (1 - ndc[:, 1]) * 0.5 * height]
    return xy, ndc[:, 2], front


def pick_segment(segments: np.ndarray, owners: np.ndarray, mvp: np.ndarray, width: float, height: float,
                 x: float, y: float, tolerance: float = PICK_TOL_PX) -> int | None:
    """The owner of the segment nearest screen point (x, y), or None if no
    segment passes within `tolerance` pixels. `segments` is (M, 2, 3) world
    coordinates, `owners` (M,) the toolpath each belongs to. Among segments
    within 2 px of the best distance, the one nearest the camera wins."""
    if len(segments) == 0:
        return None
    xy, depth, front = project(segments.reshape(-1, 3), mvp, width, height)
    a, b = xy[0::2], xy[1::2]
    ok = front[0::2] & front[1::2]
    p = np.array([x, y])
    ab = b - a
    denom = np.einsum("ij,ij->i", ab, ab)
    t = np.clip(np.einsum("ij,ij->i", p - a, ab) / np.where(denom > 0, denom, 1), 0, 1)
    closest = a + ab * t[:, None]
    dist = np.where(ok, np.linalg.norm(closest - p, axis=1), np.inf)
    best = dist.min()
    if not np.isfinite(best) or best > tolerance:
        return None
    near = np.flatnonzero(dist <= best + 2)
    seg_depth = np.minimum(depth[0::2], depth[1::2])[near]
    return int(owners[near[np.argmin(seg_depth)]])


class View3D(QWidget):
    selectionEdited = Signal(list)
    toolpathActivated = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.show_travel = QCheckBox("Travel moves")
        self.show_travel.setChecked(True)
        self.show_travel.setToolTip("Laser-off moves: rapids, plunges and retracts between and inside toolpaths")
        self.show_travel.toggled.connect(self._travel_toggled)
        self.z_scale = QComboBox()
        self.z_scale.addItems([f"Z ×{k}" for k in Z_SCALES])
        self.z_scale.setToolTip("Stretch heights, to pull stacked layers apart")
        self.z_scale.currentIndexChanged.connect(lambda _: self._rebuild())
        fit = QPushButton("Fit")
        fit.clicked.connect(self.fit)
        hint = QLabel("Drag orbits · Ctrl/middle-drag pans · wheel zooms · click selects")
        hint.setProperty("role", "dim")
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        for w in (self.show_travel, self.z_scale, fit):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(hint)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.addLayout(bar)
        self.placeholder = QLabel("")
        self.layout_.addWidget(self.placeholder, 1)

        self.gl = None  # created on first show
        self.deposition: dict[int, np.ndarray] = {}  # toolpath start -> (n, 2, 3) segments
        self.travel = np.zeros((0, 2, 3))
        self.boxes: dict[int, tuple[np.ndarray, np.ndarray]] = {}  # start -> (min xyz, max xyz)
        self.styles: dict[int, ToolpathStyle] = {}
        self._selected: set[int] = set()
        self._items: dict[int, object] = {}
        self._labels: list = []
        self._static: list = []
        self._selection_item = None
        self._travel_item = None
        self._needs_fit = True

    # -- data ----------------------------------------------------------------------

    def load(self, program: GCodeProgram) -> None:
        toolpaths = analyse_toolpaths(program)
        measured = set()
        self.deposition.clear()
        self.boxes.clear()
        for tp in toolpaths:
            segs = [(m.start_xyz, m.end_xyz) for m in tp.moves if m.start_xyz and m.end_xyz]
            measured.update(m.index for m in tp.moves)
            if segs:
                arr = np.array(segs, dtype=float)
                self.deposition[tp.start_index] = arr
                pts = arr.reshape(-1, 3)
                self.boxes[tp.start_index] = (pts.min(axis=0), pts.max(axis=0))
        travel = [(m.start_xyz, m.end_xyz) for m in program_moves(program)
                  if m.index not in measured and m.start_xyz and m.end_xyz and m.start_xyz != m.end_xyz]
        self.travel = np.array(travel, dtype=float) if travel else np.zeros((0, 2, 3))
        self._selected &= set(self.deposition)
        self._needs_fit = True
        self._rebuild()

    def segments_and_owners(self) -> tuple[np.ndarray, np.ndarray]:
        """Every deposition segment (scaled as drawn) and its toolpath."""
        if not self.deposition:
            return np.zeros((0, 2, 3)), np.zeros(0, dtype=int)
        starts = list(self.deposition)
        segs = np.concatenate([self._scaled(self.deposition[s]) for s in starts])
        owners = np.concatenate([np.full(len(self.deposition[s]), s) for s in starts])
        return segs, owners

    def _zk(self) -> float:
        return float(Z_SCALES[max(0, self.z_scale.currentIndex())])

    def _scaled(self, arr: np.ndarray) -> np.ndarray:
        k = self._zk()
        if k == 1:
            return arr
        out = arr.copy()
        out[..., 2] *= k
        return out

    # -- styling and selection (mirrors BedMapView) ----------------------------------

    def set_styles(self, styles: dict[int, ToolpathStyle], tooltips: dict[int, str] | None = None) -> None:
        self.styles = dict(styles)
        self._restyle()

    def selected(self) -> list[int]:
        return sorted(self._selected)

    def set_selected(self, starts) -> None:
        self._selected = set(starts) & set(self.deposition)
        self._draw_selection()

    # -- GL -------------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.gl is None:
            self._create_gl()

    def _create_gl(self) -> None:
        import pyqtgraph.opengl as gl

        class _GLView(gl.GLViewWidget):
            clicked = Signal(QPointF, bool)
            doubleClicked = Signal(QPointF)

            def mousePressEvent(self, ev):
                self._press = ev.position()
                super().mousePressEvent(ev)

            def mouseReleaseEvent(self, ev):
                press = getattr(self, "_press", None)
                if press is not None and ev.button() == Qt.LeftButton:
                    moved = ev.position() - press
                    if abs(moved.x()) + abs(moved.y()) < CLICK_SLOP_PX:
                        self.clicked.emit(ev.position(), bool(ev.modifiers() & Qt.ControlModifier))
                self._press = None
                super().mouseReleaseEvent(ev)

            def mouseDoubleClickEvent(self, ev):
                self.doubleClicked.emit(ev.position())

        self.gl = _GLView()
        self.gl.setBackgroundColor(QColor(theme.SURFACE))
        self.gl.clicked.connect(self._clicked)
        self.gl.doubleClicked.connect(self._double_clicked)
        self.layout_.replaceWidget(self.placeholder, self.gl)
        self.placeholder.deleteLater()
        self._rebuild()

    def _rebuild(self) -> None:
        """Recreates every GL item from the data (after a load or Z change)."""
        if self.gl is None:
            return
        import pyqtgraph.opengl as gl

        for item in [*self._items.values(), *self._labels, *self._static,
                     self._selection_item, self._travel_item]:
            if item is not None:
                self.gl.removeItem(item)
        self._items.clear()
        self._labels.clear()
        self._static.clear()
        self._selection_item = self._travel_item = None
        if not self.deposition and not len(self.travel):
            return

        lo = np.min([b[0] for b in self.boxes.values()], axis=0) if self.boxes else np.zeros(3)
        hi = np.max([b[1] for b in self.boxes.values()], axis=0) if self.boxes else np.ones(3)
        grid = gl.GLGridItem(color=(255, 255, 255, 22))
        size = max(hi[0] - lo[0], hi[1] - lo[1]) + 40
        grid.setSize(size, size, 1)
        grid.setSpacing(10, 10, 1)
        grid.translate((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, 0)
        self.gl.addItem(grid)
        self._static.append(grid)

        self._travel_item = gl.GLLinePlotItem(
            pos=self._scaled(self.travel).reshape(-1, 3) if len(self.travel) else np.zeros((0, 3)),
            color=TRAVEL_RGBA, width=1, mode="lines", antialias=True)
        self._travel_item.setVisible(self.show_travel.isChecked())
        self.gl.addItem(self._travel_item)
        for start, segs in self.deposition.items():
            item = gl.GLLinePlotItem(pos=self._scaled(segs).reshape(-1, 3), mode="lines", antialias=True)
            self.gl.addItem(item)
            self._items[start] = item
        self._restyle()
        if self._needs_fit:
            self.fit()

    def _restyle(self) -> None:
        if self.gl is None:
            return
        import pyqtgraph.opengl as gl

        for label in self._labels:
            self.gl.removeItem(label)
        self._labels.clear()
        default = ToolpathStyle()
        font = QFont()
        font.setPointSizeF(9)
        for start, item in self._items.items():
            style = self.styles.get(start, default)
            width = style.width + (1.5 if start in self._selected else 0)
            item.setData(color=rgba(style.colour if style.colour else NEUTRAL), width=width)
            if style.label:
                lo, hi = self.boxes[start]
                pos = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, hi[2] * self._zk() + 2])
                bold = QFont(font)
                bold.setBold(style.label_bold)
                label = gl.GLTextItem(pos=pos, text=style.label, font=bold,
                                      color=QColor(theme.FOCUS if style.label_bold else theme.TEXT))
                self.gl.addItem(label)
                self._labels.append(label)
        self._draw_selection()

    def _draw_selection(self) -> None:
        if self.gl is None:
            return
        import pyqtgraph.opengl as gl

        if self._selection_item is not None:
            self.gl.removeItem(self._selection_item)
            self._selection_item = None
        edges = []
        k = self._zk()
        for start in self._selected:
            lo, hi = self.boxes[start]
            lo = lo - [1.5, 1.5, 0]
            hi = hi + [1.5, 1.5, 0]
            lo_z, hi_z = lo[2] * k, hi[2] * k + 0.5
            corners = [(x, y, z) for z in (lo_z, hi_z) for x, y in
                       ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1]))]
            for i in range(4):
                for a, b in ((i, (i + 1) % 4), (i + 4, (i + 1) % 4 + 4), (i, i + 4)):
                    edges += [corners[a], corners[b]]
        if edges:
            self._selection_item = gl.GLLinePlotItem(pos=np.array(edges), color=rgba(theme.FOCUS, 0.9),
                                                     width=1.5, mode="lines", antialias=True)
            self.gl.addItem(self._selection_item)
        # Selected toolpaths are also drawn thicker.
        default = ToolpathStyle()
        for start, item in self._items.items():
            style = self.styles.get(start, default)
            item.setData(width=style.width + (1.5 if start in self._selected else 0))

    def fit(self) -> None:
        if self.gl is None or not self.boxes:
            return
        lo = np.min([b[0] for b in self.boxes.values()], axis=0)
        hi = np.max([b[1] for b in self.boxes.values()], axis=0)
        k = self._zk()
        centre = QVector3D(float((lo[0] + hi[0]) / 2), float((lo[1] + hi[1]) / 2), float(hi[2] * k / 2))
        extent = float(max(hi[0] - lo[0], hi[1] - lo[1], (hi[2] - lo[2]) * k, 10))
        self.gl.setCameraPosition(pos=centre, distance=extent * 1.25, elevation=35, azimuth=-70)
        self._needs_fit = False

    def apply_motion(self, m) -> None:
        """Moves the camera from a SpaceMouse `Motion`: twist turns the bed,
        tilting forward/back changes the viewing angle, sliding pans, lifting
        or pressing pans up/down, pushing/pulling zooms."""
        if self.gl is None:
            return
        self.gl.orbit(-m.rz * SPACE_ORBIT_DEG, -m.rx * SPACE_ORBIT_DEG)
        self.gl.pan(m.tx * SPACE_PAN_PX, -m.tz * SPACE_PAN_PX, 0, relative="view")
        self.gl.opts["distance"] = max(1.0, self.gl.opts["distance"] * (1 + m.ty * SPACE_ZOOM))
        self.gl.update()

    def toggle_travel(self) -> None:
        self.show_travel.setChecked(not self.show_travel.isChecked())

    def _travel_toggled(self, on: bool) -> None:
        if self._travel_item is not None:
            self._travel_item.setVisible(on)

    # -- picking -----------------------------------------------------------------------

    def pick(self, pos: QPointF) -> int | None:
        if self.gl is None:
            return None
        w, h = self.gl.width(), self.gl.height()
        view = (0, 0, w, h)
        mvp = matrix_to_array(self.gl.projectionMatrix(view, view) * self.gl.viewMatrix())
        segs, owners = self.segments_and_owners()
        return pick_segment(segs, owners, mvp, w, h, pos.x(), pos.y())

    def _clicked(self, pos: QPointF, additive: bool) -> None:
        start = self.pick(pos)
        if additive:
            if start is None:
                return
            self._selected ^= {start}
        else:
            self._selected = {start} if start is not None else set()
        self._draw_selection()
        self.selectionEdited.emit(self.selected())

    def _double_clicked(self, pos: QPointF) -> None:
        start = self.pick(pos)
        if start is not None:
            self.toolpathActivated.emit(start)
