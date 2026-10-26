# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A Windows desktop app (PySide6) that cleans up laser metal deposition (LMD)
G-code/PTP files generated for a Fanuc-style CNC/laser deposition machine.
The user used to edit these files by hand to strip out unneeded commands
before running them. This tool automates that with a reviewable,
step-by-step UI rather than a one-shot batch transform, because whether a
given line is safe to remove is often context-dependent and the user wants a
chance to check each one.

Ways to run it:
- `python -m lmd_fixer.desktop [FILE]` from a checkout.
- `lmd-fixer` once pip-installed: a GUI script, with its entry point in
  `lmd_fixer/cli.py` and packaging in `pyproject.toml`. The
  `lmd_fixer.tests` data files are excluded from wheels.
- For people without Python, the standalone Windows build
  (`packaging/build.ps1`; see *Windows build* below).

Up to 0.3.0 the UI was a Streamlit app in a browser. It was replaced by the
desktop app in 1.0.0 (2026-10-22) and is in git history (`lmd_fixer/app.py`)
if anything there is ever needed. Feed rate adjustment per toolpath section
was also removed then, as not needed (`adjust_section_feeds.py`,
`docs/feed-adjustment-design.md` in history).

`docs/height-feedback-brief.md` is a brief for a separate, not-yet-started
project (closed-loop layer-height correction). It isn't part of this app.

## Architecture: the UI-free core

- `lmd_fixer/gcode.py` — `GCodeProgram`: a thin wrapper around `list[str]`
  (one entry per line). Fixes operate on this and return a new one; nothing
  mutates the original.
- `lmd_fixer/fixes/` — one module per fix. Each defines a `Fix` subclass
  decorated with `@register` and implements `apply(program, **options) ->
  FixResult`. A `FixResult` carries the transformed program plus a list of
  `LineChange` objects describing what changed, so the UI can offer
  per-change accept/reject rather than applying blindly.
  - `LineChange` refers to a line or line range (`original_index` to
    `end_index`, inclusive) in the *input* program to that fix, not the
    original upload. `kind` is `"removed"` or `"modified"`; an optional
    `reason` is shown in the review UI. Don't overload `new_text` for that:
    it's the replacement text for `"modified"` changes, and may hold
    several `\n`-joined lines, which `apply_accepted_changes` splits back
    out.
  - Fixes must NOT rely on line indices from a different fix's output. Each
    fix is only ever handed the program as it exists after the prior fix's
    *accepted* changes were applied.
  - Each fix declares how it's reviewed: `review_mode` is "checklist" for
    the generic list, "sections" or "recipes" for fixes with their own
    panel ("recipes" is option-driven and applied all at once), and
    `default_accept` says whether its changes start ticked.
- `lmd_fixer/pipeline.py` — `FIX_ORDER` (the fixed run order), `run_fix`,
  `apply_accepted_changes` (rebuilds a program keeping only the accepted
  changes, single lines and ranges), and `run_pipeline` (every fix
  unconditionally, no review; kept for scripting).
- `lmd_fixer/review.py` — `ReviewSession`, the review state machine:
  - the selected fixes, always run in `FIX_ORDER`
  - the current step, and a history stack for Back
  - the reviewer's choices per step: accept/reject overrides of the fix's
    default, or the options for an option-driven fix

  Choices survive Back and `restart()`. `set_selection()` starts over and
  forgets them, and `selection_change_discards_work()` says whether to ask
  first. The window and `--selftest` both drive it. `fix` is cached per
  step, because the UI asks for it on every change on every redraw.
- `lmd_fixer/export.py` — what the machine gets and what changed:
  - `plan_output`: output name, extension checks and O-number sync, with
    notes to show
  - `machine_bytes` / `write_program`: always CRLF
  - `diff_rows`: the side-by-side diff as data
  - `change_context`
- `lmd_fixer/toolpaths.py` — toolpaths, parts and paths for the maps.
  - `analyse_toolpaths` finds each `(SECTION_NAME)` block's *measured path*
    and its footprint. The measured path is the laser-on moves between
    `M323`/`M322`, or every feed move if the laser never comes on.
  - `group_into_parts` groups toolpaths by overlapping footprint.
  - `path_points` gives the polylines to draw.
  - `program_moves` returns every move, rapids included, with Z, for the 3D
    view.
  - `analyse_toolpaths` is cached on the program's lines (`lru_cache`, last
    4), since the plan map and 3D view both ask for it. **Treat its result
    as read-only**: mutating it would corrupt the cache. `_words` (a line's
    parsed G-code words) is cached per line text too, as each step changes
    only a few lines.
- `lmd_fixer/recipes.py` — the material/recipe database. It has one CSV row
  per `M98 Pxxxx` recipe number: the panel settings (laser power, powder
  flow g/min, nozzle gas) plus Material/Notes.
  - Material and Notes are operator notes and are kept out of the P step's
    descriptions (`recipe_summary`).
  - Powder flow rpm and carrier/shield gas were dropped; `RETIRED_COLUMNS`
    are removed on load.
  - It lives at `LMD_FIXER_RECIPES`, else `[database] recipes` in
    `fix_settings.toml`, else `%APPDATA%\LMD-Fixer\recipes.csv`. It's
    deliberately not beside the `.exe`, where a newly unzipped version would
    orphan it.
  - Saves are validated, written atomically, and stamp `Updated`.
  - Nothing ties it to the machine, so the P step never *requires* a P to
    be in it.
- `lmd_fixer/settings.py` + `fix_settings.toml` — which fixes the app
  offers, read when the window starts. `LMD_FIXER_SETTINGS` overrides the
  path (the Windows build points it beside the `.exe`). Only the menu is
  affected — `run_fix` runs any fix named explicitly.

## Architecture: the desktop app (`lmd_fixer/desktop/`)

- **Layout:** the fixes sidebar on the left (`steps_panel.py`: a status
  mark, title and `ToggleSwitch` per fix). On the right:
  - a card holding the Plan/3D tabs and the code (or the final diff)
  - a card holding the step panel
  - a bar with Back / Skip / Apply step

  Keyboard hints live in tooltips and Help → Keyboard and mouse, not on
  screen.
- **`MainWindow`** owns one `ReviewSession` and calls `_update()` after
  every step move, which redraws everything from the session.
  - Step panels live in `desktop/steps/`, one per `review_mode`:
    `checklist.py` serves "checklist" and "sections", `recipes.py` the P
    step.
  - Choice edits go through the window's `QUndoStack`
    (`steps/common.SetAcceptedCommand`, `SetRecipesCommand`), which is
    cleared on each step move.
  - Confirmation prompts go through `MainWindow.ask` / `confirm_restart`,
    so tests can answer them.
- **Changing the fix selection mid-review asks before discarding.** The
  review restarts from the original file whenever the chosen fixes change,
  because the program has already been through the fixes behind the current
  step. Before the first step that just happens. Past it, `confirm_restart`
  offers *Keep my review* (the default, which puts the switches back) or
  *Start over*, and **nothing changes until the user picks**. A new file
  asks first only if steps have been applied.
- **`GCodeView`** marks proposals on their lines via ExtraSelections. A
  full-width band per line is only added for changes under
  `BAND_MAX_LINES`: Qt draws full width only for an empty cursor, and a
  section removal would need thousands.
  - The cursors per change are built once per step (`_change_cursors`);
    ticking or moving the selection only recolours them.
  - The highlighter does one regex pass per line (`TOKENS`, a named group
    per colour, comments first). Six separate passes were most of a step
    change's cost on a 10,000-line program.
  - `set_lines` blocks the view's own signals while loading, and undo is
    off (the view is read-only, and undo history was a third of each load).
- **Selection is shared.** The plan map, the 3D view, the step list and
  the code cursor all show the same selection:
  - The window mirrors each into the others (`_list_selection_moved`,
    `_map_selection_edited`, `_show_selection`). `set_selected` never
    echoes back.
  - Space or double-click toggles the selected toolpaths.
  - A toolpath maps to changes through `toolpath_at_line`; a section
    change's `original_index` is its toolpath's start line.
  - Steps style both views through `_choices_changed` → `set_styles`.
- **Plan map** (`bed_map.py`): a `QGraphicsScene` in mm with Y flipped up.
  - Each toolpath has a cosmetic-pen path, a `_Box` click target and a
    `_Label`; labels ignore the view transform so they stay readable.
  - It re-fits on resize until the user zooms or pans.
  - For checklist fixes the map shows a ticked count as a *label*, not a
    colour.
- **3D view** (`view3d.py`, pyqtgraph `GLViewWidget`): deposition per
  toolpath at real Z, plus travel moves, with a travel toggle and a Z
  stretch.
  - **Picking** happens in screen space (`pick_segment`). Every deposition
    segment is projected with the camera's own matrices, and the nearest
    within 8 px wins. Among near-ties the one nearest the camera wins, so a
    stack's top layer is picked first. It's pure numpy, unit-tested
    without GL.
  - A click is a press and release under `CLICK_SLOP_PX`; anything longer
    is an orbit.
  - The GL widget is only created on first show (on a zero timer, so the
    "Loading the 3D view…" placeholder paints first), so tests never touch
    OpenGL. `main()` calls `preload()`, which imports `pyqtgraph.opengl` on
    a background thread: that import takes 1–3 s and used to freeze the
    window the first time 3D was opened. It only imports; no Qt objects.
  - **A handful of GL items, whatever the toolpath count.** A
    `GLLinePlotItem` has one width, so deposition is one item per width in
    use (`_lines`, by `group_by_width`: the style's width, plus
    `SELECTED_EXTRA_WIDTH` when selected), with per-vertex colours. Each
    toolpath is drawn once, at its own width. Don't collapse that into a
    base + "emphasis" pair: the sections step styles every section 2.0, so
    selected sections stopped standing out. Travel, the selection box and
    the grid are one item each. All labels are drawn by one `LabelLayer`
    in a single QPainter pass (pyqtgraph's `GLTextItem` opened a painter
    per label per frame). Restyling and selecting refill arrays only.
    `_flatten` builds the combined vertex array and each toolpath's range
    in it; picking uses the same array.
  - **Nothing is drawn while hidden.** Loads, styles and selections mark
    `_dirty` and `_sync()` applies them when the tab is next shown.
  - **A new GL context is handled — don't remove it.** pyqtgraph caches
    compiled shaders per process, not per context, so when Qt recreated
    the context (e.g. the window moving to another screen) every draw
    failed with "invalid value", every frame, and logging those froze the
    app. `_GLView.initializeGL` calls `reset_pyqtgraph_gl_cache()`, and on
    every context *after the first* recreates the items. The first
    `initializeGL` comes after `_create_gl` has already made them, so
    rebuilding there doubled the 3D tab's first open (`_gl_initialised`).
  - `drawn_toolpaths()` says how many toolpaths are on screen; the
    self-test uses it after a real `grabFramebuffer()`.
  - To look at it, render with the real `windows` platform and
    `grabFramebuffer()`. The offscreen platform gets a context but no image.
- **Themes** (`theme.py`): Dark (default), Light and Vivid, from the
  segmented control in the toolbar, remembered in `QSettings` ("theme").
  - A theme is a set of colour tokens. `use()` copies them into module
    globals, so draw-time reads like `theme.TEXT` follow the current theme.
  - `apply_theme` rebuilds the palette and the one app stylesheet.
  - Widgets that cache colours are refreshed by `MainWindow.set_theme`: the
    highlighter `reload()`s, and the map, 3D and code views have
    `refresh_theme()`.
  - Don't hard-code colours in a widget; add a token.
- **Map colours are validated.** Each theme's state colours are checked
  with the dataviz skill's `validate_palette.py` against that theme's
  `CANVAS`, and a state never rests on colour alone: removal is also
  dashed, tinted and ✕-labelled, and a new P is thicker with a bold label.
  Kept/removed is blue/red, not green/red: on white the green failed
  contrast, and a darker green fell into the colour-blind warning band.
  Re-validate before adding or changing one.
- **SpaceMouse** (`spacemouse.py`) reads 3Dconnexion HID reports through
  `hidapi`, which works alongside 3DxWare.
  - Report 1 is translation, report 2 rotation and report 3 buttons; newer
    devices put all six axes in report 1.
  - A timer drains the reports each tick and keeps moving while the cap is
    off-centre.
  - It's opened by `main()` via `start_spacemouse()`, never by the
    constructor, so tests don't grab a real device.
  - Response constants are at the top of `view3d.py`.
- **Material database dialog** (`recipe_dialog.py`): a
  `QStandardItemModel` behind a filter proxy. Cells are text until Save.
- **Crash handling** (`main.py`): unexpected errors go to a dialog and
  `%APPDATA%\LMD-Fixer\logs\lmd-fixer.log`, because the packaged app has no
  console.
- **Qt gotchas already handled — don't undo them:**
  - A `QTreeWidgetItem` shows no tick box until its check state is set
    explicitly, so it's set on every row, including unticked ones.
  - Step panels block their tree's signals while refilling.
    `QTreeWidget.clear()` removes rows from the end and reports the
    selection after each removal, so rows from the previous step got
    reported against the new step's list (an `IndexError`).
  - A stylesheet rule for `QTableView::item` makes the table ignore the
    model's `BackgroundRole`, which hid the diff colours.
  - Fractional border widths on `::indicator` are dropped.
  - A QSS image must be a file, so the tick is drawn at startup into the
    temp folder (`theme._check_image`).
  - Don't `blockSignals` on a model behind a proxy while loading (the
    dialog's table stayed empty); use a `_loading` flag.
  - Font weight can't be data-driven in a label layer.
  - The first OpenGL widget in a window that's already shown makes Qt
    hide it, recreate its native window and show it again: the window
    closed and reopened the first time 3D was opened. `main()` (and
    `--selftest`) call `view3d.prepare_window` *before* `show()`, which
    adds an empty hidden `QOpenGLWidget` so the window is OpenGL from the
    start. Not in the constructor, so tests stay GL-free.
  - On finishing the review the window clears the code view's changes
    (`show_changes([])`). The view is hidden, but a theme change still
    repaints it, and its stale changes asked the finished session, which
    has no current fix, whether they were accepted.

## Conventions specific to this codebase

- **Fix order is meaningful and enforced**, not cosmetic. Later fixes depend
  on earlier ones having run (e.g. dwell review only sees dwells that
  survive repeated-P-call collapsing). If you add a fix with an ordering
  dependency, put its id in `FIX_ORDER` at the right position.
- **Default review state varies by fix, deliberately.** Most fixes start
  every change ticked, since they propose a specific, usually-safe cleanup.
  `remove_named_sections` starts with nothing ticked, because ticking a
  section removes ~200+ lines at once across ~45 sections. An accidental
  "accept all" there would silently gut the program. When adding a fix
  that removes large chunks or whose correctness is uncertain, default to
  keeping and give it its own review panel.
- **Naming is part > toolpath.** A *part* is a footprint on the bed
  (`group_into_parts`); a *toolpath* is one `(SECTION_NAME)` block, which
  the section-removal fix calls a "section". `O1140 - Original.ptp` is 45
  one-toolpath parts, not 45 layers of one part.
- **G-code specifics learned from the real files** (`lmd_fixer/tests/`):
  - `G65 B0.0 F1000. D1 P8000` + following `M337`, and `G90 G0 A0.0`, are
    rotary-table commands unneeded when no rotary table is in use.
  - `M98 Pxxxx` calls a subprogram. Consecutive calls with the same P are
    redundant (the program is already loaded), and so are their `M325` and
    following `G4 X25.00` dwell. The three lines are always consecutive, so
    `remove_repeated_p_calls` proposes each group as one range
    `LineChange`.
  - The P of `M98 Pxxxx` is an LMD **recipe** stored on the control panel
    (laser power, powder flow, nozzle gas). The file only names the
    number; `recipes.py` records what each one sets, by hand. P is typed
    rather than picked from the database, because the database isn't the
    machine and may be incomplete.
  - Every toolpath sets P once, in its header before the laser comes on.
    `change_recipes` rewrites only those digits, keeping `/` and the
    zero-padding, and never inserts a call into a toolpath that lacks one.
    It sits **before** `remove_repeated_p_calls`, which compares P values:
    a P changed after the collapse would leave a collapsed toolpath
    silently inheriting its neighbour's recipe.
  - Next planned use of the database: a dwell after a recipe change whose
    powder flow differs must stay, to let the flow settle. The user's
    hand-edited references fit that rule: P20–28 behave as groups of three,
    and the dwells were kept where the group changes.
  - `G4 X25.00` after a *genuine* P change depends on machine specifics not
    recoverable from the file, so it's left to manual review, never
    auto-removed. A dwell is attributed to an `M98 Pxxxx` call only if the
    lines between are blank or `M325`. Otherwise it's labelled "unknown P
    value" rather than misattributed (`remove_dwells._preceding_p_value`).
  - Section markers are standalone comment lines like
    `(1_LAYER_STEPOVER_TEST_PATH_COPY_5)`. They're told apart from
    free-text header comments (e.g. `(PROJECT: ...)`) by having no
    whitespace inside the parentheses. A section runs to the next marker
    or EOF.
  - `M323`/`M322` switch the laser on/off. Feed rate is modal, set once
    per toolpath on its plunge. `G65 ... F1000.` is a macro argument, not a
    feed.
  - The CAM emits curves as ~0.5 mm G1 chords (no G2/G3) but straights as
    one or two very long moves.
  - **A toolpath marker is not a layer.** In `O1145.ptp` one marker block
    holds all four of a pad's layers at different Z, so a pad's layers
    share one footprint on the plan map. The 3D view separates them.
  - Source `.ptp` files are CRLF (`O1145.ptp` is LF). Output is always
    written as CRLF, which the controller expects.

## Test files

`lmd_fixer/tests/` holds real example files, useful for verifying a fix's
output against a known-good target:

- `O1140 - Original.ptp` (unedited, 45 one-toolpath coupons) and `O1140.ptp`
  (the user's manually-fixed reference version).
- `O1140 - Original - 1 path.ptp`: a single toolpath of it, for quick runs.
- `O1145.ptp`: a 17-pad Z-step sweep, 4 layers per marker block, LF endings.
- `O1162-AsPosted.ptp` / `O1162-Edited.ptp`: a 2-layer run of the same
  sweep, unedited and manually fixed, so a second reference pair.

**Check anything touching toolpath analysis or the maps against `O1145.ptp`
as well as the O1140 pair.** Its pads are rasters rather than contoured
coupons, and its layers live inside single markers, so it exercises paths
O1140 cannot reach. Two past path-analysis bugs passed on O1140 while
making O1145 useless.

These `.ptp` files are git-ignored (proprietary project data, kept out of
the public repo), so a fresh clone won't have them. Don't quote identifying
content from them (project codes, people) in tracked files.

## Windows build

`packaging/build.ps1` makes `dist/LMD-Fixer-<version>-win64.zip`: a
PyInstaller **one-folder**, **windowed** build. One-file unpacks everything to
temp on every launch, and is what antivirus flags. It's built from the pins
in `packaging/requirements-build.txt`, in a venv under
`%LOCALAPPDATA%\LMD-Fixer-build` (kept out of OneDrive, because a build is
thousands of files). The venv is recreated whenever the pins change
(`requirements.sha256` stamp).

Things that look removable but aren't:

- `cli.py` is the launcher for both `lmd-fixer` and the `.exe`. A build
  can't edit the `fix_settings.toml` inside `_internal`, so the launcher
  sets `LMD_FIXER_SETTINGS` to the copy beside the `.exe`, falling back to
  `%APPDATA%\LMD-Fixer`.
- The spec lists `lmd_fixer`'s submodules as hidden imports, because the
  launcher imports the desktop app lazily, plus `hid`, a compiled extension.
  pyqtgraph and PyOpenGL come in through pyinstaller-hooks-contrib. Other
  Qt bindings are excluded so pyqtgraph can't pull them in.
- The icon is drawn in code (`desktop/icon.py`). The build renders it to an
  `.ico` and passes it in via `LMD_FIXER_ICON`.
- `--selftest [IN OUT] [--report FILE]` checks four things:
  - the window builds offscreen in every theme
  - the 3D and SpaceMouse modules load
  - the window opens IN, and the 3D view starts OpenGL and draws it
  - the reference pipeline runs through `ReviewSession`, writing OUT

  A windowed `.exe` has no stdout, so it reports through `--report` and its
  exit code. `build.ps1` runs it through both the `.exe` and the source, and
  fails unless the outputs are byte-identical: that's the check that the
  frozen app really behaves the same.

## Testing changes

Run `python -m pytest` (install with `pip install -e .[dev]`).

- **Layout:** the suite is in `tests/` at the top level; `lmd_fixer/tests/`
  holds only the example `.ptp` files.
- **Fixtures:** most tests use `sample_program` from `tests/conftest.py`, a
  small synthetic program shaped like the real files, so they run on a fresh
  clone. Tests on the real files use the `real_file` fixture, which skips
  when a file isn't present.
- **When changing or adding a fix,** add a test against the synthetic
  program and check the real files too.
- **Desktop tests** (`tests/test_desktop.py`, pytest-qt) drive the real
  widgets with Qt's offscreen platform, which `tests/conftest.py` selects.
  Give `MainWindow` a `QSettings` on a temp ini file so tests don't touch the
  real settings. Exceptions raised in slots fail the test.
- **To look at the window,** render it offscreen with `widget.grab()` and set
  `QT_QPA_FONTDIR=C:\Windows\Fonts`; otherwise every glyph is drawn as a box.
  Check all three themes after a visual change.
- **Full-pipeline check against the references**
  (`tests/test_reference_files.py`): a `ReviewSession` over `FIX_ORDER`,
  applying every step with its defaults. That accepts every change except
  in `remove_named_sections`, where accepting everything removes all 45
  sections and leaves 21 lines. Done that way, the result differs from
  `O1140.ptp` by exactly the documented deltas: 45 `G90 G0 A0.0`, 3 dwells,
  6 `M325`, plus blank lines. It differs from `O1162-Edited.ptp` by only the
  3 genuine-P-change dwells that reference keeps; that reference removes
  every `G90 G0 A0.0`, unlike `O1140.ptp`. Ignore the
  `(PROGRAM CREATED ...)` header line when diffing.
