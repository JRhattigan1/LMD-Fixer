"""The launcher's self-test, as the Windows build runs it (offscreen)."""

import pytest

pytest.importorskip("PySide6")

from lmd_fixer.cli import _selftest  # noqa: E402
from lmd_fixer.desktop import theme  # noqa: E402
from tests.conftest import build_sample  # noqa: E402


@pytest.fixture(autouse=True)
def back_to_dark(qapp):
    yield
    theme.apply_theme(qapp, theme.DEFAULT_THEME)


def test_selftest_without_files_checks_the_window_loads(tmp_path):
    report = tmp_path / "report.txt"
    assert _selftest(["--report", str(report)]) == 0
    lines = report.read_text(encoding="utf-8").splitlines()
    assert lines[1:] == ["ok   window builds, in every theme", "ok   3D view and SpaceMouse modules load"]


def test_selftest_runs_the_review_and_writes_crlf(tmp_path):
    source = tmp_path / "O1234.ptp"
    source.write_bytes("\r\n".join(build_sample()).encode("utf-8"))
    out = tmp_path / "out.ptp"
    report = tmp_path / "report.txt"
    assert _selftest([str(source), str(out), "--report", str(report)]) == 0
    text = report.read_text(encoding="utf-8")
    assert "ok   window opens O1234.ptp" in text and "FAIL" not in text
    data = out.read_bytes()
    assert b"P8000" not in data and b"\n" not in data.replace(b"\r\n", b"")


def test_selftest_reports_a_missing_file(tmp_path):
    report = tmp_path / "report.txt"
    code = _selftest([str(tmp_path / "missing.ptp"), str(tmp_path / "o.ptp"), "--report", str(report)])
    assert code == 1 and "FAIL" in report.read_text(encoding="utf-8")
