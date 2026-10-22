# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A Streamlit app that cleans up laser metal deposition (LMD) G-code/PTP files
generated for a Fanuc-style CNC/laser deposition machine. The user manually
edits these files today to strip out unneeded commands before running them;
this tool automates that with a reviewable, step-by-step UI rather than a
one-shot batch transform, because whether a given line is safe to remove is
often context-dependent and the user wants a chance to check each one.

Run it with `streamlit run lmd_fixer/app.py` from a checkout, or via the
`lmd-fixer` console command once pip-installed (entry point in
`lmd_fixer/cli.py`, packaging in `pyproject.toml`; the `lmd_fixer.tests`
data files are excluded from wheels). For people without Python there's a
standalone Windows build (`packaging/build.ps1`, see *Windows build* below).

`docs/height-feedback-brief.md` is a brief for a separate, not-yet-started
project (closed-loop layer-height correction). It isn't part of this app.

Feed rate adjustment per toolpath section (with ramps and geometry
segmentation) used to be a fix here. It was removed on 2026-10-22 as not
needed; it's in git history (`adjust_section_feeds.py`,
`docs/feed-adjustment-design.md`) if it's ever wanted back.

## Architecture

- `lmd_fixer/gcode.py` — `GCodeProgram`: a thin wrapper around `list[str]`
  (one entry per line). Fixes operate on this and return a new one; nothing
  mutates the original.
- `lmd_fixer/fixes/` — one module per fix. Each defines a `Fix` subclass
  decorated with `@register` and implements `apply(program) -> FixResult`.
  A `FixResult` carries the transformed program plus a list of `LineChange`
  objects describing what changed, so the UI can offer per-change
  accept/reject rather than applying blindly.
  - `LineChange` refers to a line or line range (`original_index` to
    `end_index`, inclusive) in the *input* program to that fix, not the
    original upload. `kind` is `"removed"` or `"modified"`; an optional
    `reason` string is shown in the review UI (don't overload `new_text`
    for that — it's the replacement text for `"modified"` changes).
  - Fixes must NOT rely on line indices from a different fix's output — each
    fix is only ever handed the program as it exists after the prior fix's
    *accepted* changes were applied (see `pipeline.apply_accepted_changes`).
- `lmd_fixer/pipeline.py` — `FIX_ORDER` (the fixed run order), `run_fix`
  (runs one fix), `apply_accepted_changes` (rebuilds a program keeping only
  the changes the user accepted, handling both single lines and ranges), and
  `run_pipeline` (applies every fix unconditionally with no review — kept for
  scripting, not used by the UI).
- `lmd_fixer/review.py` — `ReviewSession`, the UI-free review state machine:
  the selected fixes in `FIX_ORDER`, the current step, a history stack for
  Back, and the reviewer's choices per step (accept/reject overrides of the
  fix's default, or options for an option-driven fix). Choices survive Back
  and `restart()`; `set_selection()` starts over and forgets them, and
  `selection_change_discards_work()` says whether to ask first. The desktop
  app (in progress) and `--selftest` drive it; the
  Streamlit app still keeps its own copy of these rules in session state.
- `lmd_fixer/toolpaths.py` — toolpaths, parts and their deposition paths,
  for the bed maps. `analyse_toolpaths` finds each `(SECTION_NAME)` block's
  *measured path* (laser-on moves between `M323`/`M322`, or every feed move
  if the laser is never switched on) and footprint; `group_into_parts`
  groups toolpaths by overlapping footprint; `path_points` gives the
  polylines to draw, breaking wherever the path isn't continuous.
- `lmd_fixer/export.py` — what the machine gets and what changed:
  `plan_output` (output name, extension checks, O-number sync, with notes to
  show), `machine_bytes`/`write_program` (always CRLF), `diff_rows` (the
  side-by-side diff as data, for any UI to draw), `change_context`.
- `lmd_fixer/recipes.py` — the material/recipe database: one CSV row per
  `M98 Pxxxx` recipe number: the panel settings (laser power, powder flow
  g/min, nozzle gas) plus Material/Notes, which are operator notes
  and are kept out of the P-change step's descriptions (`recipe_summary`).
  Powder flow rpm and carrier and shield gas were dropped (`RETIRED_COLUMNS`, removed on load). It
  lives at `LMD_FIXER_RECIPES`, else `[database] recipes` in
  `fix_settings.toml`, else `%APPDATA%\LMD-Fixer\recipes.csv`. It is
  deliberately not beside the `.exe`, where a new unzipped version would
  orphan it. Saves are validated, written atomically, and stamp `Updated`.
  It's edited in the sidebar's `material_database_dialog` (`app.py`), and
  the P-change step shows what each P is recorded as. Nothing ties it to the
  machine, so the step never *requires* a P to be in it.
- `lmd_fixer/settings.py` + `lmd_fixer/fix_settings.toml` — which fixes the
  app offers. Read fresh on every page load; `LMD_FIXER_SETTINGS` overrides
  the path (the Windows build points it beside the `.exe`). Only the menu is
  affected — `run_fix` runs any fix named explicitly. The shipped file
  switches every fix on.
- `lmd_fixer/desktop/` — the PySide6 desktop app replacing the Streamlit UI
  (in progress; `python -m lmd_fixer.desktop`). Layout: the fixes sidebar
  (`steps_panel.py`: status mark, title, `ToggleSwitch` per fix) on the
  left; on the right, a card holding the Plan/3D tabs and the code (or the
  final diff), a card holding the step panel, and a bar with Back / Skip /
  Apply step. Keyboard hints live in tooltips and Help → Keyboard and
  mouse, not on screen. `MainWindow` owns one
  `ReviewSession` and calls `_update()` after every step move, which redraws
  everything from the session. Step panels live in `desktop/steps/`, one per
  `review_mode` (`checklist.py` serves "checklist" and "sections",
  `recipes.py` the P step). Choice edits go through the
  window's `QUndoStack` (`steps/common.SetAcceptedCommand`), which is cleared
  on each step move. `GCodeView` marks proposals on their lines via
  ExtraSelections; a full-width band per line only for changes under
  `BAND_MAX_LINES`, since Qt draws full width only for an empty cursor and a
  section removal would need thousands. Confirmation prompts go through
  `MainWindow.ask` / `confirm_restart` so tests can answer them. Step
  panels block their tree's signals while refilling: `QTreeWidget.clear()`
  removes rows from the end and reports the selection after each removal,
  so rows from the previous step get reported against the new step's
  change list (an `IndexError` in `_starts_for_positions`).
- **Desktop bed map** (`desktop/bed_map.py`, `BedMapView`): a
  `QGraphicsScene` in mm with Y flipped up. Each toolpath has a cosmetic-pen
  path, a `_Box` click target and an `_Label`; labels ignore the view
  transform so they stay readable. It re-fits on resize until the user zooms
  or pans. **Selection on the map is the step list's selection**: the window
  mirrors one into the other (`_list_selection_moved` /
  `_map_selection_edited`); `set_selected` doesn't echo back. Space or
  double-click toggles. This differs from the browser map, where a click
  toggled. Steps style the map through `MainWindow._choices_changed` →
  `set_styles`. The colour pairs live in `theme.py` (`REMOVAL_*`,
  `RECIPE_*`) and were validated. For checklist fixes the map shows a ticked
  count as a *label*, so no unvalidated colour is added; validate any new
  map colour before using it. A step's toolpaths map to changes through
  `toolpath_at_line`. A section change's `original_index` is its
  toolpath's start line.
- **3D view** (`desktop/view3d.py`, `View3D`, the "3D" tab beside the plan
  map): pyqtgraph's `GLViewWidget`. It draws deposition per toolpath at real
  Z and travel moves (`toolpaths.program_moves`, rapids included), with a
  travel toggle and a Z stretch. It takes the same `ToolpathStyle`s and
  selection as the plan map: `MainWindow._show_selection` sets both, and
  either view's `selectionEdited` goes through `_map_selection_edited`. GL
  can't do dashes, so state there rests on colour, labels and the
  selection box. **Picking** is in screen space (`pick_segment`): project
  every deposition segment with the camera's own matrices and take the
  nearest within 8 px, nearest-to-camera among near-ties so a stack's top
  layer wins. It's pure numpy, so it's unit-tested without GL. A click is a
  press and release under `CLICK_SLOP_PX`; anything longer is an orbit. The
  GL widget is only created on first show, so tests never touch OpenGL.
  To look at it, render with the real `windows` platform and
  `grabFramebuffer()`. The offscreen platform gets a context but produces no
  image, and `widget.grab()` leaves GL panes blank.
- **Themes** (`desktop/theme.py`): Dark (default), Light and Vivid, picked
  from the segmented control at the right of the toolbar and remembered in
  `QSettings` ("theme"). A theme is a set of colour tokens. `use()` copies
  them into module globals, so draw-time reads like `theme.TEXT` follow the
  current theme; `apply_theme` also rebuilds the palette and the one app
  stylesheet. Widgets that cache colours are refreshed by
  `MainWindow.set_theme`: the highlighter `reload()`s, and the map, 3D view
  and code view each have `refresh_theme()`. Don't hard-code colours in a
  widget; add a token. Map state colours are per theme and validated
  against that theme's `CANVAS`. Kept/removed is blue/red, not green/red:
  on white the green failed contrast, and a darker green fell into the
  colour-blind warning band. Qt gotchas met here:
  - a `QTreeWidgetItem` shows no tick box until its check state is set
    explicitly, so set it on every row, including unticked ones;
  - a stylesheet rule for `QTableView::item` makes the table ignore the
    model's `BackgroundRole`, which hid the diff colours;
  - fractional border widths on `::indicator` are dropped;
  - a QSS image must be a file, so the tick is drawn at startup into the
    temp folder (`_check_image`).
- **SpaceMouse** (`desktop/spacemouse.py`): reads 3Dconnexion HID reports
  through `hidapi`. Report 1 is translation, report 2 rotation and report 3
  buttons; newer devices put all six axes in report 1. Windows lets it read
  alongside 3DxWare. A timer drains the reports each tick and keeps moving
  while the cap is held off-centre. `MainWindow.start_spacemouse()` is
  called from `main()`, never the constructor, so tests don't grab a real
  device. Motion goes to the visible view's `apply_motion`; response
  constants are at the top of `view3d.py`.
- **Material database dialog** (`desktop/recipe_dialog.py`): a
  `QStandardItemModel` behind a filter proxy. Cells are text until Save,
  which runs `recipes.validate_recipes` / `stamp_changed` / `save_recipes`.
  Don't `blockSignals` on the model while loading: the proxy then never
  sees the rows and the table stays empty. Use the `_loading` flag.
- `lmd_fixer/app.py` — Streamlit UI. Runs fixes one at a time in a fixed pipeline
  order (`pipeline.FIX_ORDER`), independent of sidebar tick order. Holds
  `original_program` and `current_program` in `st.session_state` so the
  final screen can render a side-by-side diff of the untouched upload
  against the fully-reviewed result.

## Conventions specific to this codebase

- **Fix order is meaningful and enforced**, not just cosmetic. Later fixes
  depend on earlier ones having already run (e.g. dwell review only sees
  dwells that survive repeated-P-call collapsing). If you add a fix with an
  ordering dependency, add its id to `FIX_ORDER` in `pipeline.py` at the correct
  position — don't rely on sidebar order.
- **Default review state varies by fix and is a deliberate choice, not an
  oversight.** Most fixes default every proposed change to "accept" (the
  fix is proposing a specific, usually-safe cleanup). `remove_named_sections`
  defaults to "keep everything" because ticking a section removes ~200+
  lines at once across ~45 sections — an accidental "accept all" there would
  silently gut the program. Each fix declares this itself: `default_accept`
  (False for `remove_named_sections`) and `review_mode` ("checklist" for the
  generic list; "sections" and "recipes" for the fixes with their own review
  UI, "recipes" being option-driven and applied all at once).
  `ReviewSession` reads both. When adding a fix that removes large chunks or
  whose correctness is genuinely uncertain, default to the safe (keep) side
  and give it its own review UI rather than the generic checklist.
- **Changing the fix selection mid-review asks before discarding.** The review
  restarts from the original file whenever the chosen set of fixes changes,
  because the program has already been through the fixes behind the current
  step. That's free before the first step, so it just happens; past that it
  would throw away applied work, so the app warns, shows was/now, offers
  *Keep my review* or *Start over with this selection*, and `st.stop()`s —
  **nothing is mutated until the user picks**. Making that undoable is why the
  sidebar toggles are keyed (`FIX_TOGGLE_KEY`): *Keep* writes the previous
  selection back into them. A new *file* still resets silently — there's
  nothing worth keeping. Don't collapse `file_key` and `selection` back into
  one `state_key`; that was the bug (a brushed toggle four steps in wiped
  everything with no warning).
- **Streamlit state gotchas already handled in `app.py`** — don't undo them:
  - A keyed checkbox ignores its `value=` once the key exists in session
    state, so the "Accept all" / "Remove all sections" master checkboxes push
    their value into every child checkbox key via an `on_change` callback
    (`_sync_children_to_master`).
  - All `accept_*` keys are deleted on a state reset (new file / changed fix
    selection, via `_start_review`), otherwise stale review choices leak into
    the next review wherever line indices collide. The final screen's
    **"Start over" deliberately keeps them**: it re-runs the same file through
    the same fixes, so the indices still line up and the user's choices carry
    over into the redo instead of every fix being re-reviewed from scratch.
  - `st.session_state["history"]` holds a stack of program snapshots (one
    pushed per completed step) that powers the "Back" button; every code path
    that advances `fix_index` must push onto it (use `_advance`, or mirror
    what the apply branches do).
  - Keys that must outlive a reset (the map size, the sidebar toggles) are
    deliberately **not** `accept_*`-prefixed.
- **G-code specifics learned from the real files** (`lmd_fixer/tests/`):
  - `G65 B0.0 F1000. D1 P8000` + following `M337`, and `G90 G0 A0.0`, are
    rotary-table commands unneeded when no rotary table is in use.
  - `M98 Pxxxx` calls a subprogram; consecutive calls with the same P value
    are redundant (the program is already loaded) and their `M325` +
    following `G4 X25.00` dwell are redundant too. The three lines are
    always consecutive, so `remove_repeated_p_calls` proposes each group as
    a single range `LineChange`, not three separate ones.
  - The P of `M98 Pxxxx` is an LMD **recipe** stored on the control panel
    (laser power, powder flow, nozzle gas). The file only names the number;
    `recipes.py` records what each one sets, by hand. P is typed as a
    number rather than picked from the database, because the database
    isn't the machine and may be incomplete. Every toolpath sets P once, in its header before the
    laser comes on. `change_recipes` rewrites only those digits, keeping `/`
    and the zero-padding, and never inserts a call into a toolpath that lacks
    one. It sits **before** `remove_repeated_p_calls` in `FIX_ORDER`, since
    that compares P values. A P changed after the collapse would leave a
    collapsed toolpath silently inheriting its neighbour's recipe. It
    proposes nothing without options, so its UI branch
    (`render_recipe_step`) must stay ahead of the "no changes" branch.
    Nothing changes until a P is set. Next planned use of the database:
    a dwell after a recipe change whose powder flow differs must stay, to
    let the flow settle.
  - `G4 X25.00` after a *genuine* P-value change is a dwell whose necessity
    depends on machine/program specifics not recoverable from the file, so
    it's always left to manual review, never auto-removed. A dwell is
    attributed to an `M98 Pxxxx` call only if the lines between them are
    blank or `M325`; any other command breaks the association
    (`remove_dwells._preceding_p_value`) and the dwell is labelled
    "unknown P value" rather than misattributed.
  - Section markers are standalone comment lines like
    `(1_LAYER_STEPOVER_TEST_PATH_COPY_5)` — distinguished from free-text
    header comments (e.g. `(PROJECT: ...)`) by containing no whitespace
    inside the parentheses. A section runs to the next marker or EOF.
  - `M323`/`M322` switch the laser on/off. Feed rate is modal: each
    toolpath sets `F` once on its plunge (`G1 Z0.0 F500.`) and later moves
    inherit it. `G65 ... F1000.` is a macro argument, not a feed.
  - The CAM emits curves as ~0.5 mm G1 chords (no G2/G3 at all) but
    straights as one or two very long moves.
  - **A toolpath marker is not a layer**: in `O1145.ptp` one marker block
    holds all four of a pad's layers at different Z, so the layers of a pad
    share one footprint on the maps.
  - Line endings in source `.ptp` files are CRLF (`O1145.ptp` is LF); output
    is always written back as CRLF (`GCodeProgram.to_text("\r\n")`) since
    that's what the machine controller expects, even though the UI displays
    with `\n` for readability.

## Maps — rules

- **Naming is part > toolpath.** A *part* is a footprint on the bed
  (`group_into_parts`); a *toolpath* is one `(SECTION_NAME)` block. The
  section-removal fix calls a block a "section", after its name.
  `O1140 - Original.ptp` is 45 one-toolpath parts, not 45 layers of one
  part; stacked layers of one build group into one part.
- **Map colours are validated** with the dataviz skill's
  `validate_palette.py` against the app surface (#272b33) before use, and a
  state never rests on colour alone. The desktop pairs live in
  `desktop/theme.py`; the Streamlit ones are duplicated in `app.py` until
  it's removed.
- The rest of this section is about the Streamlit maps, which go when
  `app.py` does; the desktop map's rules are under Architecture.
- **Section-removal map** (`build_removal_map`): clicking a toolpath toggles
  its checkbox in the `on_select` callback, which then bumps
  `{prefix}_mapver` (part of the chart key) so the chart comes back with an
  empty selection — without that, a second click on the same toolpath
  changes nothing and never fires. Its kept/to-remove colours were validated
  as a pair; to-remove is also dashed, tinted and marked ✕, so it never rests
  on colour alone.
- **P recipe map** (`build_recipe_map`, `render_recipe_step`) is built the
  same way: a click toggles the toolpath's tick in `on_select` and bumps
  `{prefix}_mapver`; both maps read clicks through
  `toolpath_from_map_selection`. Set/Reset act on the ticked toolpaths and
  then untick them, so the map shows the result rather than the selection.
  Two things here are deliberate:
  - The tick boxes have no `value=`, since callbacks write their keys and
    Streamlit warns about a keyed widget given both.
  - **Set is not disabled while the P box is empty.** A typed number only
    reaches the app when the box loses focus, and clicking Set is what does
    that. Disabling on it left a dead button under the cursor. The callback
    warns instead.

  Unchanged/new-P colours (`#3987e5`/`#d95a1d`) were validated as a pair.
  New P is also drawn thicker with a bold label, and font weight can't be
  data-driven, so the labels are two layers.

## Test files

`lmd_fixer/tests/` holds real example files, useful for verifying a fix's
output against a known-good target, not just for eyeballing regex matches:

- `O1140 - Original.ptp` (unedited, 45 one-toolpath coupons) and `O1140.ptp`
  (the user's manually-fixed reference version).
- `O1140 - Original - 1 path.ptp`: a single toolpath of it, for quick runs.
- `O1145.ptp`: a 17-pad Z-step sweep, 4 layers per marker block, LF endings.
- `O1162-AsPosted.ptp` / `O1162-Edited.ptp`: a 2-layer run of the same
  sweep, unedited and manually fixed, so a second reference pair.

**Check anything touching toolpath analysis or the maps against `O1145.ptp`
as well as the O1140 pair**: its pads are rasters rather than contoured
coupons, and its layers live inside single markers, so it exercises paths
O1140 cannot reach. Two past path-analysis bugs passed on O1140 while making
O1145 useless. These `.ptp` files are git-ignored (proprietary project
data, kept out of the public repo), so they exist only on the user's machine —
a fresh clone won't have them. Don't quote identifying content from them
(project codes, people) in tracked files.

Full-accept of the whole pipeline reproduces the reference except for known
review-choice differences: the reference keeps all 45 `G90 G0 A0.0` lines and
3 of the 9 genuine-P-change dwells, and removes the `M325` alongside each dwell
it removes at a genuine P change (no fix covers that M325 case yet). Ignore
the `(PROGRAM CREATED ...)` timestamp header line when diffing.

## Windows build

`packaging/build.ps1` makes `dist/LMD-Fixer-<version>-win64.zip`: a PyInstaller
**one-folder** build (one-file unpacks ~200 MB to temp on every launch and is
what antivirus flags), built from the pins in `packaging/requirements-build.txt`
in a venv under `%LOCALAPPDATA%\LMD-Fixer-build`, which is kept out of OneDrive
because a build is thousands of files. Things that look removable but aren't:

- `cli.py` is the launcher for both `lmd-fixer` and the `.exe`. Its
  `LOCAL_APP_ARGS` each fix a real failure: `server.address=127.0.0.1`
  (Streamlit otherwise listens on every interface, exposing uploaded files to
  the network), `server.headless=true` (otherwise Streamlit's first-run email
  prompt blocks a double-clicked exe on console input — the browser is opened
  by `_open_browser_when_ready` once `/_stcore/health` answers instead), and
  `global.developmentMode=false` (a frozen build has no site-packages, so
  Streamlit thinks it's a Streamlit source checkout and ignores the port). The
  port is the first free one from 8501, so a dev server or second copy doesn't
  stop it starting.
- Streamlit has no PyInstaller hook, so the spec `collect_all`s it and copies
  its metadata. `app.py` ships as a data file as well as being analysed
  (`streamlit run` executes it from disk), and the app's own imports are found
  by listing `lmd_fixer`'s submodules, since the entry script only imports
  `cli`.
- A build can't edit the `fix_settings.toml` inside `_internal`, so the
  launcher sets `LMD_FIXER_SETTINGS` (read by `settings.settings_path()`) to the
  copy beside the `.exe`, falling back to `%APPDATA%\LMD-Fixer`. The sidebar
  shows which file is in use.
- `--selftest [IN OUT]` loads the app through AppTest and runs the reference
  pipeline (accept everything except section removal). The build script runs
  it through both the `.exe` and the source and fails unless the outputs are
  byte-identical, which is the check that the frozen app really behaves the
  same.

## Testing changes

Run `python -m pytest` (install with `pip install -e .[dev]`). The suite is in
`tests/` at the top level; `lmd_fixer/tests/` holds only the example `.ptp`
files. Most tests use `sample_program` from `tests/conftest.py`, a small
synthetic program shaped like the real files (three toolpaths, recipe calls,
rotary commands, footer), so they run on a fresh clone. Tests on the real
files use the `real_file` fixture, which skips when a file isn't present.
When changing or adding a fix, add a test against the synthetic program, and
check the real files too.

Desktop tests (`tests/test_desktop.py`, pytest-qt) drive the real widgets
with Qt's offscreen platform, which `tests/conftest.py` selects. Give
`MainWindow` a `QSettings` on a temp ini file so tests don't touch the real
settings. To look at the window, render it offscreen with `widget.grab()`,
and set `QT_QPA_FONTDIR=C:\Windows\Fonts`, or the offscreen platform draws
every glyph as a box.

Full-pipeline check against the references (`tests/test_reference_files.py`):
a `ReviewSession` over `FIX_ORDER`, applying every step with its defaults,
which accepts every change except in `remove_named_sections` (accepting
everything there removes all 45 sections and leaves 21 lines). Done that way the result differs
from `O1140.ptp` by exactly the documented deltas — 45 `G90 G0 A0.0`, 3
dwells, 6 `M325`, plus blank lines — and from `O1162-Edited.ptp` by only the
3 genuine-P-change dwells that reference keeps (it removes every
`G90 G0 A0.0`, unlike `O1140.ptp`).

The UI is reachable headlessly with `streamlit.testing.v1.AppTest`, which is
worth the setup for changes to either review step that has a map. Two things
make it work: `st.file_uploader` has to be stubbed (patch it, then `exec`
`app.py` from a wrapper script that AppTest loads), and **session state has to
be seeded *after* the first `at.run()`** — the first run calls `_start_review`,
which clears every `accept_*` key and would wipe anything set beforehand.
Prefer driving the real widgets (`at.checkbox[...].set_value`) over writing
state directly: a `data_editor` caches a base frame per version key, so state
poked in behind it is overwritten by the write-back on the next run. Session
state keeps a frame per version, so read the newest, not the first match.
Importing `lmd_fixer.app` runs the whole script, so to test one of its pure
helpers outside AppTest, pull the function out with `ast` rather than
importing the module.

AppTest also can't drive anything *inside* an `st.dialog`. A click there
reruns the whole script, the sidebar button that opened the dialog reads
False, and the dialog vanishes, whereas a real browser reruns only the
dialog. Test the material database dialog in a browser too, and point
`LMD_FIXER_RECIPES` at a scratch file so the real database isn't touched.
Over the DevTools protocol, check for elements with `!!document.querySelector(...)`:
a returned DOM node serialises to `{}`, which Python reads as false. Table
cells (`st.data_editor`/`st.dataframe`) are drawn on a canvas, so their text
isn't in `innerText`.

Chart interaction (`on_select`) can only be checked in a real browser: run a
dev server, drive headless Edge over the DevTools protocol (`websockets` is
installed), upload with `DOM.setFileInputFiles` (re-query the input and retry
until the file shows), then click with `Input.dispatchMouseEvent`. Take click
coordinates from a screenshot — the legend shifts the plot down, so estimating
them from the chart's spec size misses. Edge's `--screenshot` / `--dump-dom`
capture before the websocket delivers the page, so they show only the loading
skeleton of a Streamlit app; they're fine for a static HTML render of a chart
spec.
