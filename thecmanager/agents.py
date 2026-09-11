"""The Claude Code sessions running on this machine, and what they are doing.

GSO-1 already knows every project. It did not know that six agents were at
work inside them: sessions started in a terminal were invisible to it, and the
only ones it could see were the ones it had spawned itself through
`claudebridge`. This module closes that gap by reading what Claude Code
already writes down.

Three files matter, and none of them need a process to be interrogated:

  ~/.claude/sessions/<pid>.json      who exists, where, and idle or busy
  ~/.claude/projects/<enc>/<id>.jsonl  the transcript, appended as it works
  /tmp/cc-socks/<pid>.sock           the channel a peer would talk to

Only the first two are read here. The socket is reported but never written to:
its protocol is internal and undocumented, and a feature that breaks whenever
Claude Code updates is worse than one that never claimed to be there. Sending
work to an agent stays with `claudebridge`, which owns the sessions it starts.

Two things this deliberately does not do:

* **It does not read a transcript to list sessions.** One of these files is
  140 MB. Listing is registry-only, and a transcript is touched solely when
  somebody asks what an agent is doing, from the end, a few kilobytes at a time.
* **It does not trust `status`.** A session that died mid-turn stays "busy" in
  the registry for ever, so the last transcript timestamp is reported beside it
  and the two are allowed to disagree. "Busy, silent for ten hours" is the
  useful answer, and averaging it into one word would throw that away.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterator, Optional

from . import config

SESSIONS_DIR = Path.home() / ".claude" / "sessions"
TRANSCRIPTS_DIR = Path.home() / ".claude" / "projects"

# How much of a transcript's tail to read when asked what an agent is doing.
# Enough for the last several turns; nothing like enough to matter.
_TAIL_BYTES = 64 * 1024

# session_id -> (path, mtime_when_found). Finding a transcript means globbing
# 40-odd directories, which is cheap but not free at a poll interval.
_transcript_cache: dict[str, Path] = {}


def _alive(pid: int) -> bool:
    """Is this pid still running? Signal 0 tests without touching it."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True          # exists, owned by somebody else


def _project_for(cwd: str) -> tuple[Optional[str], Optional[str]]:
    """(project name, root label) when the session sits in a watched folder.

    A session in ~/Downloads is a real session but not a plot of land, and the
    map should be able to tell the difference rather than inventing a project.
    """
    try:
        p = Path(cwd).resolve()
    except OSError:
        return None, None
    for label, root in config.PROJECT_ROOTS:
        try:
            root_r = root.resolve()
        except OSError:
            continue
        if p == root_r:
            return None, label
        if root_r in p.parents:
            # The project is the first segment under the root, not the leaf: a
            # session deep inside a repo still belongs to that repo.
            return p.relative_to(root_r).parts[0], label
    return None, None


def transcript_for(session_id: str) -> Optional[Path]:
    """The session's .jsonl, found by id rather than by encoding its path.

    The directory name is the cwd with `/`, `_` and `.` all flattened to `-`,
    which is lossy: `a_b` and `a-b` land in the same place. The session id is
    unique and is the filename, so searching for it is both exact and immune to
    that rule changing underneath us.
    """
    hit = _transcript_cache.get(session_id)
    if hit is not None and hit.exists():
        return hit
    if not TRANSCRIPTS_DIR.is_dir():
        return None
    for d in TRANSCRIPTS_DIR.iterdir():
        if not d.is_dir():
            continue
        f = d / f"{session_id}.jsonl"
        if f.exists():
            _transcript_cache[session_id] = f
            return f
    return None


def _tail_lines(path: Path, nbytes: int = _TAIL_BYTES) -> list[str]:
    """The last few complete lines of a file, without reading the whole thing."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > nbytes:
                fh.seek(size - nbytes)
                fh.readline()          # discard the partial line we landed in
            data = fh.read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()


def _blocks(entry: dict) -> list[dict]:
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def activity(session_id: str, limit: int = 8) -> dict:
    """What this agent has been doing lately: its most recent tool calls.

    The animation vocabulary, really. A tool call is a discrete verb with a
    timestamp, which is exactly what something watching an agent work needs.
    """
    path = transcript_for(session_id)
    if path is None:
        return {"available": False, "tools": [], "last_at": None, "last_text": None}

    tools: list[dict] = []
    last_at: Optional[str] = None
    last_text: Optional[str] = None

    for line in reversed(_tail_lines(path)):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        ts = entry.get("timestamp")
        if ts and last_at is None:
            last_at = ts
        for b in _blocks(entry):
            if b.get("type") == "tool_use" and len(tools) < limit:
                tools.append({"tool": b.get("name") or "?", "at": ts})
            elif b.get("type") == "text" and last_text is None:
                text = (b.get("text") or "").strip()
                if text:
                    last_text = text[:200]
        if len(tools) >= limit and last_text is not None:
            break

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    return {
        "available": True,
        "tools": tools,
        "last_at": last_at,
        "last_text": last_text,
        "mtime": mtime,
        "idle_seconds": int(time.time() - mtime) if mtime else None,
        "bytes": path.stat().st_size if path.exists() else 0,
    }


def _read_registry() -> Iterator[dict]:
    if not SESSIONS_DIR.is_dir():
        return
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            yield json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue          # a half-written entry is not an error worth raising


def sessions(include_dead: bool = False) -> list[dict]:
    """Every live Claude Code session, newest first. Registry only, no I/O per
    session beyond one small JSON file."""
    out: list[dict] = []
    for d in _read_registry():
        pid = d.get("pid")
        if not isinstance(pid, int):
            continue
        live = _alive(pid)
        if not live and not include_dead:
            continue
        cwd = d.get("cwd") or ""
        project, root = _project_for(cwd)
        sock = d.get("messagingSocketPath") or ""
        out.append({
            "pid": pid,
            "session_id": d.get("sessionId"),
            "name": d.get("name") or Path(cwd).name or f"pid {pid}",
            "cwd": cwd,
            "project": project,
            "root": root,
            "in_library": project is not None,
            "status": d.get("status") or "unknown",
            "kind": d.get("kind") or "unknown",
            "entrypoint": d.get("entrypoint"),
            "version": d.get("version"),
            "started_at": d.get("startedAt"),
            "status_updated_at": d.get("statusUpdatedAt"),
            "alive": live,
            # Reported so the UI can say an agent is reachable in principle.
            # Nothing here writes to it; see the module docstring.
            "has_socket": bool(sock) and Path(sock).exists(),
        })
    out.sort(key=lambda s: s.get("started_at") or 0, reverse=True)
    return out


def _encoded(path: Path) -> str:
    """The transcript directory name Claude Code uses for a working directory.

    `/`, `_` and `.` all flatten to `-`. That is lossy, so it is only ever used
    forwards, from a path we already have to the directory it would produce.
    """
    return "-" + str(path).replace("/", "-").replace("_", "-").replace(".", "-").lstrip("-")


def history() -> dict:
    """How much work has happened in each project, in transcript bytes.

    Not a count of files on disk and not lines of code: the size of the
    conversations held inside it. A repository you have spent months in reads
    differently from one you cloned and left, and that difference is the only
    honest basis the World has for making one settlement grander than another.
    """
    from . import scanner
    out: dict[str, dict] = {}
    if not TRANSCRIPTS_DIR.is_dir():
        return out
    for name in scanner.list_app_names():
        d = TRANSCRIPTS_DIR / _encoded(scanner.app_path(name))
        if not d.is_dir():
            continue
        total = 0
        count = 0
        try:
            for f in d.glob("*.jsonl"):
                total += f.stat().st_size
                count += 1
        except OSError:
            continue
        if count:
            out[name] = {"sessions": count, "bytes": total}
    return out


def snapshot(with_activity: bool = True, limit: int = 6) -> dict:
    """Sessions plus, optionally, what each is doing. What the map renders."""
    items = sessions()
    if with_activity:
        for s in items:
            sid = s.get("session_id")
            act = activity(sid, limit=limit) if sid else {"available": False}
            s["activity"] = act
            # The registry's own word, checked against the transcript clock.
            idle = act.get("idle_seconds")
            s["working"] = bool(
                s["status"] == "busy" and idle is not None and idle < 120
            )
            s["stale_busy"] = bool(
                s["status"] == "busy" and (idle is None or idle >= 120)
            )
    by_project: dict[str, int] = {}
    for s in items:
        if s["project"]:
            by_project[s["project"]] = by_project.get(s["project"], 0) + 1
    return {
        "sessions": items,
        "count": len(items),
        "history": history(),
        "working": sum(1 for s in items if s.get("working")),
        "projects": by_project,
        "ts": time.time(),
    }
