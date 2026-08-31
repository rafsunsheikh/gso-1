"""Local LLM control: manage a llama.cpp `llama-server` instance.

Detects a running server via its HTTP /health endpoint (so it sees servers it
didn't start, e.g. one launched by another app), lists available GGUF models,
and can start/stop a server with chosen model + config.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config

# --- locations -------------------------------------------------------------
#
# Every one of these reads os.environ when it is called, not when the module is
# imported. That is what lets the Local LLM tab add a model folder and have the
# next scan see it: as import-time constants they could only be changed by
# restarting the whole app, which is a heavy price for "I moved my models".

_BUILTIN_BIN_PATHS = (
    "llama.cpp/build/bin/llama-server",
    "Projects/llama.cpp/build/bin/llama-server",
    "Projects/llama_cpp/build/bin/llama-server",
)
_BUILTIN_MODEL_DIRS = ("unsloth", "models", ".cache/llama.cpp")

_MIN_MODEL_BYTES = 300 * 1024 * 1024  # ignore tiny vocab/test ggufs


def host() -> str:
    return os.environ.get("LLAMA_HOST") or "127.0.0.1"


def default_port() -> int:
    try:
        return int(os.environ.get("LLAMA_PORT") or "8080")
    except ValueError:
        return 8080


def model_dirs() -> list[str]:
    """Folders scanned for .gguf files: MANAGER_MODEL_DIRS, else the usual spots."""
    chosen = [d for d in os.environ.get("MANAGER_MODEL_DIRS", "").split(":") if d]
    return chosen or [str(Path.home() / d) for d in _BUILTIN_MODEL_DIRS]


@dataclass
class LlamaProc:
    popen: subprocess.Popen
    model_path: str
    model_name: str
    port: int
    ctx: int
    started_at: float
    log_file: str


_proc: Optional[LlamaProc] = None
_lock = threading.Lock()


# --- discovery -------------------------------------------------------------
def server_bin() -> Optional[str]:
    """The llama-server binary: the configured one, else wherever it usually is."""
    candidates = [os.environ.get("LLAMA_SERVER_BIN", "")]
    candidates += [str(Path.home() / c) for c in _BUILTIN_BIN_PATHS]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def list_models() -> list[dict]:
    models: list[dict] = []
    seen: set[str] = set()
    for d in model_dirs():
        base = Path(d)
        if not base.exists():
            continue
        for f in base.rglob("*.gguf"):
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if size < _MIN_MODEL_BYTES:
                continue
            rp = str(f.resolve())
            if rp in seen:
                continue
            seen.add(rp)
            models.append(
                {
                    "name": f.stem,
                    "path": rp,
                    "size_gb": round(size / 1e9, 1),
                    "dir": str(f.parent),
                }
            )
    models.sort(key=lambda m: m["name"].lower())
    return models


# --- launch presets --------------------------------------------------------
#
# The flags below are not preferences, they are what makes a local model usable
# as a Claude Code backend, and the right values differ per model: a thinking
# model needs its own reasoning format, a 30B needs a smaller window than a 4B.
# Remembering them per model is the difference between "load this one" and
# re-deriving fifteen flags from memory every time.

# Sane for any model, and specifically tuned for tool-calling. -np 1 keeps the
# whole KV pool in one slot (auto picks 4 and the context limit bites early);
# --context-shift evicts old tokens instead of erroring mid-session; q8_0 KV
# roughly halves cache memory so a large window actually fits.
DEFAULT_PRESET: dict = {
    "ctx": 131072,
    "ngl": 99,
    "jinja": True,
    "alias": "",
    "threads": None,
    "batch": None,
    "parallel": 1,
    "reasoning_format": "",
    "temp": 0.6,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "context_shift": True,
    "cache_type_k": "q8_0",
    "cache_type_v": "q8_0",
}

_PRESET_KEYS = frozenset(DEFAULT_PRESET) | {"port"}


def _llm_settings() -> dict:
    d = config.load_settings().get("llm")
    return d if isinstance(d, dict) else {}


def presets() -> dict:
    d = _llm_settings().get("presets")
    return d if isinstance(d, dict) else {}


def last_model() -> str:
    return str(_llm_settings().get("last_model") or "")


def preset_for(model_path: str) -> dict:
    """The remembered flags for one model, filled in from the defaults."""
    merged = dict(DEFAULT_PRESET)
    merged["port"] = default_port()
    saved = presets().get(model_path)
    if isinstance(saved, dict):
        merged.update({k: v for k, v in saved.items() if k in _PRESET_KEYS})
    return merged


def remember_preset(model_path: str, cfg: dict) -> None:
    """Record how this model was last started, so next time is one click.

    Best-effort: failing to save a preference must never stop a server that is
    otherwise ready to launch.
    """
    try:
        settings = config.load_settings()
        block = settings.get("llm")
        block = dict(block) if isinstance(block, dict) else {}
        saved = dict(block.get("presets") or {})
        saved[model_path] = {k: v for k, v in cfg.items() if k in _PRESET_KEYS}
        block["presets"], block["last_model"] = saved, model_path
        settings["llm"] = block
        config.save_settings(settings)
    except (OSError, ValueError):
        pass


# --- status ----------------------------------------------------------------
def _health(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://{host()}:{port}/health", timeout=2
        ) as r:
            return r.status == 200
    except Exception:
        return False


def _props(port: int) -> dict:
    try:
        with urllib.request.urlopen(
            f"http://{host()}:{port}/props", timeout=2
        ) as r:
            import json

            return json.loads(r.read().decode())
    except Exception:
        return {}


def _port_pids(port: int) -> list[int]:
    """PIDs listening on `port` (via lsof)."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        return [int(x) for x in out.split()]
    except Exception:
        return []


def status() -> dict:
    with _lock:
        p = _proc
    managed_alive = bool(p and p.popen.poll() is None)
    port = p.port if p else default_port()
    healthy = _health(port)

    model_name = None
    ctx = None
    if managed_alive:
        model_name, ctx = p.model_name, p.ctx
    elif healthy:
        props = _props(port)
        # Different llama.cpp versions expose the path under different keys.
        mp = (
            props.get("model_path")
            or (props.get("default_generation_settings") or {}).get("model")
            or props.get("model")
        )
        if mp:
            model_name = Path(mp).stem
        ctx = (props.get("default_generation_settings") or {}).get("n_ctx")

    if managed_alive:
        state = "running" if healthy else "loading"
    elif healthy:
        state = "running"  # running, but not started by us
    else:
        state = "stopped"

    return {
        "state": state,
        "healthy": healthy,
        "managed": managed_alive,
        "port": port,
        "host": host(),
        "model": model_name,
        "ctx": ctx,
        "pid": (p.popen.pid if managed_alive else (_port_pids(port)[:1] or [None])[0]),
        "uptime_seconds": int(time.time() - p.started_at) if managed_alive else None,
        "server_bin": server_bin(),
        "url": f"http://{host()}:{port}",
    }


# --- lifecycle -------------------------------------------------------------
def log_file() -> Path:
    config.ensure_dirs()
    return config.LOG_DIR / "llama-server.log"


def start(
    model_path: str,
    port: int = 0,
    ctx: int = 131072,
    ngl: int = 99,
    jinja: bool = True,
    alias: str = "",
    threads: Optional[int] = None,
    batch: Optional[int] = None,
    parallel: Optional[int] = None,
    reasoning_format: str = "",
    temp: Optional[float] = None,
    top_p: Optional[float] = None,
    top_k: Optional[int] = None,
    min_p: Optional[float] = None,
    context_shift: bool = True,
    cache_type_k: str = "q8_0",
    cache_type_v: str = "q8_0",
) -> dict:
    global _proc
    port = port or default_port()
    bin_ = server_bin()
    if not bin_:
        return {"ok": False, "message": "llama-server binary not found."}
    if not Path(model_path).exists():
        return {"ok": False, "message": f"Model not found: {model_path}"}
    if _health(port):
        return {
            "ok": False,
            "message": f"A server is already running on port {port}. Stop it first.",
        }

    # A single Claude Code client uses one slot; default to -np 1 so the whole
    # context window is available to it (auto would pick 4 and fragment the KV
    # pool, making the limit bite well before `ctx`).
    if parallel is None:
        parallel = 1
    # Sampling defaults tuned for Qwen3.x tool-calling; the proxy forwards these
    # to llama-server, and a client request can still override per call.
    if temp is None:
        temp = 0.6
    if top_p is None:
        top_p = 0.95
    if top_k is None:
        top_k = 20
    if min_p is None:
        min_p = 0.0

    lp = log_file()
    fh = open(lp, "w", encoding="utf-8")
    cmd = [
        bin_,
        "-m", model_path,
        "--host", host(),
        "--port", str(port),
        "-c", str(ctx),
        "-ngl", str(ngl),
    ]
    # Context shift evicts the oldest tokens instead of erroring when a long
    # session fills the window, without it llama-server rejects the request
    # ("exceeds the available context size") after a few Claude Code turns.
    cmd.append("--context-shift" if context_shift else "--no-context-shift")
    if cache_type_k:
        cmd += ["-ctk", cache_type_k]
    if cache_type_v:
        cmd += ["-ctv", cache_type_v]
    if jinja:
        cmd.append("--jinja")
    if alias:
        cmd += ["--alias", alias]
    if threads:
        cmd += ["-t", str(threads)]
    if batch:
        cmd += ["-b", str(batch)]
    if parallel:
        cmd += ["-np", str(parallel)]
    if reasoning_format:
        cmd += ["--reasoning-format", reasoning_format]
    if temp is not None:
        cmd += ["--temp", str(temp)]
    if top_p is not None:
        cmd += ["--top-p", str(top_p)]
    if top_k is not None:
        cmd += ["--top-k", str(top_k)]
    if min_p is not None:
        cmd += ["--min-p", str(min_p)]
    fh.write("$ " + " ".join(cmd) + "\n" + "-" * 60 + "\n")
    fh.flush()

    try:
        popen = subprocess.Popen(
            cmd,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:  # noqa: BLE001
        fh.close()
        return {"ok": False, "message": f"Failed to launch: {e}"}

    with _lock:
        _proc = LlamaProc(
            popen=popen,
            model_path=model_path,
            model_name=Path(model_path).stem,
            port=port,
            ctx=ctx,
            started_at=time.time(),
            log_file=str(lp),
        )
    return {"ok": True, "message": f"Starting {Path(model_path).stem} (loading…)."}


def stop() -> dict:
    global _proc
    with _lock:
        p = _proc
    port = p.port if p else default_port()

    killed = False
    if p and p.popen.poll() is None:
        try:
            os.killpg(os.getpgid(p.popen.pid), signal.SIGTERM)
            killed = True
        except ProcessLookupError:
            pass
        with _lock:
            _proc = None

    # Also catch an externally-started server on the port.
    if not killed:
        pids = _port_pids(port)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                killed = True
            except (ProcessLookupError, PermissionError):
                pass

    if not killed:
        return {"ok": False, "message": "No server was running."}

    # Wait briefly for the port to free.
    for _ in range(30):
        if not _health(port):
            break
        time.sleep(0.1)
    return {"ok": True, "message": "Server stopped."}


def tail_log(lines: int = 200) -> str:
    lp = log_file()
    if not lp.exists():
        return ""
    try:
        return "\n".join(lp.read_text(encoding="utf-8", errors="ignore").splitlines()[-lines:])
    except Exception:
        return ""


def stop_if_managed() -> None:
    with _lock:
        p = _proc
    if p and p.popen.poll() is None:
        try:
            os.killpg(os.getpgid(p.popen.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
