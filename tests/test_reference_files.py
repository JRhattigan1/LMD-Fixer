"""The full review on the real files, against the user's hand-fixed versions.

Accepting every fix's defaults (section removal keeps everything) reproduces
the reference except for known review-choice differences, listed per file in
CLAUDE.md. Skipped where the git-ignored .ptp files aren't present.
"""

import difflib
from collections import Counter

import pytest

from lmd_fixer.pipeline import FIX_ORDER
from lmd_fixer.review import ReviewSession


def significant(lines):
    return [l.strip() for l in lines if l.strip() and not l.startswith("(PROGRAM CREATED")]


@pytest.mark.parametrize(
    "source, reference, expected",
    [
        ("O1140 - Original.ptp", "O1140.ptp", {"+ /G4 X25.00": 3, "+ G90 G0 A0.0": 45, "- /M325": 6}),
        ("O1162-AsPosted.ptp", "O1162-Edited.ptp", {"+ /G4 X25.00": 3}),
    ],
)
def test_defaults_reproduce_the_reference(real_file, source, reference, expected):
    session = ReviewSession(real_file(source), FIX_ORDER)
    while not session.finished:
        session.apply()
    ours = significant(session.result().lines)
    theirs = significant(real_file(reference).lines)
    delta = Counter(l for l in difflib.ndiff(ours, theirs) if l[:2] in ("- ", "+ "))
    assert dict(delta) == expected
