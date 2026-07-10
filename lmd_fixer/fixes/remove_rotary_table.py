"""Removes rotary table commands: G65 ... P8000 / M337 macro pairs and
G90 G0 A0.0 axis-zero moves.

The G65/M337 pair appears once per layer:
    G65 B0.0 F1000. D1 P8000
    M337
and G90 G0 A0.0 (zeroing the rotary A axis) appears separately, once per
layer, shortly after G54. All are unnecessary when the rotary table isn't
being used for the job. G90 (absolute mode) is already asserted elsewhere
in each layer block, so dropping the combined G90 G0 A0.0 line doesn't lose
that modal state.
"""

from __future__ import annotations

from lmd_fixer.fixes import Fix, FixResult, LineChange, register
from lmd_fixer.gcode import GCodeProgram


def _is_rotary_g65(line: str) -> bool:
    stripped = line.strip().lstrip("/").upper()
    return stripped.startswith("G65") and "P8000" in stripped


def _is_m337(line: str) -> bool:
    return line.strip().lstrip("/").upper() == "M337"


def _is_rotary_axis_zero(line: str) -> bool:
    stripped = line.strip().lstrip("/").upper().replace("  ", " ")
    return stripped == "G90 G0 A0.0"


@register
class RemoveRotaryTableCommands(Fix):
    id = "remove_rotary_table"
    label = "Remove rotary table commands (G65/M337/A-axis)"
    description = "Deletes G65 ... P8000 macro calls, their following M337 line, and G90 G0 A0.0 rotary axis-zero moves, used to command a rotary table that isn't needed for this job."

    def apply(self, program: GCodeProgram, **options) -> FixResult:
        out = program.copy()
        kept: list[str] = []
        changes: list[LineChange] = []
        i = 0
        lines = out.lines
        while i < len(lines):
            line = lines[i]
            if _is_rotary_g65(line):
                changes.append(LineChange(kind="removed", original_index=i, original_text=line))
                # Drop the paired M337 on the next line too, if present.
                if i + 1 < len(lines) and _is_m337(lines[i + 1]):
                    changes.append(LineChange(kind="removed", original_index=i + 1, original_text=lines[i + 1]))
                    i += 2
                else:
                    i += 1
                continue
            if _is_m337(line):
                changes.append(LineChange(kind="removed", original_index=i, original_text=line))
                i += 1
                continue
            if _is_rotary_axis_zero(line):
                changes.append(LineChange(kind="removed", original_index=i, original_text=line))
                i += 1
                continue
            kept.append(line)
            i += 1
        out.lines = kept
        return FixResult(
            program=out,
            summary=f"Removed {len(changes)} rotary table command line(s).",
            changes=changes,
        )
