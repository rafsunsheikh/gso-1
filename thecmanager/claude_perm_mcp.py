#!/usr/bin/env python3
"""Standalone MCP stdio server that routes Claude Code permission requests to
GSO-1 (which asks the human via Telegram).

Claude Code spawns this via `--mcp-config` and calls its `approve` tool through
`--permission-prompt-tool mcp__manager__approve`. The tool blocks until the
human decides, then returns the allow/deny verdict Claude Code expects.

Dependency-free (stdlib only) so it runs from any project directory. Behaviour
is driven entirely by environment variables set when Claude is launched:

  MANAGER_APPROVAL_URL   base URL of GSO-1 (e.g. http://127.0.0.1:8420)
  MANAGER_APPROVAL_CHAT  Telegram chat id to ask
  MANAGER_APPROVAL_AUTO  "allow"/"deny" -> skip Telegram (used for testing)
  MANAGER_APPROVAL_LOG   optional path to append a debug log
"""
import json
import os
import sys
import time
import urllib.request

_LOG = os.environ.get("MANAGER_APPROVAL_LOG")


def log(msg: str) -> None:
    if _LOG:
        try:
            with open(_LOG, "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass


def _post(url: str, payload: dict, timeout: int = 10) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def decide(tool_name: str, tool_input: dict) -> dict:
    """Return the permission verdict in Claude Code's expected shape."""
    auto = os.environ.get("MANAGER_APPROVAL_AUTO")
    if auto == "allow":
        return {"behavior": "allow", "updatedInput": tool_input}
    if auto == "deny":
        return {"behavior": "deny", "message": "Auto-denied (test mode)."}

    url = os.environ.get("MANAGER_APPROVAL_URL", "http://127.0.0.1:8420")
    chat = os.environ.get("MANAGER_APPROVAL_CHAT", "")
    try:
        resp = _post(
            f"{url}/api/claude/permission/request",
            {"chat_id": chat, "tool_name": tool_name, "tool_input": tool_input},
        )
        req_id = resp["id"]
    except Exception as e:  # noqa: BLE001
        return {"behavior": "deny", "message": f"Could not reach approver: {e}"}

    deadline = time.time() + 600  # 10 min to tap a button
    while time.time() < deadline:
        try:
            r = _get(f"{url}/api/claude/permission/poll/{req_id}")
        except Exception:
            time.sleep(1.5)
            continue
        decision = r.get("decision")
        if decision == "allow":
            return {"behavior": "allow", "updatedInput": tool_input}
        if decision == "deny":
            return {"behavior": "deny", "message": "Denied via Telegram."}
        time.sleep(1.2)
    return {"behavior": "deny", "message": "Approval timed out."}


def respond(mid, result: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        log(f"RECV method={method} params={json.dumps(msg.get('params', {}))[:600]}")

        if method == "initialize":
            respond(mid, {
                "protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "manager-approver", "version": "1.0.0"},
            })
        elif method == "notifications/initialized":
            continue  # notification, no reply
        elif method == "tools/list":
            respond(mid, {"tools": [{
                "name": "approve",
                "description": "Ask the human operator to approve or deny a tool use.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string"},
                        "input": {"type": "object"},
                    },
                    "required": [],
                },
            }]})
        elif method == "tools/call":
            args = (msg.get("params") or {}).get("arguments") or {}
            tname = args.get("tool_name") or args.get("toolName") or "(unknown)"
            tinput = args.get("input") if "input" in args else args.get("tool_input", {}) or {}
            verdict = decide(tname, tinput)
            log(f"VERDICT {json.dumps(verdict)[:300]}")
            respond(mid, {"content": [{"type": "text", "text": json.dumps(verdict)}]})
        elif mid is not None:
            respond(mid, {})  # ack any other request


if __name__ == "__main__":
    main()
