"""Starts the desktop app: `lmd-fixer [FILE]`, `python -m lmd_fixer.desktop
[FILE]`, or the packaged `LMD Fixer.exe`."""

from __future__ import annotations

import datetime as dt
import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from lmd_fixer import __version__
from lmd_fixer.desktop.icon import app_icon
from lmd_fixer.desktop.main_window import MainWindow
from lmd_fixer.desktop.theme import DEFAULT_THEME, apply_theme


def log_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home()) / "LMD-Fixer" / "logs"
    return base / "lmd-fixer.log"


def install_crash_handler() -> None:
    """Unexpected errors go to a log file and a dialog: the packaged app has
    no console, so otherwise they'd vanish without trace."""

    def handle(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        path = log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(f"\n--- {dt.datetime.now():%Y-%m-%d %H:%M:%S} · LMD Fixer {__version__}\n{text}")
        except OSError:
            pass
        if sys.stderr:
            sys.stderr.write(text)
        if QApplication.instance() is not None:
            box = QMessageBox(QMessageBox.Critical, "Something went wrong",
                              f"LMD Fixer hit an unexpected error:\n\n{exc}\n\n"
                              f"The details were saved to {path}. Your review is still open; "
                              "if something looks wrong, save your work and restart the app.")
            box.setDetailedText(text)
            box.exec()

    sys.excepthook = handle


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("LMD Fixer")
    app.setOrganizationName("LMD-Fixer")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    install_crash_handler()
    apply_theme(app, QSettings().value("theme", DEFAULT_THEME))
    from lmd_fixer.desktop.view3d import prepare_window, preload

    window = MainWindow()
    prepare_window(window)  # before show(), or opening 3D recreates the window
    window.show()
    window.start_spacemouse()
    preload()
    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        window.open_file(files[0])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
