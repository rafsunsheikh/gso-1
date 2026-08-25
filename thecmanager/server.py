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
    claudebridge, config, events, git_ops, health, llm, llmproxy, llmusage, planner,
    remoteauth, runner, scanner, sysmon, telegrambot, vscode,
)
from . import chat as chat_agent
from . import scheduler
from . import opsroom as opsroom_bridge
from . import overview as overview_mod
from . import site as site_cms

app = FastAPI(title="GSO-1", version="0.1.0")
# Loopback is untouched; anything arriving from another device needs the token.
app.add_middleware(remoteauth.RemoteAuthMiddleware)

STATIC_DIR = Path(__file__).resolve().parent / "static"

atexit.register(runner.stop_all)
atexit.register(llm.stop_if_managed)


@app.on_event("startup")
def _start_telegram() -> None:
    telegrambot.start()  # no-op unless MANAGER_TELEGRAM_TOKEN is set
    chat_agent.register_web_handlers()  # web Chat tab's bridge routing
    scheduler.start()  # recurring reports; jobs live in data/schedule.json
    events.record("run", "gso-1", f"dashboard up on :{config.PORT}")


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


@app.get("/health")
def health_probe() -> JSONResponse:
    """Unauthenticated liveness, the supervisor and the phone both poll it."""
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------
# Phone companion
# --------------------------------------------------------------------------
@app.get("/m")
def mobile_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "mobile.html")


@app.get("/manifest.webmanifest")
def mobile_manifest() -> JSONResponse:
    """Lets iOS "Add to Home Screen" install it as a standalone app."""
    return JSONResponse({
        "name": "GSO-1", "short_name": "GSO-1", "id": "/m",
        "start_url": "/m", "scope": "/", "display": "standalone",
        "background_color": "#09080f", "theme_color": "#09080f",
        "icons": [{"src": "/static/favicon.svg", "sizes": "any", "type": "image/svg+xml"}],
    })


class RemoteLogin(BaseModel):
    token: str


@app.post("/api/remote/login")
def remote_login(body: RemoteLogin, request: Request) -> JSONResponse:
    """Exchange the shared secret for a cookie.

    A cookie rather than a header because EventSource cannot set headers and
    the Ops Room stream is SSE. HttpOnly so page scripts cannot read it back.
    """
    if remoteauth.is_loopback(request):
        return JSONResponse({"ok": True, "loopback": True})
    if not config.MOBILE_TOKEN:
        return JSONResponse(
            {"ok": False, "error": "Remote access is off: MANAGER_MOBILE_TOKEN is not set."},
            status_code=403)
    if not remoteauth.token_ok((body.token or "").strip()):
        return JSONResponse({"ok": False, "error": "Wrong code."}, status_code=401)

    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        remoteauth.COOKIE, config.MOBILE_TOKEN,
        max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax", path="/",
    )
    return resp


@app.get("/api/remote/status")
def remote_status(request: Request) -> JSONResponse:
    return JSONResponse({
        "loopback": remoteauth.is_loopback(request),
        "authed": True,           # the middleware would have refused otherwise
        "token_configured": bool(config.MOBILE_TOKEN),
        "bound_host": config.HOST,
        "reachable_off_device": config.HOST not in ("127.0.0.1", "localhost", "::1"),
    })


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
    # runner, so `running` reads False and the UI would offer a Start button, 
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
            # The UI abbreviates paths to "~/…"; only the host knows what ~ is.
            "home": str(Path.home()),
            "user": config.display_name(),
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
    events.record(
        "run" if result["ok"] else "fail", name,
        f"started on :{cfg['port']}" if result["ok"] and cfg.get("port")
        else "started" if result["ok"] else f"failed to start, {result.get('message', '')}".strip(),
    )
    status = 200 if result["ok"] else 400
    return JSONResponse(result, status_code=status)


@app.post("/api/apps/{name}/stop")
def stop_app(name: str) -> JSONResponse:
    _require(name)
    result = runner.stop(name)
    if result["ok"]:
        events.record("stop", name, "stopped")
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
    first = (result.get("output") or "").strip().splitlines()
    events.record("git" if result["ok"] else "fail", name,
                  first[0][:110] if first else ("pulled" if result["ok"] else "pull failed"))
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


class CommitBody(BaseModel):
    message: str | None = None


@app.post("/api/apps/{name}/commit")
def commit_app(name: str, body: CommitBody) -> JSONResponse:
    """Stage and commit everything in a repo, the phone's one-tap Commit all."""
    _require(name)
    msg = (body.message or "").strip() or f"Update {name} via GSO-1"
    result = git_ops.commit_all(scanner.app_path(name), msg)
    events.record("git" if result["ok"] else "fail", name,
                  "nothing to commit" if result.get("nochange")
                  else f"committed, {msg}"[:110] if result["ok"] else "commit failed")
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


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
    if result["ok"]:
        events.record("llm", "llama-server",
                      f"started on :{body.port or llm.DEFAULT_PORT} · ctx {body.ctx:,}")
    else:
        events.record("fail", "llama-server", result.get("message", "failed to start")[:110])
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.post("/api/llm/stop")
def llm_stop() -> JSONResponse:
    result = llm.stop()
    if result["ok"]:
        events.record("llm", "llama-server", "stopped")
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.get("/api/llm/logs", response_class=PlainTextResponse)
def llm_logs(lines: int = 200) -> PlainTextResponse:
    return PlainTextResponse(llm.tail_log(lines) or "(no logs yet)")


@app.get("/api/events")
def list_events(limit: int = 60, since: float | None = None) -> JSONResponse:
    """The Ops Room timeline. `since` is a unix timestamp, the UI sends midnight."""
    return JSONResponse({"events": events.recent(limit=limit, since=since)})


@app.get("/api/overview")
def api_overview(force: bool = False) -> JSONResponse:
    """Live processes, dirty repos and failed runs in one call.

    Per-repo from the browser would be ~270 round trips; the git sweep alone
    is ~12s of subprocesses, so results are cached (see overview.py).
    """
    return JSONResponse(overview_mod.get(force=force))


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


@app.get("/api/llm/usage")
def llm_usage() -> JSONResponse:
    """The LAST HOUR histogram: proxy traffic bucketed into eight slices."""
    return JSONResponse(llmusage.summary())


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
# Chat (stub, wired for a later pass with Claude API)
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
# Site CMS, edit the Jekyll website and publish to GitHub
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


# ------------------------------------------------------------ first-run setup
#
# A packaged GSO-1 ships without a .env, so a fresh install has to ask the user
# where their code lives. These endpoints back that screen: and let anyone
# change or add a root later without editing a file.


class RootEntry(BaseModel):
    label: str | None = None
    path: str


class RootsBody(BaseModel):
    roots: list[RootEntry]


def _roots_payload() -> dict:
    return {
        "needs_onboarding": config.needs_onboarding(),
        # Roots forced by the environment cannot be changed from the UI; say so
        # rather than letting the user save into a void.
        "locked_by_env": bool(config._env_roots()),
        "home": str(Path.home()),
        "roots": [
            {"label": label, "path": str(path), "exists": path.is_dir()}
            for label, path in config.PROJECT_ROOTS
        ],
    }


@app.get("/api/settings/roots")
def get_roots() -> JSONResponse:
    return JSONResponse(_roots_payload())


@app.post("/api/settings/roots")
def set_roots(body: RootsBody) -> JSONResponse:
    if config._env_roots():
        raise HTTPException(
            status_code=409,
            detail="Project roots are set by MANAGER_PROJECTS_DIRS; unset it to "
            "choose folders from the app.",
        )
    try:
        config.set_project_roots([r.model_dump() for r in body.roots])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    scanner.invalidate()
    return JSONResponse(_roots_payload())


@app.get("/api/settings/browse")
def browse(path: str = "") -> JSONResponse:
    """List sub-directories of `path`, for the folder picker in the browser UI.

    The desktop shell uses a native dialog instead. This walks anywhere the
    user can already read, it is their machine, and the picker would be
    useless confined to roots that have not been chosen yet.
    """
    target = Path(path).expanduser() if path.strip() else Path.home()
    try:
        target = target.resolve()
        entries = sorted(
            (e for e in target.iterdir() if e.is_dir() and not e.name.startswith(".")),
            key=lambda e: e.name.lower(),
        )
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"cannot read {target}: {e}")
    return JSONResponse(
        {
            "path": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "home": str(Path.home()),
            "dirs": [{"name": e.name, "path": str(e)} for e in entries[:500]],
        }
    )


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
