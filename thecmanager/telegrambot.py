"""Telegram bot bridge — control GSO-1 from your phone, anywhere.

Uses Telegram's long-polling (getUpdates) so it works from behind home NAT
with no port-forwarding, tunnel, or public IP. No third-party deps — just the
stdlib HTTP client.

Setup:
  1. Create a bot with @BotFather in Telegram -> get the token.
  2. export MANAGER_TELEGRAM_TOKEN="123456:ABC-..."
  3. Message the bot once; it replies with your chat id.
  4. export MANAGER_TELEGRAM_ALLOWED="<your chat id>"   (comma-separated for more)
  5. Restart GSO-1.

Without a token the bot simply doesn't start.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

from . import claudebridge, git_ops, health, llm, planner, runner, scanner, sysmon

TOKEN = os.environ.get("MANAGER_TELEGRAM_TOKEN", "").strip()
ALLOWED = {
    x.strip() for x in os.environ.get("MANAGER_TELEGRAM_ALLOWED", "").split(",") if x.strip()
}

_API = f"https://api.telegram.org/bot{TOKEN}"
_state: dict = {
    "configured": bool(TOKEN),
    "running": False,
    "username": None,
    "error": None,
    "authorized_count": len(ALLOWED),
}
_thread: threading.Thread | None = None


def status() -> dict:
    return dict(_state)


def configured() -> bool:
    return bool(TOKEN)


# --------------------------------------------------------------------------
# Telegram HTTP
# --------------------------------------------------------------------------
def _call(method: str, params: dict | None = None, timeout: int = 20) -> dict:
    data = urllib.parse.urlencode(params).encode() if params else None
    req = urllib.request.Request(f"{_API}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# Telegram caps one message at 4096 chars. Long replies (the local LLM can be
# very verbose / repeat earlier turns) are chunked across messages instead of
# head-truncated, so the newest content — which lives at the END — is never the
# part that gets cut. If a reply is enormous we keep the LAST chunks.
_TG_CHUNK = 4000
_MAX_CHUNKS = 20


def _split(text: str, limit: int = _TG_CHUNK) -> list[str]:
    """Break text into <=limit-char pieces, preferring newline boundaries."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single over-long line: hard-split it
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if not cur:
            cur = line
        elif len(cur) + 1 + len(line) <= limit:
            cur += "\n" + line
        else:
            chunks.append(cur)
            cur = line
    if cur:
        chunks.append(cur)
    return chunks or [text]


def _send(chat_id, text: str, reply_markup: dict | None = None) -> None:
    chunks = _split(text)
    if len(chunks) > _MAX_CHUNKS:
        # Drop the OLDEST chunks — the latest answer is at the tail.
        chunks = ["…(earlier output trimmed)", *chunks[-_MAX_CHUNKS:]]
    for i, chunk in enumerate(chunks):
        params = {"chat_id": chat_id, "text": chunk}
        if reply_markup and i == len(chunks) - 1:  # buttons on the final chunk
            params["reply_markup"] = json.dumps(reply_markup)
        try:
            _call("sendMessage", params, timeout=15)
        except Exception:
            pass


def _answer_cb(cq_id: str, text: str = "") -> None:
    try:
        _call("answerCallbackQuery", {"callback_query_id": cq_id, "text": text}, timeout=10)
    except Exception:
        pass


def _edit_text(chat_id, message_id, text: str) -> None:
    try:
        _call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text}, timeout=10)
    except Exception:
        pass


def _send_approval(chat_id, aid: str, tool_name: str, tool_input: dict) -> None:
    """Notifier registered with claudebridge: prompt the user with buttons."""
    detail = claudebridge._fmt_tool(tool_name, tool_input or {})
    text = f"🔐 Claude wants to use a tool:\n\n{detail}\n\nApprove?"
    kb = {"inline_keyboard": [[
        {"text": "✅ Allow", "callback_data": f"appr:{aid}:allow"},
        {"text": "❌ Deny", "callback_data": f"appr:{aid}:deny"},
    ]]}
    _send(chat_id, text, reply_markup=kb)


def _restart(confirm_update_id: int | None = None) -> None:
    """Replace this process image with a fresh `python -m thecmanager`.

    Lets you reload updated code from your phone via /restart. os.execv keeps
    the same PID and never returns, so it works whether or not a launchd
    supervisor is managing us, and the current environment (incl. the Telegram
    token) carries over to the new image.

    We first acknowledge the triggering Telegram update — otherwise getUpdates
    would redeliver /restart after we come back and we'd restart in a loop.
    In-flight Claude turns are dropped; the detached llama-server survives.
    """
    if confirm_update_id is not None:
        try:
            _call("getUpdates", {"offset": confirm_update_id + 1, "timeout": 0}, timeout=10)
        except Exception:
            pass
    os.execv(sys.executable, [sys.executable, "-m", "thecmanager", *sys.argv[1:]])


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _authorized(chat_id) -> bool:
    return bool(ALLOWED) and str(chat_id) in ALLOWED


def _gb(b: int) -> float:
    return round((b or 0) / 1e9, 1)


def _resolve(query: str):
    """Resolve a user-typed app name to an exact registry name."""
    names = scanner.list_app_names()
    q = query.strip()
    if q in names:
        return q, None
    matches = [n for n in names if q.lower() in n.lower()]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f"No app matches '{q}'."
    head = "\n".join(f"• {m}" for m in matches[:15])
    more = "" if len(matches) <= 15 else f"\n…and {len(matches) - 15} more"
    return None, f"Multiple apps match '{q}':\n{head}{more}"


HELP = (
    "🗂️ GSO-1 — commands\n\n"
    "/apps [query] — list running+favourite apps, or search\n"
    "/run <app> — start an app\n"
    "/stop <app> — stop an app\n"
    "/status <app> — process + health\n"
    "/health <app> — health only\n"
    "/git <app> — git status\n"
    "/update <app> — git pull\n"
    "/desc <app> — description\n"
    "/logs <app> — recent log lines\n"
    "/running — what's currently running\n"
    "/llm — local LLM status\n"
    "/llmstart <model> — start llama-server with a model\n"
    "/llmstop — stop the local LLM\n"
    "/sys — CPU / GPU / RAM load\n"
    "/restart — restart GSO-1 (reloads updated code)\n"
    "/tasks — list planner boards\n"
    "/tasks <board> — list a board's tasks (numbered)\n"
    "/add <title> — add a task to the last board you listed\n"
    "/add <board> | <title> — add a task to a named board\n"
    "/move <number> <todo|doing|done> — move a task between columns\n"
    "\n🤖 Claude Code:\n"
    "/claude <project> — continue the project's latest Claude session (cloud LLM)\n"
    "/claude <project> new — start a fresh Claude session instead\n"
    "/claude <project> local — use the local LLM (add 'new' too if you like)\n"
    "(then just send messages here to talk to Claude; approve tool use with the buttons)\n"
    "/clear — clear the conversation (fresh session, same project)\n"
    "/context — show token / context-window usage\n"
    "/end — close the Claude session\n"
    "/sessions — list active Claude sessions\n"
    "/help — this message"
)


# --------------------------------------------------------------------------
# Command handlers (each returns reply text)
# --------------------------------------------------------------------------
def _cmd_apps(arg: str) -> str:
    names = scanner.list_app_names()
    running = runner.running_names()
    if arg:
        matches = [n for n in names if arg.lower() in n.lower()]
        if not matches:
            return f"No app matches '{arg}'."
        lines = []
        for n in matches[:30]:
            cfg = scanner.effective_config(n)
            dot = "🟢" if n in running else "⚪"
            lines.append(f"{dot} {n} ({cfg['type']})")
        extra = "" if len(matches) <= 30 else f"\n…and {len(matches) - 30} more"
        return f"Matches for '{arg}':\n" + "\n".join(lines) + extra
    # Default: running + favourites.
    favs = [n for n in names if scanner.effective_config(n)["favourite"]]
    out = [f"📊 {len(names)} apps total."]
    if running:
        out.append("\n🟢 Running:")
        out += [f"• {n}" for n in sorted(running)]
    else:
        out.append("\nNothing running.")
    if favs:
        out.append("\n⭐ Favourites:")
        out += [f"• {n}{' 🟢' if n in running else ''}" for n in favs]
    out.append("\nUse /apps <query> to search, /run <app> to start.")
    return "\n".join(out)


def _cmd_run(arg: str) -> str:
    name, err = _resolve(arg)
    if err:
        return err
    cfg = scanner.effective_config(name)
    if not cfg["start_command"]:
        return f"{name} has no start command. Set one in the dashboard."
    r = runner.start(name, cfg["start_command"], str(scanner.app_path(name)), cfg["port"])
    return ("▶️ " if r["ok"] else "⚠️ ") + r["message"]


def _cmd_stop(arg: str) -> str:
    name, err = _resolve(arg)
    if err:
        return err
    r = runner.stop(name)
    return ("⏹️ " if r["ok"] else "⚠️ ") + r["message"]


def _cmd_status(arg: str) -> str:
    name, err = _resolve(arg)
    if err:
        return err
    cfg = scanner.effective_config(name)
    h = health.check(name, cfg["port"])
    p = runner.info(name)
    lines = [f"📋 {name} ({cfg['type']})", f"State: {h['state']}"]
    if cfg["port"]:
        lines.append(f"Port {cfg['port']}: {h['port_status']}")
    if p:
        lines.append(
            f"PID {p['pid']} · {'up ' + str(p['uptime_seconds']) + 's' if p['running'] else 'exit ' + str(p['exit_code'])}"
        )
    return "\n".join(lines)


def _cmd_health(arg: str) -> str:
    name, err = _resolve(arg)
    if err:
        return err
    cfg = scanner.effective_config(name)
    h = health.check(name, cfg["port"])
    return f"❤️ {name}: {h['state']} (port {cfg['port'] or '—'}: {h['port_status'] or 'n/a'})"


def _cmd_git(arg: str) -> str:
    name, err = _resolve(arg)
    if err:
        return err
    g = git_ops.status(scanner.app_path(name))
    if not g["is_repo"]:
        return f"{name} is not a git repository."
    return (
        f"🌿 {name}\nBranch: {g['branch']}\n"
        f"{'Dirty (' + str(g['changed_count']) + ' changed)' if g['dirty'] else 'Clean'}\n"
        f"Ahead {g['ahead']} / Behind {g['behind']}\n"
        f"Last: {g['last_commit']}"
    )


def _cmd_update(arg: str) -> str:
    name, err = _resolve(arg)
    if err:
        return err
    r = git_ops.update(scanner.app_path(name))
    out = (r.get("output") or "").strip().split("\n")[0]
    return ("⟳ " if r["ok"] else "⚠️ ") + f"{name}: {out}"


def _cmd_desc(arg: str) -> str:
    name, err = _resolve(arg)
    if err:
        return err
    return f"📝 {name}\n{scanner.read_description(name)}"


def _cmd_logs(arg: str) -> str:
    name, err = _resolve(arg)
    if err:
        return err
    log = runner.tail_log(name, 25) or "(no logs yet)"
    return f"📜 {name} (last lines):\n{log}"


def _cmd_running() -> str:
    r = sorted(runner.running_names())
    return "🟢 Running:\n" + "\n".join(f"• {n}" for n in r) if r else "Nothing is running."


def _cmd_llm() -> str:
    s = llm.status()
    out = [f"🧠 LLM: {s['state']}"]
    if s.get("model"):
        out.append(f"Model: {s['model']}")
    out.append(f"URL: {s['url']}")
    if s.get("ctx"):
        out.append(f"ctx: {s['ctx']}")
    if not s.get("managed") and s["state"] != "stopped":
        out.append("(started outside GSO-1)")
    return "\n".join(out)


def _cmd_llmstart(arg: str) -> str:
    if not arg:
        models = llm.list_models()
        return "Pick a model:\n" + "\n".join(f"• {m['name']} ({m['size_gb']}GB)" for m in models)
    models = llm.list_models()
    matches = [m for m in models if arg.lower() in m["name"].lower()]
    if not matches:
        return f"No model matches '{arg}'."
    if len(matches) > 1:
        return "Multiple models match:\n" + "\n".join(f"• {m['name']}" for m in matches)
    r = llm.start(matches[0]["path"])
    return ("🧠 " if r["ok"] else "⚠️ ") + r["message"]


def _cmd_llmstop() -> str:
    r = llm.stop()
    return ("⏹️ " if r["ok"] else "⚠️ ") + r["message"]


def _cmd_sys() -> str:
    s = llm.status()
    snap = sysmon.sample(s.get("pid"))
    c, r, g = snap["cpu"], snap["ram"], snap["gpu"]
    lines = [
        "📈 System load",
        f"CPU: {c['busy']}% busy (LLM {c['llm']}% · other {c['other']}% · idle {c['idle']}%)",
        f"RAM: {_gb(r['used_bytes'])}/{_gb(r['total_bytes'])}GB ({r['percent']}%) — LLM {_gb(r['llm_bytes'])}GB, free {_gb(r['free_bytes'])}GB",
    ]
    if g.get("available"):
        lines.append(f"GPU: {g['util']}% utilization")
    lines.append("Top by RAM:")
    for p in snap["top"][:5]:
        lines.append(f"  {'🧠 ' if p['is_llm'] else ''}{p['name']}: {_gb(p['mem_bytes'])}GB")
    return "\n".join(lines)


# Remembers the numbered task list from the last `/tasks <board>` per chat,
# so `/move <number> <column>` can reference it.
_task_index: dict = {}
# Remembers the last board listed per chat, so `/add <title>` knows where to go.
_last_board: dict = {}

_COL_LABEL = {"todo": "To Do", "doing": "In Progress", "done": "Done"}
_PRI_ICON = {"high": "🔴", "med": "🟡", "low": "⚪"}
_COL_ALIASES = {
    "todo": "todo", "td": "todo", "to-do": "todo", "backlog": "todo", "1": "todo",
    "doing": "doing", "progress": "doing", "inprogress": "doing",
    "in-progress": "doing", "wip": "doing", "2": "doing",
    "done": "done", "complete": "done", "completed": "done", "finished": "done", "3": "done",
}


def _cmd_tasks(chat_id, arg: str) -> str:
    boards = planner.get_all().get("boards", [])
    if not boards:
        return "📋 No planner boards yet. Add one in the dashboard."
    if not arg:
        out = ["📋 Planner boards:"]
        for b in boards:
            c = {"todo": 0, "doing": 0, "done": 0}
            for t in b["tasks"]:
                c[t["status"]] = c.get(t["status"], 0) + 1
            out.append(f"• {b['name']}: {c['todo']} todo / {c['doing']} doing / {c['done']} done")
        out.append("\nUse /tasks <board> to list its tasks.")
        return "\n".join(out)

    matches = [b for b in boards if arg.lower() in b["name"].lower()]
    if not matches:
        return f"No board matches '{arg}'."
    if len(matches) > 1:
        return "Multiple boards match:\n" + "\n".join(f"• {b['name']}" for b in matches)
    board = matches[0]
    _last_board[str(chat_id)] = board["id"]
    order = {"todo": 0, "doing": 1, "done": 2}
    tasks = sorted(board["tasks"], key=lambda t: (order.get(t["status"], 9), t.get("order", 0)))
    _task_index[str(chat_id)] = [(board["id"], t["id"]) for t in tasks]
    if not tasks:
        return f"📋 {board['name']} — no tasks yet."
    lines, last_col, n = [f"📋 {board['name']}"], None, 0
    for t in tasks:
        if t["status"] != last_col:
            lines.append(f"\n{_COL_LABEL.get(t['status'], t['status'])}:")
            last_col = t["status"]
        n += 1
        lines.append(f"  {n}. {_PRI_ICON.get(t.get('priority'), '')} {t['title']}")
    lines.append("\nMove a task:  /move <number> <todo|doing|done>")
    return "\n".join(lines)


def _cmd_add(chat_id, arg: str) -> str:
    if not arg:
        return ("Usage:\n"
                "  /add <title>            → add to the last board you listed\n"
                "  /add <board> | <title>  → add to a named board")
    # Explicit board form: "/add <board> | <title>".
    if "|" in arg:
        board_part, title = (s.strip() for s in arg.split("|", 1))
        boards = planner.get_all().get("boards", [])
        matches = [b for b in boards if board_part.lower() in b["name"].lower()]
        if not matches:
            return f"No board matches '{board_part}'."
        if len(matches) > 1:
            return "Multiple boards match:\n" + "\n".join(f"• {b['name']}" for b in matches)
        board = matches[0]
    else:
        # Implicit form: use the last board listed with /tasks <board>.
        board_id = _last_board.get(str(chat_id))
        if not board_id:
            return ("First run /tasks <board> to pick a board, then /add <title>.\n"
                    "Or use /add <board> | <title>.")
        board = next((b for b in planner.get_all().get("boards", []) if b["id"] == board_id), None)
        if not board:
            return "That board no longer exists. Re-run /tasks <board>."
        title = arg.strip()
    if not title:
        return "The task needs a title."
    task = planner.create_task(board["id"], title=title)
    if not task:
        return "Couldn't add the task — the board may have been deleted."
    _last_board[str(chat_id)] = board["id"]
    return f"➕ Added to {board['name']} → To Do:\n{task['title']}"


def _cmd_move(chat_id, arg: str) -> str:
    idx = _task_index.get(str(chat_id))
    if not idx:
        return "First run /tasks <board> to list tasks, then /move <number> <column>."
    parts = arg.split()
    if len(parts) < 2:
        return "Usage: /move <number> <todo|doing|done>"
    try:
        num = int(parts[0])
    except ValueError:
        return "The first argument must be the task number shown by /tasks."
    col = _COL_ALIASES.get(parts[1].lower())
    if not col:
        return "Column must be one of: todo, doing, done."
    if num < 1 or num > len(idx):
        return f"No task #{num} in the last listing (it had {len(idx)}). Re-run /tasks <board>."
    board_id, task_id = idx[num - 1]
    task = planner.move_task(board_id, task_id, col, 9999)  # append to end of column
    if not task:
        return "Couldn't move that task — it may have been deleted. Re-run /tasks <board>."
    return f"✅ Moved '{task['title']}' → {_COL_LABEL[col]}."


def _dispatch(chat_id, text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    # Claude Code session commands (need the chat context).
    if cmd == "claude":
        if not arg:
            s = claudebridge.get_session(chat_id)
            return ("Usage: /claude <project> [new] [cloud|local]\n"
                    "  • /claude <project>          → continue latest session, cloud LLM\n"
                    "  • /claude <project> new      → fresh session\n"
                    "  • /claude <project> local    → use the local LLM\n"
                    "  • /claude <project> new local → fresh + local"
                    + (f"\n\nAttached: {s['project']} ({s.get('llm', 'cloud')})" if s else ""))
        tokens = arg.split()
        flags = {t.lower() for t in tokens[1:]}
        mode = "new" if "new" in flags else "continue"
        llm_mode = "local" if "local" in flags else "cloud"
        warn = ""
        if llm_mode == "local" and not llm.status().get("healthy"):
            warn = ("\n⚠️ The local LLM server isn't running — start it from the Local LLM tab "
                    "(or /llmstart), or Claude will error.")
        ok, res = claudebridge.attach(chat_id, tokens[0], mode, llm_mode)
        if not ok:
            return res
        llm_note = ("🏠 local LLM" if llm_mode == "local" else "☁️ cloud (subscription)")
        if mode == "new":
            return (f"🤖 Attached to '{res}' — fresh session · {llm_note}.\n"
                    "Send a message to begin. I'll ask before any edit/command runs. /end to close." + warn)
        if claudebridge.has_existing_session(res):
            return (f"🤖 Attached to '{res}' — continuing latest session · {llm_note}.\n"
                    "⚠️ If that session is still open in your VS Code terminal, close/idle it first.\n"
                    "Send a message to continue. /end to close." + warn)
        return (f"🤖 Attached to '{res}' — no prior session, starting fresh · {llm_note}.\n"
                "Send a message to begin. /end to close." + warn)
    if cmd == "end":
        return "🤖 Claude session closed." if claudebridge.detach(chat_id) else "No active session."
    if cmd == "clear":
        if not claudebridge.get_session(chat_id):
            return "No active Claude session. Use /claude <project> first."
        claudebridge.clear(chat_id)
        return "🧹 Conversation cleared. Your next message starts a fresh session (same project)."
    if cmd == "context":
        c = claudebridge.context(chat_id)
        if not c:
            return "No active Claude session. Use /claude <project> first."
        if not c["has_turn"]:
            return f"🧮 {c['project']} ({c['llm']}) — no turns yet. Send a message first."
        line = f"🧮 Context — {c['project']} ({c['llm']})\n~{c['context_tokens']:,} tokens in context"
        if c["window"]:
            pct = round(c["context_tokens"] / c["window"] * 100, 1)
            line += f" / {c['window']:,} ({pct}%)"
        line += f"\nLast reply: {c['output_tokens']:,} output tokens"
        return line
    if cmd == "sessions":
        s = claudebridge.list_sessions()
        if not s:
            return "No active Claude sessions."
        return "🤖 Active sessions:\n" + "\n".join(
            f"• {v['project']} [{v.get('llm', 'cloud')}]{' (working…)' if v.get('busy') else ''}"
            for v in s.values())
    if cmd == "tasks":
        return _cmd_tasks(chat_id, arg)
    if cmd in ("add", "addtask"):
        return _cmd_add(chat_id, arg)
    if cmd in ("move", "mv"):
        return _cmd_move(chat_id, arg)

    table_noarg = {
        "start": lambda: HELP,
        "help": lambda: HELP,
        "running": _cmd_running,
        "llm": _cmd_llm,
        "llmstop": _cmd_llmstop,
        "sys": _cmd_sys,
        "load": _cmd_sys,
    }
    table_arg = {
        "apps": _cmd_apps,
        "run": _cmd_run,
        "stop": _cmd_stop,
        "status": _cmd_status,
        "health": _cmd_health,
        "git": _cmd_git,
        "update": _cmd_update,
        "desc": _cmd_desc,
        "logs": _cmd_logs,
        "llmstart": _cmd_llmstart,
    }
    try:
        if cmd in table_noarg:
            return table_noarg[cmd]()
        if cmd in table_arg:
            return table_arg[cmd](arg)
    except Exception as e:  # noqa: BLE001
        return f"⚠️ Error running /{cmd}: {e}"
    return f"Unknown command /{cmd}. Send /help."


# --------------------------------------------------------------------------
# Polling loop
# --------------------------------------------------------------------------
def _loop() -> None:
    offset = 0
    # Validate token / fetch username.
    try:
        me = _call("getMe", timeout=15)
        if me.get("ok"):
            _state["username"] = me["result"].get("username")
    except Exception as e:  # noqa: BLE001
        _state["error"] = str(e)

    while True:
        try:
            resp = _call("getUpdates", {"offset": offset, "timeout": 30}, timeout=40)
        except Exception:
            time.sleep(3)
            continue
        if not resp.get("ok"):
            time.sleep(3)
            continue
        for upd in resp.get("result", []):
            offset = upd["update_id"] + 1
            try:
                _handle_update(upd)
            except Exception:
                pass


def _handle_update(upd: dict) -> None:
    # Button taps (approve / deny).
    cq = upd.get("callback_query")
    if cq:
        from_id = cq.get("from", {}).get("id")
        cq_id = cq.get("id")
        data = cq.get("data", "")
        if not _authorized(from_id):
            _answer_cb(cq_id, "Not authorized")
            return
        if data.startswith("appr:"):
            _, aid, decision = data.split(":", 2)
            entry = claudebridge.resolve_approval(aid, decision)
            _answer_cb(cq_id, "Allowed ✅" if decision == "allow" else "Denied ❌")
            m = cq.get("message", {})
            if m:
                verdict = "✅ Allowed" if decision == "allow" else "❌ Denied"
                base = m.get("text", "")
                tail = f"\n\n→ {verdict}" if entry else "\n\n(already decided / expired)"
                _edit_text(m.get("chat", {}).get("id"), m.get("message_id"), base + tail)
        return

    msg = upd.get("message") or upd.get("edited_message")
    if not msg:
        return
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "") or ""
    if not text:
        return
    if not _authorized(chat_id):
        _send(
            chat_id,
            "🔒 Not authorized.\nYour chat id is: "
            f"{chat_id}\nSet MANAGER_TELEGRAM_ALLOWED to this id and restart GSO-1.",
        )
        return

    if text.startswith("/"):
        cmd0 = text.strip().split(maxsplit=1)[0].lstrip("/").split("@")[0].lower()
        if cmd0 == "restart":
            _send(chat_id, "♻️ Restarting GSO-1… back in a few seconds. Send /help once I'm up.")
            _restart(upd.get("update_id"))  # re-execs; returns only if it failed
            _send(chat_id, "⚠️ Restart failed — check the logs or restart manually.")
            return
        _send(chat_id, _dispatch(chat_id, text))
        return

    # Non-command text: route to the active Claude session, if any.
    sess = claudebridge.get_session(chat_id)
    if not sess:
        _send(chat_id, "Send a command (/help), or /claude <project> to start a Claude session.")
        return
    err = claudebridge.submit_turn(chat_id, text)  # output streams via the sender
    if err:
        _send(chat_id, err)


def start() -> None:
    global _thread
    if not configured() or _state["running"]:
        return
    claudebridge.set_approval_notifier(_send_approval)
    claudebridge.set_sender(lambda cid, text: _send(cid, text))
    _state["running"] = True
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
