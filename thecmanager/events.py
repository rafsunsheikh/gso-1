"""A day's worth of what happened, for the Ops Room timeline.

The redesign's TODAY band is a chronological feed — a run came up, a job
finished, a test failed, a site edit went unpublished. Nothing in GSO-1 kept
that history: every view showed current state only.

This is deliberately small. Events append to one JSONL file, the file is
trimmed when it grows past `MAX_EVENTS`, and nothing here is on a hot path —
a failed write must never take an action down with it.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Optional

from . import config

MAX_EVENTS = 400
# Tones map to the timeline's dot colours; unknown kinds fall back to neutral.
TONES = {
    "run": "live", "llm": "live", "git": "brand", "job": "brand",
    "scan": "idle", "site": "warn", "fail": "err", "stop": "idle",
}

_lock = threading.Lock()


def _path():
    return config.DATA_DIR / "events.jsonl"


def record(kind: str, repo: str, text: str) -> None:
    """Append one event. Never raises — logging must not break the action."""
    row = {"at": time.time(), "kind": kind, "repo": repo, "text": text}
    try:
        with _lock:
            path = _path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            _trim(path)
    except OSError:
        pass


def _trim(path) -> None:
    """Keep the file bounded. Cheap: this runs only when it is already large."""
    try:
        if path.stat().st_size < 200_000:
            return
        lines = path.read_text(errors="ignore").splitlines()[-MAX_EVENTS:]
        path.write_text("\n".join(lines) + "\n")
    except OSError:
        pass


def recent(limit: int = 60, since: Optional[float] = None) -> list[dict]:
    """Newest first. `since` is a unix timestamp — the UI passes local midnight."""
    try:
        lines = _path().read_text(errors="ignore").splitlines()
    except OSError:
        return []

    out = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if since is not None and row.get("at", 0) < since:
            break          # the file is append-ordered, so we are past the window
        row["tone"] = TONES.get(row.get("kind"), "")
        out.append(row)
        if len(out) >= limit:
            break
    return out
