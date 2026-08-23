"""Aggregated state for the Ops Room view and the Attention nav counts.

The redesign's default view answers "what's live, what needs me, what
happened" without browsing. That needs three things `/api/apps` does not
return: live process detail (pid, uptime), per-repo git state across every
repo, and which runs failed.

Doing it per-repo from the browser would be ~270 round trips, so it is
aggregated here and cached — a full git sweep costs ~12s of subprocesses and
must not run on every poll.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import socket
from collections import defaultdict

from . import git_ops, health, runner, scanner

# A sweep shells out to git once per repo. Cache hard; the UI polls often.
_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
CACHE_SECONDS = 45
SWEEP_WORKERS = 8


def _repo_state(name: str) -> Optional[dict]:
    path = scanner.app_path(name)
    if not git_ops.is_repo(path):
        return None
    try:
        st = git_ops.status(path)
    except Exception:  # noqa: BLE001 — one bad repo must not sink the sweep
        return None
    return {
        "name": name,
        "branch": st.get("branch"),
        "dirty": bool(st.get("dirty")),
        "changed_count": st.get("changed_count", 0),
        "ahead": st.get("ahead", 0),
        "behind": st.get("behind", 0),
        "last_commit": st.get("last_commit"),
    }


def _sweep(names: list[str]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=SWEEP_WORKERS) as pool:
        return [r for r in pool.map(_repo_state, names) if r]


def _port_owner(port: int) -> bool:
    """True if something already holds this port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(0.25)
        return sk.connect_ex(("127.0.0.1", port)) == 0


def _conflicts(names: list[str], live: list[dict]) -> list[dict]:
    """Apps configured on a port that another app is already serving.

    The redesign surfaces this as a "port busy · :3000 held by <app>" row;
    without it, starting the second app just fails with an opaque bind error.
    """
    by_port: dict[int, list[str]] = defaultdict(list)
    for name in names:
        port = scanner.effective_config(name).get("port")
        if port:
            by_port[int(port)].append(name)

    holder = {int(a["port"]): a["name"] for a in live if a.get("port")}
    out = []
    for port, apps in by_port.items():
        if len(apps) < 2:
            continue
        owner = holder.get(port)
        if owner is None and not _port_owner(port):
            continue  # nobody is actually using it; a latent clash, not a live one
        for other in apps:
            if other != owner:
                out.append({"name": other, "port": port, "held_by": owner or "another process"})
    return out


def build() -> dict:
    names = scanner.list_app_names()

    live = []
    failed = []
    for name in names:
        info = runner.info(name)
        cfg = scanner.effective_config(name)
        port = cfg.get("port")
        if info and info.get("running"):
            live.append({
                "name": name,
                "port": port,
                "pid": info.get("pid"),
                "uptime_seconds": info.get("uptime_seconds", 0),
                "type": cfg.get("type"),
            })
        elif info and info.get("exit_code") not in (None, 0):
            failed.append({"name": name, "exit_code": info.get("exit_code")})
        elif port:
            # Configured to serve but not answering: a crashed or wedged run.
            probe = health.check(name, port)
            if (probe.get("state") or probe.get("status")) == "crashed":
                failed.append({"name": name, "state": "crashed"})

    conflicts = _conflicts(names, live)
    repos = _sweep(names)
    dirty = sorted(
        (r for r in repos if r["dirty"]),
        key=lambda r: -r["changed_count"],
    )
    unsynced = [r for r in repos if r["ahead"] or r["behind"]]

    return {
        "generated_at": time.time(),
        "counts": {
            "apps": len(names),
            "repos": len(repos),
            "live": len(live),
            "dirty": len(dirty),
            "failed": len(failed),
            "unsynced": len(unsynced),
            "conflicts": len(conflicts),
        },
        "live": live,
        "dirty": dirty[:50],
        "failed": failed,
        "unsynced": unsynced[:50],
        "conflicts": conflicts,
    }


def get(force: bool = False) -> dict:
    """Cached overview. `force=true` bypasses the cache for an explicit rescan."""
    now = time.time()
    if not force and _CACHE["data"] and (now - _CACHE["at"]) < CACHE_SECONDS:
        cached = dict(_CACHE["data"])
        cached["cached"] = True
        cached["age_seconds"] = round(now - _CACHE["at"], 1)
        return cached
    data = build()
    _CACHE["at"] = now
    _CACHE["data"] = data
    out = dict(data)
    out["cached"] = False
    out["age_seconds"] = 0
    return out
