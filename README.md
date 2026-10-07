# LMD-Fixer

A Streamlit tool for cleaning up laser metal deposition (LMD) G-code/PTP
programs before they're run on the machine. Upload a file, walk through a
fixed sequence of fixes, review and accept/reject each proposed change, and
download the corrected program.

## Windows app (no Python needed)

Unzip `LMD-Fixer-<version>-win64.zip` and double-click `LMD Fixer.exe` in the
unzipped folder. A console window opens and the app opens in your browser
after a few seconds. Close the console window to quit. The zip includes a
`README.txt` for people receiving it.

The app only listens on `127.0.0.1`, so nobody else on the network can reach
it, and uploaded files never leave the PC.

### Building the Windows app

```
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

This writes `dist\LMD-Fixer-<version>-win64.zip`. It builds from the exact
versions in `packaging/requirements-build.txt`, in a virtual environment
under `%LOCALAPPDATA%\LMD-Fixer-build` (outside OneDrive, since a build is a
few thousand files). The first build takes a few minutes; later ones are
quicker. Bump `__version__` in `lmd_fixer/__init__.py` before building a
release you hand out.

The build ends with a self-test: the built `.exe` must load the app, and,
where the example `.ptp` files are present, its pipeline output must match
the source code's byte for byte. You can run the same check on any machine
with `"LMD Fixer.exe" --selftest` (or `lmd-fixer --selftest IN.ptp OUT.ptp`
from a checkout).

## Install and run with Python

```
pip install git+https://github.com/JRhattigan1/LMD-Fixer.git
lmd-fixer
```

This opens the UI in your browser (http://127.0.0.1:8501, or the next free
port if that one is taken). `--no-browser` skips opening it. Extra
arguments are passed through to `streamlit run`, e.g.
`lmd-fixer --server.port 8600`.

With [uv](https://docs.astral.sh/uv/) instead of pip:

```
uv tool install git+https://github.com/JRhattigan1/LMD-Fixer.git
lmd-fixer
```

(and later `uv tool upgrade lmd-fixer` to pick up updates).

## Running from a checkout (development)

```
pip install -r requirements.txt
streamlit run lmd_fixer/app.py
```

## How it works

1. Upload a `.ptp`/`.nc`/`.gcode`/`.txt` file.
2. Tick which fixes to run in the sidebar.
3. Fixes run one at a time, in a fixed order (see below), regardless of the
   order you ticked them. For each fix, every proposed change is listed
   individually with a checkbox so you can accept or reject it, plus an
   "accept all" shortcut when everything looks right. Section removal and
   feed adjustment also show a map of the bed. **← Back** returns to the
   previous fix and undoes it.
4. Once every selected fix has been reviewed, a side-by-side diff shows the
   original file against the final result (removed lines highlighted red,
   changed/added lines highlighted green), followed by a download button.

## Fixes

Fixes always run in this order, independent of sidebar tick order:

1. **Remove rotary table commands** (`remove_rotary_table`) — deletes
   `G65 ... P8000` / `M337` macro pairs and `G90 G0 A0.0` rotary axis-zero
   moves, used to command a rotary table that isn't needed for this job.
2. **Remove named program sections** (`remove_named_sections`) — finds
   sections marked by a standalone comment line like
   `(1_LAYER_STEPOVER_TEST_PATH_COPY_5)` and lists each one so you can
   choose which to remove entirely. Defaults to keeping every section; you
   opt in per section (or via "remove all") rather than opting out. A map of
   the bed sits beside the list, so you can see where each section is
   deposited: click a section on the map to tick or untick it, and sections
   ticked for removal are drawn red, dashed and marked ✕. Each list entry
   also says which part of the bed it is on (e.g. `Part 5 (X 26–54, Y 66–84)`).
3. **Remove repeated M98/M325 program calls** (`remove_repeated_p_calls`) —
   an `M98 Pxxxx` call that repeats the same P value as the previous call is
   a no-op, so that call, its `M325` line, and the following `G4 X25.00`
   dwell (if present) are removed automatically, reviewed as one group per
   call. If the P value actually changes, the pair is left in place.
4. **Remove G4 X25.00 dwells** (`remove_dwells`) — the dwells that remain
   after step 3 follow a genuine P-value change, so their necessity can't be
   determined from the file alone. Each is proposed for removal along with
   the P value it follows, and you decide per occurrence whether to keep it.
5. **Adjust feed rate per toolpath section** (`adjust_section_feeds`) —
   divides each toolpath into sections and lets you set a feed rate for
   each: a single value, or a ramp from a start feed to an end feed along
   the section. A toolpath can be divided into equal lengths, at distances
   you type in, into its straights and arcs, and/or by layer. You pick
   sections on a map of the bed, which is coloured by the feed each section
   will run at. Because feed is modal, the original feed is restated wherever
   a change would otherwise carry over, so these changes are applied together
   rather than line by line. A ramp can split long straight moves into short
   chords so the feed has somewhere to change; the chord length is under
   ⚙ Ramp settings. **Switched off in the shipped `fix_settings.toml`** —
   set it to `true` to use it (see below).

## Switching fixes off

`fix_settings.toml` lists every fix with `true` or `false`. In the Windows
app it's the copy beside `LMD Fixer.exe`; otherwise it's
`lmd_fixer/fix_settings.toml` (or wherever the `LMD_FIXER_SETTINGS`
environment variable points). The sidebar shows which file is in use. Set a
fix to `false` and it disappears from the sidebar and never runs; save the
file and refresh the browser tab to pick up the change. A fix not listed
there counts as on. (This only affects what the app offers — `run_fix` /
`run_pipeline` from a script still run any fix you name explicitly.)

## Project layout

```
pyproject.toml                  packaging config; `lmd-fixer` entry point
lmd_fixer/
  app.py                        Streamlit UI, including both toolpath maps
  cli.py                        `lmd-fixer` command and Windows app launcher
  gcode.py                      GCodeProgram: load/save, line-based model
  pipeline.py                   FIX_ORDER, run_fix / apply_accepted_changes
  settings.py                   reads fix_settings.toml
  fix_settings.toml             which fixes the app offers
  fixes/
    __init__.py                 Fix base class, LineChange, @register registry
    remove_rotary_table.py
    remove_named_sections.py
    remove_repeated_p_calls.py
    remove_dwells.py
    adjust_section_feeds.py     toolpath analysis, segmentation, feed ramps
    example_fix.py              unregistered template for writing new fixes
  tests/                        real example .ptp files (git-ignored, so
                                only on machines that have them):
                                "O1140 - Original.ptp" (unedited),
                                "O1140.ptp" (manually-fixed reference) and
                                "O1145.ptp" (17-pad Z-step sweep)
docs/
  feed-adjustment-design.md     why the feed step and the maps work as they do
  height-feedback-brief.md      brief for a separate, not-yet-started project
packaging/
  build.ps1                     builds the Windows app zip
  lmd_fixer.spec                PyInstaller config
  launch.py                     entry script for the .exe
  requirements-build.txt        exact dependency versions for builds
  README.txt                    shipped inside the zip for end users
```

## Adding a new fix

1. Create a module in `lmd_fixer/fixes/`.
2. Subclass `Fix`, set `id`, `label`, `description`, and implement `apply()`
   returning a `FixResult` with a list of `LineChange` entries (one per
   proposed change — a single line, or a range via `end_index` for
   multi-line changes like whole sections).
3. Decorate the class with `@register`.
4. Import the module in `lmd_fixer/fixes/__init__.py`.
5. If it needs a specific position in the pipeline, add its id to
   `FIX_ORDER` in `pipeline.py`.

It will then appear automatically as a checkbox in the sidebar and go
through the same per-change review UI as the existing fixes (unless you give
it a custom rendering branch, as `remove_named_sections` does for its
default-to-keep toggle behaviour).
