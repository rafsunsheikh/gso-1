"""FastAPI app exposing the registry, launcher, git, health and config APIs."""
from __future__ import annotations

import atexit
import os
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse, JSONResponse, PlainTextResponse, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from . import (
    claudebridge, config, events, git_ops, health, llm, llmproxy, llmusage,
    modelsetup, planner, remoteauth, runner, scanner, summarize, sysmon,
    telegrambot, vscode,
)
from . import scheduler
from . import opsroom as opsroom_bridge
from . import overview as overview_mod
from . import site as site_cms

app = FastAPI(title="GSO-1", version=__version__)
# Loopback is untouched; anything arriving from another device needs the token.
app.add_middleware(remoteauth.RemoteAuthMiddleware)

STATIC_DIR = Path(__file__).resolve().parent / "static"

atexit.register(runner.stop_all)
atexit.register(llm.stop_if_managed)


@app.on_event("startup")
def _start_telegram() -> None:
    telegrambot.start()  # no-op unless MANAGER_TELEGRAM_TOKEN is set
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


@app.get("/api/apps/{name}/summary")
def app_summary(name: str) -> JSONResponse:
    """The cached summary plus the measured signals, which need no model."""
    _require(name)
    return JSONResponse({
        "name": name,
        "record": summarize.get(name),
        "signals": summarize.signals(name),
        "llm": llm.status().get("state"),
    })


class SummaryBody(BaseModel):
    force: bool = False


@app.post("/api/apps/{name}/summary")
def make_summary(name: str, body: SummaryBody) -> JSONResponse:
    _require(name)
    result = summarize.generate(name, force=body.force)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.get("/api/apps/{name}/branches")
def app_branches(name: str) -> JSONResponse:
    _require(name)
    return JSONResponse(git_ops.branches(scanner.app_path(name)))


class CheckoutBody(BaseModel):
    branch: str


@app.post("/api/apps/{name}/checkout")
def checkout_app(name: str, body: CheckoutBody) -> JSONResponse:
    _require(name)
    result = git_ops.checkout(scanner.app_path(name), body.branch)
    events.record("git" if result["ok"] else "fail", name,
                  (f"switched to {body.branch}" if result["ok"]
                   else f"switch to {body.branch} failed")[:110])
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.post("/api/apps/{name}/push")
def push_app(name: str) -> JSONResponse:
    """Push the current branch. The only thing here that leaves the machine."""
    _require(name)
    result = git_ops.push(scanner.app_path(name))
    first = (result.get("output") or "").strip().splitlines()
    events.record("git" if result["ok"] else "fail", name,
                  first[-1][:110] if first else ("pushed" if result["ok"] else "push failed"))
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
    """Models on disk, each with the flags it was last started with.

    The presets ride along with the listing rather than sitting behind their own
    endpoint: the UI needs both to draw a single row, and one round trip cannot
    show a model with somebody else's settings attached.
    """
    models = llm.list_models()
    saved = llm.presets()
    return JSONResponse(
        {
            "server_bin": llm.server_bin(),
            "server_bin_set": os.environ.get("LLAMA_SERVER_BIN", ""),
            "server_bin_locked": config.set_by_environment("LLAMA_SERVER_BIN"),
            "model_dirs": [
                {"path": d, "exists": Path(d).expanduser().is_dir()}
                for d in llm.model_dirs()
            ],
            "model_dirs_default": not os.environ.get("MANAGER_MODEL_DIRS", "").strip(),
            "model_dirs_locked": config.set_by_environment("MANAGER_MODEL_DIRS"),
            "default_port": llm.default_port(),
            "defaults": llm.DEFAULT_PRESET,
            "last_model": llm.last_model(),
            "models": [
                dict(m, preset=llm.preset_for(m["path"]), has_preset=m["path"] in saved)
                for m in models
            ],
        }
    )


# ---- getting a model in the first place
#
# Installing software and downloading gigabytes are both explicit acts, so
# every one of these is a POST the user has to press, never something a page
# load triggers.

@app.get("/api/llm/setup")
def llm_setup() -> JSONResponse:
    """Whether llama.cpp is missing, and which models could be fetched."""
    ram = sysmon.total_memory()
    return JSONResponse({
        "install": modelsetup.install_plan(),
        "catalog": modelsetup.catalog(ram),
        "download": modelsetup.status(),
        "ram_bytes": ram,
    })


@app.get("/api/llm/install")
def llm_install():
    """SSE stream of the install command's output. GET so EventSource works."""
    def gen():
        for line in modelsetup.install_llama():
            for part in str(line).split("\n"):
                yield f"data: {part}\n"
            yield "\n"
        yield "event: done\ndata: \n\n"
    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class LlmDownloadBody(BaseModel):
    id: str


@app.post("/api/llm/download")
def llm_download(body: LlmDownloadBody) -> JSONResponse:
    result = modelsetup.start(body.id, sysmon.total_memory())
    if result["ok"]:
        events.record("llm", "model download", result["message"][:110])
    return JSONResponse(result, status_code=200 if result["ok"] else 400)


@app.get("/api/llm/download")
def llm_download_status() -> JSONResponse:
    return JSONResponse(modelsetup.status())


@app.post("/api/llm/download/cancel")
def llm_download_cancel() -> JSONResponse:
    return JSONResponse({"cancelled": modelsetup.cancel()})


class LlmPathsBody(BaseModel):
    server_bin: str | None = None
    model_dirs: list[str] | None = None


@app.post("/api/llm/paths")
def llm_paths(body: LlmPathsBody) -> JSONResponse:
    """Where the binary and the models live. Takes effect without a restart."""
    rejected = []
    if body.server_bin is not None:
        if not config.save_env_setting("LLAMA_SERVER_BIN", body.server_bin.strip()):
            rejected.append("LLAMA_SERVER_BIN")
    if body.model_dirs is not None:
        dirs = ":".join(d.strip() for d in body.model_dirs if d.strip())
        if not config.save_env_setting("MANAGER_MODEL_DIRS", dirs):
            rejected.append("MANAGER_MODEL_DIRS")
    if rejected:
        raise HTTPException(
            status_code=409,
            detail=_env_locked_message(rejected[0]),
        )
    return llm_models()


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
    port = body.port or llm.default_port()
    result = llm.start(
        model_path=body.model_path,
        port=port,
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
        # Remember on success only: flags that failed to start a server are not
        # the ones to greet you with next time.
        llm.remember_preset(body.model_path, dict(body.model_dump(), port=port))
        events.record("llm", "llama-server",
                      f"started on :{port} · ctx {body.ctx:,}")
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
    suggested = config.suggested_root()
    return {
        "needs_onboarding": config.needs_onboarding(),
        # Where the first-run picker should open. A suggestion, not a choice.
        "suggested": str(suggested) if suggested else None,
        # Roots forced by the environment cannot be changed from the UI; say so
        # rather than letting the user save into a void, and name the file that
        # did it when we can find one: "unset MANAGER_PROJECTS_DIRS" is only
        # actionable if you know where it was set.
        "locked_by_env": bool(config._env_roots()),
        "locked_by_file": config.env_origin("MANAGER_PROJECTS_DIRS")
        or config.env_origin("MANAGER_PROJECTS_DIR"),
        "home": str(Path.home()),
        "roots": [
            {"label": label, "path": str(path), "exists": path.is_dir()}
            for label, path in config.PROJECT_ROOTS
        ],
    }


def _env_locked_message(key: str) -> str:
    """Why one setting cannot be saved, naming the culprit file when known."""
    where = config.env_origin(key)
    return (f"{key} is set in {where}; remove that line and restart GSO-1 to "
            "change it here.") if where else (
        f"{key} is set in the environment GSO-1 was started with; unset it to "
        "change it here.")


def _roots_locked_message() -> str:
    """Why the folders cannot be edited, naming the culprit file when known."""
    where = config.env_origin("MANAGER_PROJECTS_DIRS") or config.env_origin(
        "MANAGER_PROJECTS_DIR")
    if where:
        return (f"Project folders are set by MANAGER_PROJECTS_DIRS in {where}. "
                "Remove that line and restart GSO-1 to choose folders here.")
    return ("Project folders are set by MANAGER_PROJECTS_DIRS in the environment "
            "GSO-1 was started with; unset it to choose folders from the app.")


@app.get("/api/settings/roots")
def get_roots() -> JSONResponse:
    return JSONResponse(_roots_payload())


@app.post("/api/settings/roots")
def set_roots(body: RootsBody) -> JSONResponse:
    if config._env_roots():
        raise HTTPException(
            status_code=409,
            detail=_roots_locked_message(),
        )
    try:
        config.set_project_roots([r.model_dump() for r in body.roots])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    scanner.invalidate()
    return JSONResponse(_roots_payload())


# ----------------------------------------------------------------- settings
#
# Everything GSO-1 can be configured with, editable from the app rather than a
# terminal. Values are written to settings.json and injected into the
# environment at startup, which is why saving one needs a restart: the modules
# that consume them read os.environ once, at import.

# name -> (secret?, label). Secrets are never sent back to the browser in full;
# the client is told whether one is set, not what it is.
SETTING_KEYS: dict[str, tuple[bool, str]] = {
    "MANAGER_TELEGRAM_TOKEN":   (True,  "Bot token"),
    "MANAGER_TELEGRAM_ALLOWED": (False, "Allowed chat ids"),
    "MANAGER_APPROVAL_CHAT":    (False, "Approval chat id"),
    "MANAGER_MOBILE_TOKEN":     (True,  "Phone access token"),
    "MANAGER_HOST":             (False, "Bind address"),
    "MANAGER_PORT":             (False, "Port"),
    "MANAGER_SITE_DIR":         (False, "Jekyll checkout"),
    "TAVILY_API_KEY":           (True,  "Tavily API key"),
}


class SettingsPatch(BaseModel):
    values: dict[str, str]


def _settings_payload() -> dict:
    saved = (config.load_settings().get("env") or {})
    out = {}
    for key, (secret, label) in SETTING_KEYS.items():
        live = os.environ.get(key, "")
        out[key] = {
            "label": label,
            "secret": secret,
            # A secret's value never leaves the process; the UI shows presence.
            "value": "" if secret else live,
            "is_set": bool(live),
            "saved_here": key in saved,
            "locked_by_env": config.set_by_environment(key),
            "locked_by_file": config.env_origin(key),
        }
    return {"settings": out, "restart_required": _RESTART_REQUIRED}


_RESTART_REQUIRED = False


@app.get("/api/settings")
def get_settings() -> JSONResponse:
    return JSONResponse(_settings_payload())


@app.post("/api/settings")
def patch_settings(body: SettingsPatch) -> JSONResponse:
    """Save settings. An empty string clears one rather than storing a blank."""
    global _RESTART_REQUIRED
    saved = config.load_settings()
    env = dict(saved.get("env") or {})
    rejected = []
    for key, value in body.values.items():
        if key not in SETTING_KEYS:
            rejected.append(key)
            continue
        if config.set_by_environment(key):
            rejected.append(key)
            continue
        if value == "":
            env.pop(key, None)
        else:
            env[key] = value
    saved["env"] = env
    config.save_settings(saved)
    _RESTART_REQUIRED = True
    payload = _settings_payload()
    payload["rejected"] = rejected
    return JSONResponse(payload)


@app.post("/api/settings/restart")
def restart_app() -> JSONResponse:
    """Re-exec so the new settings are picked up.

    os.execv keeps the pid and the environment, so a supervisor or LaunchAgent
    watching this process does not see it die. The response goes out first,
    otherwise the browser gets a dropped connection instead of an answer.
    """
    def _go() -> None:
        time.sleep(0.4)
        os.execv(sys.executable, [sys.executable, "-m", "thecmanager", *sys.argv[1:]])

    threading.Thread(target=_go, daemon=True).start()
    return JSONResponse({"restarting": True})


@app.post("/api/settings/telegram/test")
def telegram_test() -> JSONResponse:
    """Send a message to the configured chat, so the user finds out here rather
    than by wondering why nothing arrives later."""
    return JSONResponse(telegrambot.send_test())


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
