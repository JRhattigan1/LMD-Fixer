"""The app icon, drawn in code so there's no image to keep in sync: a rounded
blue tile with a white zig-zag deposition path and the nozzle at its end.

`python -m lmd_fixer.desktop.icon OUT.ico` writes it as a Windows icon for
the build.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

SIZES = (16, 24, 32, 48, 64, 128, 256)


def icon_pixmap(size: int) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    tile = QRectF(8 * s, 8 * s, 240 * s, 240 * s)
    grad = QLinearGradient(tile.topLeft(), tile.bottomRight())
    grad.setColorAt(0, QColor("#0a84ff"))
    grad.setColorAt(1, QColor("#30c8ff"))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(tile, 56 * s, 56 * s)

    path = QPainterPath(QPointF(62 * s, 70 * s))
    for x, y in ((194, 70), (194, 112), (62, 112), (62, 154), (194, 154), (194, 196), (120, 196)):
        path.lineTo(x * s, y * s)
    pen = QPen(QColor("#ffffff"), max(1.5, 17 * s))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffd60a"))
    r = 20 * s
    p.drawEllipse(QPointF(120 * s, 196 * s), r, r)
    p.end()
    return pix


def app_icon() -> QIcon:
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(icon_pixmap(size))
    return icon


def main(argv: list[str]) -> int:
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(argv[:1])  # noqa: F841 (painting needs it)
    out = argv[1] if len(argv) > 1 else "icon.ico"
    ok = icon_pixmap(256).save(out)
    print(f"{'wrote' if ok else 'could not write'} {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
