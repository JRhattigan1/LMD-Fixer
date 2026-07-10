"""Removes G4 X25.00 dwell lines, subject to manual review per occurrence.

These dwells follow an M98 Pxxxx subprogram call. Whether the dwell is still
needed depends on the specific P value being switched to, which isn't
recoverable from the line itself, so this fix proposes removing every
occurrence (naming the preceding P value) and lets the reviewer opt to keep
individual ones instead.
"""

from __future__ import annotations

import re

from lmd_fixer.fixes import Fix, FixResult, LineChange, register
from lmd_fixer.gcode import GCodeProgram

_DWELL_RE = re.compile(r"^/?\s*G4\s*X25\.0*\s*$", re.IGNORECASE)
_M98_RE = re.compile(r"^/?\s*M98\s+P(\d+)", re.IGNORECASE)


def _preceding_p_value(lines: list[str], index: int) -> str | None:
    for j in range(index - 1, -1, -1):
        match = _M98_RE.match(lines[j].strip())
        if match:
            return match.group(1)
        if lines[j].strip():
            # Stop at the nearest non-blank line that isn't the M98 call
            # so we don't attribute a dwell to a distant, unrelated P value.
            continue
    return None


@register
class FlagDwells(Fix):
    id = "flag_dwells"
    label = "Remove G4 X25.00 dwells (review each)"
    description = "Removes G4 X25.00 dwell lines following an M98 Pxxxx subprogram call. Each is listed with its preceding P value so you can choose to keep it instead of removing it."

    def apply(self, program: GCodeProgram, **options) -> FixResult:
        out = program.copy()
        changes: list[LineChange] = []
        kept: list[str] = []
        for i, line in enumerate(out.lines):
            if not _DWELL_RE.match(line.strip()):
                kept.append(line)
                continue
            p_value = _preceding_p_value(out.lines, i)
            p_desc = f"P{p_value}" if p_value else "unknown P value"
            changes.append(
                LineChange(
                    kind="removed",
                    original_index=i,
                    original_text=line,
                    new_text=f"after M98 {p_desc}",
                )
            )
        out.lines = kept
        return FixResult(
            program=out,
            summary=f"Removed {len(changes)} G4 X25.00 dwell line(s).",
            changes=changes,
        )
