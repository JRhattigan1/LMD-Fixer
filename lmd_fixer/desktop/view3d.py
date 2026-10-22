"""3D view of the program: deposition paths at their real Z, and optionally
the travel moves between them.

Drag to orbit, Ctrl-drag or middle-drag to pan, wheel to zoom; a click
(press and release without dragging) selects the toolpath under the cursor,
Ctrl-click adds or removes it, double-click toggles it like on the plan map.
It takes the same `ToolpathStyle`s and selection as the plan map, so it
always shows the same state.

Built to stay cheap however much is on the bed:

- **A handful of GL items, whatever the toolpath count.** Every deposition
  path is in one line item with per-vertex colours; an "emphasis" item
  redraws the selected and changed toolpaths wider; travel, the selection
  box and the grid are one item each; all labels are drawn by one item in a
  single QPainter pass. Restyling or selecting only recolours arrays and
  updates those items in place — nothing is created or destroyed.
- **Nothing happens while hidden.** Loads, styles and selections are stored
  and applied when the 3D tab is next shown.
- **It survives a new GL context.** pyqtgraph caches compiled shaders and
  vertex buffers per process, not per context, so when Qt recreates the
  context (e.g. the window moving to another screen) every draw failed with
  "invalid value", every frame, and printing those errors froze the app.
  `_GLView.initializeGL` clears pyqtgraph's shader cache and the items are
  rebuilt for the new context.

Picking is done in screen space: every deposition segment is projected with
the camera's own matrices and the nearest one within a few pixels wins,
the one nearest the camera among near-ties, so the top layer of a stack is
picked first. The GL widget is only created the first time the view is
shown, so nothing touches OpenGL until someone looks at it.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QMatrix4x4, QPainter, QVector3D
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lmd_fixer.desktop import theme
from lmd_fixer.desktop.bed_map import ToolpathStyle
from lmd_fixer.gcode import GCodeProgram
from lmd_fixer.toolpaths import analyse_toolpaths, program_moves

# SpaceMouse response per poll tick at full deflection. Axes (3Dconnexion's
# raw HID frame): x right, y towards the user, z down; rotations about those.
SPACE_ORBIT_DEG = 2.5
SPACE_PAN_PX = 14.0
SPACE_ZOOM = 0.035
CLICK_SLOP_PX = 4  # movement below this between press and release is a click
PICK_TOL_PX = 8
Z_SCALES = [1, 2, 5, 10]
BASE_WIDTH = 1.5
EMPHASIS_WIDTH = 3.0


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


def preload() -> None:
    """Imports pyqtgraph's OpenGL support on a background thread, so it's
    ready before anyone opens the 3D tab (importing it takes 1-3 s, which
    used to freeze the window the first time 3D was shown). Pure imports
    only — no Qt objects are created here. If the tab is opened first, the
    import lock simply makes it wait for this to finish."""
    import threading

    def load() -> None:
        try:
            import pyqtgraph.opengl  # noqa: F401
        except Exception:  # the tab will report it properly when opened
            pass

    threading.Thread(target=load, name="preload-3d", daemon=True).start()


def reset_pyqtgraph_gl_cache() -> None:
    """Forgets pyqtgraph's compiled shaders, which belong to whichever GL
    context compiled them; the next draw compiles them for the current one.
    They're cached in two places: the shared ShaderProgram table, and a
    `_shaderProgram` class attribute on item classes such as GLLinePlotItem."""
    import pyqtgraph.opengl as gl
    from pyqtgraph.opengl import shaders

    for program in shaders.ShaderProgram.names.values():
        program.prog = None
        for shader in program.shaders:
            shader.compiled = None
    for name in dir(gl):
        cls = getattr(gl, name)
        if isinstance(cls, type) and "_shaderProgram" in vars(cls):
            cls._shaderProgram = None


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
        self.z_scale.currentIndexChanged.connect(self._z_changed)
        fit = QPushButton("Fit")
        fit.setProperty("flat", True)
        fit.clicked.connect(self.fit)
        self.setToolTip("Drag orbits · Ctrl/middle-drag pans · wheel zooms · click selects · "
                        "SpaceMouse moves the view")
        bar = QHBoxLayout()
        bar.setContentsMargins(10, 2, 10, 2)
        bar.addWidget(self.show_travel)
        bar.addStretch(1)
        bar.addWidget(self.z_scale)
        bar.addWidget(fit)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.addLayout(bar)
        self.placeholder = QLabel("Loading the 3D view…")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setProperty("role", "dim")
        self.layout_.addWidget(self.placeholder, 1)

        self.gl = None  # created on first show
        # The program's geometry, as loaded (Z unscaled).
        self.deposition: dict[int, np.ndarray] = {}  # toolpath start -> (n, 2, 3) segments
        self.travel = np.zeros((0, 2, 3), dtype=np.float32)
        self.boxes: dict[int, tuple[np.ndarray, np.ndarray]] = {}  # start -> (min xyz, max xyz)
        self.styles: dict[int, ToolpathStyle] = {}
        self._selected: set[int] = set()
        # Flattened for drawing and picking (rebuilt on load / Z change).
        self._order: list[int] = []
        self._vertex_range: dict[int, tuple[int, int]] = {}
        self._pos = np.zeros((0, 3), dtype=np.float32)
        self._owners = np.zeros(0, dtype=np.int64)  # per segment
        # GL items (created once per context).
        self._base = self._emphasis = self._travel_item = self._selection_item = None
        self._labels = self._grid = None
        self._dirty = {"geometry", "style", "selection"}
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
                arr = np.asarray(segs, dtype=np.float32)
                self.deposition[tp.start_index] = arr
                pts = arr.reshape(-1, 3)
                self.boxes[tp.start_index] = (pts.min(axis=0), pts.max(axis=0))
        travel = [(m.start_xyz, m.end_xyz) for m in program_moves(program)
                  if m.index not in measured and m.start_xyz and m.end_xyz and m.start_xyz != m.end_xyz]
        self.travel = np.asarray(travel, dtype=np.float32) if travel else np.zeros((0, 2, 3), dtype=np.float32)
        self._selected &= set(self.deposition)
        self._needs_fit = True
        self._flatten()
        self._dirty |= {"geometry", "style", "selection"}
        self._sync()

    def _flatten(self) -> None:
        """Every deposition segment in one (scaled) array, with each toolpath's
        vertex range, for drawing and picking."""
        self._order = list(self.deposition)
        k = self._zk()
        chunks, owners, start = [], [], 0
        self._vertex_range.clear()
        for s in self._order:
            segs = self.deposition[s]
            n = len(segs) * 2
            self._vertex_range[s] = (start, start + n)
            start += n
            chunks.append(segs.reshape(-1, 3))
            owners.append(np.full(len(segs), s, dtype=np.int64))
        self._pos = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros((0, 3), dtype=np.float32)
        if k != 1:
            self._pos[:, 2] *= k
        self._owners = np.concatenate(owners) if owners else np.zeros(0, dtype=np.int64)

    def drawn_toolpaths(self) -> int:
        """How many toolpaths are on screen (0 until the GL view exists and
        has its geometry)."""
        if self.gl is None or self._base is None or self._base.pos is None or not len(self._base.pos):
            return 0
        return len(self._order)

    def segments_and_owners(self) -> tuple[np.ndarray, np.ndarray]:
        """Every deposition segment (scaled as drawn) and its toolpath."""
        return self._pos.reshape(-1, 2, 3), self._owners

    def _zk(self) -> float:
        return float(Z_SCALES[max(0, self.z_scale.currentIndex())])

    def _scaled(self, arr: np.ndarray) -> np.ndarray:
        k = self._zk()
        if k == 1:
            return arr
        out = np.array(arr, dtype=np.float32, copy=True)
        out[..., 2] *= k
        return out

    # -- styling and selection (mirrors BedMapView) ----------------------------------

    def set_styles(self, styles: dict[int, ToolpathStyle], tooltips: dict[int, str] | None = None) -> None:
        self.styles = dict(styles)
        self._dirty.add("style")
        self._sync()

    def selected(self) -> list[int]:
        return sorted(self._selected)

    def set_selected(self, starts) -> None:
        wanted = set(starts) & set(self.deposition)
        if wanted != self._selected:
            self._selected = wanted
            self._dirty |= {"selection", "style"}  # selected paths are drawn wider
            self._sync()

    # -- GL -------------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.gl is None:
            # Let the placeholder paint before the (slow, first-time) import.
            QTimer.singleShot(0, self._create_gl)
        else:
            self._sync()

    def _create_gl(self) -> None:
        if self.gl is not None:
            return
        import pyqtgraph.opengl as gl

        view3d = self

        class _GLView(gl.GLViewWidget):
            clicked = Signal(QPointF, bool)
            doubleClicked = Signal(QPointF)

            def initializeGL(self):
                # A new context: shaders and buffers from any earlier one are
                # invalid here (see the module docstring).
                reset_pyqtgraph_gl_cache()
                super().initializeGL()
                if view3d._base is not None:
                    # Items from the old context hold buffers that mean
                    # nothing in this one: replace them before anything draws.
                    view3d._recreate_items()

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
        self.gl.setBackgroundColor(QColor(theme.CANVAS))
        self.gl.clicked.connect(self._clicked)
        self.gl.doubleClicked.connect(self._double_clicked)
        self.layout_.replaceWidget(self.placeholder, self.gl)
        self.placeholder.deleteLater()
        self._create_items()
        self._dirty |= {"geometry", "style", "selection"}
        self._sync()

    def _create_items(self) -> None:
        import pyqtgraph.opengl as gl

        self._grid = gl.GLGridItem()
        self._travel_item = gl.GLLinePlotItem(mode="lines", width=1, antialias=True)
        self._base = gl.GLLinePlotItem(mode="lines", width=BASE_WIDTH, antialias=True)
        self._emphasis = gl.GLLinePlotItem(mode="lines", width=EMPHASIS_WIDTH, antialias=True)
        self._selection_item = gl.GLLinePlotItem(mode="lines", width=1.5, antialias=True)
        self._labels = _LabelLayer()
        self._labels.setDepthValue(10)  # drawn last, over everything
        for item in (self._grid, self._travel_item, self._base, self._emphasis, self._selection_item,
                     self._labels):
            self.gl.addItem(item)

    def _recreate_items(self) -> None:
        """After a context change: drop the old items' (now invalid) buffers."""
        if self.gl is None:
            return
        for item in (self._grid, self._travel_item, self._base, self._emphasis, self._selection_item,
                     self._labels):
            if item is not None and item in self.gl.items:
                self.gl.removeItem(item)
        self._create_items()
        self._dirty |= {"geometry", "style", "selection"}
        self._sync()

    def _sync(self) -> None:
        """Applies whatever changed since the last time the view was shown."""
        # _base is None between the GL view being placed (which re-shows this
        # widget) and its items being created.
        if self.gl is None or self._base is None or not self.isVisible() or not self._dirty:
            return
        dirty, self._dirty = self._dirty, set()
        if "geometry" in dirty:
            self._apply_geometry()
        if "style" in dirty:
            self._apply_style()
        if "selection" in dirty:
            self._apply_selection()
        if self._needs_fit:
            self.fit()

    def _empty(self) -> np.ndarray:
        return np.zeros((0, 3), dtype=np.float32)

    def _apply_geometry(self) -> None:
        travel = self._scaled(self.travel).reshape(-1, 3) if len(self.travel) else self._empty()
        self._travel_item.setData(pos=travel, color=rgba(theme.TRAVEL, 0.4))
        self._travel_item.setVisible(self.show_travel.isChecked() and len(travel) > 0)
        self._base.setData(pos=self._pos if len(self._pos) else self._empty())
        self._grid.resetTransform()
        if self.boxes:
            lo = np.min([b[0] for b in self.boxes.values()], axis=0)
            hi = np.max([b[1] for b in self.boxes.values()], axis=0)
            size = float(max(hi[0] - lo[0], hi[1] - lo[1]) + 40)
            self._grid.setSize(size, size, 1)
            self._grid.setSpacing(10, 10, 1)
            self._grid.translate(float(lo[0] + hi[0]) / 2, float(lo[1] + hi[1]) / 2, 0)
        g = QColor(theme.TEXT)
        self._grid.setColor((g.red(), g.green(), g.blue(), 26))

    def _apply_style(self) -> None:
        """Recolours the one deposition item and refills the emphasis item and
        labels — arrays only, no GL objects created."""
        default = rgba(theme.NEUTRAL)
        colours = np.empty((len(self._pos), 4), dtype=np.float32)
        emph_pos, emph_col, labels = [], [], []
        k = self._zk()
        for s in self._order:
            a, b = self._vertex_range[s]
            style = self.styles.get(s)
            colour = rgba(style.colour) if style and style.colour else default
            colours[a:b] = colour
            if (style and style.width > BASE_WIDTH) or s in self._selected:
                emph_pos.append(self._pos[a:b])
                emph_col.append(colours[a:b])
            if style and style.label:
                lo, hi = self.boxes[s]
                labels.append(((float(lo[0] + hi[0]) / 2, float(lo[1] + hi[1]) / 2, float(hi[2] * k + 2)),
                               style.label, style.label_bold))
        if len(colours):
            self._base.setData(color=colours)
        if emph_pos:
            self._emphasis.setData(pos=np.concatenate(emph_pos), color=np.concatenate(emph_col))
            self._emphasis.setVisible(True)
        else:
            self._emphasis.setVisible(False)
        self._labels.set_labels(labels)

    def _apply_selection(self) -> None:
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
            self._selection_item.setData(pos=np.asarray(edges, dtype=np.float32), color=rgba(theme.FOCUS, 0.9))
            self._selection_item.setVisible(True)
        else:
            self._selection_item.setVisible(False)

    def refresh_theme(self) -> None:
        if self.gl is not None:
            self.gl.setBackgroundColor(QColor(theme.CANVAS))
        self._dirty |= {"geometry", "style", "selection"}
        self._sync()

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

    def _z_changed(self, _index: int) -> None:
        self._flatten()
        self._dirty |= {"geometry", "style", "selection"}
        self._sync()

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
            self._travel_item.setVisible(on and len(self.travel) > 0)

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
            selection = self._selected ^ {start}
        else:
            selection = {start} if start is not None else set()
        self.set_selected(selection)
        self.selectionEdited.emit(self.selected())

    def _double_clicked(self, pos: QPointF) -> None:
        start = self.pick(pos)
        if start is not None:
            self.toolpathActivated.emit(start)


def _label_layer_base():
    import pyqtgraph.opengl as gl

    return gl.GLGraphicsItem.GLGraphicsItem


class _LabelLayer:
    """Placeholder replaced on first use by the real GL item class (pyqtgraph
    is imported lazily, so the class can't be defined at import time)."""

    def __new__(cls, *args, **kwargs):
        return _make_label_layer_class()(*args, **kwargs)


_LABEL_LAYER_CLASS = None


def _make_label_layer_class():
    global _LABEL_LAYER_CLASS
    if _LABEL_LAYER_CLASS is not None:
        return _LABEL_LAYER_CLASS
    base = _label_layer_base()

    class LabelLayer(base):
        """Every label in one QPainter pass, each on a pill so it reads over
        the paths. (pyqtgraph's GLTextItem opens a QPainter per label per
        frame, which is what made restyling and orbiting slow.)"""

        def __init__(self):
            super().__init__()
            self._points = np.zeros((0, 3))
            self._text: list[tuple[str, bool]] = []

        def set_labels(self, labels) -> None:
            self._points = np.asarray([p for p, _, _ in labels], dtype=float).reshape(-1, 3)
            self._text = [(t, b) for _, t, b in labels]
            self.update()

        def paint(self):
            view = self.view()
            if view is None or not self._text:
                return
            w, h = view.width(), view.height()
            vp = (0, 0, w, h)
            mvp = matrix_to_array(view.projectionMatrix(vp, vp) * view.viewMatrix())
            xy, _, front = project(self._points, mvp, w, h)
            painter = QPainter(view)
            painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
            fonts = {}
            for bold in (False, True):
                f = QFont()
                f.setPointSizeF(8.5)
                f.setBold(bold)
                fonts[bold] = (f, QFontMetricsF(f))
            pill = QColor(theme.CANVAS)
            pill.setAlpha(215)
            for (x, y), ok, (text, bold) in zip(xy, front, self._text):
                if not ok or not (-50 < x < w + 50 and -20 < y < h + 20):
                    continue
                font, metrics = fonts[bold]
                tw, th = metrics.horizontalAdvance(text) + 8, metrics.height() + 2
                rect = QRectF(x - tw / 2, y - th / 2, tw, th)
                painter.setPen(Qt.NoPen)
                painter.setBrush(pill)
                painter.drawRoundedRect(rect, 4, 4)
                painter.setFont(font)
                painter.setPen(QColor(theme.FOCUS if bold else theme.TEXT))
                painter.drawText(rect, Qt.AlignCenter, text)
            painter.end()

    _LABEL_LAYER_CLASS = LabelLayer
    return LabelLayer
