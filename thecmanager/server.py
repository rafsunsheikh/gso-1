"""FastAPI app exposing the registry, launcher, git, health and config APIs."""
from __future__ import annotations

import atexit
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, git_ops, health, llm, planner, runner, scanner, vscode

app = FastAPI(title="The Manager", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"

atexit.register(runner.stop_all)
atexit.register(llm.stop_if_managed)


def _require(name: str) -> None:
    if not scanner.exists(name):
        raise HTTPException(status_code=404, detail=f"App '{name}' not found.")


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# --------------------------------------------------------------------------
# Registry / listing
# --------------------------------------------------------------------------
@app.get("/api/apps")
def list_apps() -> JSONResponse:
    running = runner.running_names()
    vscode_open = vscode.open_folders()
    apps = []
    for name in scanner.list_app_names():
        cfg = scanner.effective_config(name)
        path = str(scanner.app_path(name).resolve())
        apps.append(
            {
                "name": name,
                "type": cfg["type"],
                "language": cfg["language"],
                "running": name in running,
                "has_start_command": bool(cfg["start_command"]),
                "port": cfg["port"],
                "favourite": cfg["favourite"],
                "vscode_open": path in vscode_open,
            }
        )
    return JSONResponse(
        {"projects_dir": str(config.PROJECTS_DIR), "count": len(apps), "apps": apps}
    )


@app.get("/api/apps/{name}")
def app_detail(name: str) -> JSONResponse:
    _require(name)
    cfg = scanner.effective_config(name)
    return JSONResponse(
        {
            "name": name,
            "path": str(scanner.app_path(name)),
            "config": cfg,
            "description": scanner.read_description(name),
            "git": git_ops.status(scanner.app_path(name)),
            "health": health.check(name, cfg["port"]),
            "process": runner.info(name),
        }
    )


@app.get("/api/apps/{name}/description")
def app_description(name: str) -> JSONResponse:
    _require(name)
    return JSONResponse({"name": name, "description": scanner.read_description(name)})


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------
@app.post("/api/apps/{name}/start")
def start_app(name: str) -> JSONResponse:
    _require(name)
    cfg = scanner.effective_config(name)
    result = runner.start(
        name, cfg["start_command"], str(scanner.app_path(name)), cfg["port"]
    )
    status = 200 if result["ok"] else 400
    return JSONResponse(result, status_code=status)


@app.post("/api/apps/{name}/stop")
def stop_app(name: str) -> JSONResponse:
    _require(name)
    result = runner.stop(name)
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.get("/api/apps/{name}/status")
def app_status(name: str) -> JSONResponse:
    _require(name)
    cfg = scanner.effective_config(name)
    return JSONResponse(
        {
            "name": name,
            "process": runner.info(name),
            "health": health.check(name, cfg["port"]),
        }
    )


@app.get("/api/apps/{name}/health")
def app_health(name: str) -> JSONResponse:
    _require(name)
    cfg = scanner.effective_config(name)
    return JSONResponse(health.check(name, cfg["port"]))


@app.get("/api/apps/{name}/logs", response_class=PlainTextResponse)
def app_logs(name: str, lines: int = 200) -> PlainTextResponse:
    _require(name)
    return PlainTextResponse(runner.tail_log(name, lines) or "(no logs yet)")


# --------------------------------------------------------------------------
# Setup / install
# --------------------------------------------------------------------------
@app.post("/api/apps/{name}/setup")
def run_setup(name: str) -> JSONResponse:
    _require(name)
    cfg = scanner.effective_config(name)
    if not cfg["setup_command"]:
        return JSONResponse(
            {"ok": False, "message": "No setup/install command for this app."},
            status_code=400,
        )
    result = runner.start_setup(name, cfg["setup_command"], str(scanner.app_path(name)))
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.get("/api/apps/{name}/setup/status")
def setup_status(name: str) -> JSONResponse:
    _require(name)
    return JSONResponse({"name": name, "setup": runner.setup_info(name)})


@app.get("/api/apps/{name}/setup/logs", response_class=PlainTextResponse)
def setup_logs(name: str, lines: int = 400) -> PlainTextResponse:
    _require(name)
    return PlainTextResponse(runner.tail_setup_log(name, lines) or "(no setup logs yet)")


# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------
@app.get("/api/apps/{name}/git")
def app_git(name: str) -> JSONResponse:
    _require(name)
    return JSONResponse(git_ops.status(scanner.app_path(name)))


@app.post("/api/apps/{name}/update")
def update_app(name: str) -> JSONResponse:
    _require(name)
    result = git_ops.update(scanner.app_path(name))
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


# --------------------------------------------------------------------------
# Config / overrides
# --------------------------------------------------------------------------
class ConfigPatch(BaseModel):
    start_command: str | None = None
    setup_command: str | None = None
    port: int | None = None
    description: str | None = None


@app.put("/api/apps/{name}/config")
def update_config(name: str, patch: ConfigPatch) -> JSONResponse:
    from . import registry

    _require(name)
    # Only include explicitly-provided fields so we don't wipe others.
    data = patch.model_dump(exclude_unset=True)
    registry.update(name, data)
    return JSONResponse({"ok": True, "config": scanner.effective_config(name)})


@app.post("/api/apps/{name}/favourite")
def toggle_favourite(name: str) -> JSONResponse:
    from . import registry

    _require(name)
    now_fav = not bool(registry.get(name).get("favourite"))
    # Store True when favourited; clear the key when un-favourited.
    registry.update(name, {"favourite": True if now_fav else None})
    return JSONResponse({"ok": True, "favourite": now_fav})


# --------------------------------------------------------------------------
# VS Code
# --------------------------------------------------------------------------
@app.post("/api/apps/{name}/vscode/open")
def vscode_open(name: str) -> JSONResponse:
    _require(name)
    path = scanner.app_path(name)
    # If it's already open, focus it instead of opening a duplicate window.
    if vscode.is_open(path):
        result = vscode.focus_project(path)
    else:
        result = vscode.open_project(path, new_window=True)
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.post("/api/apps/{name}/vscode/focus")
def vscode_focus(name: str) -> JSONResponse:
    _require(name)
    result = vscode.focus_project(scanner.app_path(name))
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.get("/api/vscode/folders")
def vscode_folders() -> JSONResponse:
    """Folders currently open in VS Code windows (for the 'Opened in VSCode' view)."""
    proj_root = str(config.PROJECTS_DIR.resolve())
    folders = []
    for p in sorted(vscode.open_folders()):
        folders.append(
            {
                "name": Path(p).name,
                "path": p,
                "in_projects": p.startswith(proj_root),
            }
        )
    return JSONResponse(
        {"available": vscode.code_bin() is not None, "count": len(folders), "folders": folders}
    )


class FocusBody(BaseModel):
    path: str


@app.post("/api/vscode/focus-path")
def vscode_focus_path(body: FocusBody) -> JSONResponse:
    result = vscode.focus_project(body.path)
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


# --------------------------------------------------------------------------
# Local LLM (llama.cpp)
# --------------------------------------------------------------------------
@app.get("/api/llm/status")
def llm_status() -> JSONResponse:
    return JSONResponse(llm.status())


@app.get("/api/llm/models")
def llm_models() -> JSONResponse:
    return JSONResponse(
        {
            "server_bin": llm.server_bin(),
            "default_port": llm.DEFAULT_PORT,
            "models": llm.list_models(),
        }
    )


class LlmStartBody(BaseModel):
    model_path: str
    port: int | None = None
    ctx: int = 65536
    ngl: int = 99
    jinja: bool = True
    alias: str = ""


@app.post("/api/llm/start")
def llm_start(body: LlmStartBody) -> JSONResponse:
    result = llm.start(
        model_path=body.model_path,
        port=body.port or llm.DEFAULT_PORT,
        ctx=body.ctx,
        ngl=body.ngl,
        jinja=body.jinja,
        alias=body.alias,
    )
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.post("/api/llm/stop")
def llm_stop() -> JSONResponse:
    result = llm.stop()
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.get("/api/llm/logs", response_class=PlainTextResponse)
def llm_logs(lines: int = 200) -> PlainTextResponse:
    return PlainTextResponse(llm.tail_log(lines) or "(no logs yet)")


# --------------------------------------------------------------------------
# Planner (kanban boards + tasks)
# --------------------------------------------------------------------------
@app.get("/api/planner")
def planner_all() -> JSONResponse:
    return JSONResponse(planner.get_all())


class BoardBody(BaseModel):
    name: str
    app: str | None = None


@app.post("/api/planner/boards")
def planner_create_board(body: BoardBody) -> JSONResponse:
    return JSONResponse({"ok": True, "board": planner.create_board(body.name, body.app)})


class BoardPatch(BaseModel):
    name: str | None = None
    app: str | None = None
    order: int | None = None


@app.put("/api/planner/boards/{board_id}")
def planner_update_board(board_id: str, patch: BoardPatch) -> JSONResponse:
    board = planner.update_board(board_id, patch.model_dump(exclude_unset=True))
    if board is None:
        raise HTTPException(status_code=404, detail="Board not found.")
    return JSONResponse({"ok": True, "board": board})


@app.delete("/api/planner/boards/{board_id}")
def planner_delete_board(board_id: str) -> JSONResponse:
    if not planner.delete_board(board_id):
        raise HTTPException(status_code=404, detail="Board not found.")
    return JSONResponse({"ok": True})


class TaskBody(BaseModel):
    title: str
    notes: str | None = None
    status: str | None = None
    priority: str | None = None
    due: str | None = None
    app: str | None = None


@app.post("/api/planner/boards/{board_id}/tasks")
def planner_create_task(board_id: str, body: TaskBody) -> JSONResponse:
    task = planner.create_task(board_id, **body.model_dump(exclude_unset=True))
    if task is None:
        raise HTTPException(status_code=404, detail="Board not found.")
    return JSONResponse({"ok": True, "task": task})


class TaskPatch(BaseModel):
    title: str | None = None
    notes: str | None = None
    status: str | None = None
    priority: str | None = None
    due: str | None = None
    app: str | None = None
    order: int | None = None


@app.put("/api/planner/boards/{board_id}/tasks/{task_id}")
def planner_update_task(board_id: str, task_id: str, patch: TaskPatch) -> JSONResponse:
    task = planner.update_task(board_id, task_id, patch.model_dump(exclude_unset=True))
    if task is None:
        raise HTTPException(status_code=404, detail="Board or task not found.")
    return JSONResponse({"ok": True, "task": task})


@app.delete("/api/planner/boards/{board_id}/tasks/{task_id}")
def planner_delete_task(board_id: str, task_id: str) -> JSONResponse:
    if not planner.delete_task(board_id, task_id):
        raise HTTPException(status_code=404, detail="Board or task not found.")
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------
# Chat (stub — wired for a later pass with Claude API)
# --------------------------------------------------------------------------
class ChatMessage(BaseModel):
    message: str


@app.post("/api/apps/{name}/chat")
def chat(name: str, msg: ChatMessage) -> JSONResponse:
    _require(name)
    return JSONResponse(
        {
            "ok": False,
            "reply": (
                "💬 AI chat is not wired up yet. This is a planned next pass: "
                "it will read this app's README, file tree and git log and answer "
                "questions via the Claude API. For now, use the Description, Git "
                "and Health panels."
            ),
        }
    )


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
