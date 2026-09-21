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
data files are excluded from wheels).

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
- `lmd_fixer/pipeline.py` — `run_fix` (runs one fix), `apply_accepted_changes`
  (rebuilds a program keeping only the changes the user accepted, handling
  both single lines and ranges), and `run_pipeline` (applies every fix
  unconditionally with no review — kept for scripting, not used by the UI).
- `lmd_fixer/app.py` — Streamlit UI. Runs fixes one at a time in a fixed pipeline
  order (`FIX_ORDER`), independent of sidebar tick order. Holds
  `original_program` and `current_program` in `st.session_state` so the
  final screen can render a side-by-side diff of the untouched upload
  against the fully-reviewed result.

## Conventions specific to this codebase

- **Fix order is meaningful and enforced**, not just cosmetic. Later fixes
  depend on earlier ones having already run (e.g. dwell review only sees
  dwells that survive repeated-P-call collapsing). If you add a fix with an
  ordering dependency, add its id to `FIX_ORDER` in `app.py` at the correct
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
    neighbour. Both test programs split into 29 parts per layer — 16
    straights and 13 arcs. Geometry cuts land exactly on move boundaries, so
    they're fed through the same `splits` machinery and the midpoint rule
    reproduces the runs move for move.
  - Line endings in source `.ptp` files are CRLF; output is written back as
    CRLF (`GCodeProgram.to_text("\r\n")`) since that's what the machine
    controller expects, even though the UI displays with `\n` for
    readability.
- `lmd_fixer/tests/` holds real example files (`O1140 - Original.ptp` is the
  unedited original; `O1140.ptp` is the user's manually-fixed reference
  version) — useful for verifying a fix's output against a known-good
  target, not just for eyeballing regex matches. These `.ptp` files are
  git-ignored (proprietary project data, kept out of the public repo), so
  they exist only on the user's machine — a fresh clone won't have them. Full-accept of the whole
  pipeline reproduces the reference except for known review-choice
  differences: the reference keeps all 45 `G90 G0 A0.0` lines and 3 of the
  9 genuine-P-change dwells, and removes the `M325` alongside each dwell it
  removes at a genuine P change (no fix covers that M325 case yet). Ignore
  the `(PROGRAM CREATED ...)` timestamp header line when diffing.

## Testing changes

There's no formal test suite yet. When changing or adding a fix, verify by
running it against `lmd_fixer/tests/O1140 - Original.ptp` via a quick Python
snippet (see recent commits for the pattern: `run_fix` then
`apply_accepted_changes` with `{c.original_index for c in result.changes}`
for full-accept, or a subset to check partial-accept behaves correctly) and
sanity-check the before/after line counts and a few sample changes.
