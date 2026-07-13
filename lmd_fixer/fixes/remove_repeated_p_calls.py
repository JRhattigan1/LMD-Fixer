"""Removes M98 Pxxxx / M325 pairs (and their dwell) when the P value repeats.

Each layer normally starts with:
    /M98 P0020
    /M325
    /G4 X25.00
Setting the laser program to P0020 again when it is already P0020 is a
no-op, so if the immediately-preceding M98 call used the same P value, the
M98 + M325 pair is redundant and removed automatically, along with the
G4 X25.00 dwell that follows it (since there's no longer a program change to
wait on). Where the P value actually changes, the M98/M325 pair is left in
place and the dwell is handled separately by the manual review fix.
"""

from __future__ import annotations

import re

from lmd_fixer.fixes import Fix, FixResult, LineChange, register, section_names
from lmd_fixer.gcode import GCodeProgram

_M98_RE = re.compile(r"^/?\s*M98\s+P(\d+)\s*$", re.IGNORECASE)
_M325_RE = re.compile(r"^/?\s*M325\s*$", re.IGNORECASE)
_DWELL_RE = re.compile(r"^/?\s*G4\s*X25\.0*\s*$", re.IGNORECASE)


@register
class RemoveRepeatedPCalls(Fix):
    id = "remove_repeated_p_calls"
    label = "Remove repeated M98/M325 program calls"
    description = "Removes M98 Pxxxx + M325 (+ following G4 X25.00 dwell) when the P value is the same as the previous M98 call. Each group is reviewed as one change. Kept if the P value actually changes."

    def apply(self, program: GCodeProgram, **options) -> FixResult:
        out = program.copy()
        lines = out.lines
        changes: list[LineChange] = []
        remove_indices: set[int] = set()
        sections = section_names(lines)

        last_p: str | None = None
        i = 0
        while i < len(lines):
            match = _M98_RE.match(lines[i].strip())
            if match:
                p_value = match.group(1)
                if p_value == last_p:
                    # The M98, its M325, and the dwell are always consecutive,
                    # so the group is proposed as a single reviewable change.
                    j = i + 1
                    if j < len(lines) and _M325_RE.match(lines[j].strip()):
                        j += 1
                    if j < len(lines) and _DWELL_RE.match(lines[j].strip()):
                        j += 1
                    remove_indices.update(range(i, j))
                    changes.append(
                        LineChange(
                            kind="removed",
                            original_index=i,
                            end_index=j - 1,
                            original_text=lines[i],
                            reason=f"repeated P{p_value}",
                            label=sections[i],
                        )
                    )
                    i = j
                    continue
                last_p = p_value
            i += 1

        out.lines = [line for idx, line in enumerate(lines) if idx not in remove_indices]
        return FixResult(
            program=out,
            summary=(
                f"Removed {len(changes)} repeated M98 program-call group(s) "
                f"({len(remove_indices)} lines)."
            ),
            changes=changes,
        )
