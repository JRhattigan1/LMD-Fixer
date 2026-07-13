"""Registry of available G-code fixes.

Each fix is a small, independent, composable transformation over a
GCodeProgram. To add a new fix: create a module in this package that
defines a subclass of Fix and decorate it with @register.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from lmd_fixer.gcode import GCodeProgram

_REGISTRY: dict[str, type["Fix"]] = {}

# Standalone comment line naming a program section, e.g. `(1_LAYER_...)` —
# no whitespace inside the parens, distinguishing it from free-text header
# comments like `(PROJECT: ...)`.
SECTION_MARKER_RE = re.compile(r"^\(([^\s()]+)\)$")


def section_names(lines: list[str]) -> list[str | None]:
    """Maps each line index to the name of its enclosing (SECTION_NAME) block,
    or None for lines before the first marker."""
    names: list[str | None] = []
    current: str | None = None
    for line in lines:
        match = SECTION_MARKER_RE.match(line.strip())
        if match:
            current = match.group(1)
        names.append(current)
    return names

ChangeKind = Literal["removed", "modified", "flagged"]


@dataclass
class LineChange:
    """One reviewable change made by a fix, referring to a line (or line range) in the *original* program.

    For single-line changes, end_index defaults to original_index. For a
    multi-line change (e.g. removing a whole named section), end_index is
    the last original line index included in the change, inclusive.
    """

    kind: ChangeKind
    original_index: int
    original_text: str
    new_text: str | None = None  # None when kind == "removed"
    end_index: int | None = None  # None means single-line; defaults to original_index
    label: str | None = None  # optional human-readable name for the change (e.g. section name)

    def __post_init__(self) -> None:
        if self.end_index is None:
            self.end_index = self.original_index


@dataclass
class FixResult:
    program: GCodeProgram
    summary: str = ""
    changes: list[LineChange] = field(default_factory=list)


class Fix(ABC):
    """Base class for a single G-code fix/transformation."""

    id: str = ""
    label: str = ""
    description: str = ""

    @abstractmethod
    def apply(self, program: GCodeProgram, **options) -> FixResult:
        ...


def register(fix_cls: type[Fix]) -> type[Fix]:
    if not fix_cls.id:
        raise ValueError(f"{fix_cls.__name__} must define a non-empty id")
    _REGISTRY[fix_cls.id] = fix_cls
    return fix_cls


def available_fixes() -> dict[str, type[Fix]]:
    return dict(_REGISTRY)


__all__ = [
    "Fix",
    "FixResult",
    "LineChange",
    "register",
    "available_fixes",
    "section_names",
    "SECTION_MARKER_RE",
]

# Import fix modules so their @register decorators run.
from lmd_fixer.fixes import example_fix  # noqa: E402,F401
from lmd_fixer.fixes import flag_dwells  # noqa: E402,F401
from lmd_fixer.fixes import remove_named_sections  # noqa: E402,F401
from lmd_fixer.fixes import remove_repeated_p_calls  # noqa: E402,F401
from lmd_fixer.fixes import remove_rotary_table  # noqa: E402,F401
