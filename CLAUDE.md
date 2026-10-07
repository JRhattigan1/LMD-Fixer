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

Longer design notes live in `docs/`:

- `docs/feed-adjustment-design.md` — the reasoning behind
  `adjust_section_feeds`, its review step and both toolpath maps. **Read it
  before changing any of those**; the rules below are the short version.
- `docs/height-feedback-brief.md` — brief for a separate, not-yet-started
  project (closed-loop layer-height correction). Not part of this app.

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
- `lmd_fixer/settings.py` + `lmd_fixer/fix_settings.toml` — which fixes the
  app offers. Read fresh on every page load; `LMD_FIXER_SETTINGS` overrides
  the path (the Windows build points it beside the `.exe`). Only the menu is
  affected — `run_fix` runs any fix named explicitly. The shipped file has
  `adjust_section_feeds = false`.
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
  - Keys that must outlive a reset (display preferences, the ramp chord
    length, the sidebar toggles) are deliberately **not** `accept_*`-prefixed.
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
    header comments (e.g. `(PROJECT: ...)`) by containing no whitespace
    inside the parentheses. A section runs to the next marker or EOF.
  - Feed rate is modal: each toolpath sets `F` once on its plunge
    (`G1 Z0.0 F500.`) and later moves inherit it. `G65 ... F1000.` is a
    macro argument, not a feed. `M323`/`M322` switch the laser on/off.
  - The CAM emits curves as ~0.5 mm G1 chords (no G2/G3 at all) but
    straights as one or two very long moves. Several designs below exist
    because of that lopsidedness.
  - **A toolpath marker is not a layer**: in `O1145.ptp` one marker block
    holds all four of a pad's layers at different Z.
  - Line endings in source `.ptp` files are CRLF (`O1145.ptp` is LF); output
    is always written back as CRLF (`GCodeProgram.to_text("\r\n")`) since
    that's what the machine controller expects, even though the UI displays
    with `\n` for readability.

## `adjust_section_feeds` and the maps — rules

Short form only; the reasoning, and the real-file numbers behind each rule,
are in `docs/feed-adjustment-design.md`.

- **Naming is part > toolpath > section.** A *part* is a footprint on the bed
  (`group_into_parts`), a *toolpath* is one `(SECTION_NAME)` block, a
  *section* is a stretch of a toolpath with its own feed. Don't reuse "part"
  for a slice of a toolpath. (`O1140 - Original.ptp` is 45 one-toolpath parts,
  not 45 layers of one part.)
- **Changes are all-or-nothing.** The fix restates the original feed after an
  override, so its changes depend on each other; the UI previews and applies
  them together. It proposes nothing without options, so its branch must stay
  ahead of the generic "no changes" branch in `app.py`.
- **Ramps:** `feeds_state` (start) and `ramps_state` (end) are keyed
  identically; a ramp needs both ends and wins over `feeds`. Feeds snap to
  `RAMP_FEED_STEP`; long plain-XY G1 moves are split into `chord_mm` chords
  (never Z, rotary or G2/G3), last chord on the original endpoint. A
  `"modified"` change may hold several `\n`-joined lines, which
  `apply_accepted_changes` splits back out.
- **The map colours per vertex** (`t` from `path_points`), and
  `densify_ramped_path` adds display-only vertices so a ramp on a one-segment
  straight still shades.
- **Watch the map payload.** Keep `pts` to encoded columns; tooltip text is
  joined per section via `transform_lookup`; drop unused columns (e.g.
  `Layer`) rather than just hiding them.
- **Slots render in call order:** section table, then editor, then map. Don't
  reorder — the editor would lag a rerun behind.
- **The cursor lives in session state, set in the map's `on_select`
  callback** (`cursor_from_selection`), because only inside the callback does
  an empty selection unambiguously mean "clicked empty space". AppTest can't
  fire `on_select`; tests set `{prefix}_cursor` directly.
- **Callbacks bind what they need as default arguments**, and buttons that
  only set state use `on_click=` rather than `if st.button(): ...; st.rerun()`.
  `format_func` labels are built eagerly for the same reason.
- **Feed colour ramps** (`MAP_RAMPS`) are validated with the dataviz skill's
  `validate_palette.py --ordinal`; re-run it before adding or editing one.
  Plasma stays the default — Rainbow's green and yellow share a lightness.
- **Segmentation:** a junction counts only where moves actually meet
  (`joined_at`); a corner needs both the angle (`CORNER_DEG`) and a long
  enough move (`CORNER_MIN_MOVE_MM`), and is never absorbed into an arc.
  Re-check the shapes listed in the design notes after any change.
- **Layers compose with** the chosen division; `kind`/`layer` are carried per
  move; layers prefix `spec_for`.
- The bulk-edit panel was removed on purpose (2026-09-21); only the "split
  every toolpath into lines & arcs" checkbox stays.
- **Section-removal map** (`build_removal_map`): clicking a toolpath toggles
  its checkbox in the `on_select` callback, which then bumps
  `{prefix}_mapver` (part of the chart key) so the chart comes back with an
  empty selection — without that, a second click on the same toolpath
  changes nothing and never fires. Its kept/to-remove colours were validated
  as a pair; to-remove is also dashed, tinted and marked ✕, so it never rests
  on colour alone.

## Test files

`lmd_fixer/tests/` holds real example files (`O1140 - Original.ptp` is the
unedited original; `O1140.ptp` is the user's manually-fixed reference version)
— useful for verifying a fix's output against a known-good target, not just
for eyeballing regex matches. **Check anything touching segmentation or the
maps against `O1145.ptp` as well as the O1140 pair**: it is a 17-pad Z-step
sweep, its pads are rasters rather than contoured coupons, and its layers live
inside single markers, so it exercises paths O1140 cannot reach. Both of the
segmentation bugs in the design notes passed on O1140 while making O1145
useless. These `.ptp` files are git-ignored (proprietary project data, kept
out of the public repo), so they exist only on the user's machine — a fresh
clone won't have them. Don't quote identifying content from them (project
codes, people) in tracked files.

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
worth the setup for changes to either review step that has a map. Two things
make it work: `st.file_uploader` has to be stubbed (patch it, then `exec`
`app.py` from a wrapper script that AppTest loads), and **session state has to
be seeded *after* the first `at.run()`** — the first run calls `_start_review`,
which clears every `accept_*` key and would wipe anything set beforehand.
Prefer driving the real widgets (`at.checkbox[...].set_value`) over writing
division state directly: the tables cache a base frame per `ver`, so state
poked in behind them is overwritten by the write-back on the next run. Session
state keeps a frame per `ver`, so read the newest, not the first match.
Importing `lmd_fixer.app` runs the whole script, so to test one of its pure
helpers outside AppTest, pull the function out with `ast` rather than
importing the module.

Chart interaction (`on_select`) can only be checked in a real browser: run a
dev server, drive headless Edge over the DevTools protocol (`websockets` is
installed), upload with `DOM.setFileInputFiles` (re-query the input and retry
until the file shows), then click with `Input.dispatchMouseEvent`. Take click
coordinates from a screenshot — the legend shifts the plot down, so estimating
them from the chart's spec size misses. Edge's `--screenshot` / `--dump-dom`
capture before the websocket delivers the page, so they show only the loading
skeleton of a Streamlit app; they're fine for a static HTML render of a chart
spec.
