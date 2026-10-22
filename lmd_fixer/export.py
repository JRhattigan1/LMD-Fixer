"""Turning a reviewed program into the file the machine gets, and showing
what changed. UI-free, so every front-end (and the self-test) shares it.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from lmd_fixer.gcode import GCodeProgram

# Standalone program-number header line, e.g. `O1140` (optionally `/`-blocked
# like other lines). The machine expects this to match the program's filename.
O_NUMBER_LINE_RE = re.compile(r"^(/?\s*O)(\d+)\s*$")
O_NUMBER_IN_NAME_RE = re.compile(r"O(\d+)", re.IGNORECASE)

# The machine controller expects CRLF, whatever the source file used.
MACHINE_LINE_ENDING = "\r\n"


def sync_o_number(program: GCodeProgram, output_name: str) -> tuple[GCodeProgram, str | None, str | None]:
    """Rewrites the program's `Oxxxx` header line to match the O-number found
    in `output_name`, if both are present. Returns (possibly new program,
    old number, new number); numbers are None where there's nothing to sync
    (no O-number line, or no O#### in the chosen filename)."""
    name_match = O_NUMBER_IN_NAME_RE.search(output_name)
    if not name_match:
        return program, None, None
    new_number = name_match.group(1)
    for i, line in enumerate(program.lines):
        line_match = O_NUMBER_LINE_RE.match(line.strip())
        if line_match:
            old_number = line_match.group(2)
            if old_number == new_number:
                return program, old_number, new_number
            out = program.copy()
            out.lines[i] = f"{line_match.group(1)}{new_number}"
            return out, old_number, new_number
    return program, None, None


NoteLevel = Literal["info", "warning"]


@dataclass
class OutputPlan:
    """What will be written: the file name, the program with its O-number
    synced to that name, and notes worth showing the user about either."""

    name: str
    program: GCodeProgram
    notes: list[tuple[NoteLevel, str]] = field(default_factory=list)

    def to_bytes(self) -> bytes:
        return machine_bytes(self.program)


def default_output_name(upload_name: str) -> str:
    return f"fixed_{upload_name}"


def plan_output(program: GCodeProgram, upload_name: str, chosen_name: str) -> OutputPlan:
    """Settles the output file name and syncs the O-number to it.

    An empty name falls back to `fixed_<upload>`; a name with no extension
    gets the upload's; a different extension is allowed but warned about,
    since the controller may expect the original one.
    """
    notes: list[tuple[NoteLevel, str]] = []
    fallback = default_output_name(upload_name)
    if not chosen_name.strip():
        notes.append(("warning", f"Output file name is empty — falling back to '{fallback}'."))
    name = chosen_name.strip() or fallback

    upload_suffix = Path(upload_name).suffix.lower()
    suffix = Path(name).suffix.lower()
    if upload_suffix and not suffix:
        name += upload_suffix
        notes.append(("info", f"No file extension given — appended '{upload_suffix}' to match the uploaded file."))
    elif upload_suffix and suffix != upload_suffix:
        notes.append((
            "warning",
            f"File extension changed from '{upload_suffix}' to '{suffix}' — "
            "the machine controller may expect the original extension.",
        ))

    found = O_NUMBER_IN_NAME_RE.findall(name)
    if len(found) > 1:
        notes.append((
            "warning",
            f"Multiple O-numbers found in the file name ({', '.join('O' + n for n in found)}) — "
            f"using the first one, O{found[0]}.",
        ))
    synced, old, new = sync_o_number(program, name)
    if old and new and old != new:
        notes.append(("info", f"Program number: O{old} → O{new} (to match the file name)."))
    elif old and new:
        notes.append(("info", f"Program number already matches the file name (O{new})."))
    elif found:
        notes.append(("info", "No O-number header line found in this program — file name left as the only reference."))
    return OutputPlan(name=name, program=synced, notes=notes)


def machine_bytes(program: GCodeProgram) -> bytes:
    """The program as the controller wants it: CRLF line endings, UTF-8."""
    return program.to_text(MACHINE_LINE_ENDING).encode("utf-8")


def write_program(program: GCodeProgram, path: str | Path) -> None:
    Path(path).write_bytes(machine_bytes(program))


DiffTag = Literal["equal", "delete", "insert", "replace", "gap"]


@dataclass
class DiffRow:
    """One row of a side-by-side diff. For a "gap" row, `left` says how many
    unchanged lines were collapsed and `right` is empty. Line numbers are
    1-based, None where that side has no line."""

    left: str
    right: str
    tag: DiffTag
    left_no: int | None = None
    right_no: int | None = None


def diff_rows(original: list[str], final: list[str], n_context: int = 3) -> list[DiffRow]:
    """Two-column diff of the untouched upload against the result. Runs of
    unchanged lines longer than `2 * n_context + 1` are collapsed to
    `n_context` either side of a "gap" row."""
    matcher = difflib.SequenceMatcher(a=original, b=final, autojunk=False)
    rows: list[DiffRow] = []

    def same(i: int, j: int) -> DiffRow:
        return DiffRow(original[i], final[j], "equal", i + 1, j + 1)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            n = i2 - i1
            if n > 2 * n_context + 1:
                rows.extend(same(i1 + k, j1 + k) for k in range(n_context))
                rows.append(DiffRow(f"{n - 2 * n_context} unchanged lines hidden", "", "gap"))
                rows.extend(same(i1 + k, j1 + k) for k in range(n - n_context, n))
            else:
                rows.extend(same(i, j) for i, j in zip(range(i1, i2), range(j1, j2)))
        elif tag == "delete":
            rows.extend(DiffRow(original[i], "", "delete", i + 1, None) for i in range(i1, i2))
        elif tag == "insert":
            rows.extend(DiffRow("", final[j], "insert", None, j + 1) for j in range(j1, j2))
        elif tag == "replace":
            left, right = list(range(i1, i2)), list(range(j1, j2))
            for k in range(max(len(left), len(right))):
                i = left[k] if k < len(left) else None
                j = right[k] if k < len(right) else None
                rows.append(DiffRow(
                    original[i] if i is not None else "",
                    final[j] if j is not None else "",
                    "replace",
                    i + 1 if i is not None else None,
                    j + 1 if j is not None else None,
                ))
    return rows


def change_context(lines: list[str], start: int, end: int, n_context: int = 3) -> str:
    """Plain-text excerpt around a change: line numbers, with the changed
    lines (`start`..`end`, 0-based inclusive) marked by a leading arrow."""
    lo = max(0, start - n_context)
    hi = min(len(lines) - 1, end + n_context)
    return "\n".join(
        f"{'->' if start <= i <= end else '  '} {i + 1:>6}  {lines[i]}" for i in range(lo, hi + 1)
    )
