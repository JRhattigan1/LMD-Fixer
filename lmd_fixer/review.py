"""The step-by-step review of one program, independent of any UI.

A `ReviewSession` runs the chosen fixes one at a time in `FIX_ORDER`. Each
step proposes changes against the program *as it is after the steps before
it*, the reviewer decides which to keep, and applying the step moves on.
Back undoes the last step. This is the state machine the front-ends drive,
so its rules live here rather than in widget state:

- **Defaults come from the fix.** A change starts accepted or not according
  to the fix's `default_accept`; the reviewer's choices are stored only as
  overrides of that default.
- **Choices outlive Back and restart.** Undoing a step, or starting the
  same file over, keeps what was ticked: the same fix sees the same program
  at the same step, so the line indices still mean the same lines.
- **Changing which fixes run starts over and forgets the choices.** The
  program has already been through the earlier fixes, and the step numbers
  no longer line up. `selection_change_discards_work` says whether that would
  throw applied work away, so the UI can ask first.
- **Option-driven fixes** (`review_mode` "recipes") propose nothing
  until given options, and their changes depend on each other, so applying
  the step takes all of them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from lmd_fixer.export import OutputPlan, plan_output
from lmd_fixer.fixes import Fix, FixResult, available_fixes
from lmd_fixer.gcode import GCodeProgram
from lmd_fixer.pipeline import FIX_ORDER, apply_accepted_changes, run_fix

OPTION_MODES = {"recipes"}


def ordered_fix_ids(fix_ids: Iterable[str]) -> tuple[str, ...]:
    """The fixes in run order: `FIX_ORDER` first, then any others in the
    order given. Duplicates are dropped."""
    wanted = list(dict.fromkeys(fix_ids))
    return tuple([f for f in FIX_ORDER if f in wanted] + [f for f in wanted if f not in FIX_ORDER])


@dataclass
class StepChoices:
    """What the reviewer decided at one step: accept/reject overrides of the
    fix's default (keyed by a change's `original_index`), and the options
    for an option-driven fix."""

    overrides: dict[int, bool] = field(default_factory=dict)
    options: dict = field(default_factory=dict)


@dataclass
class _Done:
    program_before: GCodeProgram
    summary: str
    skipped: bool


class ReviewSession:
    def __init__(self, program: GCodeProgram, fix_ids: Iterable[str]) -> None:
        self.original = program
        self.fix_ids = ordered_fix_ids(fix_ids)
        # Every registered fix, not just the ones offered: a session may name
        # a fix that fix_settings.toml hides (the self-test does).
        self._classes = available_fixes(include_disabled=True)
        unknown = [f for f in self.fix_ids if f not in self._classes]
        if unknown:
            raise KeyError(f"Unknown fix id(s): {', '.join(unknown)}")
        self._choices: dict[tuple[int, str], StepChoices] = {}
        self._reset_progress()

    def _reset_progress(self) -> None:
        self.current = self.original.copy()
        self.index = 0
        self._done: list[_Done] = []
        self._proposal: tuple[tuple, FixResult] | None = None

    # -- where we are ----------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.index >= len(self.fix_ids)

    @property
    def fix_id(self) -> str | None:
        return None if self.finished else self.fix_ids[self.index]

    @property
    def fix(self) -> Fix | None:
        # Cached per step: the UI asks for it thousands of times per edit
        # (is_accepted is called for every change on every redraw).
        if self.finished:
            return None
        cached = getattr(self, "_fix_cache", None)
        if cached is None or cached[0] != self.fix_id:
            cached = self._fix_cache = (self.fix_id, self._classes[self.fix_id]())
        return cached[1]

    def fix_label(self, fix_id: str) -> str:
        return self._classes[fix_id].label or fix_id

    @property
    def summaries(self) -> list[str]:
        return [d.summary for d in self._done]

    def step_status(self, i: int) -> str:
        """"done", "skipped", "current" or "pending" for step `i`."""
        if i < self.index:
            return "skipped" if self._done[i].skipped else "done"
        return "current" if i == self.index else "pending"

    # -- the current step's proposal and choices ---------------------------

    @property
    def choices(self) -> StepChoices:
        if self.finished:
            raise RuntimeError("The review is finished; there is no current step.")
        return self._choices.setdefault((self.index, self.fix_id), StepChoices())

    def proposal(self) -> FixResult:
        """The current step's proposed changes, against the current program.
        Cached until the step or its options change."""
        if self.finished:
            raise RuntimeError("The review is finished; there is no current step.")
        key = (self.index, self.fix_id, repr(sorted(self.choices.options.items(), key=lambda kv: kv[0])))
        if self._proposal is None or self._proposal[0] != key:
            self._proposal = (key, run_fix(self.current, self.fix_id, self.choices.options).result)
        return self._proposal[1]

    def set_options(self, **options) -> None:
        """Replaces the current step's options (for option-driven fixes)."""
        self.choices.options = dict(options)

    def is_accepted(self, original_index: int) -> bool:
        if self.fix.review_mode in OPTION_MODES:
            return True
        return self.choices.overrides.get(original_index, self.fix.default_accept)

    def set_accepted(self, original_index: int, accepted: bool) -> None:
        self.choices.overrides[original_index] = bool(accepted)

    def set_all_accepted(self, accepted: bool) -> None:
        for change in self.proposal().changes:
            self.set_accepted(change.original_index, accepted)

    def accepted_indices(self) -> set[int]:
        return {c.original_index for c in self.proposal().changes if self.is_accepted(c.original_index)}

    def preview(self) -> GCodeProgram:
        """The program as it would be after applying the current step."""
        return apply_accepted_changes(self.current, self.proposal(), self.accepted_indices())

    # -- moving between steps ----------------------------------------------

    def apply(self) -> str:
        """Applies the current step's accepted changes and moves on. Returns
        the summary recorded for the step."""
        result = self.proposal()
        accepted = self.accepted_indices()
        mode = self.fix.review_mode
        if not result.changes or mode in OPTION_MODES:
            summary = result.summary
        elif mode == "sections":
            summary = f"Removed {len(accepted)} of {len(result.changes)} section(s)."
        else:
            summary = f"Applied {len(accepted)} of {len(result.changes)} proposed change(s)."
        new_program = apply_accepted_changes(self.current, result, accepted)
        self._advance(new_program, f"[{self.fix_id}] {summary}", skipped=False)
        return self._done[-1].summary

    def skip(self) -> str:
        self._advance(self.current, f"[{self.fix_id}] Skipped.", skipped=True)
        return self._done[-1].summary

    def _advance(self, new_program: GCodeProgram, summary: str, skipped: bool) -> None:
        if self.finished:
            raise RuntimeError("The review is already finished.")
        self._done.append(_Done(self.current, summary, skipped))
        self.current = new_program
        self.index += 1
        self._proposal = None

    def back(self) -> None:
        """Undoes the last step, restoring the program as it was before it.
        That step's choices are kept."""
        if not self._done:
            raise RuntimeError("Nothing to go back to.")
        self.current = self._done.pop().program_before
        self.index -= 1
        self._proposal = None

    def back_to(self, step: int) -> None:
        """Undoes every step from `step` on, making it current again."""
        if not 0 <= step <= self.index:
            raise IndexError(f"Can't go back to step {step} from step {self.index}.")
        while self.index > step:
            self.back()

    def restart(self, keep_choices: bool = True) -> None:
        """Starts the same file over from the first step. With `keep_choices`
        (the default) earlier decisions carry over, so a redo doesn't mean
        reviewing everything again."""
        if not keep_choices:
            self._choices.clear()
        self._reset_progress()

    # -- changing which fixes run ------------------------------------------

    def selection_change_discards_work(self, fix_ids: Iterable[str]) -> bool:
        """Whether switching to `fix_ids` would throw away applied steps."""
        return ordered_fix_ids(fix_ids) != self.fix_ids and self.index > 0

    def set_selection(self, fix_ids: Iterable[str]) -> None:
        """Runs a different set of fixes, starting over from the original
        file with no remembered choices. A no-op if nothing changes."""
        wanted = ordered_fix_ids(fix_ids)
        if wanted == self.fix_ids:
            return
        unknown = [f for f in wanted if f not in self._classes]
        if unknown:
            raise KeyError(f"Unknown fix id(s): {', '.join(unknown)}")
        self.fix_ids = wanted
        self._choices.clear()
        self._reset_progress()

    # -- the result ----------------------------------------------------------

    def result(self) -> GCodeProgram:
        return self.current

    def plan_output(self, chosen_name: str) -> OutputPlan:
        """The file that would be saved under `chosen_name` (see
        `export.plan_output`)."""
        # source_name may be a full path (GCodeProgram.from_file); the output
        # name is built from the file name alone.
        return plan_output(self.current, Path(self.original.source_name).name, chosen_name)
