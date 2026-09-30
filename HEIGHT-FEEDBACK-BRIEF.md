# Project brief: closed-loop layer-height correction for LMD

**Status:** not started. This is a brief for a new project, not a description
of something that exists.

**How to use this document:** paste it as the opening prompt of a new session,
or read it as a spec. It is written to be self-contained — you do not need
access to the LMD-Fixer application or the conversation that produced this.

---

## What you are building

A tool that reads a **height scan** of a partly-built laser metal deposition
(LMD) part and rewrites the remaining G-code so the **feed rate varies with
position** to correct height error: slower where the part is low (deposit more
material), faster where it is high (deposit less).

The intended workflow on the machine:

1. Build N layers.
2. Scan the top surface to get a height map.
3. Compare against the nominal height for that layer.
4. Adjust feed per-position in the following layers to pull the surface back
   to nominal.
5. Repeat.

## Why this is worth building

Layer height in LMD is set by how much material lands per unit length, and it
drifts from nominal. Today this is handled by *guessing the Z-step up front*
and hoping it matches reality.

There is direct evidence of that cost. A real production file from this lab,
`O1145.ptp`, is a 17-pad parameter sweep whose toolpath names encode what was
being varied:

```
PAD2_1.2MM_1.28Z_4LAYERS      stepover 1.2 mm, Z-step 1.28 mm, 4 layers
PAD3_1.6MM_1.03Z_4LAYERS      stepover 1.6 mm, Z-step 1.03 mm
PAD9_1.6MM_0.76Z_4LAYERS
PAD21_1.6MM_1.59Z_4LAYERS     Z-steps across the file span 0.76 -> 1.59 mm
```

That is an entire build spent empirically hunting for the Z-step that matches
actual deposition. This project is the closed-loop version of that search.

---

## Build it in stages. Ship stage 1 first.

**Do not start by writing G-code.** The process model is the risky part, and it
is cheap to validate before you act on it.

### Stage 1 — Measure and propose (the deliverable that de-risks everything)

Ingest a scan, register it to the part, compare to nominal, and **show** the
operator:

- a plan view of height deviation across the part;
- per-position statistics (mean, spread, where the extremes are);
- the feed correction the model *would* command, and the resulting predicted
  height — **without writing any G-code**.

Run this against several real builds and check the predicted correction
against what actually happened. This validates the feed↔height model before
anything is trusted to touch a machine. It is independently useful even if you
never build stage 2, because it replaces eyeballing pads by hand.

### Stage 2 — Apply the correction

Only once stage 1's model is calibrated. Rewrite feeds in the remaining
layers, with mandatory review before anything reaches the machine (see
Safety).

### Stage 3 — Multi-cycle

Scan → correct → scan again. Only attempt after stage 2 has been through
several supervised builds.

---

## Resolve these before writing code

**1. The scan — this is blocking, and everything else depends on it.**

- What produces it? An on-machine probe, a line scanner, an offline system?
- What format does it emit, and what is its resolution and noise floor?
- **What coordinate frame is it in?** A machine probe returning a grid in part
  coordinates is a completely different project from an offline scanner that
  needs fiducial registration. Do not design around an assumption here.
- How does a scan point map onto a toolpath position? How much do you trust a
  single point versus a local average?

**2. The feed↔height coefficient.** Must be calibrated empirically, per
material and per parameter set. Do not ship a hard-coded constant — derive it
from measured builds and store it as calibration data.

**3. The process window.** What are the minimum and maximum feeds that still
produce sound material for this process? These become hard clamps, and you
need real numbers from whoever owns the process.

---

## The process model, and where it breaks

To first order, deposited cross-sectional area is proportional to powder mass
flow divided by travel speed:

```
area ∝ ṁ / v        so        height ≈ k · ṁ / (ρ · w · v)
```

So height is roughly **inversely proportional to feed**: to add 10% height,
slow down about 10%. That model is approximately right *inside the process
window*. Know where it fails:

- **Bead width also changes with speed.** Change feed and you change track
  overlap, which changes height again. Height and width are coupled, so a
  correction computed as if only height responds will overshoot.
- **Thermal history.** A slower pass puts in more heat — more dilution, hotter
  substrate for the next layer. Your correction perturbs the very thing you
  will measure next cycle.
- **It is non-linear near the edges** of the process window, and outside it
  you get lack of fusion (too fast) or overheating, excessive dilution and
  balling (too slow).

**Be honest that feed is the available lever, not necessarily the best one.**
Powder mass flow and laser power are more direct controls on deposition rate.
Feed is being used because it can be varied per-move from the G-code. That is
a legitimate engineering constraint — but if mass flow can be commanded from
the program, evaluate it as an alternative before committing.

## This is a control loop with long dead time — treat it as one

You correct only every N layers, so the loop has substantial dead time. A gain
of 1 is a textbook recipe for oscillation: over-correct a low spot into a high
spot, then chase it back.

Required from the start, not as a later refinement:

- **Gain well below 1.** Start around 0.3–0.5 and tune from real builds.
- **A deadband**, so scan noise is not chased.
- **Hard feed clamps**, absolute and per-step, so no correction can ever
  command a feed outside the validated window.
- **Per-cycle logging** of commanded vs. achieved correction, so the gain can
  be tuned against evidence rather than intuition.

---

## G-code facts you must respect

These are hard-won from real files produced by this CAM/machine combination.
Getting any of them wrong produces subtly broken programs.

**The CAM emits curves and straights completely differently, and this is the
single most important fact for this project.** Curves arrive as fans of short
chords (~0.5 mm, turning ~9° each). Straights arrive as **one or two very long
moves** — a 26 mm straight is 2 moves.

A feed word attaches to a move. So you cannot vary feed along a straight
without first **subdividing long moves into shorter chords**. In one measured
toolpath, 7 of 16 straight runs were a single move — a position-varying feed
across those is impossible until they are split. Any spatial feed control
scheme has to do this. Budget for it.

When subdividing:

- Only split plain XY linear (G1) moves. **Never** split a Z plunge, a rotary
  (A/B/C) move, or a real G2/G3 arc.
- The final chord must land exactly on the original endpoint.
- Match the source file's coordinate precision (3 decimal places here).

**Feed is modal.** Each toolpath sets `F` once, typically on its plunge
(`G1 Z0.0 F500.`), and every later move inherits it. So a changed feed leaks
forward into everything that follows — you must restate the original feed on
the first move after any overridden stretch. Watch out: `G65 ... F1000.` is a
macro argument, **not** a feed. `G4 X25.00` is a dwell time, not a move.

**Round commanded feeds to a step** (e.g. 25 mm/min). Consecutive moves that
round to the same value then need only one `F` word instead of dozens of
near-identical ones — it keeps both the file and the diff readable.

**Other format details:**

- Line endings are **CRLF**; the controller expects them.
- Section/toolpath markers are standalone comment lines with no internal
  whitespace: `(PAD2_1.2MM_1.28Z_4LAYERS)`. Free-text comments like
  `(PROJECT: ...)` contain spaces and are *not* markers.
- `M323` / `M322` switch the laser on and off. Measure deposited path length
  between them, not total travel — a rapid to a tool-change position is not
  deposition.
- **Layers may live inside a single named block.** In `O1145.ptp`, `PAD2` is
  one marker covering all 4 layers, spanning Z 0→4.84. Do not assume one
  toolpath equals one layer; segment layers by watching Z increments.

---

## Safety

This is the part that makes this project different in kind from a file-cleanup
tool. There, a bug yields an annoying file. Here, a bug yields a wrecked part
or a crashed deposition head.

- **Keep a human in the loop.** The operator sees and approves the proposed
  correction before it reaches the machine. Do not build a fully automatic
  path, even if it seems convenient.
- **Clamps are not optional** and must be enforced at the point of G-code
  generation, not only in the UI.
- **Fail closed.** A missing, stale, unregistered or out-of-range scan must
  produce *no correction* — never a guessed one.
- **Make every change reviewable and reversible**, with the original program
  always recoverable.

---

## Suggested starting question

Before any code, answer: *what exactly does the height scan look like, and in
what coordinate frame?* Bring back a real example file. Everything downstream —
resolution, sampling, registration, how much a single point can be trusted —
follows from that answer.
