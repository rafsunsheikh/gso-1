"""Kanban-style project planner (boards -> tasks), persisted to planner.json.

A board is a project (optionally linked to a registry app). Each task lives in
one of four columns (backlog / todo / doing / done), carries notes, priority and a due
date, and may itself link to an app.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Optional

from . import config

_lock = threading.Lock()

# "backlog" arrived with the redesign's four-column board; tasks written
# before it default to "todo", which is now the "Today" column.
STATUSES = ("backlog", "todo", "doing", "done")
PRIORITIES = ("low", "med", "high")

_PLANNER_FILE = config.DATA_DIR / "planner.json"


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _load() -> dict:
    if not _PLANNER_FILE.exists():
        return {"boards": []}
    try:
        import json

        data = json.loads(_PLANNER_FILE.read_text())
        if "boards" not in data:
            data["boards"] = []
        return data
    except Exception:
        return {"boards": []}


def _save(data: dict) -> None:
    import json

    config.ensure_dirs()
    tmp = _PLANNER_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_PLANNER_FILE)


def _find_board(data: dict, board_id: str) -> Optional[dict]:
    return next((b for b in data["boards"] if b["id"] == board_id), None)


def _find_task(board: dict, task_id: str) -> Optional[dict]:
    return next((t for t in board["tasks"] if t["id"] == task_id), None)


# --------------------------------------------------------------------------
# Boards
# --------------------------------------------------------------------------
def get_all() -> dict:
    with _lock:
        return _load()


def create_board(name: str, app: Optional[str] = None) -> dict:
    with _lock:
        data = _load()
        board = {
            "id": _new_id("b"),
            "name": name.strip() or "Untitled board",
            "app": app or None,
            "created_at": _now(),
            "order": len(data["boards"]),
            "tasks": [],
        }
        data["boards"].append(board)
        _save(data)
        return board


def update_board(board_id: str, patch: dict[str, Any]) -> Optional[dict]:
    with _lock:
        data = _load()
        board = _find_board(data, board_id)
        if not board:
            return None
        for k in ("name", "app", "order"):
            if k in patch:
                board[k] = patch[k]
        _save(data)
        return board


def delete_board(board_id: str) -> bool:
    with _lock:
        data = _load()
        before = len(data["boards"])
        data["boards"] = [b for b in data["boards"] if b["id"] != board_id]
        _save(data)
        return len(data["boards"]) < before


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------
def create_task(board_id: str, **fields: Any) -> Optional[dict]:
    with _lock:
        data = _load()
        board = _find_board(data, board_id)
        if not board:
            return None
        status = fields.get("status") or "todo"
        if status not in STATUSES:
            status = "todo"
        column_size = sum(1 for t in board["tasks"] if t["status"] == status)
        task = {
            "id": _new_id("t"),
            "title": (fields.get("title") or "Untitled").strip(),
            "notes": fields.get("notes") or "",
            "status": status,
            "priority": fields.get("priority") if fields.get("priority") in PRIORITIES else "med",
            "due": fields.get("due") or None,
            "app": fields.get("app") or None,
            "order": column_size,
            "created_at": _now(),
        }
        board["tasks"].append(task)
        _save(data)
        return task


def update_task(board_id: str, task_id: str, patch: dict[str, Any]) -> Optional[dict]:
    with _lock:
        data = _load()
        board = _find_board(data, board_id)
        if not board:
            return None
        task = _find_task(board, task_id)
        if not task:
            return None
        # If moving columns, append to the end of the destination column.
        if "status" in patch and patch["status"] in STATUSES and patch["status"] != task["status"]:
            dest = patch["status"]
            task["order"] = sum(1 for t in board["tasks"] if t["status"] == dest)
            task["status"] = dest
        for k in ("title", "notes", "priority", "due", "app", "order"):
            if k in patch:
                task[k] = patch[k]
        _save(data)
        return task


def move_task(board_id: str, task_id: str, status: str, index: int) -> Optional[dict]:
    """Move a task to `status` at position `index`, renumbering both columns."""
    with _lock:
        data = _load()
        board = _find_board(data, board_id)
        if not board:
            return None
        task = _find_task(board, task_id)
        if not task:
            return None
        if status not in STATUSES:
            status = task["status"]
        src_status = task["status"]
        task["status"] = status

        # Rebuild destination column with the task inserted at `index`.
        dest = sorted(
            (t for t in board["tasks"] if t["status"] == status and t["id"] != task_id),
            key=lambda t: t["order"],
        )
        index = max(0, min(index, len(dest)))
        dest.insert(index, task)
        for i, t in enumerate(dest):
            t["order"] = i

        # Renumber the source column if the task changed columns.
        if src_status != status:
            src = sorted(
                (t for t in board["tasks"]
                 if t["status"] == src_status and t["id"] != task_id),
                key=lambda t: t["order"],
            )
            for i, t in enumerate(src):
                t["order"] = i

        _save(data)
        return task


def delete_task(board_id: str, task_id: str) -> bool:
    with _lock:
        data = _load()
        board = _find_board(data, board_id)
        if not board:
            return False
        before = len(board["tasks"])
        board["tasks"] = [t for t in board["tasks"] if t["id"] != task_id]
        _save(data)
        return len(board["tasks"]) < before
