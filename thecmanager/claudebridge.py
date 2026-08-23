"""Drive Claude Code headlessly for a project and relay it over Telegram.

Each Telegram chat can attach to ONE project session (`/claude <project>`).
A turn spawns `claude -p --resume <session_id>` in that project, streams the
output back, and routes any permission-gated tool use to an interactive
Allow/Deny approval (handled by claude_perm_mcp.py -> these HTTP endpoints ->
the Telegram bot's inline buttons).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Callable, Optional

from . import config, scanner

# Read-only tools that run without asking. Everything else (Write, Edit, Bash,
# Task, …) triggers the Telegram approval prompt.
AUTO_ALLOWED = ["Read", "Glob", "Grep", "NotebookRead", "TodoWrite", "WebSearch", "WebFetch"]

_MCP_NAME = "manager"
_PERM_TOOL = f"mcp__{_MCP_NAME}__approve"


def _claude_bin() -> Optional[str]:
    return shutil.which("claude") or (
        str(Path.home() / ".local/bin/claude")
        if (Path.home() / ".local/bin/claude").exists()
        else None
    )


def _mcp_config_path() -> Path:
    """Write (once) the MCP config pointing Claude at our approval server."""
    config.ensure_dirs()
    p = config.DATA_DIR / "claude_mcp.json"
    script = str(Path(__file__).resolve().parent / "claude_perm_mcp.py")
    cfg = {"mcpServers": {_MCP_NAME: {"command": __import__("sys").executable, "args": [script]}}}
    p.write_text(json.dumps(cfg, indent=2))
    return p


# --------------------------------------------------------------------------
# Sessions: one per chat
# --------------------------------------------------------------------------
_sessions: dict[str, dict] = {}     # chat_id(str) -> {project, cwd, session_id, busy}
_sess_lock = threading.Lock()


def _has_existing_session(cwd: str) -> bool:
    """True if Claude Code has any saved session for this project directory.

    Claude keys sessions by the *resolved* absolute path (e.g. /tmp ->
    /private/tmp on macOS), so resolve before encoding to match its scheme.
    """
    try:
        real = str(Path(cwd).resolve())
    except OSError:
        real = str(cwd)
    encoded = re.sub(r"[^A-Za-z0-9]", "-", real)
    d = Path.home() / ".claude" / "projects" / encoded
    try:
        return d.is_dir() and any(d.glob("*.jsonl"))
    except OSError:
        return False


def has_existing_session(project: str) -> bool:
    return _has_existing_session(str(scanner.app_path(project)))


# Anthropic env vars that point Claude at a local (vs cloud) endpoint.
_ANTHROPIC_VARS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# Where `local` sessions send Claude. Defaults to GSO-1's own built-in
# Anthropic->llama proxy; override with MANAGER_LOCAL_ANTHROPIC_BASE_URL to use
# your own proxy (LiteLLM, claude-code-router, etc.).
_LOCAL_OVERRIDE_URL = os.environ.get("MANAGER_LOCAL_ANTHROPIC_BASE_URL")
_LOCAL_KEY = os.environ.get("MANAGER_LOCAL_ANTHROPIC_API_KEY", "local-proxy")


def local_configured() -> bool:
    return True  # always available via the built-in proxy


def _build_env(sess: dict, chat_id) -> dict:
    """Environment for the Claude subprocess, with cloud/local LLM selection."""
    env = os.environ.copy()
    env["MANAGER_APPROVAL_URL"] = f"http://127.0.0.1:{config.PORT}"
    env["MANAGER_APPROVAL_CHAT"] = str(chat_id)
    # Always strip any inherited Anthropic pointers first (your shell may set a
    # local one that breaks cloud auth).
    for k in _ANTHROPIC_VARS:
        env.pop(k, None)
    if sess.get("llm") == "local":
        base = _LOCAL_OVERRIDE_URL or f"http://127.0.0.1:{config.PORT}"
        env["ANTHROPIC_BASE_URL"] = base
        env["ANTHROPIC_API_KEY"] = _LOCAL_KEY
    # cloud: leave stripped -> Claude uses your subscription auth.
    return env


# How to reach the bot for streamed output: fn(chat_id, text).
_sender: Optional[Callable] = None

# Per-chat web sinks: chat_id(str) -> {"sender": fn, "notifier": fn}. Lets the
# web Chat tab receive a session's output/approvals while Telegram keeps using
# the global callbacks. Routing is by chat_id.
_web_handlers: dict[str, dict] = {}


def set_sender(fn: Callable) -> None:
    global _sender
    _sender = fn


def register_web(chat_id, sender: Callable, notifier: Callable) -> None:
    _web_handlers[str(chat_id)] = {"sender": sender, "notifier": notifier}


def _emit(chat_id, text: str) -> None:
    if not text:
        return
    h = _web_handlers.get(str(chat_id))
    fn = h["sender"] if h else _sender
    if fn:
        try:
            fn(chat_id, text)
        except Exception:
            pass


def attach(chat_id, project: str, mode: str = "continue",
           llm: str = "cloud") -> tuple[bool, str]:
    project = project.strip()
    names = scanner.list_app_names()
    if project not in names:
        matches = [n for n in names if project.lower() in n.lower()]
        if len(matches) == 1:
            project = matches[0]
        elif not matches:
            return False, f"No project matches '{project}'."
        else:
            return False, "Multiple projects match:\n" + "\n".join(f"• {m}" for m in matches[:12])
    if not _claude_bin():
        return False, "The `claude` CLI was not found on this machine."
    detach(chat_id)  # close any previous session for this chat first
    with _sess_lock:
        _sessions[str(chat_id)] = {
            "project": project,
            "cwd": str(scanner.app_path(project)),
            "session_id": None,
            "busy": False,
            "mode": mode if mode in ("continue", "new") else "continue",
            "llm": llm if llm in ("cloud", "local") else "cloud",
            "proc": None,
            "started": False,  # whether the long-lived process has been launched
        }
    return True, project


def attach_path(chat_id, cwd: str, llm: str = "local", mode: str = "continue") -> tuple[bool, str]:
    """Attach a session to an explicit directory (for the web Chat tab), rather
    than a named scanner project. Defaults to the local LLM."""
    if not _claude_bin():
        return False, "The `claude` CLI was not found on this machine."
    cwd = str(Path(cwd).expanduser())
    if not Path(cwd).is_dir():
        return False, f"Directory not found: {cwd}"
    detach(chat_id)
    with _sess_lock:
        _sessions[str(chat_id)] = {
            "project": Path(cwd).name or cwd,
            "cwd": cwd,
            "session_id": None,
            "busy": False,
            "mode": mode if mode in ("continue", "new") else "continue",
            "llm": llm if llm in ("cloud", "local") else "local",
            "proc": None,
            "started": False,
        }
    return True, cwd


def detach(chat_id) -> bool:
    with _sess_lock:
        sess = _sessions.pop(str(chat_id), None)
    if not sess:
        return False
    proc = sess.get("proc")
    if proc and proc.poll() is None:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
    return True


def get_session(chat_id) -> Optional[dict]:
    with _sess_lock:
        return _sessions.get(str(chat_id))


def clear(chat_id) -> bool:
    """Reset the session so the next message starts a fresh conversation.

    Keeps the project/LLM choice but drops history: kills the live process and
    forgets the session id (next turn launches a brand-new session).
    """
    sess = get_session(chat_id)
    if not sess:
        return False
    proc = sess.get("proc")
    if proc and proc.poll() is None:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
    with _sess_lock:
        sess.update(proc=None, started=False, session_id=None, busy=False,
                    mode="new", last_usage=None, context_window=None)
    return True


def context(chat_id) -> Optional[dict]:
    """Token/context-window usage from the most recent turn."""
    sess = get_session(chat_id)
    if not sess:
        return None
    u = sess.get("last_usage")
    ctx = 0
    if u:
        ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
               + u.get("cache_creation_input_tokens", 0))
    return {
        "project": sess["project"],
        "llm": sess.get("llm", "cloud"),
        "has_turn": bool(u),
        "context_tokens": ctx,
        "output_tokens": u.get("output_tokens", 0) if u else 0,
        "window": sess.get("context_window"),
    }


def list_sessions() -> dict:
    with _sess_lock:
        return {
            k: {"project": v["project"], "busy": v["busy"],
                "session_id": v["session_id"], "mode": v["mode"],
                "llm": v.get("llm", "cloud")}
            for k, v in _sessions.items()
        }


def _fmt_tool(name: str, inp: dict) -> str:
    if name == "Bash":
        return f"Bash: {str(inp.get('command', ''))[:300]}"
    if name in ("Write", "Edit", "Read", "NotebookEdit"):
        return f"{name}: {inp.get('file_path', '')}"
    blob = json.dumps(inp)
    return f"{name}: {blob[:200]}"


def _alive(sess: dict) -> bool:
    proc = sess.get("proc")
    return bool(proc and proc.poll() is None)


def _start_proc(sess: dict, chat_id) -> None:
    """Launch the long-lived streaming Claude process for this session."""
    cmd = [
        _claude_bin(), "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json", "--verbose",
        "--mcp-config", str(_mcp_config_path()),
        "--permission-prompt-tool", _PERM_TOOL,
        "--allowedTools", *AUTO_ALLOWED,
    ]
    # Recover/continue context only when (re)starting with no live process.
    if sess.get("session_id"):
        cmd += ["--resume", sess["session_id"]]
    elif not sess.get("started") and sess.get("mode", "continue") == "continue" \
            and _has_existing_session(sess["cwd"]):
        cmd += ["--continue"]

    env = _build_env(sess, chat_id)

    proc = subprocess.Popen(
        cmd, cwd=sess["cwd"], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    sess["proc"] = proc
    sess["started"] = True
    threading.Thread(target=_read_loop, args=(sess, chat_id, proc), daemon=True).start()


def _read_loop(sess: dict, chat_id, proc: subprocess.Popen) -> None:
    """Consume one process's streamed events for the life of that process."""
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except Exception:
            continue
        t = evt.get("type")
        if t == "system" and evt.get("subtype") == "init":
            with _sess_lock:
                sess["session_id"] = evt.get("session_id")
        elif t == "assistant":
            for block in evt.get("message", {}).get("content", []):
                bt = block.get("type")
                if bt == "text" and block.get("text", "").strip():
                    _emit(chat_id, block["text"].strip())
                elif bt == "tool_use":
                    _emit(chat_id, "🔧 " + _fmt_tool(block.get("name", "?"), block.get("input", {})))
        elif t == "result":
            if evt.get("is_error"):
                _emit(chat_id, "⚠️ " + str(evt.get("result") or evt.get("subtype") or "error"))
            dur = round(evt.get("duration_ms", 0) / 1000, 1)
            usage = evt.get("usage", {}) or {}
            out_tok = usage.get("output_tokens", 0)
            _emit(chat_id, f"✓ done · {dur}s · {out_tok} tokens out")
            window = max((m.get("contextWindow", 0)
                          for m in (evt.get("modelUsage", {}) or {}).values()), default=0)
            with _sess_lock:
                sess["busy"] = False
                sess["last_usage"] = usage
                if window:
                    sess["context_window"] = window

    # Process ended (stdin closed, crash, or claude exit).
    with _sess_lock:
        was_busy = sess.get("busy")
        sess["proc"] = None
        sess["busy"] = False
    if was_busy:
        _emit(chat_id, "⚠️ Claude session ended. Send another message to resume it.")


def submit_turn(chat_id, text: str) -> Optional[str]:
    """Queue a user turn into the live session. Returns an error string, or None
    on success (output then streams asynchronously via the reader thread)."""
    sess = get_session(chat_id)
    if not sess:
        return "No active Claude session. Use /claude <project> first."
    with _sess_lock:
        if sess["busy"]:
            return "⏳ Claude is still working on the previous message."
        if not _alive(sess):
            try:
                _start_proc(sess, chat_id)
            except Exception as e:  # noqa: BLE001
                return f"⚠️ Could not start Claude: {e}"
        sess["busy"] = True
        proc = sess["proc"]
    payload = json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"
    try:
        proc.stdin.write(payload)
        proc.stdin.flush()
    except Exception as e:  # noqa: BLE001
        with _sess_lock:
            sess["busy"] = False
        return f"⚠️ Could not send to Claude: {e}"
    return None


# --------------------------------------------------------------------------
# Permission approvals (shared with the HTTP endpoints + the bot)
# --------------------------------------------------------------------------
_approvals: dict[str, dict] = {}    # id -> {decision, tool_name, tool_input, chat_id}
_appr_lock = threading.Lock()
_notifier: Optional[Callable] = None


def set_approval_notifier(fn: Callable) -> None:
    """fn(chat_id, approval_id, tool_name, tool_input) — sends the Telegram prompt."""
    global _notifier
    _notifier = fn


def create_approval(chat_id, tool_name: str, tool_input: dict) -> str:
    aid = uuid.uuid4().hex[:12]
    with _appr_lock:
        _approvals[aid] = {
            "decision": None, "chat_id": chat_id,
            "tool_name": tool_name, "tool_input": tool_input,
        }
    h = _web_handlers.get(str(chat_id))
    notifier = h["notifier"] if h else _notifier
    if notifier:
        try:
            notifier(chat_id, aid, tool_name, tool_input)
        except Exception:
            pass
    return aid


def poll_approval(aid: str) -> Optional[str]:
    with _appr_lock:
        entry = _approvals.get(aid)
        return entry["decision"] if entry else "deny"


def resolve_approval(aid: str, decision: str) -> Optional[dict]:
    with _appr_lock:
        entry = _approvals.get(aid)
        if not entry or entry["decision"] is not None:
            return None
        entry["decision"] = decision
        return entry
