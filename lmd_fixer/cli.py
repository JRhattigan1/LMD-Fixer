"""Entry point: `lmd-fixer [FILE]` (and the packaged `LMD Fixer.exe`) starts
the desktop app.

Also handles what a packaged build needs that a checkout doesn't: a settings
file the user can edit, and `--selftest`, which checks a build works.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from lmd_fixer import __version__
from lmd_fixer.settings import DEFAULT_SETTINGS_PATH, SETTINGS_ENV_VAR


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _use_editable_settings() -> None:
    """Points the app at a `fix_settings.toml` the user can edit.

    In a packaged build the bundled copy sits among the app's internal files,
    so use one beside the .exe (where the build already puts one), creating
    it from the bundled default if it's missing — or under %APPDATA% if the
    app's folder can't be written to.
    """
    if os.environ.get(SETTINGS_ENV_VAR):
        return
    folders = [
        Path(sys.executable).parent,
        Path(os.environ.get("APPDATA", Path.home())) / "LMD-Fixer",
    ]
    for folder in folders:
        target = folder / DEFAULT_SETTINGS_PATH.name
        try:
            if not target.exists():
                folder.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(DEFAULT_SETTINGS_PATH, target)
        except OSError:
            continue
        os.environ[SETTINGS_ENV_VAR] = str(target)
        return


def _selftest(args: list[str]) -> int:
    """`--selftest [INPUT OUTPUT] [--report FILE]`.

    Checks the window builds (offscreen) and that the 3D and SpaceMouse
    modules load; then, given files, opens INPUT in the window and runs every
    fix in pipeline order with its defaults (every change accepted except
    section removal), writing the result to OUTPUT as Save would. The build
    compares that output from the .exe with the same run from source.

    A packaged app has no console, so `--report FILE` writes the log to a
    file too. The exit code is 0 only if every check passed.
    """
    report_path = None
    if "--report" in args:
        i = args.index("--report")
        report_path = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    lines: list[str] = []

    def say(text: str) -> None:
        lines.append(text)
        if sys.stdout:
            print(text, flush=True)

    def finish(code: int) -> int:
        if report_path:
            Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return code

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    say(f"LMD Fixer {__version__} self-test")
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        from lmd_fixer.desktop.main_window import MainWindow
        from lmd_fixer.desktop.theme import THEME_NAMES, apply_theme

        app = QApplication.instance() or QApplication([sys.argv[0]])
        for name in THEME_NAMES:
            apply_theme(app, name)
        settings = QSettings(str(Path(tempfile.mkdtemp()) / "selftest.ini"), QSettings.IniFormat)
        window = MainWindow(settings)
        # Nobody is there to dismiss a dialog: a modal one would hang the
        # self-test (and the build waiting on it) forever.
        errors: list[str] = []
        window.show_error = lambda title, text: errors.append(f"{title}: {text}")
        window.ask = lambda title, text: True
        window.confirm_restart = lambda: True
        say("ok   window builds, in every theme")
        import hid  # noqa: F401
        import OpenGL.GL  # noqa: F401
        import pyqtgraph.opengl  # noqa: F401
        say("ok   3D view and SpaceMouse modules load")
    except Exception as exc:  # report it rather than crash: there's no console
        say(f"FAIL {type(exc).__name__}: {exc}")
        return finish(1)

    if not args:
        return finish(0)
    if len(args) != 2:
        say("usage: --selftest [INPUT OUTPUT] [--report FILE]")
        return finish(2)

    from lmd_fixer.export import write_program
    from lmd_fixer.gcode import GCodeProgram
    from lmd_fixer.pipeline import FIX_ORDER
    from lmd_fixer.review import ReviewSession

    source, dest = args
    try:
        if not window.open_file(source) or window.session is None:
            say(f"FAIL the window couldn't open {source}" + (f" ({errors[-1]})" if errors else ""))
            return finish(1)
        say(f"ok   window opens {Path(source).name}: {len(window.session.original.lines):,} lines, "
            f"{len(window.map.info)} toolpaths on the map")
        # Show the 3D tab, so OpenGL really starts: the frozen app has to
        # bundle PyOpenGL's platform support for this to work.
        window.show()
        window.map_tabs.setCurrentWidget(window.view3d)
        for _ in range(3):  # the GL view is created on a timer after the tab shows
            QApplication.processEvents()
        window.view3d.gl.grabFramebuffer()  # a real frame: shaders compile and buffers upload
        if not window.view3d.drawn_toolpaths():
            say("FAIL the 3D view didn't draw")
            return finish(1)
        say(f"ok   3D view draws {window.view3d.drawn_toolpaths()} toolpaths")
        # Through the same state machine the window drives, applying each
        # step with its fix's defaults.
        session = ReviewSession(GCodeProgram.from_file(source), FIX_ORDER)
        while not session.finished:
            fix_id = session.fix_id
            n_changes = len(session.proposal().changes)
            session.apply()
            say(f"ok   {fix_id}: {n_changes} change(s)")
        write_program(session.result(), dest)
        say(f"ok   wrote {dest}: {len(session.result().lines):,} lines")
    except Exception as exc:
        say(f"FAIL {type(exc).__name__}: {exc}")
        return finish(1)
    return finish(0)


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--version"]:
        if sys.stdout:
            print(__version__)
        return
    if _is_frozen():
        _use_editable_settings()
    if args[:1] == ["--selftest"]:
        sys.exit(_selftest(args[1:]))
    from lmd_fixer.desktop.main import main as desktop_main

    sys.exit(desktop_main([sys.argv[0], *args]))


if __name__ == "__main__":
    main()
