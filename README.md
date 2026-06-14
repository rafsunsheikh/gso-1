# 🗂️ GSO-1

A local application registry & launcher. GSO-1 scans your `~/Projects`
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
| **Telegram remote** | Control GSO-1 from your phone, anywhere — a built-in bot (long-polling, no port-forwarding/tunnel needed). Commands: `/apps`, `/run`, `/stop`, `/status`, `/git`, `/update`, `/logs`, `/llm`, `/sys`, `/restart`, `/tasks`, `/tasks <board>`, `/move <n> <col>`. Locked to your chat id. |
| **Claude Code over Telegram** | `/claude <project>` attaches a Claude Code session to a project; your messages become turns, replies stream back. Read-only tools auto-run; edits/commands send an **Allow/Deny** button to Telegram and Claude waits for your tap. Multi-turn via `--resume`. `/end` closes. |
| **Chat** | Stubbed — planned next pass (reads README + code + git, answers via Claude API). |

## Configuration

Environment variables (all optional):

- `MANAGER_PROJECTS_DIR` — folder to scan (default `~/Projects`)
- `MANAGER_HOST` — bind host (default `127.0.0.1`)
- `MANAGER_PORT` — port (default `8420`)
- `MANAGER_TELEGRAM_TOKEN` — bot token from @BotFather (enables the Telegram bridge)
- `MANAGER_TELEGRAM_ALLOWED` — comma-separated Telegram chat id(s) allowed to control it
- `LLAMA_SERVER_BIN` / `MANAGER_MODEL_DIRS` — override llama.cpp binary / model search dirs

### Control it from your phone (Telegram)

1. In Telegram, message **@BotFather** → `/newbot` → copy the **token**.
2. `export MANAGER_TELEGRAM_TOKEN="<token>"` and restart GSO-1 (or set it in `~/run_manager.sh`).
3. Open your new bot and send any message — it replies with **your chat id**.
4. `export MANAGER_TELEGRAM_ALLOWED="<that id>"` and restart. Now only you can control it.
5. Send `/help` for the command list.

Works from anywhere with no port-forwarding, tunnel, or public IP — the bot
long-polls Telegram's servers (outbound only) from behind your home network.

### Drive Claude Code from Telegram

1. `/claude <project>` — **continues the project's most recent session** (e.g.
   the one from your VS Code terminal), so you can pick up where you left off.
   Use `/claude <project> new` to start a **fresh** session instead.
   Add `cloud` (default) or `local` to choose the model backend — e.g.
   `/claude <project> local`. **cloud** strips `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY`
   so Claude uses your subscription; **local** points Claude at GSO-1's built-in
   Anthropic→llama proxy (`/v1/messages`, in `llmproxy.py`) so the whole session
   runs on your local `llama-server` — start it first from the Local LLM tab.
   (Override the local endpoint with `MANAGER_LOCAL_ANTHROPIC_BASE_URL` to use your
   own proxy like LiteLLM / claude-code-router instead.) Local tool-use reliability
   depends on the model — run llama-server with `--jinja` and a tool-capable model.
   (Only continue a session that's idle/closed in VS Code — don't drive the same
   session from two places at once.)
2. Send normal messages (no slash) — each becomes a turn; Claude works in that
   project's directory and streams its reply back to you.
3. When Claude wants to **edit a file, run a command, etc.**, you get an
   **✅ Allow / ❌ Deny** button. Tap it; Claude continues or stops. Read-only
   tools (read/search) run automatically.
4. The conversation continues across messages (`--resume`). `/clear` starts a
   fresh conversation (same project), `/context` shows token/context-window
   usage, and `/end` closes the session.

Requires the `claude` CLI installed and signed in on the host. Permission
requests are routed through a small MCP server (`claude_perm_mcp.py`) that
calls back into GSO-1, which sends you the Telegram buttons.

Each session keeps **one long-lived `claude` process** alive (streaming
stdin/stdout), so follow-up turns reuse the already-loaded context (cached) —
much cheaper and faster than re-loading per message. Each turn's footer shows
`✓ done · <seconds> · <output tokens>` (no dollar figure; on a Claude
subscription there's no per-token charge anyway). `/end` shuts the process down.

Per-app overrides live in `data/registry.json` and can be edited from the UI
(✎ Edit start command / port) or by hand.

### Restart remotely & keep it running (launchd)

After updating GSO-1's own code from your phone, you need to restart the
process to load it — but the Telegram bot lives *inside* that process. Two
pieces solve this:

**`/restart` command.** Sending `/restart` re-execs the process in place
(`os.execv`) — same PID, current environment (incl. your Telegram token)
preserved, updated code loaded. It comes back in a few seconds and resumes
polling. Any in-flight Claude turn is dropped (just re-send it); the detached
`llama-server` survives. Works whether or not launchd is managing GSO-1.

**launchd supervisor (optional).** Run GSO-1 under a macOS LaunchAgent so it
**starts on login** and **auto-restarts if it crashes** while you're away. A
sample plist lives at `~/Library/LaunchAgents/com.gso1.manager.plist`; it runs
`~/run_manager.sh` (which sets the Telegram env), with `RunAtLoad` and
`KeepAlive`/`SuccessfulExit=false` (restart on crash, but a deliberate stop
stays stopped), and `MANAGER_NO_BROWSER=1` so it doesn't pop a browser on each
(re)start.

Activate it (one-time, at the computer — this replaces the running instance):

```bash
kill "$(lsof -nP -iTCP:8420 -sTCP:LISTEN -t)"            # stop the current instance
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gso1.manager.plist
launchctl print gui/$(id -u)/com.gso1.manager | grep -E "state|pid"   # verify
```

Manage it:

| Goal | Command |
| --- | --- |
| Force restart | `launchctl kickstart -k gui/$(id -u)/com.gso1.manager` |
| Stop now (until next login) | `launchctl bootout gui/$(id -u)/com.gso1.manager` |
| Stop & keep off across reboots | `launchctl disable gui/$(id -u)/com.gso1.manager` then `bootout` (re-enable with `launchctl enable …` + `bootstrap …`) |
| Remove launchd entirely | `launchctl bootout …` then `rm ~/Library/LaunchAgents/com.gso1.manager.plist` |
| Keep login-start, drop crash-restart | set `KeepAlive` → `<false/>` in the plist, then `bootout` + `bootstrap` |

Removing launchd doesn't affect `/restart` — that re-execs the process
directly and works on its own.

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
  telegrambot.py # Telegram long-polling bot — remote control from your phone
  claudebridge.py    # drives Claude Code headlessly per project + approval registry
  claude_perm_mcp.py # standalone MCP server routing tool-permission asks to Telegram
  llmproxy.py        # Anthropic /v1/messages -> local llama-server (for `local` mode)
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
