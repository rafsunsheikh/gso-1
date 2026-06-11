"""Lightweight health checks for an app."""
from __future__ import annotations

import socket
from typing import Optional

from . import runner


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check(name: str, port: Optional[int]) -> dict:
    """Return a health summary combining process state and port reachability."""
    proc_info = runner.info(name)
    running = bool(proc_info and proc_info["running"])

    port_status = None
    if port:
        port_status = "open" if _port_open(port) else "closed"

    if running and port and port_status == "open":
        state = "healthy"
    elif running and not port:
        state = "running"  # alive but we can't probe a port
    elif running and port_status == "closed":
        state = "starting"  # process up, port not yet listening
    elif proc_info and not running:
        # Distinguish a deliberate stop from an unexpected exit. A non-zero/
        # signal exit code that the user did NOT trigger means it crashed
        # (e.g. failed to bind a port, missing deps).
        if proc_info.get("stopped_by_user"):
            state = "stopped"
        elif proc_info.get("exit_code") in (0, None):
            state = "stopped"
        else:
            state = "crashed"
    else:
        state = "stopped"

    return {
        "state": state,
        "running": running,
        "port": port,
        "port_status": port_status,
        "exit_code": proc_info.get("exit_code") if proc_info else None,
        "uptime_seconds": proc_info.get("uptime_seconds") if proc_info else 0,
        # Port may be open due to an unrelated process on a shared port.
        "port_note": (
            "port reachable but process not tracked as running"
            if (port_status == "open" and not running)
            else None
        ),
    }
