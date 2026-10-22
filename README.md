# LMD Fixer

A Windows desktop app for cleaning up laser metal deposition (LMD)
G-code/PTP programs before they're run on the machine. Open a program, walk
through a fixed sequence of fixes, review every proposed change on a map of
the bed, in 3D and in the code, and save the corrected program.

## Windows app (no Python needed)

Unzip `LMD-Fixer-<version>-win64.zip` and double-click `LMD Fixer.exe` in the
unzipped folder. It's an ordinary desktop app: no console window and no
browser, and files never leave the PC. The zip includes a `README.txt` for
people receiving it.

### Building the Windows app

```
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

This writes `dist\LMD-Fixer-<version>-win64.zip`. It builds from the exact
versions in `packaging/requirements-build.txt`, in a virtual environment
under `%LOCALAPPDATA%\LMD-Fixer-build` (outside OneDrive, since a build is a
few thousand files), recreated whenever those pins change. Bump
`__version__` in `lmd_fixer/__init__.py` before building a release you hand
out.

The build ends with a self-test. The built `.exe` must build its window in
every theme and load the 3D and SpaceMouse modules. Where the example
`.ptp` files are present, it must also produce the same output as the
source code, byte for byte. You can run the same check on any machine
with `"LMD Fixer.exe" --selftest IN.ptp OUT.ptp --report report.txt`. The
`.exe` has no console, so it writes its results to the report file.

## Install and run with Python

Needs Python 3.11 or newer.

```
pip install git+https://github.com/JRhattigan1/LMD-Fixer.git
lmd-fixer [FILE]
```

With [uv](https://docs.astral.sh/uv/) instead of pip:

```
uv tool install git+https://github.com/JRhattigan1/LMD-Fixer.git
lmd-fixer
```

(and later `uv tool upgrade lmd-fixer` to pick up updates).

## Running from a checkout (development)

```
pip install -e .[dev]
python -m lmd_fixer.desktop [FILE]
python -m pytest
```

The tests in `tests/` run anywhere, including the desktop ones, which use
Qt's offscreen mode. The ones that use the example `.ptp` files are skipped
on machines that don't have them.

## Using it

1. **Open a program:** File → Open (Ctrl+O), drag a `.ptp`/`.nc`/`.gcode`/
   `.txt` onto the window, or pick one from the recent files.
2. **Choose the fixes:** switch them on or off in the sidebar. They always
   run in a fixed order (see below), whatever order you switch them on in.
3. **Review each step:**
   - The **plan view** shows the bed. Scroll to zoom, right- or middle-drag
     to pan, press F to fit, and click, Ctrl-click or drag a box to select
     toolpaths.
   - The **3D** tab shows the deposition paths at their real height. Travel
     moves (rapids, plunges, retracts) can be switched off, and a Z stretch
     pulls stacked layers apart. Drag to orbit, Ctrl- or middle-drag to pan,
     scroll to zoom, click to select.
   - The **program** sits beside them, with each proposed change marked on
     its lines.
   - The **list** of changes is below. Selecting in any of these selects the
     same thing in the others.
   - Most fixes start with every change ticked. Section removal starts with
     nothing ticked, and the P recipe step changes nothing until you set a
     P.
   - **Space** (or double-clicking a toolpath) flips the selected rows, and
     Ctrl+Z undoes.
   - **Apply step** (Ctrl+Enter) moves on, **Skip** moves on without
     changing anything, and **Back** undoes the previous step.
   - Double-click a finished step in the sidebar to go back to it.
   - Changing which fixes are switched on part-way through restarts the
     review from the original file, so the app asks first.
4. **Save:** when every step is done you get a summary and a side-by-side
   diff of the original against the result.
   - Save (Ctrl+S) suggests `fixed_<original name>`.
   - If the name you choose contains an O-number such as `O1162`, the
     program's `Oxxxx` header line is rewritten to match.
   - Files are always saved with CRLF line endings, as the controller
     expects.

**Help → Keyboard and mouse** lists every shortcut.

**Looks:** **Dark** (the default), **Light** and a colourful **Vivid**, from
the control at the right of the toolbar. Your choice is remembered.

**SpaceMouse:** a 3Dconnexion SpaceMouse moves whichever view is showing.

| Move | 3D tab | Plan view |
|---|---|---|
| Twist and tilt | Orbit | — |
| Slide | Pan | Pan |
| Lift | Pan | — |
| Push / pull | Zoom | Zoom |
| Button 1 | Fit | Fit |
| Button 2 | Show or hide travel moves | — |

It reads the device directly, so it works with 3DxWare running.

## Fixes

Fixes always run in this order:

1. **Remove rotary table commands** (`remove_rotary_table`) — deletes
   `G65 ... P8000` / `M337` macro pairs and `G90 G0 A0.0` rotary axis-zero
   moves, used to command a rotary table that isn't needed for this job.
2. **Remove named program sections** (`remove_named_sections`) — lists each
   block marked by a standalone comment line like
   `(1_LAYER_STEPOVER_TEST_PATH_COPY_5)` so you can remove whole blocks.
   Nothing is ticked to start with. Each entry says which part of the bed
   it's on (e.g. `Part 5 (X 26–54, Y 66–84)`), and blocks ticked for removal
   are drawn red, dashed and marked ✕ on the map.
3. **Change P recipe numbers** (`change_recipes`) — the P of each
   toolpath's `M98 Pxxxx` call selects an LMD recipe stored on the machine's
   control panel (laser power, powder flow and nozzle gas). Select
   toolpaths, type the new P and press Enter; each toolpath on the map shows
   the P it will run, and new ones turn orange. Only the digits of the
   existing `M98` line change, keeping their zero-padding; a toolpath with
   no `M98` call can't be given one. Check the control panel holds the
   settings you expect before running. It runs before step 4, so repeated
   calls are judged on the numbers you chose.
4. **Remove repeated M98/M325 program calls** (`remove_repeated_p_calls`) —
   an `M98 Pxxxx` call that repeats the previous call's P is a no-op, so it
   is removed with its `M325` line and the following `G4 X25.00` dwell,
   reviewed as one group per call. Where the P actually changes, the call
   stays.
5. **Remove G4 X25.00 dwells** (`remove_dwells`) — the dwells left after
   step 4 follow a genuine P change, so whether they're needed can't be told
   from the file alone. Each is listed with the P it follows, for you to
   decide.

## Material database

Tools → **Material database** (Ctrl+M) is a table with one row per P
number. Each row holds the laser power (W), powder flow (g/min) and nozzle
gas (l/min) as set on the control panel, plus Material and Notes for your
own reference. The P step shows only the panel settings.

You can add, edit and delete rows, filter by material, and add rows for
any P numbers the open program uses. Nothing is written until **Save**.
Save checks that every row has a unique P from 1 to 9999, and stamps
**Updated** on each row that changed.

Nothing links the database to the machine: it records what you've set on
the panel, and is only as right as it's kept. The plan is to use it to
decide which `G4 X25.00` dwells must stay, e.g. where the powder flow
changes and needs time to settle.

It's a plain CSV, so it also opens in Excel; close it there before saving
from the app, since Excel locks the file. It lives in
`%APPDATA%\LMD-Fixer\recipes.csv`, so it survives unzipping a new version.
To share one copy between PCs (e.g. on OneDrive), set `recipes = "path"`
under `[database]` in `fix_settings.toml`. The `LMD_FIXER_RECIPES`
environment variable overrides both.

## Switching fixes off

`fix_settings.toml` lists every fix with `true` or `false`. In the Windows
app it's the copy beside `LMD Fixer.exe`; otherwise it's
`lmd_fixer/fix_settings.toml`, or wherever the `LMD_FIXER_SETTINGS`
environment variable points. Hover the status bar to see which file is in
use.

Set a fix to `false` and it disappears from the sidebar. Restart the app
to pick up a change. A fix not listed counts as on.

This only affects what the app offers. `run_fix` / `run_pipeline` from a
script still run any fix you name explicitly.

## If something goes wrong

Unexpected errors are shown in a dialog and logged to
`%APPDATA%\LMD-Fixer\logs\lmd-fixer.log`.

## Project layout

```
pyproject.toml                  packaging config; `lmd-fixer` entry point
requirements.txt                runtime dependencies for a checkout
lmd_fixer/
  cli.py                        `lmd-fixer` / LMD Fixer.exe launcher, --selftest
  gcode.py                      GCodeProgram: load/save, line-based model
  pipeline.py                   FIX_ORDER, run_fix / apply_accepted_changes
  review.py                     ReviewSession: the step-by-step review, UI-free
  export.py                     output name, O-number sync, CRLF save, diff rows
  toolpaths.py                  toolpaths, parts and paths for the maps
  settings.py                   reads fix_settings.toml
  recipes.py                    material/recipe database (recipes.csv)
  fix_settings.toml             which fixes the app offers
  fixes/
    __init__.py                 Fix base class, LineChange, @register registry
    remove_rotary_table.py
    remove_named_sections.py
    change_recipes.py           rewrites each toolpath's M98 recipe number
    remove_repeated_p_calls.py
    remove_dwells.py
    example_fix.py              unregistered template for writing new fixes
  desktop/
    main.py                     starts the app; crash log
    main_window.py              the window: sidebar, cards, linking, saving
    steps_panel.py              the fixes sidebar
    steps/                      review panels (checklist, P recipes)
    bed_map.py                  plan view of the bed
    view3d.py                   3D view and click-picking
    gcode_view.py               the highlighted, change-marked program
    final_view.py               the final diff and save
    recipe_dialog.py            material database editor
    spacemouse.py               3Dconnexion SpaceMouse input
    theme.py                    Dark / Light / Vivid themes
    icon.py                     the app icon, drawn in code
  tests/                        real example .ptp files (git-ignored, so
                                only on machines that have them)
tests/                          pytest suite (python -m pytest)
docs/
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
2. Subclass `Fix`, set `id`, `label` and `description`, and implement
   `apply()`. It returns a `FixResult` with a list of `LineChange`
   entries, one per proposed change. A change is a single line, or a range
   via `end_index` for multi-line changes like whole sections. Set
   `default_accept = False` if its changes should start unticked.
3. Decorate the class with `@register`.
4. Import the module in `lmd_fixer/fixes/__init__.py`.
5. If it needs a specific position in the pipeline, add its id to
   `FIX_ORDER` in `pipeline.py`.

It then appears in the sidebar (unless `fix_settings.toml` switches it off)
and uses the standard tick-list review. A fix that removes large chunks, or
whose changes are genuinely uncertain, should default to keeping instead and
get its own review panel, as section removal does. Add a test in `tests/`
against the synthetic program in `tests/conftest.py`.
