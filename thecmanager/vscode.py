"""VS Code integration: open/focus projects and detect open windows.

Open-window detection reads VS Code's saved window state
(`storage.json` -> windowsState.openedWindows[].folder), which VS Code updates
as windows open, close and gain focus. It can lag a focus change by a moment,
but it's the most reliable signal available without an extension.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

# Candidate locations for the `code` CLI (PATH first, then the app bundle).
_CODE_CANDIDATES = [
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    "/usr/local/bin/code",
    str(Path.home() / ".local/bin/code"),
]

_STORAGE = (
    Path.home()
    / "Library/Application Support/Code/User/globalStorage/storage.json"
)


def code_bin() -> str | None:
    """Return a usable `code` CLI path, or None if VS Code isn't installed."""
    found = shutil.which("code")
    if found:
        return found
    for c in _CODE_CANDIDATES:
        if Path(c).exists():
            return c
    return None


def _norm(path: str | Path) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(path)


def open_folders() -> set[str]:
    """Set of absolute folder paths currently open in VS Code windows."""
    if not _STORAGE.exists():
        return set()
    try:
        data = json.loads(_STORAGE.read_text(encoding="utf-8"))
    except Exception:
        return set()

    ws = data.get("windowsState", {}) or {}
    windows = list(ws.get("openedWindows", []) or [])
    last = ws.get("lastActiveWindow")
    if last:
        windows.append(last)

    folders: set[str] = set()
    for w in windows:
        uri = w.get("folder")
        if not uri:
            continue
        parsed = urlparse(uri)
        if parsed.scheme and parsed.scheme != "file":
            continue
        folders.add(_norm(unquote(parsed.path)))
    return folders


def is_open(path: str | Path) -> bool:
    return _norm(path) in open_folders()


def open_project(path: str | Path, new_window: bool = True) -> dict:
    """Open `path` in VS Code. new_window=True forces a fresh window."""
    cb = code_bin()
    if not cb:
        return {"ok": False, "message": "VS Code CLI not found."}
    args = [cb]
    if new_window:
        args.append("-n")
    args.append(str(path))
    try:
        subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return {"ok": True, "message": "Opening in VS Code…"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"Failed to open VS Code: {e}"}


def focus_project(path: str | Path) -> dict:
    """Bring the existing VS Code window for `path` to the front.

    `code <folder>` focuses the window already showing that folder.
    """
    cb = code_bin()
    if not cb:
        return {"ok": False, "message": "VS Code CLI not found."}
    try:
        subprocess.Popen(
            [cb, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        # Also raise the app itself to the foreground.
        subprocess.Popen(
            ["open", "-a", "Visual Studio Code"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "message": "Focusing VS Code window…"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"Failed: {e}"}
