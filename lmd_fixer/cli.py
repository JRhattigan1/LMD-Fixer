"""Console entry point: `lmd-fixer` launches the Streamlit UI."""

from __future__ import annotations

import sys
from pathlib import Path


# Mirrors .streamlit/config.toml, which pip-installed runs don't have
# (Streamlit only reads it from the working directory).
THEME_ARGS = [
    "--theme.base", "dark",
    "--theme.primaryColor", "#00d86c",
    "--theme.backgroundColor", "#272b33",
    "--theme.secondaryBackgroundColor", "#192231",
    "--theme.textColor", "#c2c7d1",
]


def main() -> None:
    from streamlit.web import cli as stcli

    app_path = Path(__file__).parent / "app.py"
    # Any extra arguments are passed through to `streamlit run`
    # (e.g. `lmd-fixer --server.port 8600`) and win over the theme defaults.
    sys.argv = ["streamlit", "run", str(app_path), *THEME_ARGS, *sys.argv[1:]]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
