"""Chat module, drives Claude Code (full agent + tools) on the LOCAL LLM,
surfaced in the dashboard's chat UI, with multiple persisted conversations.

Each conversation maps to a Claude Code session (resumed via --resume), so the
left-hand history can switch between past chats. State persists to
data/chats.json. Start the model in the Local LLM tab first.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from . import claudebridge, config, llm

CHAT_ID = "web-chat"
CHAT_DIR = os.environ.get("MANAGER_CHAT_DIR", str(Path.home()))
_FILE = config.DATA_DIR / "chats.json"

_lock = threading.RLock()
_convos: dict[str, dict] = {}   # id -> {id, title, created, messages, session_id}
_active: str | None = None
_pending: dict | None = None
_registered = False


# ---- persistence ----------------------------------------------------------
def _load() -> None:
    global _convos, _active
    try:
        d = json.loads(_FILE.read_text())
        _convos = {c["id"]: c for c in d.get("convos", [])}
        _active = d.get("active")
    except Exception:
        _convos, _active = {}, None


def _save() -> None:
    try:
        config.ensure_dirs()
        _FILE.write_text(json.dumps(
            {"active": _active, "convos": list(_convos.values())}, indent=2))
    except Exception:
        pass


_load()


# ---- conversations --------------------------------------------------------
def _new_convo() -> dict:
    global _active
    cid = uuid.uuid4().hex[:8]
    _convos[cid] = {"id": cid, "title": "New chat", "created": time.time(),
                    "messages": [], "session_id": None}
    _active = cid
    return _convos[cid]


def _ensure_active() -> dict:
    global _active
    if _active not in _convos:
        _new_convo()
    return _convos[_active]


# ---- bridge callbacks (routed here for the web session) -------------------
def _classify(text: str) -> tuple[str, str]:
    if text.startswith("🔧 "):
        return "action", text[2:].strip()
    if text.startswith("⚠️ "):
        return "error", text[2:].strip()
    if text.startswith("✓ done"):
        return "meta", text
    return "assistant", text


def _on_output(chat_id, text: str) -> None:
    role, t = _classify(text)
    with _lock:
        convo = _convos.get(_active) or _ensure_active()
        convo["messages"].append({"role": role, "text": t})
        if role == "meta":  # turn finished, capture session id + persist
            sess = claudebridge.get_session(CHAT_ID)
            if sess and sess.get("session_id"):
                convo["session_id"] = sess["session_id"]
            _save()


def _on_approval(chat_id, aid: str, tool_name: str, tool_input: dict) -> None:
    global _pending
    with _lock:
        _pending = {"id": aid, "tool": tool_name,
                    "command": claudebridge._fmt_tool(tool_name, tool_input)}


def register_web_handlers() -> None:
    global _registered
    if not _registered:
        claudebridge.register_web(CHAT_ID, _on_output, _on_approval)
        _registered = True


def _busy() -> bool:
    s = claudebridge.get_session(CHAT_ID)
    return bool(s and s.get("busy"))


# ---- public API (used by /api/chat/*) -------------------------------------
def send(text: str) -> dict:
    register_web_handlers()
    if not text.strip():
        return state()
    if not claudebridge._claude_bin():
        return state(error="The `claude` CLI was not found on this machine.")
    if llm.status().get("state") != "running":
        return state(error="The local LLM isn't running. Start it in the Local LLM tab first.")
    global _pending
    with _lock:
        convo = _ensure_active()
        sess = claudebridge.get_session(CHAT_ID)
        if not sess:
            ok, msg = claudebridge.attach_path(CHAT_ID, CHAT_DIR, llm="local", mode="new")
            if not ok:
                return state(error=msg)
            sess = claudebridge.get_session(CHAT_ID)
            if convo.get("session_id"):
                sess["session_id"] = convo["session_id"]  # resume this conversation
        if not convo["messages"]:
            convo["title"] = text.strip()[:40] + ("…" if len(text.strip()) > 40 else "")
        convo["messages"].append({"role": "user", "text": text})
        _pending = None
        _save()
    err = claudebridge.submit_turn(CHAT_ID, text)
    if err:
        with _lock:
            convo["messages"].append({"role": "error", "text": err})
            _save()
    return state()


def approve(aid: str, ok: bool) -> dict:
    global _pending
    claudebridge.resolve_approval(aid, "allow" if ok else "deny")
    with _lock:
        if _pending and _pending.get("id") == aid:
            _pending = None
    return state()


def new_chat() -> dict:
    with _lock:
        claudebridge.detach(CHAT_ID)   # stop any running session
        _new_convo()
        _save()
    return state()


def select_chat(cid: str) -> dict:
    global _active
    with _lock:
        if cid not in _convos:
            return state(error="No such conversation.")
        claudebridge.detach(CHAT_ID)   # the next turn re-attaches + resumes
        _active = cid
        _save()
    return state()


def delete_chat(cid: str) -> dict:
    global _active
    with _lock:
        _convos.pop(cid, None)
        if _active == cid:
            claudebridge.detach(CHAT_ID)
            _active = next(iter(_convos), None)
        _save()
    return state()


# reset == start a new chat (kept for the existing route)
def reset() -> dict:
    return new_chat()


def state(error: str = "") -> dict:
    st = llm.status()
    with _lock:
        convo = _convos.get(_active)
        msgs = list(convo["messages"]) if convo else []
        convos = sorted(
            ({"id": c["id"], "title": c["title"], "created": c["created"]}
             for c in _convos.values()),
            key=lambda c: c["created"], reverse=True)
        pend = dict(_pending) if _pending else None
        active = _active
    return {
        "engine": "claude-code-local",
        "busy": _busy(),
        "messages": msgs,
        "action": pend,
        "status": "needs_approval" if pend else ("working" if _busy() else "done"),
        "error": error,
        "llm": {"state": st.get("state"), "port": st.get("port")},
        "dir": CHAT_DIR,
        "convos": convos,
        "active": active,
    }


def history() -> dict:
    return state()
