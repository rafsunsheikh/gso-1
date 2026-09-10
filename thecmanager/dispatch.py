"""Giving an agent work from the World, and reading back what it says.

`agents.py` watches; this sends. They are deliberately separate, because what
GSO-1 may do to a session depends entirely on who started it:

* **A session GSO-1 started** is a child process it holds the stdin of, through
  `claudebridge`. It can be told to do things.
* **A session you started in a terminal** cannot. It has a Unix socket, and the
  protocol on it is internal and undocumented; a feature built on that breaks
  on the next Claude Code update. So the World will show you those agents and
  what they are doing, and will not pretend it can command them.

The gap between the two is filled by settling: GSO-1 can put *its own* agent on
a project, which then appears on the map like any other and can be given work.
That is offered even where somebody else's agent is already at work, but never
quietly: two agents editing one repository is a real way to lose an afternoon,
so the caller has to ask for it explicitly.

Output arrives asynchronously on a reader thread, so it is buffered per project
and polled, rather than held open on a request. The World is already polling
every few seconds; a second channel would be a second thing to get wrong.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

from . import claudebridge, scanner

# One conversation per project, named so it is recognisable in logs and cannot
# collide with a Telegram chat id.
def chat_id_for(project: str) -> str:
    return f"world:{project}"


_BUFFER_LINES = 200
_buffers: dict[str, deque] = {}
_lock = threading.Lock()


def _sink(chat_id, text: str) -> None:
    """Where a session's output lands. Called from claudebridge's reader."""
    if not text:
        return
    with _lock:
        buf = _buffers.setdefault(str(chat_id), deque(maxlen=_BUFFER_LINES))
        buf.append({"at": time.time(), "text": str(text)})


def _notify(chat_id, *_args, **_kwargs) -> None:
    """Approval prompts. Recorded like any other line rather than dropped: a
    session waiting on a permission it will never be granted looks exactly like
    one that has hung, and that is a miserable thing to debug."""
    _sink(chat_id, "[waiting for an approval GSO-1 cannot grant from here]")


def owns(project: str) -> bool:
    """Is there a GSO-1-owned session for this project?"""
    return claudebridge.get_session(chat_id_for(project)) is not None


def status(project: str) -> dict:
    chat_id = chat_id_for(project)
    sess = claudebridge.get_session(chat_id)
    with _lock:
        lines = list(_buffers.get(chat_id, ()))
    return {
        "project": project,
        "owned": sess is not None,
        "busy": bool(sess and sess.get("busy")),
        "lines": lines,
    }


def send(project: str, prompt: str, allow_second_agent: bool = False) -> dict:
    """Give GSO-1's agent for `project` a turn, starting one if needed."""
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "message": "Nothing to send."}
    if project not in scanner.list_app_names():
        return {"ok": False, "message": f"No project called \"{project}\"."}

    chat_id = chat_id_for(project)
    if claudebridge.get_session(chat_id) is None:
        # Somebody else's agent already working here is a decision, not a
        # detail: two of them editing the same files is how work gets lost.
        if not allow_second_agent:
            from . import agents
            snap = agents.snapshot()
            if any(s.get("project") == project and s.get("working")
                   for s in snap["sessions"]):
                return {
                    "ok": False,
                    "needs_confirm": True,
                    "message": (
                        f"An agent you started is already working in {project}. "
                        "GSO-1 would add a second one, in the same files. "
                        "Send anyway?"
                    ),
                }
        ok, detail = claudebridge.attach(chat_id, project, mode="continue")
        if not ok:
            return {"ok": False, "message": detail}
        claudebridge.register_web(chat_id, _sink, _notify)
        _sink(chat_id, f"[GSO-1 started an agent in {project}]")

    err = claudebridge.submit_turn(chat_id, prompt)
    if err:
        return {"ok": False, "message": err}
    _sink(chat_id, f"> {prompt}")
    return {"ok": True, "message": "Sent.", "project": project}


def stop(project: str) -> dict:
    """Close GSO-1's agent for this project. The land goes back to terrain."""
    chat_id = chat_id_for(project)
    if claudebridge.get_session(chat_id) is None:
        return {"ok": False, "message": "GSO-1 has no agent there."}
    claudebridge.detach(chat_id)
    with _lock:
        _buffers.pop(chat_id, None)
    return {"ok": True, "message": "Stopped."}


def owned_projects() -> list[str]:
    """Projects GSO-1 currently has an agent in."""
    out = []
    for chat_id, sess in (claudebridge.list_sessions() or {}).items():
        if str(chat_id).startswith("world:") and sess.get("project"):
            out.append(sess["project"])
    return sorted(set(out))
