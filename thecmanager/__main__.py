"""Entry point: `python -m thecmanager` launches the dashboard."""
from __future__ import annotations

import os
import webbrowser

import uvicorn

from . import config


def main() -> None:
    config.ensure_dirs()
    url = f"http://{config.HOST}:{config.PORT}"
    loopback_only = config.HOST in ("127.0.0.1", "localhost", "::1")
    print("=" * 60)
    print("  GSO-1, local application registry")
    print(f"  Projects: {', '.join(str(d) for d in config.PROJECTS_DIRS)}")
    print(f"  Dashboard: {url}")
    print(f"  Bound to:  {config.HOST}  "
          f"({'this machine only' if loopback_only else 'every interface'})")
    if config.MOBILE_TOKEN and loopback_only:
        # The commonest way to get here: MANAGER_HOST is exported in the shell
        # that launched GSO-1, so it wins over the .env the setup script wrote.
        print("  WARNING: a phone access code is set, but GSO-1 is bound to "
              "loopback,\n           so no other device can reach it. "
              "Set MANAGER_HOST=0.0.0.0, \n           and check it is not "
              "exported in your shell, which overrides .env.")
    print("=" * 60)
    if not os.environ.get("MANAGER_NO_BROWSER"):
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
