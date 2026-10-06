"""Reads `fix_settings.toml`, the hand-edited switch board for which fixes
the app offers."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.10: Streamlit already depends on `toml`
    import toml as _toml

    def _load_toml(path: Path) -> dict:
        return _toml.loads(path.read_text(encoding="utf-8"))
else:
    def _load_toml(path: Path) -> dict:
        with path.open("rb") as f:
            return tomllib.load(f)

SETTINGS_PATH = Path(__file__).parent / "fix_settings.toml"


def disabled_fix_ids(path: Path = SETTINGS_PATH) -> set[str]:
    """Ids of fixes switched off (`= false`) in the settings file.

    Read fresh on every call, so an edit takes effect on the next page
    refresh without restarting the app. A missing file or a fix not listed
    in it means "on". A value that isn't true/false is an error rather than
    a guess, since guessing wrong would quietly run (or hide) a fix.
    """
    if not path.exists():
        return set()
    try:
        data = _load_toml(path)
    except Exception as exc:
        raise ValueError(f"Couldn't read {path.name}: {exc}") from exc
    fixes = data.get("fixes", {})
    disabled = set()
    for fix_id, enabled in fixes.items():
        if not isinstance(enabled, bool):
            raise ValueError(
                f"{path.name}: `{fix_id} = {enabled!r}` must be true or false (lower case, no quotes)"
            )
        if not enabled:
            disabled.add(fix_id)
    return disabled
