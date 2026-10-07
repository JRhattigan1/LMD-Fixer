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
- `lmd_fixer/pipeline.py` — `FIX_ORDER` (the fixed run order), `run_fix` (runs one fix), `apply_accepted_changes`
  (rebuilds a program keeping only the changes the user accepted, handling
  both single lines and ranges), and `run_pipeline` (applies every fix
  unconditionally with no review — kept for scripting, not used by the UI).
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
  silently gut the program. When adding a fix that removes large chunks or
  whose correctness is genuinely uncertain, default to the safe (keep) side
  and give the UI its own rendering branch in `app.py` rather than reusing
  the generic accept-by-default checklist.
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
  a keyed checkbox ignores its `value=` once the key exists in session
  state, so the "Accept all" / "Remove all sections" master checkboxes push
  their value into every child checkbox key via an `on_change` callback
  (`_sync_children_to_master`). All `accept_*` keys are deleted both on
  state reset (new file / changed fix selection) and on "Start over" —
  otherwise stale review choices leak into the next review wherever line
  indices collide. `st.session_state["history"]` holds a stack of program
  snapshots (one pushed per completed step) that powers the "Back to
  previous fix" button; every code path that advances `fix_index` must push
  onto it (use `_advance`, or mirror what the apply branches do).
- **G-code specifics learned from the real files** (`lmd_fixer/tests/`):
  - `G65 B0.0 F1000. D1 P8000` + following `M337`, and `G90 G0 A0.0`, are
    rotary-table commands unneeded when no rotary table is in use.
  - `M98 Pxxxx` calls a subprogram; consecutive calls with the same P value
    are redundant (the program is already loaded) and their `M325` +
    following `G4 X25.00` dwell are redundant too. The three lines are
    always consecutive, so `remove_repeated_p_calls` proposes each group as
    a single range `LineChange`, not three separate ones.
  - `G4 X25.00` after a *genuine* P-value change is a dwell whose necessity
    depends on machine/program specifics not recoverable from the file, so
    it's always left to manual review, never auto-removed. A dwell is
    attributed to an `M98 Pxxxx` call only if the lines between them are
    blank or `M325`; any other command breaks the association
    (`remove_dwells._preceding_p_value`) and the dwell is labelled
    "unknown P value" rather than misattributed.
  - Section markers are standalone comment lines like
    `(1_LAYER_STEPOVER_TEST_PATH_COPY_5)` — distinguished from free-text
    header comments (e.g. `(PROJECT: DIGF-CRAD-06736)`) by containing no
    whitespace inside the parentheses. A section runs to the next marker or
    EOF.
  - Feed rate is modal: each toolpath sets `F` once on its plunge
    (`G1 Z0.0 F500.`) and later moves inherit it. `G65 ... F1000.` is a
    macro argument, not a feed. `M323`/`M322` switch the laser on/off.
    `adjust_section_feeds` uses a three-level naming that the UI and the
    module stick to, because a program holds several of each: a **part** is
    one deposition area (a footprint on the bed), a **toolpath** is one
    `(SECTION_NAME)` block, and a **section** is a stretch of a toolpath with
    its own feed. Parts come from `group_into_parts`, which groups toolpaths
    by overlapping XY footprint (measured path only, so a rapid to a
    tool-change position doesn't stretch the area) and numbers them in bed
    order, row then column. `O1140 - Original.ptp` is **not** 45 layers of
    one part as it first appears — it's 45 separate 30x50 coupons on a 12x4
    grid, none overlapping, so it comes out as 45 parts of one toolpath each;
    stacked layers of a single build group into one part with many toolpaths.
    Don't reintroduce "part" as a name for a slice of toolpath — that was the
    old naming and it collided with the machining sense of the word.
    It splits toolpaths by laser-on length and, rather
    than editing only the overridden part, restates the original feed on the
    first move after it — so its changes are interdependent and the UI
    (`render_feed_sections_step`) previews and applies them all-or-nothing
    instead of per-line accept. It proposes nothing without options, so it
    must stay ahead of the generic "no changes" branch in `app.py`.
  - A section takes either one feed or a **ramp** between a start and an end
    feed. In the UI these are two dicts keyed identically — `feeds_state`
    holds the start, `ramps_state` the optional end — and the fix takes them
    as `ramps: {(toolpath, section): (start, end)}`, which wins over `feeds`
    for that section. A ramp needs both ends; one alone is ignored. Two
    numbers from the real files drive the whole design, and neither is
    obvious: feeds are snapped to `RAMP_FEED_STEP` (25 mm/min) so a run of
    moves rounding to the same value writes **one** F word rather than dozens
    of near-identical ones, and long moves are cut into `chord_mm` chords
    because the CAM emits curves as ~0.5 mm chords but **straights as one or
    two very long moves** — a 26 mm straight is 2 moves, so without splitting
    a ramp across it could only step once. Only plain XY G1 moves are split
    (`_subdividable`); a Z plunge, a rotary move or a real G2/G3 arc is left
    whole, and the last chord lands on the original endpoint exactly, so the
    path is unchanged to the 3 dp the files are written at. Chord length is a
    machine/material preference, not a per-file review choice, so it lives in
    a ⚙ popover (`render_ramp_settings`) under `RAMP_CHORD_KEY` — like the map
    display keys, deliberately **not** `accept_*`-prefixed, so it outlives the
    state reset. Ramping is the one thing here that *adds* lines: a
    `"modified"` `LineChange` may now carry several lines joined by `\n`, and
    `apply_accepted_changes` splits them back out, since a program line must
    never contain a newline itself.
  - The map colours **per vertex**, not per section, so a ramp shades along
    its length the way the machine will run it. `path_points` therefore
    returns `t` (0→1 along each section) and `build_toolpath_map` takes
    `ramp_for_section` to interpolate. A line mark is one flat colour per
    *segment*, so a section only reads as a gradient if it has vertices to
    break it up — and the same lopsidedness that forces chord-splitting in the
    G-code bites here: 7 of the 16 straights in a test toolpath arrive as a
    single segment, which can only ever be one colour, so a ramp on a straight
    looked like it hadn't applied. `densify_ramped_path` inserts plotting
    vertices inside ramped sections only, one per `RAMP_FEED_STEP` the feed
    crosses — exactly the number of distinct colours the section can show, so
    it costs nothing extra. It's display-only: `path_points` stays geometry-
    honest and the G-code was always right.
  - Map payload is worth watching: the whole-bed view is ~11,000 vertices, and
    it is rebuilt and re-sent on **every** rerun. Keep `pts` to the columns
    Vega actually encodes — everything the tooltip shows is per *section* and
    is joined on in the browser via `transform_lookup` against `section_desc`
    (~1,300 rows), and x/y are rounded to the 3 dp the source files use. That
    took the spec from 5.19 MB to 3.08 MB. Note that layers sharing one
    DataFrame is **not** where the savings are: Altair already hashes identical
    frames into a single `datasets` entry, so `alt.layer(..., data=pts)` is
    about saying once where the vertices come from, not about size. A `format_func` that closes over
    `feeds_state` re-labels its options from whatever the dict holds *later*
    in the run, after the section table has written to it — so the jump
    selectbox's labels are built eagerly into a dict and bound as a default
    argument, the same rule the widget callbacks follow.
    The step is laid out as **map beside editor** in one `st.columns` row, so
    a section can be picked and re-fed without scrolling between the two —
    that was a direct complaint about the earlier stacked layout. Both are
    *slots* (`map_slot`, `editor_slot`) filled later in the run: Streamlit
    places output where the container was created but runs it in call order,
    so the section table executes first (reading its edits), then the editor
    (showing fresh values), then the map (showing everything). Keep that
    order — rendering the editor before the table puts its feed box one rerun
    behind. Dividing (step 1), the all-sections table and the line-by-line
    change list are expanders so they stay out of the way; the map is the
    working preview, not the change list.
    The map (`build_toolpath_map`, Altair) doubles as picker and result view
    since it's coloured by the feed each section ends up at. **⤢ Whole
    program** clears the cursor and the part filter in one `on_click`; clicking
    empty space on the map and the editor's ✕ do the same for the cursor.
    Its click selections are read from session state up front and scope the
    tables. A point selection arrives as a list of
    `{"key": ...}` dicts, or `{}` when empty. The map carries *two* point
    selections — a per-toolpath one on the bounding boxes and a per-part one
    (`pkey` = `"<toolpath key>|<section no>"`) on a fat transparent copy of the
    path laid over everything else, which is how a single section is picked
    by eye. Their conditions are combined with `&`, which works because an
    empty selection matches everything.
  - Picking a section on the map sets a *cursor* (`{prefix}_cursor`, a
    `(toolpath key, section number)` pair) and the section editor edits exactly
    that section — the feed box, Clear, and "apply to every arc/straight in
    this toolpath" all act on it, and the cursor scopes the tables to its
    toolpath. Prev/Next/Jump move the cursor too, which is why it lives in
    session state instead of being read off the chart: a chart selection
    can't be written back to the browser from Python. It is maintained in the
    map's **`on_select` callback** (`cursor_from_selection`), not by reading
    the chart's state each run. That distinction is the whole point: read
    outside the callback, an empty selection is ambiguous — it could be a
    click on empty space or a chart that re-rendered — so clearing had to be
    ignored, and then clicking the map could never deselect. Inside the
    callback an empty selection is unambiguously a user clearing it, so
    clicking empty space resets to the whole program, as do ✕ and ⤢.
    `cursor_from_selection` is module-level and pure so it can be tested
    directly; AppTest can't fire a chart's `on_select`, so tests simulate a
    click by setting `{prefix}_cursor` the way the callback would. The cursor
    highlight is a data-driven layer keyed on `pkey`, not a selection
    condition, for the same reason.
  - Feed is coloured by its **actual value**, on a ramp chosen in the map's
    ⚙ Display popover (`render_map_settings`, `MAP_RAMPS`). Every ramp is
    trimmed so both ends clear the app surface (#272b33) and validated with
    the dataviz skill's `scripts/validate_palette.py --ordinal`; don't add or
    edit one without re-running it. Plasma is the default. Plasma and Viridis
    rise monotonically in lightness, so brighter always means faster and the
    ordering survives colour-blindness; **Rainbow does not** — its green and
    yellow sit 0.005 apart in lightness, so two different feeds can look
    identical. It's there because the hue variety was asked for, not because
    it reads best, so leave the default alone. A single-hue blue ramp was the
    original default and proved too flat to read at a glance, which is why
    it's now one option among four. Sections given a new feed are drawn
    thicker, so "what did I change" doesn't depend on the colour channel.
    `MAP_RAMP_KEY`/`MAP_SIZE_KEY` are deliberately **not** `accept_*`-prefixed:
    a display preference has to outlive the state reset that clears review
    choices.
  - Only the toolpaths in scope are plotted. With nothing picked that's every
    part, which for a coupon array is the useful overview of the bed; picking
    one zooms to it, and for a stacked build (layers sharing a footprint) it's
    the only way to see a single layer at all.
  - Buttons that only move the cursor or set state use `on_click=` callbacks,
    never `if st.button(): ...; st.rerun()`. The click already reruns the
    script; forcing a second one is what made stepping through sections
    visibly redraw the page twice.
  - The bulk-edit panel was removed deliberately (2026-09-21) to simplify the
    step — per-section editing plus direct editing of the parts table covers
    it. The one bulk control kept is the "split every toolpath into lines &
    arcs" checkbox, because a file holds ~45 near-identical toolpaths.
  - Widget callbacks in `render_feed_sections_step` must bind what they need
    as default arguments. They outlive the run that created them, and the
    step reuses names like `feed_key` further down (the parts-table
    write-back loop), so a closure would fire against whatever the last
    iteration left behind — the symptom is a feed landing on the last part in
    the table instead of the edited one.
  - The CAM output contains no G2/G3: every curve is a fan of short G1
    chords (~0.5 mm, turning ~9° each), so `segment_by_geometry` recovers
    curvature from the geometry — local radius at a junction is
    `mean(move length) / turn angle`. Two rules stop it shredding the path:
    an *arc* needs at least two consecutive junctions turning the same way
    (a lone turning junction is a sharp corner, which ends a straight run but
    isn't an arc), and runs under `MIN_SEGMENT_MM` are absorbed into a
    neighbour. Both O1140 programs split into 29 sections per layer — 16
    straights and 13 arcs. Geometry cuts land exactly on move boundaries, so
    they're fed through the same `splits` machinery and the midpoint rule
    reproduces the runs move for move.
    Two further rules exist because the radius test alone was silently wrong
    on real files, and both cost a whole file's worth of usefulness before
    they were found. **A junction only counts where the moves actually meet.**
    `moves` is the *measured* path, with the laser-off repositioning already
    dropped, so a raster pad arrives as parallel strokes that never join —
    all 78 laser-on moves of `PAD2` in `O1145.ptp` are disconnected from the
    next, and reading them as one continuous straight collapsed every pad in
    that file to a single section. `joined_at` gates the turn measurement,
    the breaks, the sliver absorption and the arc re-join. **And a sharp
    corner is always split out, never absorbed into an arc.** That rule needs
    all three of its parts, and the reasoning is worth keeping because each
    part alone is wrong:
    - The radius test divides by move length, so on its own it misses a
      corner between *long* moves — a 90° turn between two 28 mm moves reads
      as a 40 mm radius and slips through, collapsing a rectangle drawn as
      four long moves into one "straight".
    - But **angle alone is worse.** Genuine arcs in `O1140 - Original.ptp`
      hold junctions turning up to **46.6°** (mean 11.4°), so a bare
      `> CORNER_DEG` rule shreds real curves. What separates the two is the
      length of what runs into the junction: a curve arrives as ~0.5 mm
      chords, a polygon corner sits between multi-millimetre straights —
      hence `CORNER_MIN_MOVE_MM` (2 mm) on the longer of the two moves.
    - And `arc_junction` must subtract corners (`sustained(j) and not
      corner[j]`), or a polygon still reads as a curve: the four 90° turns of
      a rectangle with 8 mm sides are each "turning" (radius 5.1 mm) and all
      bend the same way, so `sustained` called the whole shape one arc and no
      corner could split it.

    Shapes worth re-checking after any change here: rectangles at 30/50, 8, 4
    and 2.5 mm sides, an L, a zigzag and an octagon should each split at
    every corner, while a circle of 0.5 mm chords stays one arc and
    straight-arc-straight stays three sections.
  - **A toolpath marker is not a layer.** In `O1145.ptp` one
    `(PAD2_1.2MM_1.28Z_4LAYERS)` block holds all four of its layers, at Z
    0.0 / 1.28 / 2.56 / 3.84 — the Z-step the name promises. `segment_by_layer`
    recovers them from the Z each move deposits at (`_Move.z`), starting a new
    layer when Z departs from the *current layer's* Z by more than
    `LAYER_STEP_MM`, so a path that merely isn't flat stays one layer. A
    continuously climbing path has no layers to find and comes back cut every
    `LAYER_STEP_MM` of climb; that division is meaningless, which is why the
    UI shows the layer count and the Z levels and lets the operator decide
    rather than dividing on its own.
    Layers **compose with** whichever division is chosen rather than replacing
    it — ticking Layers and Lines & arcs gives one section per straight/arc
    *within* each layer. That is why `kind` and `layer` are carried per *move*
    in `_analyse` and read off each section's first move, instead of being
    indexed positionally off the run list: once layer cuts are merged into the
    geometry cuts, sections and runs are no longer the same list. Layers also
    prefix `spec_for` (`"L+geo"`), since toggling them renumbers every section
    and must invalidate the feed keys.
    Two numbers from the real files are worth keeping: a pad's layers
    cross-hatch, so `PAD2` comes out 15 / 24 / 15 / 24 strokes per layer (a
    28×18 mm pad at 1.2 mm stepover, rastered the long way then the short
    way), and the 17 pads have *different* Z-steps — that file is a Z-step
    sweep, so the layer Z values differ per pad and can't be assumed shared.
    On the map, layers of one build occupy the same footprint and stack into
    an unreadable pile, so the **Show layer** filter beside the map is the
    only way to see a single layer of a stacked build at all. The `Layer`
    tooltip column is *dropped* from `section_desc`, not merely left out of
    the tooltip field list, when nothing is layer-split — the frame is
    serialised into the spec whole, so an unused column is pure payload.
  - Line endings in source `.ptp` files are CRLF; output is written back as
    CRLF (`GCodeProgram.to_text("\r\n")`) since that's what the machine
    controller expects, even though the UI displays with `\n` for
    readability.
- `lmd_fixer/tests/` holds real example files (`O1140 - Original.ptp` is the
  unedited original; `O1140.ptp` is the user's manually-fixed reference
  version) — useful for verifying a fix's output against a known-good
  target, not just for eyeballing regex matches. **Check anything touching
  segmentation against `O1145.ptp` as well as the O1140 pair**: it is a
  17-pad Z-step sweep, its pads are rasters rather than contoured coupons,
  and its layers live inside single markers, so it exercises paths O1140
  cannot reach. Both of the segmentation bugs above passed on O1140 while
  making O1145 useless. These `.ptp` files are
  git-ignored (proprietary project data, kept out of the public repo), so
  they exist only on the user's machine — a fresh clone won't have them. Full-accept of the whole
  pipeline reproduces the reference except for known review-choice
  differences: the reference keeps all 45 `G90 G0 A0.0` lines and 3 of the
  9 genuine-P-change dwells, and removes the `M325` alongside each dwell it
  removes at a genuine P change (no fix covers that M325 case yet). Ignore
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
  same. The UI itself was checked by driving headless Edge over the DevTools
  protocol; Edge's `--screenshot` / `--dump-dom` capture before the websocket
  delivers the page, so they show only the loading skeleton, even for a
  working dev server.

## Testing changes

There's no formal test suite yet. When changing or adding a fix, verify by
running it against `lmd_fixer/tests/O1140 - Original.ptp` via a quick Python
snippet (see recent commits for the pattern: `run_fix` then
`apply_accepted_changes` with `{c.original_index for c in result.changes}`
for full-accept, or a subset to check partial-accept behaves correctly) and
sanity-check the before/after line counts and a few sample changes.

Full-pipeline check against the reference: run `FIX_ORDER` accepting every
change **except** in `remove_named_sections`, where accepting everything
removes all 45 sections and leaves 21 lines. Done that way the result differs
from `O1140.ptp` by exactly the documented deltas — 45 `G90 G0 A0.0`, 3
dwells, 6 `M325`, plus blank lines.

The UI is reachable headlessly with `streamlit.testing.v1.AppTest`, which is
worth the setup for changes to `render_feed_sections_step`. Two things make it
work: `st.file_uploader` has to be stubbed (patch it, then `exec` `app.py` from
a wrapper script that AppTest loads), and **session state has to be seeded
*after* the first `at.run()`** — the first run calls `_start_review`, which
clears every `accept_*` key and would wipe anything set beforehand. Prefer
driving the real widgets (`at.checkbox[...].set_value`) over writing division
state directly: the tables cache a base frame per `ver`, so state poked in
behind them is overwritten by the write-back on the next run. Session state
keeps a frame per `ver`, so read the newest, not the first match.
