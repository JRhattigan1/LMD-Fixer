# LMD-Fixer

A Streamlit tool for cleaning up laser metal deposition (LMD) G-code/PTP
programs before they're run on the machine. Upload a file, walk through a
fixed sequence of fixes, review and accept/reject each proposed change, and
download the corrected program.

## Setup

```
pip install -r requirements.txt
```

## Running

```
streamlit run app.py
```

This opens the UI in your browser (defaults to http://localhost:8501).

## How it works

1. Upload a `.ptp`/`.nc`/`.gcode`/`.txt` file.
2. Tick which fixes to run in the sidebar.
3. Fixes run one at a time, in a fixed order (see below), regardless of the
   order you ticked them. For each fix, every proposed change is listed
   individually with a checkbox so you can accept or reject it, plus an
   "accept all" shortcut when everything looks right.
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
   opt in per section (or via "remove all") rather than opting out.
3. **Remove repeated M98/M325 program calls** (`remove_repeated_p_calls`) —
   an `M98 Pxxxx` call that repeats the same P value as the previous call is
   a no-op, so that call, its `M325` line, and the following `G4 X25.00`
   dwell (if present) are removed automatically. If the P value actually
   changes, the pair is left in place.
4. **Remove G4 X25.00 dwells** (`flag_dwells`) — the dwells that remain
   after step 3 follow a genuine P-value change, so their necessity can't be
   determined from the file alone. Each is proposed for removal along with
   the P value it follows, and you decide per occurrence whether to keep it.

## Project layout

```
app.py                          Streamlit UI
lmd_fixer/
  gcode.py                      GCodeProgram: load/save, line-based model
  pipeline.py                   run_fix / apply_accepted_changes
  fixes/
    __init__.py                 Fix base class, LineChange, @register registry
    remove_rotary_table.py
    remove_named_sections.py
    remove_repeated_p_calls.py
    flag_dwells.py
sample_data/                    example .ptp files used during development
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
   `FIX_ORDER` in `app.py`.

It will then appear automatically as a checkbox in the sidebar and go
through the same per-change review UI as the existing fixes (unless you give
it a custom rendering branch, as `remove_named_sections` does for its
default-to-keep toggle behaviour).
