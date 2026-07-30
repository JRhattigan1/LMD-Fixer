"""Lets the user selectively remove named program sections.

Each section is marked by a standalone comment line of the form
`(SECTION_NAME)` (no spaces/colons inside the parens, distinguishing it from
free-text header comments like `(PROJECT: ...)`). A section runs from its
marker line up to, but not including, the next section marker (or end of
file). Every section is proposed for removal by default so the reviewer
can toggle individual ones to keep.

The trailing end-of-program footer (Z/XY retract via G91 G28, rotary-disable
G65...P8000/M337, G69/M9, then M30 + %) isn't part of any section's job
content — it's the fixed shutdown sequence that must run after whichever
layer happens to be last. Without special handling the last section's range
would otherwise extend to true EOF and swallow it, so it's excluded from
every section's removable range regardless of section boundaries.
"""

from __future__ import annotations

import re

from lmd_fixer.fixes import SECTION_MARKER_RE, Fix, FixResult, LineChange, register
from lmd_fixer.gcode import GCodeProgram

_FOOTER_LINE_RE = re.compile(
    r"^/?\s*("
    r"M30"
    r"|M321"
    r"|M324"
    r"|M337"
    r"|M9"
    r"|(?:G0\s+)?G69"
    r"|G91\s+G28(?:\s+[XYZ][\d.]*)+"
    r"|G90\s+(?:G0\s+)?A[\d.]+"
    r"|G65\b.*P8000"
    r"|G4\s*X[\d.]+"
    r")\s*$",
    re.IGNORECASE,
)


def _is_footer_line(line: str) -> bool:
    stripped = line.strip()
    return stripped in ("", "%") or bool(_FOOTER_LINE_RE.match(stripped))


def _footer_start(lines: list[str]) -> int:
    """Index where the trailing end-of-program footer begins, scanning
    backward from EOF while lines match known shutdown/end-of-tape patterns.
    Returns len(lines) if there's no such footer."""
    i = len(lines)
    while i > 0 and _is_footer_line(lines[i - 1]):
        i -= 1
    return i


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

        footer_start = _footer_start(lines)

        changes: list[LineChange] = []
        for k, (start, name) in enumerate(markers):
            if k + 1 < len(markers):
                end = markers[k + 1][0] - 1
            else:
                end = max(start, min(len(lines) - 1, footer_start - 1))
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
