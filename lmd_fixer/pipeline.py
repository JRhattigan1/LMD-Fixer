"""Runs a selected, ordered set of fixes over a GCodeProgram."""

from __future__ import annotations

from dataclasses import dataclass, field

from lmd_fixer.fixes import FixResult, available_fixes
from lmd_fixer.gcode import GCodeProgram


@dataclass
class PipelineResult:
    program: GCodeProgram
    summaries: list[str] = field(default_factory=list)


@dataclass
class FixRunResult:
    """Result of running a single fix, kept separate so the UI can offer per-fix review."""

    fix_id: str
    result: FixResult


def run_fix(program: GCodeProgram, fix_id: str, options: dict | None = None) -> FixRunResult:
    fix_cls = available_fixes().get(fix_id)
    if fix_cls is None:
        raise KeyError(f"Unknown fix id: {fix_id}")
    result = fix_cls().apply(program, **(options or {}))
    return FixRunResult(fix_id=fix_id, result=result)


def run_pipeline(program: GCodeProgram, fix_ids: list[str], options: dict | None = None) -> PipelineResult:
    """Runs every fix unconditionally, with no review step. Kept for scripting/CLI use."""
    options = options or {}
    current = program
    summaries: list[str] = []
    for fix_id in fix_ids:
        run_result = run_fix(current, fix_id, options.get(fix_id))
        current = run_result.result.program
        if run_result.result.summary:
            summaries.append(f"[{fix_id}] {run_result.result.summary}")
    return PipelineResult(program=current, summaries=summaries)


def apply_accepted_changes(original: GCodeProgram, result: FixResult, accepted_indices: set[int]) -> GCodeProgram:
    """Rebuilds a program from `original`, applying only the accepted changes.

    `accepted_indices` holds each accepted change's `original_index` (its
    identity for review purposes, even when the change spans a range via
    `end_index`).
    """
    accepted = [c for c in result.changes if c.original_index in accepted_indices]
    out = original.copy()
    new_lines: list[str] = []
    i = 0
    accepted_by_start = {c.original_index: c for c in accepted}
    removed_or_replaced = set()
    for c in accepted:
        end = c.end_index if c.end_index is not None else c.original_index
        for idx in range(c.original_index, end + 1):
            removed_or_replaced.add(idx)

    while i < len(out.lines):
        change = accepted_by_start.get(i)
        if change is not None:
            end = change.end_index if change.end_index is not None else change.original_index
            if change.kind != "removed":
                new_lines.append(change.new_text if change.new_text is not None else out.lines[i])
            i = end + 1
            continue
        if i in removed_or_replaced:
            i += 1
            continue
        new_lines.append(out.lines[i])
        i += 1
    out.lines = new_lines
    return out
