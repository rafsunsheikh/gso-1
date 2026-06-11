"""Persistent per-app overrides and metadata (registry.json).

Stores anything the user customises about an app: a custom start command,
port, a hand-written description, pinned/favourite flag, etc. Detection fills
in everything that isn't overridden here.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from . import config

_lock = threading.Lock()


def _load() -> dict:
    if not config.REGISTRY_FILE.exists():
        return {}
    try:
        return json.loads(config.REGISTRY_FILE.read_text())
    except Exception:
        return {}


def _save(data: dict) -> None:
    config.ensure_dirs()
    tmp = config.REGISTRY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(config.REGISTRY_FILE)


def get(name: str) -> dict:
    with _lock:
        return _load().get(name, {})


def all() -> dict:
    with _lock:
        return _load()


def update(name: str, patch: dict[str, Any]) -> dict:
    """Merge `patch` into the stored config for `name`. None values clear keys."""
    with _lock:
        data = _load()
        entry = data.get(name, {})
        for k, v in patch.items():
            if v is None:
                entry.pop(k, None)
            else:
                entry[k] = v
        if entry:
            data[name] = entry
        else:
            data.pop(name, None)
        _save(data)
        return entry
