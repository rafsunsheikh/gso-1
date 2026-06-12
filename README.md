# 🗂️ The Manager

A local application registry & launcher. The Manager scans your `~/Projects`
folder, lists every app, and lets you **start/stop**, **check status &
health**, **inspect git**, **update (git pull)**, and **read the description**
of any app — from one dashboard, with a click.

![type: localhost tool](https://img.shields.io/badge/runs-localhost-blue)

## Quick start

```bash
cd ~/Projects/the-manager
./run.sh
```

This creates a virtualenv, installs dependencies, and opens the dashboard at
**http://127.0.0.1:8420**.

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m thecmanager
```

## What it does

| Feature | How |
| --- | --- |
| **List all apps** | Scans every top-level dir in `~/Projects`. |
| **Start / Stop** | One click. Each app runs in its own process group; logs stream to `data/logs/<app>.log`. |
| **Status & Health** | Tracks the process and probes the app's port (healthy / starting / crashed / stopped). |
| **Git status** | Branch, dirty file count, ahead/behind, last commit, remote. |
| **Update** | `git pull --ff-only` from the dashboard. |
| **Description** | Pulled from README first paragraph or `package.json`. |
| **Auto-detect** | Guesses run command for explicit `run.sh`/`Makefile`, Node, Django, Streamlit, FastAPI, Flask, plain Python, Rust, Go, static sites. |
| **Run setup / install** | Runs the detected setup command (`install.sh`, `npm install`, `pip install`) as a tracked job with live log tailing. |
| **Per-app override** | Edit the start command / port; saved in `data/registry.json`. |
| **Favourites** | Star apps; the home view shows favourites, "All Apps" shows everything. |
| **Edit with VSCode** | Open a project in a new VS Code window (or focus it if already open). |
| **Opened in VSCode** | Nav view listing folders open in VS Code windows; click to focus the window. |
| **Local LLM** | Nav view to start/stop a llama.cpp `llama-server`, pick a GGUF model and set ctx / GPU layers / port; detects servers it didn't start. Includes a live **System load** panel (CPU / GPU / RAM, with the LLM process broken out and a top-processes list) via native macOS tools — no extra deps. |
| **Planner** | Nav view with kanban boards (To Do / In Progress / Done), draggable task cards with notes, priority, due date, and optional links to registry apps. Saved in `data/planner.json`. |
| **Chat** | Stubbed — planned next pass (reads README + code + git, answers via Claude API). |

## Configuration

Environment variables (all optional):

- `MANAGER_PROJECTS_DIR` — folder to scan (default `~/Projects`)
- `MANAGER_HOST` — bind host (default `127.0.0.1`)
- `MANAGER_PORT` — port (default `8420`)

Per-app overrides live in `data/registry.json` and can be edited from the UI
(✎ Edit start command / port) or by hand.

## Layout

```
thecmanager/
  __main__.py    # entry point (python -m thecmanager)
  server.py      # FastAPI routes
  scanner.py     # list apps, descriptions, effective config
  detector.py    # auto-detect run command / type
  registry.py    # per-app overrides (registry.json)
  runner.py      # start/stop process management + logs (apps + setup jobs)
  git_ops.py     # git status / pull
  health.py      # process + port health checks
  vscode.py      # open/focus projects + detect open VS Code windows
  llm.py         # llama.cpp server control (start/stop/status/models)
  sysmon.py      # CPU/GPU/RAM monitor via top + ioreg (macOS, no deps)
  planner.py     # kanban boards + tasks (planner.json)
  static/index.html   # single-page dashboard
```

## API (for scripting)

- `GET  /api/apps` — list
- `GET  /api/apps/{name}` — full detail
- `POST /api/apps/{name}/start` · `/stop`
- `GET  /api/apps/{name}/status` · `/health` · `/git` · `/logs` · `/description`
- `POST /api/apps/{name}/update`
- `PUT  /api/apps/{name}/config` — `{start_command, port, setup_command, description}`
- `POST /api/apps/{name}/setup` · `GET /api/apps/{name}/setup/status` · `/setup/logs`
- `POST /api/apps/{name}/favourite` — toggle favourite
- `POST /api/apps/{name}/vscode/open` · `/vscode/focus` · `GET /api/vscode/folders`
- `GET  /api/llm/status` · `/llm/models` · `/llm/metrics` · `POST /api/llm/start` · `/llm/stop` · `GET /api/llm/logs`
- `GET  /api/planner` · `POST /api/planner/boards` · `PUT|DELETE /api/planner/boards/{id}`
- `POST /api/planner/boards/{id}/tasks` · `PUT|DELETE /api/planner/boards/{id}/tasks/{task_id}`
```
```
