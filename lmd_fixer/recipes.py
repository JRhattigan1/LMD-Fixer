"""The material/recipe database: what each `M98 Pxxxx` recipe number means.

The P in a toolpath's `/M98 Pxxxx` call selects an LMD recipe stored on the
machine's control panel — the laser power, powder flow and nozzle gas. The
program only names the number, so this keeps a record of what each one sets,
built up from trials, in a CSV that opens in Excel too. Material and Notes are
the operator's own notes, not panel settings. Nothing ties it to the machine: it's only as right as it was kept.

Where the file lives, highest priority first:

1. the `LMD_FIXER_RECIPES` environment variable
2. `[database] recipes = "path"` in `fix_settings.toml` (a relative path is
   taken from the settings file's folder), e.g. to share one copy on OneDrive
3. `%APPDATA%\\LMD-Fixer\\recipes.csv` (`~/.lmd-fixer/recipes.csv` off Windows)

Deliberately not beside the packaged `.exe`: a new version is unzipped into a
new folder, and the database would be left behind in the old one.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from pathlib import Path

import pandas as pd

from lmd_fixer.settings import _load_toml, settings_path

RECIPES_ENV_VAR = "LMD_FIXER_RECIPES"
RECIPES_FILE_NAME = "recipes.csv"

P_COLUMN = "P"
MATERIAL_COLUMN = "Material"
NUMERIC_COLUMNS = [
    "Laser power (W)",
    "Powder flow (g/min)",
    "Nozzle gas (l/min)",
]
# Columns an older version of the database had. Dropped on load (and so on
# the next save) rather than kept as unknown extras.
RETIRED_COLUMNS = ["Powder flow (rpm)", "Carrier gas (l/min)", "Shield gas (l/min)"]
NOTES_COLUMN = "Notes"
UPDATED_COLUMN = "Updated"
COLUMNS = [P_COLUMN, MATERIAL_COLUMN, *NUMERIC_COLUMNS, NOTES_COLUMN, UPDATED_COLUMN]
# Columns whose edit counts as changing the recipe (so `Updated` is restamped).
_CONTENT_COLUMNS = [MATERIAL_COLUMN, *NUMERIC_COLUMNS, NOTES_COLUMN]


def default_recipes_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "LMD-Fixer" / RECIPES_FILE_NAME
    return Path.home() / ".lmd-fixer" / RECIPES_FILE_NAME


def recipes_path() -> Path:
    """Where the recipe database is read from and saved to (see module docstring)."""
    override = os.environ.get(RECIPES_ENV_VAR)
    if override:
        return Path(override)
    settings = settings_path()
    if settings.exists():
        try:
            configured = _load_toml(settings).get("database", {}).get("recipes")
        except Exception as exc:
            raise ValueError(f"Couldn't read {settings.name}: {exc}") from exc
        if configured:
            if not isinstance(configured, str):
                raise ValueError(f"{settings.name}: [database] recipes must be a quoted file path")
            path = Path(os.path.expandvars(configured)).expanduser()
            return path if path.is_absolute() else settings.parent / path
    return default_recipes_path()


def empty_recipes() -> pd.DataFrame:
    return normalise(pd.DataFrame(columns=COLUMNS))


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Puts columns in the standard order with the dtypes the editor expects.
    Unknown extra columns (added by hand in Excel) are kept, after ours."""
    df = df.copy()
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    p = pd.to_numeric(df[P_COLUMN], errors="coerce")
    # A fractional P can't be an Int64; it's blanked here, and caught by
    # validate_recipes, which looks at the raw values, before any save.
    df[P_COLUMN] = p.where(p.isna() | (p % 1 == 0)).astype("Int64")
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Float64")
    for col in (MATERIAL_COLUMN, NOTES_COLUMN, UPDATED_COLUMN):
        df[col] = df[col].astype("string")
    extra = [c for c in df.columns if c not in COLUMNS and c not in RETIRED_COLUMNS]
    return df[COLUMNS + extra].reset_index(drop=True)


def load_recipes(path: Path | None = None) -> pd.DataFrame:
    """The recipe table, sorted by P. A missing file is an empty database."""
    path = path or recipes_path()
    if not path.exists():
        return empty_recipes()
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except PermissionError as exc:
        raise ValueError(f"Couldn't open {path} — is it open in another program?") from exc
    except Exception as exc:
        raise ValueError(f"Couldn't read {path}: {exc}") from exc
    if P_COLUMN not in raw.columns:
        raise ValueError(f"{path.name} has no `{P_COLUMN}` column, so it isn't a recipe database.")
    # Blank cells come in as "" — treat them as missing, not as text.
    raw = raw.replace("", None)
    return normalise(raw).sort_values(P_COLUMN, na_position="last", kind="stable").reset_index(drop=True)


def validate_recipes(df: pd.DataFrame) -> list[str]:
    """Problems that would stop the table being saved, as readable messages."""
    errors: list[str] = []
    p_values = pd.to_numeric(df[P_COLUMN], errors="coerce")
    for row, (raw, p) in enumerate(zip(df[P_COLUMN], p_values), start=1):
        if pd.isna(raw) or str(raw).strip() == "":
            errors.append(f"Row {row}: every recipe needs a P number.")
        elif pd.isna(p) or p != int(p) or not 0 < p <= 9999:
            errors.append(f"Row {row}: P must be a whole number from 1 to 9999 (got {raw!r}).")
    dupes = sorted({int(p) for p in p_values[p_values.duplicated(keep=False)].dropna()})
    if dupes:
        errors.append("Each P number can appear only once: " + ", ".join(format_p(p) for p in dupes) + " repeated.")
    for col in NUMERIC_COLUMNS:
        if col not in df.columns:
            continue
        values = df[col]
        bad = values.notna() & pd.to_numeric(values, errors="coerce").isna()
        for row in bad[bad].index:
            errors.append(f"{_row_name(df, row)}: {col} must be a number (got {values[row]!r}).")
    return errors


def _row_name(df: pd.DataFrame, row: int) -> str:
    p = pd.to_numeric(pd.Series([df[P_COLUMN].iloc[row]]), errors="coerce").iloc[0]
    return format_p(int(p)) if pd.notna(p) else f"Row {row + 1}"


def stamp_changed(new: pd.DataFrame, old: pd.DataFrame, today: dt.date | None = None) -> pd.DataFrame:
    """Sets `Updated` to today on every row that's new, or whose content
    differs from the row with the same P in `old`."""
    today_text = (today or dt.date.today()).isoformat()
    new = normalise(new)
    old = normalise(old)
    old_by_p = {int(r[P_COLUMN]): r for _, r in old.iterrows() if pd.notna(r[P_COLUMN])}

    def same(a, b) -> bool:
        return (pd.isna(a) and pd.isna(b)) or (not pd.isna(a) and not pd.isna(b) and a == b)

    for i, row in new.iterrows():
        before = old_by_p.get(int(row[P_COLUMN])) if pd.notna(row[P_COLUMN]) else None
        if before is None or not all(same(row[c], before[c]) for c in _CONTENT_COLUMNS):
            new.at[i, UPDATED_COLUMN] = today_text
    return new


def save_recipes(df: pd.DataFrame, path: Path | None = None) -> Path:
    """Validates and writes the table, replacing the file in one step so a
    failure part-way never leaves it half-written. Returns the path written."""
    path = path or recipes_path()
    errors = validate_recipes(df)
    if errors:
        raise ValueError("\n".join(errors))
    df = normalise(df).sort_values(P_COLUMN, kind="stable")
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel reads non-ASCII text (µm, °C) correctly.
    fd, tmp = tempfile.mkstemp(prefix=".recipes-", suffix=".csv", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            df.to_csv(f, index=False)
        os.replace(tmp, path)
    except PermissionError as exc:
        raise ValueError(
            f"Couldn't save {path.name} — if it's open in Excel, close it and try again."
        ) from exc
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def format_p(p: int, width: int = 4) -> str:
    """`21` -> `P0021`, the way the programs write recipe numbers."""
    return f"P{int(p):0{width}d}"


def recipe_summary(row) -> str:
    """One-line description of what a recipe sets on the panel, e.g.
    `800 W · 6 g/min · nozzle 8 l/min`; empty when none of it is recorded.
    Material is left out: it's the operator's note, not a panel setting."""
    formats = {
        "Laser power (W)": "{:g} W",
        "Powder flow (g/min)": "{:g} g/min",
        "Nozzle gas (l/min)": "nozzle {:g} l/min",
    }
    return " · ".join(fmt.format(row[col]) for col, fmt in formats.items() if pd.notna(row.get(col)))


def summaries_by_p(df: pd.DataFrame) -> dict[int, str]:
    """P -> `recipe_summary`, for every recipe in the table (an entry with
    nothing but its number recorded maps to "")."""
    return {int(row[P_COLUMN]): recipe_summary(row) for _, row in df.iterrows() if pd.notna(row[P_COLUMN])}
