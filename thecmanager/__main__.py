"""Entry point: `python -m thecmanager` launches the dashboard."""
from __future__ import annotations

import webbrowser

import uvicorn

from . import config


def main() -> None:
    config.ensure_dirs()
    url = f"http://{config.HOST}:{config.PORT}"
    print("=" * 60)
    print("  The Manager — local application registry")
    print(f"  Projects: {config.PROJECTS_DIR}")
    print(f"  Dashboard: {url}")
    print("=" * 60)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    uvicorn.run(
        "thecmanager.server:app",
        host=config.HOST,
        port=config.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
