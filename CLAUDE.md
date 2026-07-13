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
