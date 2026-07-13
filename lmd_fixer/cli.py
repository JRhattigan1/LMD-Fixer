"""Console entry point: `lmd-fixer` launches the Streamlit UI."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    from streamlit.web import cli as stcli

    app_path = Path(__file__).parent / "app.py"
    # Any extra arguments are passed through to `streamlit run`
    # (e.g. `lmd-fixer --server.port 8600`).
    sys.argv = ["streamlit", "run", str(app_path), *sys.argv[1:]]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
