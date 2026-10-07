"""Console entry point: `lmd-fixer` launches the Streamlit UI.

It's also the entry point of the packaged Windows build (`packaging/`), which
is why it handles things `streamlit run` from a checkout doesn't need: finding
a free port, giving the user a settings file they can edit, opening the
browser itself, and a `--selftest` that checks a build works without one.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

from lmd_fixer import __version__
from lmd_fixer.settings import DEFAULT_SETTINGS_PATH, SETTINGS_ENV_VAR

APP_PATH = Path(__file__).parent / "app.py"
DEFAULT_PORT = 8501

# Mirrors .streamlit/config.toml, which pip-installed runs don't have
# (Streamlit only reads it from the working directory).
CONFIG_ARGS = [
    "--theme.base", "dark",
    "--theme.primaryColor", "#00d86c",
    "--theme.backgroundColor", "#272b33",
    "--theme.secondaryBackgroundColor", "#192231",
    "--theme.textColor", "#c2c7d1",
    "--server.maxUploadSize", "100",
]

# Running as a local app rather than a development server.
LOCAL_APP_ARGS = [
    # Only this PC can reach it. By default Streamlit listens on every network
    # interface, which would let anyone on the network open the uploaded files.
    "--server.address", "127.0.0.1",
    # Headless skips Streamlit's first-run email prompt, which would leave a
    # double-clicked .exe waiting on console input; the browser is opened by
    # `_open_browser_when_ready` instead.
    "--server.headless", "true",
    "--browser.gatherUsageStats", "false",
    # Hides the Deploy button and developer menu entries.
    "--client.toolbarMode", "viewer",
    # A frozen build has no site-packages, so Streamlit takes it for a source
    # checkout of Streamlit itself and starts in development mode, which
    # ignores --server.port.
    "--global.developmentMode", "false",
]


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _port_is_free(port: int) -> bool:
    # Connecting catches a server bound to 0.0.0.0, which a bind to 127.0.0.1
    # alone can miss on Windows.
    with socket.socket() as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _free_port(start: int = DEFAULT_PORT, attempts: int = 50) -> int:
    """The first free port from `start`, so a second copy of the app (or a
    dev server already on 8501) doesn't stop this one starting."""
    for port in range(start, start + attempts):
        if _port_is_free(port):
            return port
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _requested_port(args: list[str]) -> int | None:
    for i, arg in enumerate(args):
        if arg.startswith("--server.port="):
            return int(arg.split("=", 1)[1])
        if arg == "--server.port" and i + 1 < len(args):
            return int(args[i + 1])
    return None


def _open_browser_when_ready(url: str, timeout_s: float = 60.0) -> None:
    """Opens `url` once the server answers its health check — opening it
    straight away shows a connection error while Streamlit is still starting."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=1) as resp:
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            pass
        time.sleep(0.25)


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
    """`--selftest [INPUT OUTPUT]`: checks the app loads, then (given files)
    runs every fix in pipeline order, accepting everything except section
    removal, and writes the result as the app's download would. Comparing
    that output from a build against the same run from a checkout shows the
    build behaves identically."""
    from streamlit import config, logger
    from streamlit.testing.v1 import AppTest

    # Same reason as in LOCAL_APP_ARGS: a frozen build otherwise runs in
    # development mode and logs at debug level. Set directly because Streamlit
    # only reads STREAMLIT_* environment variables via `streamlit run`. Errors
    # still surface through `at.exception`.
    config.set_option("global.developmentMode", False)
    logger.set_log_level("error")

    from lmd_fixer.gcode import GCodeProgram
    from lmd_fixer.pipeline import FIX_ORDER, apply_accepted_changes, run_fix

    print(f"LMD Fixer v{__version__} self-test")
    at = AppTest.from_file(str(APP_PATH), default_timeout=120).run()
    if at.exception:
        for exc in at.exception:
            print(f"FAIL: app raised {exc.message}")
        return 1
    print("ok   app loads and renders its start page")

    if not args:
        return 0
    if len(args) != 2:
        print("usage: --selftest [INPUT OUTPUT]")
        return 2
    source, dest = args
    program = GCodeProgram.from_file(source)
    print(f"ok   read {source}: {len(program.lines):,} lines")
    for fix_id in FIX_ORDER:
        # Accepting every proposed section removal would delete every section.
        if fix_id == "remove_named_sections":
            continue
        result = run_fix(program, fix_id).result
        program = apply_accepted_changes(program, result, {c.original_index for c in result.changes})
        print(f"ok   {fix_id}: {len(result.changes)} change(s)")
    Path(dest).write_bytes(program.to_text("\r\n").encode("utf-8"))
    print(f"ok   wrote {dest}: {len(program.lines):,} lines")
    return 0


def _run(args: list[str]) -> int:
    from streamlit.web import cli as stcli

    open_browser = "--no-browser" not in args
    args = [a for a in args if a != "--no-browser"]

    extra: list[str] = []
    if _is_frozen():
        _use_editable_settings()
        # There's no source to watch for edits in a build.
        extra += ["--server.fileWatcherType", "none"]
    port = _requested_port(args)
    if port is None:
        port = _free_port()
        extra += ["--server.port", str(port)]

    url = f"http://127.0.0.1:{port}"
    print(f"LMD Fixer v{__version__}")
    print(f"Opening {url} in your browser...")
    print("Keep this window open while you use LMD Fixer. Close it to quit.\n")
    if open_browser:
        threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()

    # Any extra arguments are passed through to `streamlit run`
    # (e.g. `lmd-fixer --server.port 8600`) and win over the defaults above.
    sys.argv = ["streamlit", "run", str(APP_PATH), *CONFIG_ARGS, *LOCAL_APP_ARGS, *extra, *args]
    return stcli.main()


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--version"]:
        print(__version__)
        return
    if args[:1] == ["--selftest"]:
        sys.exit(_selftest(args[1:]))
    try:
        sys.exit(_run(args))
    except Exception:
        # A double-clicked .exe closes its window the moment it exits, so
        # without this a startup error would vanish before anyone could read it.
        if not _is_frozen():
            raise
        traceback.print_exc()
        input("\nLMD Fixer couldn't start. Press Enter to close this window.")
        sys.exit(1)


if __name__ == "__main__":
    main()
