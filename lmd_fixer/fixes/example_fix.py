"""Template demonstrating the shape a real fix should take.

Not registered (and not imported by the package), so it does not appear in
the UI. To activate a fix like this one: decorate it with @register and
import its module in lmd_fixer/fixes/__init__.py.
"""

from __future__ import annotations

from lmd_fixer.fixes import Fix, FixResult, LineChange, register  # noqa: F401
from lmd_fixer.gcode import GCodeProgram


class StripTrailingWhitespace(Fix):
    id = "strip_trailing_whitespace"
    label = "Strip trailing whitespace"
    description = "Removes trailing spaces/tabs from every line."

    def apply(self, program: GCodeProgram, **options) -> FixResult:
        out = program.copy()
        changes: list[LineChange] = []
        for i, line in enumerate(out.lines):
            stripped = line.rstrip(" \t")
            if stripped != line:
                changes.append(LineChange(kind="modified", original_index=i, original_text=line, new_text=stripped))
            out.lines[i] = stripped
        return FixResult(
            program=out,
            summary=f"Stripped trailing whitespace on {len(changes)} line(s).",
            changes=changes,
        )
