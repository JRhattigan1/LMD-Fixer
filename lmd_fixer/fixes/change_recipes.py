"""Changes the LMD recipe number (the P of `M98 Pxxxx`) used by each toolpath.

The P number selects a recipe stored on the machine's control panel — laser
power, powder flow and nozzle gas. The program
only names the number; what it sets lives on the machine, so this fix just
changes which number each toolpath calls. Every toolpath in the real files
sets it once, in its header before the laser comes on:
    /M98 P0020
    /M325
    /G4 X25.00
so giving a toolpath a different recipe is a matter of rewriting the digits
of that one line. A toolpath whose block has no M98 call can't be changed —
inserting a new recipe call is out of scope — and is left alone.

Runs before `remove_repeated_p_calls`, which compares P values: changed
afterwards, a toolpath whose call had been collapsed would silently inherit
the previous toolpath's recipe.

Options (with none, no changes are proposed):
    recipes: {toolpath_start_index: int}   new P number for that toolpath
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lmd_fixer.fixes import SECTION_MARKER_RE, Fix, FixResult, LineChange, register
from lmd_fixer.gcode import GCodeProgram

# Groups: everything up to the digits (keeps `/` and spacing), the digits, the rest.
_M98_RE = re.compile(r"^(\s*/?\s*M98\s+P)(\d+)(.*)$", re.IGNORECASE)


@dataclass
class ToolpathRecipeCall:
    name: str
    start_index: int  # the (SECTION_NAME) marker line
    end_index: int  # inclusive
    call_index: int | None  # the M98 line, None if the block has none
    p_value: int | None
    p_width: int = 4  # digits as written, so a rewrite keeps the padding


def toolpath_recipe_calls(program: GCodeProgram) -> list[ToolpathRecipeCall]:
    """Each `(SECTION_NAME)` block, with the first `M98 Pxxxx` call inside it."""
    lines = program.lines
    markers = [(i, m.group(1)) for i, line in enumerate(lines) if (m := SECTION_MARKER_RE.match(line.strip()))]
    calls: list[ToolpathRecipeCall] = []
    for k, (start, name) in enumerate(markers):
        end = markers[k + 1][0] - 1 if k + 1 < len(markers) else len(lines) - 1
        call = ToolpathRecipeCall(name, start, end, None, None)
        for i in range(start + 1, end + 1):
            match = _M98_RE.match(lines[i])
            if match:
                call.call_index = i
                call.p_value = int(match.group(2))
                call.p_width = len(match.group(2))
                break
        calls.append(call)
    return calls


def set_p_value(line: str, p_value: int, width: int) -> str:
    match = _M98_RE.match(line)
    if not match:
        raise ValueError(f"Not an M98 Pxxxx line: {line!r}")
    return f"{match.group(1)}{p_value:0{width}d}{match.group(3)}"


@register
class ChangeRecipes(Fix):
    id = "change_recipes"
    review_mode = "recipes"
    label = "Change P recipe numbers"
    description = (
        "Changes the recipe number — the P of each toolpath's M98 Pxxxx call, which selects laser power, "
        "powder flow and nozzle gas settings stored on the control panel. Leave a toolpath blank to keep its recipe."
    )

    def apply(self, program: GCodeProgram, **options) -> FixResult:
        wanted: dict[int, int] = {int(k): int(v) for k, v in (options.get("recipes") or {}).items() if v}
        out = program.copy()
        changes: list[LineChange] = []
        unassignable: list[str] = []
        for call in toolpath_recipe_calls(program):
            p = wanted.get(call.start_index)
            if p is None:
                continue
            if call.call_index is None:
                unassignable.append(call.name)
                continue
            if p == call.p_value:
                continue
            original = program.lines[call.call_index]
            new_line = set_p_value(original, p, call.p_width)
            out.lines[call.call_index] = new_line
            changes.append(
                LineChange(
                    kind="modified",
                    original_index=call.call_index,
                    original_text=original,
                    new_text=new_line,
                    label=call.name,
                    reason=f"recipe P{call.p_value:0{call.p_width}d} -> P{p:0{call.p_width}d}",
                )
            )
        summary = f"Changed the recipe of {len(changes)} toolpath(s)."
        if unassignable:
            summary += f" {len(unassignable)} toolpath(s) have no M98 call to change: {', '.join(unassignable)}."
        return FixResult(program=out, summary=summary, changes=changes)
