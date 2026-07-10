"""Core representation of a G-code program as an editable list of lines."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GCodeProgram:
    """A G-code/PTP program, kept as raw lines to preserve formatting and comments."""

    lines: list[str] = field(default_factory=list)
    source_name: str = "program"

    @classmethod
    def from_text(cls, text: str, source_name: str = "program") -> "GCodeProgram":
        # Normalise line endings; downstream fixes work on a plain list of lines.
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        return cls(lines=lines, source_name=source_name)

    @classmethod
    def from_file(cls, path: str) -> "GCodeProgram":
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return cls.from_text(f.read(), source_name=path)

    def to_text(self, line_ending: str = "\r\n") -> str:
        return line_ending.join(self.lines)

    def copy(self) -> "GCodeProgram":
        return GCodeProgram(lines=list(self.lines), source_name=self.source_name)
