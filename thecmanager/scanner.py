"""Scan the Projects folder, resolve effective app config, read descriptions."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from . import config, detector, registry


def list_app_names() -> list[str]:
    """Top-level project directories across every configured root.

    Names are de-duplicated (the first root that has a given name wins) and
    returned alphabetically.
    """
    names: list[str] = []
    seen: set[str] = set()
    for root in config.PROJECTS_DIRS:
        if not root.exists():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            if entry.name in config.IGNORED_NAMES:
                continue
            if entry.name.startswith(config.IGNORED_PREFIXES):
                continue
            if entry.name in seen:
                continue
            seen.add(entry.name)
            names.append(entry.name)
    return sorted(names, key=str.lower)


def app_path(name: str) -> Path:
    """Path to a project, searching each root in order. Falls back to the
    primary root if it isn't found anywhere (e.g. for a not-yet-created dir)."""
    for root in config.PROJECTS_DIRS:
        p = root / name
        if p.is_dir():
            return p
    return config.PROJECTS_DIRS[0] / name


def exists(name: str) -> bool:
    p = app_path(name)
    return p.exists() and p.is_dir()


def root_label(name: str) -> str:
    """Display label of the root that contains `name` (first match wins)."""
    for label, root in config.PROJECT_ROOTS:
        if (root / name).is_dir():
            return label
    return config.PROJECT_ROOTS[0][0]


# Simple in-memory detection cache (path mtime keyed).
_detect_cache: dict[str, tuple[float, dict]] = {}


def invalidate() -> None:
    """Drop the detection cache.

    Entries are keyed by app name, not by full path, so changing the project
    roots can otherwise serve a stale detection for a same-named app under a
    different root.
    """
    _detect_cache.clear()


def detect_cached(name: str) -> dict:
    path = app_path(name)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return detector.detect(path)
    cached = _detect_cache.get(name)
    if cached and cached[0] == mtime:
        return cached[1]
    result = detector.detect(path)
    _detect_cache[name] = (mtime, result)
    return result


def read_description(name: str) -> str:
    """Best-effort short description: registry override > README > package.json."""
    override = registry.get(name).get("description")
    if override:
        return override

    path = app_path(name)
    # README first paragraph.
    for readme in ("README.md", "README.rst", "README.txt", "readme.md"):
        f = path / readme
        if f.exists():
            try:
                text = f.read_text(errors="ignore")
            except Exception:
                continue
            desc = _first_paragraph(text)
            if desc:
                return desc

    # package.json description.
    pkg = path / "package.json"
    if pkg.exists():
        try:
            d = json.loads(pkg.read_text()).get("description")
            if d:
                return d
        except Exception:
            pass

    return "No description available."


def _first_paragraph(markdown: str) -> Optional[str]:
    lines = markdown.splitlines()
    buf: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            if buf:
                break
            continue
        if s.startswith("#"):  # skip headings
            if buf:
                break
            continue
        if s.startswith(("![", "<", "[![", "---", "===")):  # badges/html/rules
            continue
        buf.append(s)
        if len(" ".join(buf)) > 280:
            break
    text = " ".join(buf).strip()
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # strip md links
    text = re.sub(r"[*_`]", "", text)  # strip emphasis
    return text[:400] if text else None


def effective_config(name: str) -> dict:
    """Merge detection with registry overrides into the config used to run."""
    detected = detect_cached(name)
    override = registry.get(name)
    start_command = override.get("start_command") or detected.get("start_command")
    port = override.get("port", detected.get("default_port"))
    setup_command = override.get("setup_command", detected.get("setup_command"))

    # For static sites the port is embedded in the command. If the user
    # overrode the port (but not the command), keep them in sync so the health
    # probe and the actual server agree.
    if (
        detected.get("type") == "static"
        and not override.get("start_command")
        and port
    ):
        start_command = re.sub(
            r"http\.server\s+\d+", f"http.server {port}", start_command or ""
        )
    return {
        "name": name,
        "type": detected.get("type"),
        "language": detected.get("language"),
        "start_command": start_command,
        "setup_command": setup_command,
        "port": port,
        "notes": detected.get("notes", ""),
        "has_override": bool(override),
        "favourite": bool(override.get("favourite")),
    }
