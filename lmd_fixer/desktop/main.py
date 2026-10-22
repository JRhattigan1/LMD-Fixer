"""Entry point for the desktop app: `python -m lmd_fixer.desktop [FILE]`."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from lmd_fixer import __version__
from lmd_fixer.desktop.main_window import MainWindow
from lmd_fixer.desktop.theme import apply_theme


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("LMD Fixer")
    app.setOrganizationName("LMD-Fixer")
    app.setApplicationVersion(__version__)
    apply_theme(app)
    window = MainWindow()
    window.show()
    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        window.open_file(files[0])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
