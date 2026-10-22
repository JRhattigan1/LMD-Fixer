"""Shared fixtures.

`sample_program` is a small synthetic program shaped like the real files:
three toolpath blocks, each with its own header (recipe call, rotary
commands, plunge, laser on/off), then the end-of-program footer. The real
`.ptp` files are git-ignored, so tests that need them use `real_file`, which
skips when the file isn't on this machine.
"""

from __future__ import annotations

import os
from pathlib import Path

# Desktop tests run without a display. Set before Qt is first imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from lmd_fixer.gcode import GCodeProgram

REAL_FILES = Path(__file__).resolve().parent.parent / "lmd_fixer" / "tests"


def toolpath_block(name: str, p: int, x0: float) -> list[str]:
    """One toolpath as the CAM writes it: a 10 mm square deposited at x0."""
    return [
        f"({name})",
        "M5",
        "G94",
        "T1 M6",
        "/M321",
        "G91 G28 Z0.0",
        f"/M98 P{p:04d}",
        "/M325",
        "/G4 X25.00",
        "G90",
        "G65 B0.0 F1000. D1 P8000",
        "M337",
        "G54",
        "G90 G0 A0.0",
        f"X{x0:g}. Y5.",
        "G43 Z8. H01",
        "/M322",
        "G17",
        "Z3.",
        "G1 Z0.0 F500.",
        "/M323",
        "Y15.",
        f"X{x0 + 10:g}.",
        "Y5.",
        f"X{x0:g}.",
        "/M322",
        "G0 Z8.",
    ]


def build_sample() -> list[str]:
    return [
        "%",
        "O1234",
        "(PROJECT: SYNTHETIC TEST)",
        "(PROGRAM CREATED Thu Jan 1 00:00:00 2026)",
        "G40 G90 G80 G21",
        "G0 G69",
        "G91 G28 Z0.0",
        "G28 X0.0 Y0.0",
        *toolpath_block("PATH_A", 20, 0),
        *toolpath_block("PATH_B", 20, 30),
        *toolpath_block("PATH_C", 21, 60),
        "G91 G28 Z0.0",
        "G65 B0.0 F1000. D1 P8000",
        "M337",
        "G91 G28 X0.0 Y0.0",
        "M30",
        "%",
    ]


@pytest.fixture
def sample_program() -> GCodeProgram:
    return GCodeProgram(lines=build_sample(), source_name="O1234.ptp")


@pytest.fixture
def real_file():
    def load(name: str) -> GCodeProgram:
        path = REAL_FILES / name
        if not path.exists():
            pytest.skip(f"{name} isn't on this machine (the .ptp files are git-ignored)")
        return GCodeProgram.from_file(str(path))

    return load
