# Feed adjustment and the toolpath maps: design notes

Why `adjust_section_feeds` and its review step (`render_feed_sections_step` in
`app.py`) work the way they do, and why the section-removal map does too.
Each rule here cost a real bug or a real complaint to find. `CLAUDE.md` keeps
the one-line version of each one. This file holds the reasoning, so read it
before changing anything it covers.

Two real files are referred to throughout. `O1140 - Original.ptp` is a coupon
array, and `O1145.ptp` is a 17-pad Z-step sweep. Both are git-ignored and exist
only on the developer's machine.

## Naming: part, toolpath, section

Feed rate is modal: each toolpath sets `F` once on its plunge
(`G1 Z0.0 F500.`) and later moves inherit it. `G65 ... F1000.` is a macro
argument, not a feed. `M323`/`M322` switch the laser on/off.

A program holds several parts, toolpaths and sections, so the module and the
UI keep to three names:

- A **part** is one deposition area (a footprint on the bed).
- A **toolpath** is one `(SECTION_NAME)` block.
- A **section** is a stretch of a toolpath with its own feed.

Parts come from `group_into_parts`. It groups toolpaths by overlapping XY
footprint and numbers the parts in bed order, row then column. Only the
measured path counts towards a footprint, so a rapid to a tool-change position
doesn't stretch the area. `O1140 - Original.ptp` looks at first like 45 layers
of one part, but it's 45 separate 30x50 coupons on a 12x4 grid with no
overlaps, so it comes out as 45 parts of one toolpath each. Stacked layers of a
single build group into one part with many toolpaths. Don't bring back "part"
as a name for a slice of a toolpath. That was the old naming, and it collided
with the machining sense of the word.

The section-removal step uses the same parts to say where each block sits on
the bed. That step still calls a marker block a "section", after the fix's
name. In this file's terms it removes toolpaths.

The fix splits toolpaths by laser-on length. It doesn't only edit the
overridden section: it also restates the original feed on the first move
after it. Its changes therefore depend on each other, so the UI previews and
applies them all-or-nothing instead of accepting line by line. The fix proposes
nothing without options, so its UI branch must stay ahead of the generic "no
changes" branch in `app.py`.

## Ramps

A section takes either one feed or a **ramp** between a start feed and an end
feed. In the UI these are two dicts keyed identically: `feeds_state` holds the
start and `ramps_state` the optional end. The fix takes them as
`ramps: {(toolpath, section): (start, end)}`, which wins over `feeds` for that
section. A ramp needs both ends; one alone is ignored.

Two numbers from the real files drive the whole design, and neither is
obvious:

- Feeds are snapped to `RAMP_FEED_STEP` (25 mm/min), so a run of moves that
  round to the same value writes **one** F word rather than dozens of
  near-identical ones.
- Long moves are cut into `chord_mm` chords. The CAM emits curves as ~0.5 mm
  chords but **straights as one or two very long moves**. A 26 mm straight is
  2 moves, so without splitting, a ramp across it could only step once.

Only plain XY G1 moves are split (`_subdividable`). A Z plunge, a rotary move
or a real G2/G3 arc is left whole. The last chord lands exactly on the original
endpoint, so the path is unchanged to the 3 dp the files are written at.

Chord length is a machine/material preference, not a per-file review choice.
It therefore lives in a ⚙ popover (`render_ramp_settings`) under
`RAMP_CHORD_KEY`. Like the map display keys, that key is deliberately **not**
`accept_*`-prefixed, so it outlives the state reset.

Ramping is the one thing here that *adds* lines. A `"modified"` `LineChange`
may carry several lines joined by `\n`, and `apply_accepted_changes` splits
them back out, since a program line must never contain a newline itself.

## The feed map

### Colour per vertex

The map colours **per vertex**, not per section, so a ramp shades along its
length the way the machine will run it. `path_points` therefore returns `t`
(0→1 along each section), and `build_toolpath_map` takes `ramp_for_section`
to interpolate.

A line mark is one flat colour per *segment*, so a section only reads as a
gradient if it has vertices to break it up. The lopsidedness that forces
chord-splitting in the G-code applies here too. 7 of the 16 straights in a test
toolpath arrive as a single segment, which can only ever be one colour, so a
ramp on a straight looked as if it hadn't been applied. `densify_ramped_path`
inserts plotting vertices inside ramped sections only, one per
`RAMP_FEED_STEP` the feed crosses. That is exactly the number of distinct
colours the section can show, so it costs nothing extra. It's display-only:
`path_points` stays true to the geometry, and the G-code was always right.

### Payload

The whole-bed view is ~11,000 vertices, and it is rebuilt and re-sent on
**every** rerun. Keep `pts` to the columns Vega actually encodes. Everything
the tooltip shows is per *section*, so it's joined on in the browser via
`transform_lookup` against `section_desc` (~1,300 rows). x/y are rounded to the
3 dp the source files use. Together that took the spec from 5.19 MB to
3.08 MB. Having layers share one DataFrame is **not** where the savings are:
Altair already hashes identical frames into a single `datasets` entry. So
`alt.layer(..., data=pts)` only says once where the vertices come from; it
doesn't make the spec smaller.

A `format_func` that closes over `feeds_state` re-labels its options from
whatever the dict holds *later* in the run, after the section table has
written to it. So the jump selectbox's labels are built eagerly into a dict
and bound as a default argument, the same rule the widget callbacks follow.

### Layout: map beside editor

The step is laid out as **map beside editor** in one `st.columns` row, so a
section can be picked and re-fed without scrolling between the two. That was a
direct complaint about the earlier stacked layout. Both are *slots*
(`map_slot`, `editor_slot`) filled later in the run. Streamlit places output
where the container was created but runs it in call order. So the section
table runs first (reading its edits), then the editor (showing fresh values),
then the map (showing everything). Keep that order: rendering the editor
before the table puts its feed box one rerun behind.

Dividing (step 1), the all-sections table and the line-by-line change list are
expanders, so they stay out of the way. The map is the working preview, not
the change list.

### Picking: two selections and a cursor

The map (`build_toolpath_map`, Altair) is both the picker and the result view,
since it's coloured by the feed each section ends up at. **⤢ Whole program**
clears the cursor and the part filter in one `on_click`. Clicking empty space
on the map and the editor's ✕ do the same for the cursor.

Its click selections are read from session state up front and scope the
tables. A point selection arrives as a list of `{"key": ...}` dicts, or `{}`
when empty. The map carries *two* point selections:

- a per-toolpath one on the bounding boxes
- a per-section one (`pkey` = `"<toolpath key>|<section no>"`) on a fat
  transparent copy of the path laid over everything else, which is how a
  single section is picked by eye

Their conditions are combined with `&`, which works because an empty
selection matches everything.

Picking a section on the map sets a *cursor* (`{prefix}_cursor`, a
`(toolpath key, section number)` pair), and the section editor edits exactly
that section. The feed box, Clear, and "apply to every arc/straight in this
toolpath" all act on it, and the cursor scopes the tables to its toolpath.
Prev/Next/Jump move the cursor too, which is why it lives in session state
instead of being read off the chart: a chart selection can't be written back
to the browser from Python.

The cursor is maintained in the map's **`on_select` callback**
(`cursor_from_selection`), not by reading the chart's state each run, and
that distinction is the whole point. Read outside the callback, an empty
selection is ambiguous: it could be a click on empty space or a chart that
re-rendered. So clearing had to be ignored, and then clicking the map could
never deselect. Inside the callback, an empty selection is unambiguously the
user clearing it, so clicking empty space resets to the whole program, as do ✕
and ⤢. `cursor_from_selection` is module-level and pure so it can be tested
directly. AppTest can't fire a chart's `on_select`, so tests simulate a click
by setting `{prefix}_cursor` the way the callback would. The cursor highlight
is a data-driven layer keyed on `pkey`, not a selection condition, for the
same reason.

### Feed colours

Feed is coloured by its **actual value**, on a ramp chosen in the map's ⚙
Display popover (`render_map_settings`, `MAP_RAMPS`). Every ramp is trimmed so
both ends clear the app surface (#272b33), and validated with the dataviz
skill's `scripts/validate_palette.py --ordinal`. Don't add or edit one without
re-running it.

Plasma is the default. Plasma and Viridis rise monotonically in lightness, so
brighter always means faster and the ordering survives colour-blindness.
**Rainbow does not**: its green and yellow sit 0.005 apart in lightness, so two
different feeds can look identical. It's there because the hue variety was
asked for, not because it reads best, so leave the default alone. A
single-hue blue ramp was the original default and proved too flat to read at
a glance, which is why it's now one option among four. Sections given a new
feed are drawn thicker, so "what did I change" doesn't depend on the colour
channel. `MAP_RAMP_KEY`/`MAP_SIZE_KEY` are deliberately **not**
`accept_*`-prefixed: a display preference has to outlive the state reset that
clears review choices.

### Scope

Only the toolpaths in scope are plotted. With nothing picked, that's every
part, which for a coupon array is the useful overview of the bed. Picking one
zooms to it, and for a stacked build (layers sharing a footprint) it's the
only way to see a single layer at all.

## Streamlit callback rules for the step

- Buttons that only move the cursor or set state use `on_click=` callbacks,
  never `if st.button(): ...; st.rerun()`. The click already reruns the
  script; forcing a second rerun is what made stepping through sections
  visibly redraw the page twice.
- Widget callbacks in `render_feed_sections_step` must bind what they need as
  default arguments. They outlive the run that created them, and the step
  reuses names like `feed_key` further down (the parts-table write-back loop).
  A closure would fire against whatever the last iteration left behind. The
  symptom is a feed landing on the last part in the table instead of the
  edited one.
- The bulk-edit panel was removed deliberately (2026-09-21) to simplify the
  step: per-section editing plus direct editing of the parts table covers it.
  The one bulk control kept is the "split every toolpath into lines & arcs"
  checkbox, because a file holds ~45 near-identical toolpaths.

## Geometry segmentation (lines & arcs)

The CAM output contains no G2/G3: every curve is a fan of short G1 chords
(~0.5 mm, turning ~9° each). So `segment_by_geometry` recovers curvature from
the geometry: the local radius at a junction is `mean(move length) / turn
angle`. Two rules stop it shredding the path:

- An *arc* needs at least two consecutive junctions turning the same way. A
  lone turning junction is a sharp corner, which ends a straight run but isn't
  an arc.
- Runs under `MIN_SEGMENT_MM` are absorbed into a neighbour.

Both O1140 programs split into 29 sections per layer: 16 straights and 13 arcs.
Geometry cuts land exactly on move boundaries, so they're fed through the same
`splits` machinery and the midpoint rule reproduces the runs move for move.

Two further rules exist because the radius test alone was silently wrong on
real files. Both cost a whole file's worth of usefulness before they were
found.

**A junction only counts where the moves actually meet.** `moves` is the
*measured* path, with the laser-off repositioning already dropped, so a raster
pad arrives as parallel strokes that never join. All 78 laser-on moves of
`PAD2` in `O1145.ptp` are disconnected from the next one, and reading them as
one continuous straight collapsed every pad in that file to a single section.
`joined_at` gates the turn measurement, the breaks, the sliver absorption and
the arc re-join.

**A sharp corner is always split out, never absorbed into an arc.** That rule
needs all three of its parts, and each part alone is wrong:

- The radius test divides by move length, so on its own it misses a corner
  between *long* moves. A 90° turn between two 28 mm moves reads as a 40 mm
  radius and slips through, collapsing a rectangle drawn as four long moves
  into one "straight".
- But **angle alone is worse.** Genuine arcs in `O1140 - Original.ptp` hold
  junctions turning up to **46.6°** (mean 11.4°), so a bare `> CORNER_DEG`
  rule shreds real curves. What separates the two is the length of what runs
  into the junction. A curve arrives as ~0.5 mm chords; a polygon corner sits
  between multi-millimetre straights. Hence `CORNER_MIN_MOVE_MM` (2 mm) on the
  longer of the two moves.
- And `arc_junction` must subtract corners (`sustained(j) and not corner[j]`),
  or a polygon still reads as a curve. The four 90° turns of a rectangle with
  8 mm sides each count as "turning" (radius 5.1 mm) and all bend the same
  way, so `sustained` called the whole shape one arc and no corner could split
  it.

Shapes worth re-checking after any change here: rectangles at 30/50, 8, 4 and
2.5 mm sides, an L, a zigzag and an octagon should each split at every corner.
A circle of 0.5 mm chords should stay one arc, and straight-arc-straight should
stay three sections.

## Layers

**A toolpath marker is not a layer.** In `O1145.ptp`, one
`(PAD2_1.2MM_1.28Z_4LAYERS)` block holds all four of its layers, at Z 0.0 /
1.28 / 2.56 / 3.84, the Z-step the name promises. `segment_by_layer` recovers
them from the Z each move deposits at (`_Move.z`). It starts a new layer when Z
departs from the *current layer's* Z by more than `LAYER_STEP_MM`, so a path
that merely isn't flat stays one layer. A continuously climbing path has no
layers to find, and comes back cut every `LAYER_STEP_MM` of climb. That
division is meaningless, which is why the UI shows the layer count and the Z
levels and lets the operator decide rather than dividing on its own.

Layers **compose with** whichever division is chosen rather than replacing it.
Ticking Layers and Lines & arcs gives one section per straight/arc *within*
each layer. That's why `kind` and `layer` are carried per *move* in `_analyse`
and read off each section's first move, instead of being indexed by position
in the run list. Once layer cuts are merged into the geometry cuts, sections
and runs are no longer the same list. Layers also prefix `spec_for` (`"L+geo"`),
since toggling them renumbers every section and must invalidate the feed keys.

Two numbers from the real files are worth keeping:

- A pad's layers cross-hatch, so `PAD2` comes out at 15 / 24 / 15 / 24 strokes
  per layer: a 28×18 mm pad at 1.2 mm stepover, rastered the long way, then
  the short way.
- The 17 pads have *different* Z-steps. That file is a Z-step sweep, so the
  layer Z values differ per pad and can't be assumed shared.

On the map, layers of one build occupy the same footprint and stack into an
unreadable pile, so the **Show layer** filter beside the map is the only way
to see a single layer of a stacked build at all. When nothing is layer-split,
the `Layer` tooltip column is *dropped* from `section_desc`, not merely left
out of the tooltip field list. The frame is serialised into the spec whole, so
an unused column is pure payload.

## The section-removal map

The section-removal step (`remove_named_sections`) shows the bed beside its
checklist (`build_removal_map`), because names like `..._COPY_35` don't say
where a block sits, and that's what decides whether it should go. Each block
starts at its marker line, which is also where its toolpath starts, so changes
and toolpaths are matched on that index. The checklist is a slot filled
first, like the feed step's map, so the map shows the current run's ticks.

**A click toggles, and the chart's key is versioned to make that possible.**
The `on_select` callback flips the clicked toolpath's checkbox, then bumps
`{prefix}_mapver`, which is part of the chart's key, so the chart comes back
with an empty selection. Without the bump, clicking the same toolpath twice
leaves the selection unchanged, the callback never fires, and the toolpath can
never be toggled back. A click on empty space selects nothing and changes
nothing. The callback reads the checkbox's current state, so the map and the
list stay one control. Checked in a live browser: ticking, unticking, ticking
again, and an empty-space click.

Colours: kept is `#1baf7a` and to-remove is `#d03b3b`, validated as a pair
against the app surface with the dataviz skill's `validate_palette.py` (deutan
ΔE 9.9). The app's teal accent fails the lightness band, so it isn't used
here. The red sits just under 3:1 on the surface, so it is never the only cue:
a toolpath ticked for removal is also dashed, its box is tinted and marked ✕,
and the tooltip says which way a click goes. The ✕ matters on `O1145.ptp`,
where a raster pad's dashes run together and read as a solid line. It's drawn
under the click boxes so clicking it still hits the box.

Where layers of one build share a footprint, only the top box can be clicked,
and the caption says to use the list for the rest.
