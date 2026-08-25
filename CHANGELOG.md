# Changelog

All notable changes to GSO-1 are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Apache-2.0 license, `NOTICE`, and full community docs (`CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `SECURITY.md`).
- `.env.example` documenting every supported configuration variable.
- `/api/apps` now reports the host's home directory so the UI can abbreviate
  paths correctly on any machine.

### Fixed
- Removed hardcoded developer paths from the dashboard and the Ops Room plan —
  path abbreviation now works for any user on macOS and Linux.

## [0.1.0] — 2026-08-23

The first release worth cutting: a dashboard that finds every app in your
projects folder and runs it.

### Added

**Registry & launcher**
- Scans every top-level directory across one or more project roots.
- Start / stop any app in its own process group, with logs streaming to
  `data/logs/<app>.log`.
- Health tracking — probes the app's port and reports healthy, starting,
  crashed, or stopped.
- Port-conflict detection before launch.
- Auto-detected run and setup commands for explicit `run.sh` / `Makefile`,
  Node, Django, Streamlit, FastAPI, Flask, plain Python, Rust, Go, and static
  sites. Per-app overrides live in `data/registry.json` and are editable
  from the UI.
- Git panel per app: branch, dirty file count, ahead/behind, last commit, and
  remote — plus `git pull --ff-only` from the dashboard.
- Descriptions pulled from the README's first paragraph or `package.json`.

**Interface**
- Full UI rebuild against the redesign handoff: design tokens, light and dark
  themes, a sidebar shell, the Library view with a status strip, a Details
  dock, and per-view chrome.
- Ops Room as a dock column with its own overview endpoint.
- Works offline, keyboard-accessible, and honest about failure states.

**Agents & automation**
- Ops Room: a pi-based agent sidecar with an HTTP bridge.
- Claude bridge with an MCP permission server — read-only tools run
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

[Unreleased]: https://github.com/rafsunsheikh/the-manager/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rafsunsheikh/the-manager/releases/tag/v0.1.0
