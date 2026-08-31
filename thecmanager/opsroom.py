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

from . import config, llm

MAX_PROMPT_CHARS = 4000
IDLE_TIMEOUT = 900  # a stuck run must not hold the lock forever

_lock = threading.Lock()
_current: Optional[subprocess.Popen] = None


def _agent_env() -> dict:
    """Environment for `./ops`, pointed at the server GSO-1 is actually running.

    The sidecar reads OPSROOM_LLAMA_URL and OPSROOM_MODEL. Asking somebody to
    type those is asking them to keep two screens in sync by hand: change the
    port in the Local LLM tab and the Ops Room breaks with a "model mismatch"
    that names a variable they never set. So we fill both from live status and
    let an explicit export still win, for the case where the agent really is
    meant to talk to some other endpoint.
    """
    env = {**os.environ, "OPSROOM_NO_COLOR": "1"}
    # Which brain answers. Stored in settings.json rather than the environment
    # so it survives a restart and can be changed from the app.
    prov = provider()
    env["OPSROOM_PROVIDER"] = prov
    if prov == "anthropic":
        model = config.load_settings().get("opsroom", {}).get("model")
        if model:
            env["OPSROOM_ANTHROPIC_MODEL"] = model
        # A Claude turn does not touch llama-server, and pointing it at a
        # stopped one would only produce a misleading warning.
        return env
    st = llm.status()
    if not os.environ.get("OPSROOM_LLAMA_URL"):
        env["OPSROOM_LLAMA_URL"] = f"{st['url']}/v1"
    # Only when a model is loaded: an empty value would fail the sidecar's
    # own mismatch check more confusingly than its default does.
    if not os.environ.get("OPSROOM_MODEL") and st.get("model"):
        env["OPSROOM_MODEL"] = st["model"]
    return env


# --------------------------------------------------------------- provider

def provider() -> str:
    """Which model backs the Ops Room: 'local' or 'anthropic'."""
    if os.environ.get("OPSROOM_PROVIDER") in ("local", "anthropic"):
        return os.environ["OPSROOM_PROVIDER"]
    chosen = (config.load_settings().get("opsroom") or {}).get("provider")
    return chosen if chosen in ("local", "anthropic") else "local"


def set_provider(name: str, model: str | None = None) -> dict:
    """Choose the brain, and optionally which Claude model."""
    if name not in ("local", "anthropic"):
        raise ValueError("provider must be 'local' or 'anthropic'")
    settings = config.load_settings()
    block = dict(settings.get("opsroom") or {})
    block["provider"] = name
    if model:
        block["model"] = model
    settings["opsroom"] = block
    config.save_settings(settings)
    return {"provider": name, "model": block.get("model")}


def _login_cmd(*args: str) -> list[str]:
    return [str(launcher()), "login", *args]


def _run_login(*args: str, timeout: int = 60) -> dict:
    """One-shot login subcommands. Each prints a single JSON event."""
    try:
        res = subprocess.run(
            _login_cmd(*args), cwd=str(repo_root()), env=_agent_env(),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.SubprocessError as exc:
        return {"event": "error", "message": str(exc)}
    for line in reversed((res.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {"event": "error",
            "message": (res.stderr or "the sidecar printed nothing").strip()[:400]}


def credential_status() -> dict:
    """Whether Claude is connected. Reports presence, never the credential."""
    ev = _run_login("anthropic", "--status", timeout=45)
    connected = bool(ev.get("connected"))
    return {"provider": provider(),
            "model": (config.load_settings().get("opsroom") or {}).get("model"),
            "connected": connected,
            "error": ev.get("message") if ev.get("event") == "error" else None}


def anthropic_models() -> list[dict]:
    ev = _run_login("anthropic", "--models", timeout=45)
    return ev.get("models") or []


def logout() -> dict:
    ev = _run_login("anthropic", "--logout", timeout=45)
    return {"ok": ev.get("event") != "error", "message": ev.get("message", "")}


# ------------------------------------------------------------ login flow
#
# The OAuth flow is interactive: it emits a URL the human must open, and may
# ask for a code pasted back. So it runs as a live subprocess whose JSON events
# are streamed to the browser, and whose stdin takes the answer.

_login_lock = threading.Lock()
_login: dict = {"proc": None, "queue": None}


def login_stream() -> Iterator[str]:
    """Run the OAuth flow, relaying each event to the browser as SSE."""
    global _login
    with _login_lock:
        old = _login.get("proc")
        if old is not None and old.poll() is None:
            old.kill()
        q: "queue.Queue[Optional[str]]" = queue.Queue()
        try:
            proc = subprocess.Popen(
                _login_cmd("anthropic"), cwd=str(repo_root()), env=_agent_env(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
        except OSError as exc:
            yield _sse("error", f"could not start the sidecar: {exc}")
            return
        _login = {"proc": proc, "queue": q}

    def pump() -> None:
        for line in proc.stdout:            # type: ignore[union-attr]
            q.put(line.rstrip("\n"))
        q.put(None)

    threading.Thread(target=pump, daemon=True).start()
    yield _sse("start", json.dumps({"provider": "anthropic"}))
    try:
        while True:
            try:
                line = q.get(timeout=15)
            except queue.Empty:
                yield _sse("ping", "")       # keep the connection from idling out
                continue
            if line is None:
                break
            line = line.strip()
            if not line.startswith("{"):
                continue                     # sidecar noise, not an event
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            yield _sse(str(ev.get("event") or "info"), json.dumps(ev))
            if ev.get("event") in ("done", "error"):
                break
    finally:
        yield _sse("end", "")
        with _login_lock:
            if _login.get("proc") is proc and proc.poll() is None:
                proc.terminate()


def login_answer(text: str) -> bool:
    """Feed one prompted value back into a running login flow."""
    with _login_lock:
        proc = _login.get("proc")
        if proc is None or proc.poll() is not None or not proc.stdin:
            return False
        try:
            proc.stdin.write(json.dumps({"answer": text}) + "\n")
            proc.stdin.flush()
            return True
        except OSError:
            return False


def login_cancel() -> bool:
    with _login_lock:
        proc = _login.get("proc")
        if proc is None or proc.poll() is not None:
            return False
        proc.terminate()
        return True


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
    """Whether the Ops Room can actually answer, and on what.

    Reports the model that is loaded rather than a name picked at build time.
    A dock that says "GLM-4.7 · local" while llama-server is stopped is worse
    than one that says nothing: it is a green light on a dead circuit, and the
    first thing you learn from it is not to trust the header.
    """
    ops = launcher()
    prov = provider()
    base = {
        "available": ops.is_file() and os.access(ops, os.X_OK),
        "launcher": str(ops),
        "busy": busy(),
        "provider": prov,
    }
    if prov == "anthropic":
        # Claude does not need llama-server, and reporting it as stopped would
        # put a dead-circuit warning on a working configuration.
        model = (config.load_settings().get("opsroom") or {}).get("model") \
            or "claude-sonnet-5"
        return {**base, "llm_state": "running", "model": model,
                "endpoint": "anthropic"}
    st = llm.status()
    return {**base, "llm_state": st.get("state"), "model": st.get("model"),
            "endpoint": os.environ.get("OPSROOM_LLAMA_URL") or f"{st['url']}/v1"}


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
            env=_agent_env(),
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
