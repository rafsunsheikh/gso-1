"""Bridge from GSO-1 to the Ops Room sidecar.

Spawns `./ops` and streams its output back as Server-Sent Events so the
dashboard can host the agent in a docked panel instead of a terminal.

Two details worth knowing:

* **Launcher resolution.** When GSO-1 runs from a release, `APP_DIR` is
  `var/releases/<stamp>`, and that release contains its own copy of `ops`, 
  which would resolve its repo root to the release and look for
  `var/current/opsroom` *inside* it. So we read `.release.json`, written at
  snapshot time, to find the real repo and use its launcher.
* **One run at a time.** Local inference is ~30 s a turn and holds the whole
  model; letting the UI fan out concurrent runs would just thrash.
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterator, Optional

from . import config

MAX_PROMPT_CHARS = 4000
IDLE_TIMEOUT = 900  # a stuck run must not hold the lock forever

_lock = threading.Lock()
_current: Optional[subprocess.Popen] = None


def repo_root() -> Path:
    """The real checkout, even when running from a release snapshot."""
    meta = config.APP_DIR / ".release.json"
    try:
        src = json.loads(meta.read_text()).get("source")
        if src and Path(src).is_dir():
            return Path(src)
    except (OSError, ValueError):
        pass
    return config.APP_DIR


def launcher() -> Path:
    return repo_root() / "ops"


def available() -> dict:
    ops = launcher()
    return {
        "available": ops.is_file() and os.access(ops, os.X_OK),
        "launcher": str(ops),
        "busy": busy(),
    }


def busy() -> bool:
    return _current is not None and _current.poll() is None


def cancel() -> bool:
    """Stop the in-flight run, if any."""
    global _current
    proc = _current
    if proc is None or proc.poll() is not None:
        return False
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except OSError:
            return False
    return True


def _sse(event: str, data: str) -> str:
    # Every newline needs its own data: line, or the client sees a truncated event.
    payload = "\n".join(f"data: {line}" for line in data.split("\n"))
    return f"event: {event}\n{payload}\n\n"


def ask_stream(prompt: str) -> Iterator[str]:
    """Run one prompt, yielding SSE frames as output arrives."""
    global _current

    prompt = (prompt or "").strip()
    if not prompt:
        yield _sse("error", "Empty prompt.")
        return
    if len(prompt) > MAX_PROMPT_CHARS:
        yield _sse("error", f"Prompt too long ({len(prompt)} > {MAX_PROMPT_CHARS} chars).")
        return

    ops = launcher()
    if not (ops.is_file() and os.access(ops, os.X_OK)):
        yield _sse("error", f"Launcher not found or not executable: {ops}")
        return

    if not _lock.acquire(blocking=False):
        yield _sse("error", "Ops Room is already running a request. Wait for it to finish, or cancel it.")
        return

    proc = None
    try:
        yield _sse("start", json.dumps({"prompt": prompt, "launcher": str(ops)}))
        proc = subprocess.Popen(
            [str(ops), prompt],
            cwd=str(repo_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,  # own group, so cancel kills the whole tree
            env={**os.environ, "OPSROOM_NO_COLOR": "1"},
        )
        _current = proc

        # Reader thread + queue: lets us emit heartbeats while the model thinks,
        # so the browser does not sit on an idle connection for 30 s.
        q: "queue.Queue[Optional[str]]" = queue.Queue()

        def pump() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    q.put(line.rstrip("\n"))
            finally:
                q.put(None)

        threading.Thread(target=pump, daemon=True).start()

        started = time.time()
        while True:
            try:
                line = q.get(timeout=5)
            except queue.Empty:
                if time.time() - started > IDLE_TIMEOUT:
                    cancel()
                    yield _sse("error", f"Timed out after {IDLE_TIMEOUT}s.")
                    break
                yield _sse("ping", "")  # keep the connection alive
                continue
            if line is None:
                break
            yield _sse("output", line)

        code = proc.wait()
        yield _sse("done", json.dumps({"exit_code": code}))
    except Exception as exc:  # noqa: BLE001
        yield _sse("error", f"{exc.__class__.__name__}: {exc}")
    finally:
        _current = None
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        _lock.release()
