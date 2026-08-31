"""Start / stop / track running app processes.

Each app is launched in its own process group so we can kill the entire tree
(dev servers spawn children). Stdout/stderr are streamed to a per-app log file
that the UI can tail.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import config


@dataclass
class Proc:
    name: str
    command: str
    cwd: str
    popen: subprocess.Popen
    started_at: float
    log_file: str
    port: Optional[int] = None
    stopped_by_user: bool = False


# name -> Proc (long-running app servers)
_procs: dict[str, Proc] = {}
# name -> Proc (one-shot setup/install jobs)
_setup_procs: dict[str, Proc] = {}
_lock = threading.Lock()


def log_path(name: str) -> Path:
    config.ensure_dirs()
    safe = name.replace("/", "_")
    return config.LOG_DIR / f"{safe}.log"


def setup_log_path(name: str) -> Path:
    config.ensure_dirs()
    safe = name.replace("/", "_")
    return config.LOG_DIR / f"{safe}.setup.log"


def is_running(name: str) -> bool:
    with _lock:
        proc = _procs.get(name)
        if not proc:
            return False
        return proc.popen.poll() is None


def info(name: str) -> Optional[dict]:
    with _lock:
        proc = _procs.get(name)
        if not proc:
            return None
        alive = proc.popen.poll() is None
        return {
            "running": alive,
            "pid": proc.popen.pid,
            "command": proc.command,
            "port": proc.port,
            "started_at": proc.started_at,
            "uptime_seconds": int(time.time() - proc.started_at) if alive else 0,
            "exit_code": None if alive else proc.popen.returncode,
            "stopped_by_user": proc.stopped_by_user,
        }


def start(name: str, command: str, cwd: str, port: Optional[int] = None) -> dict:
    if is_running(name):
        return {"ok": False, "message": f"{name} is already running.", "info": info(name)}

    if not command:
        return {"ok": False, "message": "No start command configured for this app."}

    lp = log_path(name)
    log_fh = open(lp, "w", encoding="utf-8")
    header = f"$ cd {cwd}\n$ {command}\n{'-' * 60}\n"
    log_fh.write(header)
    log_fh.flush()

    try:
        popen = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # own process group -> killable as a tree
        )
    except Exception as e:  # noqa: BLE001
        log_fh.close()
        return {"ok": False, "message": f"Failed to start: {e}"}

    with _lock:
        _procs[name] = Proc(
            name=name,
            command=command,
            cwd=cwd,
            popen=popen,
            started_at=time.time(),
            log_file=str(lp),
            port=port,
        )
    return {"ok": True, "message": f"Started {name}.", "info": info(name)}


def stop(name: str) -> dict:
    with _lock:
        proc = _procs.get(name)
    if not proc or proc.popen.poll() is not None:
        return {"ok": False, "message": f"{name} is not running."}

    proc.stopped_by_user = True
    try:
        pgid = os.getpgid(proc.popen.pid)
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return {"ok": True, "message": f"{name} already exited."}

    # Give it a moment, then SIGKILL the group if still alive.
    for _ in range(20):
        if proc.popen.poll() is not None:
            break
        time.sleep(0.1)
    else:
        try:
            os.killpg(os.getpgid(proc.popen.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass

    return {"ok": True, "message": f"Stopped {name}."}


def tail_log(name: str, lines: int = 200) -> str:
    lp = log_path(name)
    if not lp.exists():
        return ""
    try:
        content = lp.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(content[-lines:])
    except Exception:
        return ""


def running_names() -> set[str]:
    with _lock:
        return {n for n, p in _procs.items() if p.popen.poll() is None}


# --------------------------------------------------------------------------
# Setup / install jobs (one-shot, run to completion)
# --------------------------------------------------------------------------
def setup_running(name: str) -> bool:
    with _lock:
        proc = _setup_procs.get(name)
        return bool(proc and proc.popen.poll() is None)


def start_setup(name: str, command: str, cwd: str) -> dict:
    if not command:
        return {"ok": False, "message": "No setup command configured for this app."}
    if setup_running(name):
        return {"ok": False, "message": f"Setup for {name} is already running."}

    lp = setup_log_path(name)
    log_fh = open(lp, "w", encoding="utf-8")
    log_fh.write(f"$ cd {cwd}\n$ {command}\n{'-' * 60}\n")
    log_fh.flush()

    try:
        popen = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:  # noqa: BLE001
        log_fh.close()
        return {"ok": False, "message": f"Failed to start setup: {e}"}

    with _lock:
        _setup_procs[name] = Proc(
            name=name,
            command=command,
            cwd=cwd,
            popen=popen,
            started_at=time.time(),
            log_file=str(lp),
        )
    return {"ok": True, "message": f"Setup started for {name}."}


def setup_info(name: str) -> Optional[dict]:
    with _lock:
        proc = _setup_procs.get(name)
        if not proc:
            return None
        alive = proc.popen.poll() is None
        return {
            "running": alive,
            "pid": proc.popen.pid,
            "command": proc.command,
            "uptime_seconds": int(time.time() - proc.started_at),
            "exit_code": None if alive else proc.popen.returncode,
        }


def tail_setup_log(name: str, lines: int = 400) -> str:
    lp = setup_log_path(name)
    if not lp.exists():
        return ""
    try:
        content = lp.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(content[-lines:])
    except Exception:
        return ""


def stop_all() -> None:
    for name in list(running_names()):
        stop(name)
    # Kill any in-flight setup jobs too.
    with _lock:
        setups = list(_setup_procs.values())
    for proc in setups:
        if proc.popen.poll() is None:
            try:
                os.killpg(os.getpgid(proc.popen.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
