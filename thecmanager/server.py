"""FastAPI app exposing the registry, launcher, git, health and config APIs."""
from __future__ import annotations

import atexit
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, git_ops, health, runner, scanner

app = FastAPI(title="The Manager", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"

atexit.register(runner.stop_all)


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
    apps = []
    for name in scanner.list_app_names():
        cfg = scanner.effective_config(name)
        apps.append(
            {
                "name": name,
                "type": cfg["type"],
                "language": cfg["language"],
                "running": name in running,
                "has_start_command": bool(cfg["start_command"]),
                "port": cfg["port"],
                "favourite": cfg["favourite"],
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
