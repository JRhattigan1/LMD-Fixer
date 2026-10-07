# PyInstaller spec for the Windows build. Run through packaging/build.ps1
# rather than directly, so it's built from the pinned dependencies.
#
# One-folder rather than one-file: a one-file build unpacks ~200 MB to a temp
# folder on every launch, which is slow and is what antivirus tends to flag.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

REPO = Path(SPECPATH).parent

# Streamlit has no PyInstaller hook. Its web frontend is data files, it loads
# some modules dynamically, and it reads its own version from package metadata.
st_datas, st_binaries, st_hidden = collect_all("streamlit")

datas = [
    *st_datas,
    *copy_metadata("streamlit"),
    # `streamlit run` executes app.py from disk, so it ships as a file as well
    # as being analysed for imports below.
    (str(REPO / "lmd_fixer" / "app.py"), "lmd_fixer"),
    (str(REPO / "lmd_fixer" / "fix_settings.toml"), "lmd_fixer"),
]

# The launcher only imports the CLI; app.py's imports (the fixes, altair,
# pandas) are found by listing the package's modules.
hiddenimports = [
    *st_hidden,
    *[m for m in collect_submodules("lmd_fixer") if not m.startswith("lmd_fixer.tests")],
]

a = Analysis(
    [str(REPO / "packaging" / "launch.py")],
    pathex=[str(REPO)],
    binaries=st_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # Pulled in by Streamlit's optional integrations, never used here.
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LMD Fixer",
    # Keeps a console window: it's how the user quits, and where any error shows.
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LMD Fixer", upx=False)
