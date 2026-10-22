import pandas as pd
import pytest

from lmd_fixer import recipes as R


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "recipes.csv"
    monkeypatch.setenv(R.RECIPES_ENV_VAR, str(path))
    return path


def test_missing_file_is_an_empty_database(db_path):
    df = R.load_recipes()
    assert df.empty and list(df.columns) == R.COLUMNS


def test_round_trip_and_summary(db_path):
    df = R.normalise(pd.DataFrame([
        {"P": 21, "Material": "316L", "Laser power (W)": 800, "Powder flow (g/min)": 6, "Notes": "µm"},
        {"P": 20, "Nozzle gas (l/min)": 8},
    ]))
    R.save_recipes(df)
    back = R.load_recipes()
    assert list(back["P"]) == [20, 21]  # sorted by P
    assert back["Notes"].iloc[1] == "µm"
    assert db_path.read_bytes().startswith(b"\xef\xbb\xbf")  # Excel-friendly BOM
    # Material is a note, not a panel setting, so it stays out of the summary.
    assert R.summaries_by_p(back) == {20: "nozzle 8 l/min", 21: "800 W · 6 g/min"}


def test_retired_columns_are_dropped(db_path):
    db_path.write_text("P,Shield gas (l/min),Powder flow (rpm),My column\n20,8,4,x\n", encoding="utf-8")
    df = R.load_recipes()
    assert "Shield gas (l/min)" not in df.columns and "Powder flow (rpm)" not in df.columns
    assert "My column" in df.columns  # columns added by hand are kept


def test_validation(db_path):
    df = pd.DataFrame({"P": [20, 20, None, 1.5]})
    errors = R.validate_recipes(df)
    assert any("P0020 repeated" in e for e in errors)
    assert any("Row 3" in e and "needs a P" in e for e in errors)
    assert any("Row 4" in e and "whole number" in e for e in errors)
    with pytest.raises(ValueError):
        R.save_recipes(df)


def test_not_a_recipe_file(db_path):
    db_path.write_text("Material\n316L\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no `P` column"):
        R.load_recipes()


def test_locked_file_gives_a_friendly_error(db_path, monkeypatch):
    def locked(*args):
        raise PermissionError

    monkeypatch.setattr(R.os, "replace", locked)
    with pytest.raises(ValueError, match="open in Excel"):
        R.save_recipes(R.normalise(pd.DataFrame([{"P": 20}])))
    assert not list(db_path.parent.glob(".recipes-*"))  # temp file cleaned up


def test_path_from_settings_file(tmp_path, monkeypatch):
    monkeypatch.delenv(R.RECIPES_ENV_VAR, raising=False)
    settings = tmp_path / "fix_settings.toml"
    settings.write_text('[database]\nrecipes = "shared/r.csv"\n', encoding="utf-8")
    monkeypatch.setenv("LMD_FIXER_SETTINGS", str(settings))
    assert R.recipes_path() == tmp_path / "shared" / "r.csv"


def test_stamp_changed_marks_only_new_or_edited_rows():
    old = R.normalise(pd.DataFrame([{"P": 20, "Laser power (W)": 700, "Updated": "2026-01-01"},
                                    {"P": 21, "Laser power (W)": 800, "Updated": "2026-01-01"}]))
    new = old.copy()
    new.loc[1, "Laser power (W)"] = 900
    new = pd.concat([new, pd.DataFrame([{"P": 22}])], ignore_index=True)
    import datetime as dt

    stamped = R.stamp_changed(new, old, today=dt.date(2026, 10, 22))
    assert list(stamped["Updated"]) == ["2026-01-01", "2026-10-22", "2026-10-22"]
