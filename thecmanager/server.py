"""FastAPI app exposing the registry, launcher, git, health and config APIs."""
from __future__ import annotations

import atexit
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse, JSONResponse, PlainTextResponse, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    claudebridge, config, git_ops, health, llm, llmproxy, planner, runner, scanner,
    sysmon, telegrambot, vscode,
)
from . import chat as chat_agent
from . import scheduler
from . import opsroom as opsroom_bridge
from . import site as site_cms

app = FastAPI(title="GSO-1", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"

atexit.register(runner.stop_all)
atexit.register(llm.stop_if_managed)


@app.on_event("startup")
def _start_telegram() -> None:
    telegrambot.start()  # no-op unless MANAGER_TELEGRAM_TOKEN is set
    chat_agent.register_web_handlers()  # web Chat tab's bridge routing
    scheduler.start()  # recurring reports; jobs live in data/schedule.json


atexit.register(scheduler.stop)


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
    claude_state: dict[str, str] = {}
    for s in claudebridge.list_sessions().values():
        claude_state[s["project"]] = "working" if s.get("busy") else "attached"
    # GSO-1 lists itself. It is running (the supervisor started it), but not via
    # runner, so `running` reads False and the UI would offer a Start button —
    # which would launch a second copy and collide on the same port. Flag it so
    # the client can show state honestly and refuse to start it.
    self_path = str(opsroom_bridge.repo_root().resolve())

    apps = []
    for name in scanner.list_app_names():
        cfg = scanner.effective_config(name)
        path = str(scanner.app_path(name).resolve())
        is_self = path == self_path
        apps.append(
            {
                "name": name,
                "type": cfg["type"],
                "language": cfg["language"],
                "running": True if is_self else (name in running),
                "is_self": is_self,
                "has_start_command": False if is_self else bool(cfg["start_command"]),
                "port": cfg["port"],
                "favourite": cfg["favourite"],
                "vscode_open": path in vscode_open,
                "claude_session": claude_state.get(name),
                "root_label": scanner.root_label(name),
            }
        )
    return JSONResponse(
        {
            "projects_dir": str(config.PROJECTS_DIR),
            "projects_dirs": [str(d) for d in config.PROJECTS_DIRS],
            "roots": [label for label, _ in config.PROJECT_ROOTS],
            "count": len(apps),
            "apps": apps,
        }
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
    folders = []
    for p in sorted(vscode.open_folders()):
        folders.append(
            {
                "name": Path(p).name,
                "path": p,
                "in_projects": config.under_any_root(p),
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
    ctx: int = 131072
    ngl: int = 99
    jinja: bool = True
    alias: str = ""
    threads: int | None = None
    batch: int | None = None
    parallel: int | None = None
    reasoning_format: str = ""
    temp: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    context_shift: bool = True
    cache_type_k: str = "q8_0"
    cache_type_v: str = "q8_0"


@app.post("/api/llm/start")
def llm_start(body: LlmStartBody) -> JSONResponse:
    result = llm.start(
        model_path=body.model_path,
        port=body.port or llm.DEFAULT_PORT,
        ctx=body.ctx,
        ngl=body.ngl,
        jinja=body.jinja,
        alias=body.alias,
        threads=body.threads,
        batch=body.batch,
        parallel=body.parallel,
        reasoning_format=body.reasoning_format,
        temp=body.temp,
        top_p=body.top_p,
        top_k=body.top_k,
        min_p=body.min_p,
        context_shift=body.context_shift,
        cache_type_k=body.cache_type_k,
        cache_type_v=body.cache_type_v,
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
# Ops Room sidecar
# --------------------------------------------------------------------------
@app.get("/api/opsroom/status")
def opsroom_status() -> JSONResponse:
    return JSONResponse(opsroom_bridge.available())


@app.post("/api/opsroom/cancel")
def opsroom_cancel() -> JSONResponse:
    return JSONResponse({"cancelled": opsroom_bridge.cancel()})


@app.get("/api/opsroom/ask")
def opsroom_ask(prompt: str):
    """SSE stream. GET so EventSource can consume it directly."""
    return StreamingResponse(
        opsroom_bridge.ask_stream(prompt),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------
# Scheduler
# --------------------------------------------------------------------------
@app.get("/api/schedule")
def schedule_status() -> JSONResponse:
    return JSONResponse(scheduler.status())


@app.post("/api/schedule/{job_id}/run")
def schedule_run(job_id: str, notify: bool = True) -> JSONResponse:
    for job in scheduler.load_jobs():
        if job.get("id") == job_id:
            return JSONResponse(scheduler.run_job(job, notify=notify))
    raise HTTPException(status_code=404, detail=f"No such job: {job_id}")


@app.post("/api/schedule/{job_id}/enabled")
def schedule_enabled(job_id: str, enabled: bool) -> JSONResponse:
    jobs = scheduler.load_jobs()
    for job in jobs:
        if job.get("id") == job_id:
            job["enabled"] = enabled
            scheduler.save_jobs(jobs)
            return JSONResponse({"ok": True, "id": job_id, "enabled": enabled})
    raise HTTPException(status_code=404, detail=f"No such job: {job_id}")


@app.get("/api/telegram/status")
def telegram_status() -> JSONResponse:
    return JSONResponse(telegrambot.status())


# --------------------------------------------------------------------------
# Claude Code permission bridge (called by claude_perm_mcp.py)
# --------------------------------------------------------------------------
class PermissionRequest(BaseModel):
    chat_id: str
    tool_name: str
    tool_input: dict = {}


@app.post("/api/claude/permission/request")
def claude_permission_request(body: PermissionRequest) -> JSONResponse:
    aid = claudebridge.create_approval(body.chat_id, body.tool_name, body.tool_input)
    return JSONResponse({"id": aid})


@app.get("/api/claude/permission/poll/{aid}")
def claude_permission_poll(aid: str) -> JSONResponse:
    return JSONResponse({"decision": claudebridge.poll_approval(aid)})


@app.get("/api/claude/sessions")
def claude_sessions() -> JSONResponse:
    sessions = [
        {
            "project": s["project"],
            "busy": s.get("busy", False),
            "has_session": bool(s.get("session_id")),
        }
        for s in claudebridge.list_sessions().values()
    ]
    return JSONResponse({"count": len(sessions), "sessions": sessions})


# --------------------------------------------------------------------------
# Anthropic-compatible proxy (so `local` Claude sessions use the local model)
# --------------------------------------------------------------------------
@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    body = await request.json()
    if body.get("stream"):
        return StreamingResponse(llmproxy.stream(body), media_type="text/event-stream")
    return JSONResponse(llmproxy.complete(body))


@app.post("/v1/messages/count_tokens")
async def anthropic_count_tokens(request: Request) -> JSONResponse:
    return JSONResponse(llmproxy.count_tokens(await request.json()))


@app.get("/api/llm/metrics")
def llm_metrics() -> JSONResponse:
    """System CPU/GPU/RAM load, with the LLM process broken out."""
    st = llm.status()
    snap = sysmon.get_snapshot(st.get("pid"))
    if snap is None:
        return JSONResponse({"warming": True})
    return JSONResponse(snap)


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


class TaskMove(BaseModel):
    status: str
    index: int = 0


@app.put("/api/planner/boards/{board_id}/tasks/{task_id}/move")
def planner_move_task(board_id: str, task_id: str, body: TaskMove) -> JSONResponse:
    task = planner.move_task(board_id, task_id, body.status, body.index)
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


# --------------------------------------------------------------------------
# Computer assistant chat (tool-using agent on the local LLM)
# --------------------------------------------------------------------------
class ChatSend(BaseModel):
    message: str


class ChatApprove(BaseModel):
    id: str
    approve: bool


@app.get("/api/chat")
def chat_get() -> JSONResponse:
    return JSONResponse(chat_agent.history())


@app.post("/api/chat/send")
def chat_send(body: ChatSend) -> JSONResponse:
    return JSONResponse(chat_agent.send(body.message))


@app.post("/api/chat/approve")
def chat_approve(body: ChatApprove) -> JSONResponse:
    return JSONResponse(chat_agent.approve(body.id, body.approve))


@app.post("/api/chat/reset")
def chat_reset() -> JSONResponse:
    return JSONResponse(chat_agent.reset())


# --------------------------------------------------------------------------
# Site CMS — edit the Jekyll website and publish to GitHub
# --------------------------------------------------------------------------
class SiteSave(BaseModel):
    frontmatter: dict
    body: str


class SiteCreate(BaseModel):
    file: str
    frontmatter: dict = {}
    body: str = ""


class SitePublish(BaseModel):
    message: str = ""


@app.get("/api/site")
def site_overview() -> JSONResponse:
    return JSONResponse(site_cms.overview())


@app.get("/api/site/{coll}")
def site_list(coll: str) -> JSONResponse:
    try:
        return JSONResponse({"items": site_cms.list_items(coll)})
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/site/{coll}/{name}")
def site_read(coll: str, name: str) -> JSONResponse:
    try:
        return JSONResponse(site_cms.read_item(coll, name))
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.put("/api/site/{coll}/{name}")
def site_save(coll: str, name: str, body: SiteSave) -> JSONResponse:
    try:
        return JSONResponse(site_cms.save_item(coll, name, body.frontmatter, body.body))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/site/{coll}")
def site_create(coll: str, body: SiteCreate) -> JSONResponse:
    try:
        return JSONResponse(site_cms.create_item(coll, body.file, body.frontmatter, body.body))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/site/publish")
def site_publish(body: SitePublish) -> JSONResponse:
    return JSONResponse(site_cms.publish(body.message))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
