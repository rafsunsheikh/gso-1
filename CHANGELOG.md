# Changelog

All notable changes to GSO-1 are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.3] — 2026-08-26

### Changed
- **First run always asks which folder holds your projects.** It previously
  adopted `~/Projects` without asking whenever that folder happened to exist,
  which meant a new install could open on a list the user never chose and had
  no obvious way to change. The picker now opens on a suggested folder, so the
  common case is still one click, but it is a suggestion rather than a decision.
  Only an environment override or a saved choice counts as an answer.

## [0.1.2] — 2026-08-26

GSO-1 can now be set up entirely from the application. Nothing in this release
requires a terminal.

### Added
- **Settings**, with five tabs: project folders, Telegram, Local LLM, phone
  access, and the rest. Values are saved to `settings.json` and injected into
  the environment at startup, which is the only way a GUI can configure things
  that are read from `os.environ` at import time. A real environment variable
  still wins and is shown as read-only rather than accepting an edit that would
  never take effect.
- **Add and remove project folders** from the app. The Library could previously
  only ever show the folders chosen at first run; adding another meant setting
  `MANAGER_PROJECTS_DIRS` and restarting, which a packaged install has no
  obvious way to do.
- **A Telegram test button.** Configuring a bot is four steps across two apps
  and the usual failure is silent: a good token with the wrong chat id, and
  nothing ever arrives.
- **Restart from the app**, since saved settings are read at startup. The
  restart re-execs in place and keeps the pid, so a supervisor or LaunchAgent
  watching the process does not see it die.

### Security
- `settings.json` is written `0600`. It holds the bot token, the phone access
  token and any API keys set in Settings, and the mode is applied before the
  rename so there is no instant where a complete file of secrets is
  world-readable.
- Secrets are write-only from the browser: the server reports whether one is
  set, never what it is.

### Documentation
- The macOS install instructions described a route Apple removed in macOS 15.
  Right-click and Open no longer works; the dialog offers only Done and Move to
  Bin. Every instruction now describes Privacy & Security → **Open Anyway**.
- A page for [the Ops Room](https://rafsunsheikh.github.io/gso-1/ops-room.html):
  all ten agent tools, the sandbox model, and how to connect a local model.
- The git and GitHub answer, which is that there is nothing to connect: GSO-1
  uses the `git` already on your machine and stores no credentials of its own.

## [0.1.1] — 2026-08-26

Both of these were found by downloading the v0.1.0 installer and running it,
which is the only test that would have caught either.

### Fixed
- **macOS builds reported themselves as damaged.** electron-builder copies the
  frozen backend in after Electron's own code signature is applied, which
  invalidates it, and with no signing identity nothing put it back. An invalid
  signature is worse than no signature: macOS refuses the app outright rather
  than offering the usual unidentified-developer prompt, and right-clicking to
  Open cannot get past it. Builds are now ad-hoc signed after packing, and the
  hook verifies its own work and fails the build rather than shipping a
  signature that does not validate.
- **Application state was written into Electron's browser profile.** The
  packaged app pointed `MANAGER_DATA_DIR` at Electron's `userData` directory,
  so the registry, event log and job state sat interleaved with Chromium's
  cookies, caches and lock files. State now lives in a `data` subdirectory,
  where clearing a cache cannot take the registry with it.

### Changed
- The install instructions describe what actually happens on macOS: right-click
  and Open rather than double-click, and what "damaged" means if it appears.

## [0.1.0], 2026-08-25

The first public release: a dashboard that finds every app in your projects
folder and runs it, on your machine, not just on the one it was written on.

### Packaging & distribution
- Installers for macOS (`.dmg`, Apple Silicon and Intel), Windows (NSIS), and
  Linux (AppImage, `.deb`). The Python backend is frozen with PyInstaller and
  bundled, so **no Python is required** to run GSO-1.
- First-run screen: pick the folder holding your projects, browse or type a
  path, add several roots. No config file, no terminal.
- State is stored per platform (`Application Support`, `%APPDATA%`,
  `XDG_DATA_HOME`) when packaged, and beside the code in a source checkout.
- Apache-2.0 license with `NOTICE`, plus `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, and an `.env.example` documenting every
  configuration variable.

### Security
- Remote-auth public paths are matched on a path boundary rather than as a bare
  string prefix. Previously a route named `/models` or `/metrics` would have
  been reachable from the network without a token; no such route existed, but
  the gate was one route name away from opening.

### Fixed, portability
Everything below only ever worked on the original author's machine:
- The dashboard abbreviated paths against a hardcoded home directory; the
  server now reports the real one via `/api/apps`.
- The greeting, sidebar and avatars were hardcoded to one name; they now come
  from the OS account record.
- The Jekyll CMS defaulted to one specific personal repo; `MANAGER_SITE_DIR`
  now has no default and the CMS stays off until it names a real checkout.
- Ops Room's sandbox root, the only directory the agent may write to, 
  defaulted to a fixed `~/Projects/…` path; it is now resolved from the
  sidecar's own install location.
- Ops Room's readable roots named a specific work folder; they now follow
  `MANAGER_PROJECTS_DIRS`.

### Registry & launcher
- Scans every top-level directory across one or more project roots.
- Start / stop any app in its own process group, with logs streaming to
  `data/logs/<app>.log`.
- Health tracking, probes the app's port and reports healthy, starting,
  crashed, or stopped.
- Port-conflict detection before launch.
- Auto-detected run and setup commands for explicit `run.sh` / `Makefile`,
  Node, Django, Streamlit, FastAPI, Flask, plain Python, Rust, Go, and static
  sites. Per-app overrides live in `data/registry.json` and are editable
  from the UI.
- Git panel per app: branch, dirty file count, ahead/behind, last commit, and
  remote, plus `git pull --ff-only` from the dashboard.
- Descriptions pulled from the README's first paragraph or `package.json`.

**Interface**
- Full UI rebuild against the redesign handoff: design tokens, light and dark
  themes, a sidebar shell, the Library view with a status strip, a Details
  dock, and per-view chrome.
- Ops Room as a dock column with its own overview endpoint.
- Works offline, keyboard-accessible, and honest about failure states.

**Agents & automation**
- Ops Room: a pi-based agent sidecar with an HTTP bridge.
- Claude bridge with an MCP permission server, read-only tools run
  automatically, writes and commands require explicit Allow/Deny.
- Anthropic→llama proxy (`/v1/messages`) so agent sessions can run entirely on
  a local `llama-server`.
- Recurring scheduled jobs.
- Telegram approvals and an activity monitor with kanban boards.

**Operations**
- Supervisor with versioned release snapshots, a health gate, and rollback.
- Electron desktop shell that owns the window and child lifecycle, and leaves
  no orphaned processes on quit.
- iPhone companion at `/m`, gated behind a shared token for remote access.
- launchd integration for start-on-login and crash restart.

[Unreleased]: https://github.com/rafsunsheikh/gso-1/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/rafsunsheikh/gso-1/releases/tag/v0.1.3
[0.1.2]: https://github.com/rafsunsheikh/gso-1/releases/tag/v0.1.2
[0.1.1]: https://github.com/rafsunsheikh/gso-1/releases/tag/v0.1.1
[0.1.0]: https://github.com/rafsunsheikh/gso-1/releases/tag/v0.1.0
