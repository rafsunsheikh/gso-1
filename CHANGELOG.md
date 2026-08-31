# Changelog

All notable changes to GSO-1 are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.5] — 2026-08-31

### Added
- **The Ops Room can run on Claude.** Its model is now a choice between the
  local llama.cpp server and Anthropic's models, made in Settings and stored
  with the rest of your configuration. Everything else is unchanged on purpose:
  the same tools, the same sandbox, the same build-verify-promote sequence. The
  guarantee that matters, that the agent cannot touch the supervisor which rolls
  back its mistakes, comes from the path policy rather than from which model is
  answering, so it holds either way.
- **Sign in from the app.** Connecting Claude runs the OAuth flow from
  Settings, opens the authorisation page for you and stores the token in
  GSO-1's data folder, owner-readable only. Tokens refresh under a lock, so two
  Ops Room runs waking at once cannot race each other's rotation.
- **GSO-1 can tell you what a repo is.** Half a typical library detects as
  `unknown`, with no type, no language and often no README: the folder name is
  everything you get. Open a repo and GSO-1 reads it, asks your local model
  what it is for, and shows a headline, what it does, its stack, where to start
  and anything worth knowing before you open it. Summaries follow a fixed
  schema, constrained at sampling time, so they cannot drift into whatever
  shape the model felt like.
- **Facts are measured, not generated.** Tests, CI, lockfile, licence,
  contributors, file count and last-commit age are counted by GSO-1 and shown
  beneath the model's text. They are correct even when no model has ever run,
  and the card renders them either way rather than waiting on one.
- **A model recommended from your own memory.** GSO-1 works out which model
  suits the machine it is on, prefers one already downloaded when the size is
  comparable, warns when the machine is too busy to load it right now, and
  estimates a full pass from speed measured locally rather than a number
  carried over from a developer's laptop.
- **Summarise every repo in one run**, serially, resumably and cancellably,
  most-recently-touched first so the repos you are working on are described in
  the first minute. Summaries cache per commit, so it is paid once per repo per
  change, and a commit that touches nothing the model was shown re-stamps
  rather than regenerates.
- **Finish a piece of work without leaving GSO-1.** A repo's drawer now shows
  the branch it is on, exactly which files are uncommitted, and how many
  commits are unpushed or unpulled, with **Commit**, **Commit & push**,
  **Push** and **Pull** beside them. Git's own output is printed verbatim when
  something refuses, because that text is the reason.
- **The branches of a repo, in the drawer.** Local and remote-only branches,
  newest first, with the current one pinned to the top. Picking a remote-only
  one creates the local tracking branch for it.
- **Unpushed and unpulled counts in the Library list**, next to the branch, so
  a repo that needs attention says so without being opened.

### Fixed
- **System load disagreed with Activity Monitor, and the app was the one that
  was wrong.** Two separate causes. Memory was divided by 1,000,000,000 while
  macOS labels binary units as "GB", so the same 34,359,738,368 bytes read as
  "34.4 GB" here and "32.00 GB" there. And "used" came from `top`, which counts
  the file cache as used, where Activity Monitor lists cached files separately
  because the kernel reclaims them the instant anything needs the space. GSO-1
  now reports app, wired, compressed, cached and available the way Apple does,
  and shows swap, which is the honest sign a machine is over-committed.
- **The model recommendation warned about the wrong number.** It read free
  memory, which on a healthy Mac is near zero by design, and told you 0.2 GB
  was left when 8.8 GB was genuinely available. It reads available memory now.
- **An update could arrive half-applied: new markup wearing the previous
  release's stylesheet.** Static assets were served with an etag but no
  `Cache-Control`, so browsers cached them heuristically and reused a
  stylesheet for hours without revalidating. Asset URLs now carry the file's
  own modification time, so an unchanged file still caches hard while a changed
  one is a URL that cannot be served from cache.
- **Settings showed a permanently disabled Save button** on tabs that have
  nothing to save, which reads like something is broken. Only the tabs made of
  environment fields show it; Project folders and Ops Room apply immediately.
- **Committing from GSO-1 failed on any machine that signs commits.** The app
  is normally started by a LaunchAgent, whose `PATH` is four directories long,
  so git could not find `gpg` and every commit died with "cannot run gpg" while
  the identical commit worked in a terminal. Git now runs with a login-shell
  `PATH`, and with `GIT_TERMINAL_PROMPT=0` so a credential prompt fails
  readably instead of hanging on a terminal that is not there.
- **The first changed file in every repo had its name truncated by one
  character.** `git status --porcelain` marks an unstaged change with a leading
  space, and the output was being stripped before parsing, so ` M .DS_Store`
  became `M .DS_Store` and the path read as `DS_Store`. This also fed the Site
  tab's list of changed files.

## [0.1.4] — 2026-08-26

### Added
- **GSO-1 can get you a local model from nothing.** With llama.cpp missing, the
  Local LLM tab now shows the install command for your package manager and, on
  a machine with Homebrew, offers to run it and streams the output. With no
  `.gguf` files, it offers a short list of tool-capable models sized against
  your actual RAM, resolved against Hugging Face so the sizes are real, and
  downloads one into the folder it already scans. Downloads resume after an
  interruption. Both are explicit button presses behind a confirmation; nothing
  installs or downloads because you opened a tab.
- **The Local LLM tab remembers how each model was started.** Context window,
  reasoning format, sampling, cache type: every flag is saved per model and
  restored when you pick it, so loading a model is one click instead of
  fifteen fields. **Start again** repeats the last launch verbatim.
- **A "Where things live" section on the Local LLM tab** for the
  `llama-server` binary and the folders scanned for `.gguf` files. Changes
  apply to the next scan with no restart.
- **Settings names the file that pinned a value.** A setting locked by an
  exported variable now reads "set in ~/run_manager.sh" rather than "set by
  your environment", because the old wording told you to unset something
  without telling you where it was set.

### Fixed
- **The Ops Room header claimed a model was connected when nothing was
  running.** It read `GLM-4.7 · local` from a hardcoded string, and both status
  dots were painted green unconditionally. It now reports the model that is
  actually loaded, or says so when there is none.
- **The Library search box was partly covered by the filter chips.** The chip
  row bled upward 22px into a 16px gap, and painted over the bottom of the
  input. Search, chips and the column header are now one flush sticky stack, so
  the search box also stays put instead of scrolling away in a long list.
- **The Site tab said "not found at ." when nothing was configured.** Unset and
  set-but-missing are now different messages, and both link to the setting
  instead of asking you to restart.
- **Model folders you never chose were listed as if you had.** The built-in
  guesses are labelled as guesses, and folders that do not exist say so.

### Changed
- **The Ops Room agent follows the running server.** `OPSROOM_LLAMA_URL` and
  `OPSROOM_MODEL` default to the endpoint and model GSO-1 has loaded, instead
  of being two fields to keep in sync with the Local LLM tab by hand. Export
  either one to point the agent somewhere else.

### Removed
- **The Settings → Local LLM tab.** Everything in it moved to the Local LLM
  tab, which now owns the model end to end.
- **The Chat tab.** It duplicated the Ops Room, which is docked on every view,
  talks to llama-server directly rather than through the Anthropic shim, and
  already knows your project roots. Existing `chats.json` is left on disk.
- **The Archived nav item**, a placeholder for a concept that does not exist.

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

[Unreleased]: https://github.com/rafsunsheikh/gso-1/compare/v0.1.5...HEAD
[0.1.5]: https://github.com/rafsunsheikh/gso-1/releases/tag/v0.1.5
[0.1.4]: https://github.com/rafsunsheikh/gso-1/releases/tag/v0.1.4
[0.1.3]: https://github.com/rafsunsheikh/gso-1/releases/tag/v0.1.3
[0.1.2]: https://github.com/rafsunsheikh/gso-1/releases/tag/v0.1.2
[0.1.1]: https://github.com/rafsunsheikh/gso-1/releases/tag/v0.1.1
[0.1.0]: https://github.com/rafsunsheikh/gso-1/releases/tag/v0.1.0
