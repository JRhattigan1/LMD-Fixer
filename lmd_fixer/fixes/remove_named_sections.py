"""Lets the user selectively remove named program sections.

Each section is marked by a standalone comment line of the form
`(SECTION_NAME)` (no spaces/colons inside the parens, distinguishing it from
free-text header comments like `(PROJECT: ...)`). A section runs from its
marker line up to, but not including, the next section marker (or end of
file). Every section is proposed for removal by default so the reviewer
can toggle individual ones to keep.
"""

from __future__ import annotations

from lmd_fixer.fixes import SECTION_MARKER_RE, Fix, FixResult, LineChange, register
from lmd_fixer.gcode import GCodeProgram


@register
class RemoveNamedSections(Fix):
    id = "remove_named_sections"
    label = "Remove named program sections"
    description = "Lists each named (SECTION_NAME) block in the program so you can choose which ones to remove entirely."

    def apply(self, program: GCodeProgram, **options) -> FixResult:
        out = program.copy()
        lines = out.lines

        markers: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            match = SECTION_MARKER_RE.match(line.strip())
            if match:
                markers.append((i, match.group(1)))

        changes: list[LineChange] = []
        for k, (start, name) in enumerate(markers):
            end = (markers[k + 1][0] - 1) if k + 1 < len(markers) else (len(lines) - 1)
            changes.append(
                LineChange(
                    kind="removed",
                    original_index=start,
                    end_index=end,
                    original_text=lines[start],
                    label=name,
                )
            )

        return FixResult(
            program=out,
            summary=f"Found {len(changes)} named section(s).",
            changes=changes,
        )
