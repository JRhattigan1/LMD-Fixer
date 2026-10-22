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
2. Switch on the fixes you want in the sidebar.
3. Fixes run one at a time, in a fixed order (see below), regardless of the
   order you switched them on. For most fixes, every proposed change is
   listed individually with a checkbox, ticked by default, so you can
   accept or reject it, plus an "Accept all" shortcut. Section removal
   starts with nothing ticked, the P recipe step changes nothing until
   you set a P, and feed adjustment applies its changes as one set. All
   three show a map of the bed. **Skip fix** moves on without changing
   anything, and **← Back** returns to the previous fix and undoes it.
   Changing which fixes are switched on part-way through restarts the
   review from the original file. Before the first step that just happens.
   After it, the app asks first: **Keep my review** puts the sidebar back,
   and **Start over with this selection** discards what's been applied.
4. Once every selected fix has been reviewed, you get a summary of what was
   done, then a side-by-side diff of the original file against the final
   result. Removed lines are red, changed or added lines green, and long
   unchanged runs are collapsed. Choose the output file name (it defaults
   to `fixed_<upload name>`). If the name contains an O-number such as
   `O1162`, the program's `Oxxxx` header line is rewritten to match it.
   The download always uses CRLF line endings, as the controller expects.
   **Start over** re-runs the same file from the first fix and keeps your
   earlier choices ticked, so a redo doesn't mean reviewing everything again.

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
3. **Change P recipe numbers** (`change_recipes`) — the P of each
   toolpath's `M98 Pxxxx` call selects an LMD recipe stored on the machine's
   control panel (laser power, powder flow and nozzle gas). Laid out like
   section removal: a map of the bed, with the P each toolpath will run
   written in its box, beside a checklist of toolpaths. Tick toolpaths in
   the list or by clicking them on the map, enter a P, and press **Set**.
   **Reset** puts ticked toolpaths back to their original P. Toolpaths
   given a new P turn orange on the map. Each list entry shows what the
   material database (below) records its P as setting. Nothing changes
   until you set a P. Only the digits of the existing `M98` line change,
   keeping its zero-padding, and a toolpath with no `M98` call can't be
   given one. The program only names the recipe number, so check the
   control panel holds the settings you expect before running. It runs before step 4, so repeated calls are judged on
   the numbers you chose.
4. **Remove repeated M98/M325 program calls** (`remove_repeated_p_calls`) —
   an `M98 Pxxxx` call that repeats the same P value as the previous call is
   a no-op, so that call, its `M325` line, and the following `G4 X25.00`
   dwell (if present) are removed automatically, reviewed as one group per
   call. If the P value actually changes, the pair is left in place.
5. **Remove G4 X25.00 dwells** (`remove_dwells`) — the dwells that remain
   after step 4 follow a genuine P-value change, so their necessity can't be
   determined from the file alone. Each is proposed for removal along with
   the P value it follows, and you decide per occurrence whether to keep it.
6. **Adjust feed rate per toolpath section** (`adjust_section_feeds`) —
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

## Material database

**🧪 Material database** in the sidebar opens the recipe table: one row per
P number. Each row holds the laser power (W), powder flow (g/min) and nozzle
gas (l/min) as set on the control panel, plus Material and Notes for your
own reference (the P-change step shows only the panel settings). You can
add, edit and delete rows, and filter by material.
Nothing is written until you press **Save**. Save checks that every row has
a unique P from 1 to 9999, and stamps **Updated** on each row that changed.
With a file loaded, the dialog also lists any P numbers the file uses that
the database doesn't have yet, and can add rows for them.

Nothing links the database to the machine: it records what you've set on
the panel, and is only as right as it's kept. For now it's for reference in
the P-change step. The plan is to use it to decide which `G4 X25.00` dwells
must stay, e.g. where the powder flow changes and needs time to settle.

It's a plain CSV, so it also opens in Excel. Close it there before saving
from the app, though, since Excel locks the file. It lives in
`%APPDATA%\LMD-Fixer\recipes.csv`, so it survives unzipping a new version of
the Windows app. To share one copy between PCs (e.g. on OneDrive), set
`recipes = "path"` under `[database]` in `fix_settings.toml`. The
`LMD_FIXER_RECIPES` environment variable overrides both.

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
requirements.txt                runtime dependency for running from a checkout
.streamlit/config.toml          theme when run with `streamlit run` (cli.py
                                passes the same settings as arguments)
lmd_fixer/
  app.py                        Streamlit UI, including both toolpath maps
  cli.py                        `lmd-fixer` command and Windows app launcher
  gcode.py                      GCodeProgram: load/save, line-based model
  pipeline.py                   FIX_ORDER, run_fix / apply_accepted_changes
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
    adjust_section_feeds.py     toolpath analysis, segmentation, feed ramps
    example_fix.py              unregistered template for writing new fixes
  tests/                        real example .ptp files (git-ignored, so
                                only on machines that have them):
                                "O1140 - Original.ptp" (unedited coupon
                                array) and "O1140.ptp" (manually-fixed
                                reference), "O1140 - Original - 1 path.ptp"
                                (one toolpath cut from it), "O1145.ptp"
                                (17-pad Z-step sweep, 4 layers per pad), and
                                "O1162-AsPosted.ptp" / "O1162-Edited.ptp"
                                (2-layer run of the same sweep, unedited and
                                manually fixed)
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

It will then appear automatically as a toggle in the sidebar (unless
`fix_settings.toml` switches it off) and go through the same per-change
review UI as the existing fixes, with every change accepted by default.
A fix that removes large chunks, or whose changes are genuinely uncertain,
should default to keeping instead and get its own rendering branch in
`app.py`, as `remove_named_sections` does.
