"""3Dconnexion SpaceMouse input, read straight from the device.

The app has no 3DxWare SDK integration, so it reads the device's HID reports
itself through `hidapi` (the `hid` module). Windows lets more than one
program read a multi-axis controller, so this works with 3DxWare running.
Polled on a timer in the GUI thread: each tick drains whatever reports have
arrived and emits the latest state, so a slow frame never builds a backlog.

Report layout (all 3Dconnexion devices): report 1 is translation x, y, z as
little-endian int16; report 2 is rotation x, y, z; report 3 is a button
bitmask. Newer devices send all six axes in one 13-byte report 1.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

VENDOR_IDS = {0x046D, 0x256F}  # Logitech-era 3Dconnexion, 3Dconnexion
MULTI_AXIS = (0x01, 0x08)  # HID usage page / usage: multi-axis controller
POLL_MS = 16
AXIS_MAX = 350.0  # typical full deflection


@dataclass
class Motion:
    tx: float = 0.0
    ty: float = 0.0
    tz: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0

    def is_idle(self, deadzone: float = 0.04) -> bool:
        return all(abs(v) < deadzone for v in (self.tx, self.ty, self.tz, self.rx, self.ry, self.rz))


def parse_report(report: list[int], motion: Motion) -> int | None:
    """Updates `motion` (axes scaled to about -1..1) from one HID report.
    Returns the button bitmask for a button report, else None."""
    if not report:
        return None
    rid, data = report[0], bytes(report[1:])
    if rid == 1 and len(data) >= 12:
        vals = struct.unpack_from("<6h", data)
        motion.tx, motion.ty, motion.tz, motion.rx, motion.ry, motion.rz = (v / AXIS_MAX for v in vals)
    elif rid == 1 and len(data) >= 6:
        motion.tx, motion.ty, motion.tz = (v / AXIS_MAX for v in struct.unpack_from("<3h", data))
    elif rid == 2 and len(data) >= 6:
        motion.rx, motion.ry, motion.rz = (v / AXIS_MAX for v in struct.unpack_from("<3h", data))
    elif rid == 3 and data:
        return int.from_bytes(data[:4], "little")
    return None


class SpaceMouse(QObject):
    """Emits `moved(Motion)` every tick while the cap is off-centre, and
    `buttonPressed(int)` (0-based) on each button press."""

    moved = Signal(object)
    buttonPressed = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.device = None
        self.name = ""
        self.motion = Motion()
        self._buttons = 0
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self) -> bool:
        """Opens the first SpaceMouse found. False (and quietly inactive) if
        there's none, or hidapi isn't installed."""
        try:
            import hid
        except ImportError:
            return False
        for info in hid.enumerate():
            if info["vendor_id"] in VENDOR_IDS and (info["usage_page"], info["usage"]) == MULTI_AXIS:
                try:
                    dev = hid.device()
                    dev.open_path(info["path"])
                    dev.set_nonblocking(True)
                except OSError:
                    continue
                self.device = dev
                self.name = info.get("product_string") or "SpaceMouse"
                self._timer.start()
                return True
        return False

    def stop(self) -> None:
        self._timer.stop()
        if self.device is not None:
            try:
                self.device.close()
            except OSError:
                pass
            self.device = None

    def _poll(self) -> None:
        try:
            for _ in range(64):  # drain everything queued since the last tick
                report = self.device.read(64)
                if not report:
                    break
                buttons = parse_report(report, self.motion)
                if buttons is not None:
                    pressed = buttons & ~self._buttons
                    self._buttons = buttons
                    for bit in range(32):
                        if pressed >> bit & 1:
                            self.buttonPressed.emit(bit)
        except OSError:
            self.stop()  # unplugged
            return
        # The device only reports changes; while the cap is held off centre,
        # keep moving at the last reported rate.
        if not self.motion.is_idle():
            self.moved.emit(self.motion)
