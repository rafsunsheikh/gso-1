"""Entry point for the packaged GSO-1 backend.

The Electron shell spawns this binary directly, so there is no venv, no
supervisor, and no promoted release in the picture, just the server. Anything
the shell needs to control is passed in the environment.
"""
from __future__ import annotations

import multiprocessing
import os
import sys


def main() -> None:
    # A frozen build must never open a browser: the shell owns the window.
    os.environ.setdefault("MANAGER_NO_BROWSER", "1")

    from thecmanager import config, server  # noqa: F401  (imported for side effects)
    import uvicorn

    config.ensure_dirs()
    uvicorn.run(
        server.app,
        host=config.HOST,
        port=config.PORT,
        log_level=os.environ.get("MANAGER_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    # PyInstaller re-executes the binary for each child process; without this
    # the server forks copies of itself on start.
    multiprocessing.freeze_support()
    sys.exit(main())
