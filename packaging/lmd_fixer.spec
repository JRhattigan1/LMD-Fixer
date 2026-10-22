# PyInstaller spec for the Windows build. Run through packaging/build.ps1
# rather than directly, so it's built from the pinned dependencies.
#
# One-folder rather than one-file: a one-file build unpacks everything to a
# temp folder on every launch, which is slow and is what antivirus tends to
# flag. Windowed (no console): errors go to a dialog and a log file instead
# (lmd_fixer/desktop/main.py), and --selftest writes a report file.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

REPO = Path(SPECPATH).parent
ICON = Path(os.environ.get("LMD_FIXER_ICON", ""))  # drawn by build.ps1

datas = [(str(REPO / "lmd_fixer" / "fix_settings.toml"), "lmd_fixer")]

# The launcher imports the desktop app lazily, so list the package's modules;
# hid is a compiled extension PyInstaller can't see through.
hiddenimports = [
    *[m for m in collect_submodules("lmd_fixer") if not m.startswith("lmd_fixer.tests")],
    "hid",
]

a = Analysis(
    [str(REPO / "packaging" / "launch.py")],
    pathex=[str(REPO)],
    datas=datas,
    hiddenimports=hiddenimports,
    # Other Qt bindings pyqtgraph can use, and things nothing here imports.
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2", "streamlit"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LMD Fixer",
    console=False,
    icon=str(ICON) if ICON.is_file() else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LMD Fixer", upx=False)
