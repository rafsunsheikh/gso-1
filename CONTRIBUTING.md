# Contributing to GSO-1

Thanks for wanting to help. GSO-1 is a localhost tool that starts, stops, and
watches the projects already on your disk — so the fastest way to contribute is
to run it against your own `~/Projects` and fix whatever annoys you.

- [Code of Conduct](#code-of-conduct)
- [Ways to contribute](#ways-to-contribute)
- [Development setup](#development-setup)
- [Project layout](#project-layout)
- [Making a change](#making-a-change)
- [Commit style](#commit-style)
- [Pull requests](#pull-requests)
- [Adding a project-type detector](#adding-a-project-type-detector)
- [Security](#security)

## Code of Conduct

This project ships a [Code of Conduct](CODE_OF_CONDUCT.md). By participating you
agree to uphold it.

## Ways to contribute

| | |
|---|---|
| 🐛 **Bug reports** | Something started, crashed, or reported the wrong health state. Include your OS, the app type, and the relevant lines from the log. |
| 🔍 **Detectors** | GSO-1 guesses how to run a project. If it guessed wrong for your stack, that is a great first PR — see [below](#adding-a-project-type-detector). |
| 📖 **Docs** | If a step in the README did not work on your machine, that is a bug in the docs. |
| ✨ **Features** | Open an issue first for anything that changes the UI or adds a background process. |

## Development setup

**Requirements:** Python 3.11+, Node 20+ (only for the desktop shell), git.

```bash
git clone https://github.com/rafsunsheikh/gso-1.git
cd gso-1
cp .env.example .env      # then edit MANAGER_PROJECTS_DIRS to point at your code
./run.sh
```

`run.sh` creates `.venv`, installs `requirements.txt`, and serves the dashboard
on <http://127.0.0.1:8420>.

To run the desktop shell against that server:

```bash
cd desktop && npm install && npm start
```

**Do not commit `.env`.** It is gitignored, and releases deliberately ship
without one so secrets stay in exactly one place.

## Project layout

| Path | What lives there |
|---|---|
| `thecmanager/` | The FastAPI app — the whole dashboard backend |
| `thecmanager/scanner.py` | Walks the project roots and finds apps |
| `thecmanager/detector.py` | Guesses the run/setup command per project type |
| `thecmanager/runner.py` | Process lifecycle — start, stop, process groups, logs |
| `thecmanager/health.py` | Port probing and healthy/starting/crashed state |
| `thecmanager/git_ops.py` | Branch, dirty count, ahead/behind, `git pull --ff-only` |
| `thecmanager/static/` | The single-page dashboard UI |
| `supervisor/` | Release snapshots, promotion, and the restart-safe parent process |
| `desktop/` | Electron shell — owns the window and the child lifecycle, nothing else |
| `opsroom/` | The agent sidecar |
| `scripts/` | Host setup helpers (launchd, GPU limit, mobile) |

## Making a change

1. **Open an issue first** for features and anything user-visible. Bug fixes and
   docs can go straight to a PR.
2. Branch from `main`:
   ```bash
   git checkout -b feat/short-description
   ```
3. Make the change. Match the surrounding style — the codebase leans on plain
   stdlib Python, type hints on function signatures, and docstrings that explain
   *why* rather than restating the code.
4. **Test it against real projects.** GSO-1 has few automated tests; the real
   test is that it correctly detects, starts, stops, and reports health for the
   apps in your own projects folder. Say in the PR what you ran it against.
5. Check nothing leaked:
   ```bash
   git diff --cached | grep -inE 'sk-|ghp_|/Users/|/home/[a-z]' || echo clean
   ```

## Commit style

[Conventional Commits](https://www.conventionalcommits.org/), matching the
existing history:

```
feat(ui): title bar, real identity, and the Ops Room band that was missing
fix(mobile): the supervisor now reads .env, so MANAGER_HOST actually applies
chore: ignore the runtime event log
docs: record the UI redesign phases
```

Common scopes: `ui`, `mobile`, `build`, `ci`, `desktop`, `opsroom`, `supervisor`.
Write the subject as what the change *does*, not what you did.

## Pull requests

- One logical change per PR. A refactor and a feature are two PRs.
- Fill in the PR template — especially **what you tested it against**.
- Screenshots or a short clip for anything that changes the UI.
- Keep `README.md` in sync when you change setup, config, or behavior.
- CI must be green.

## Adding a project-type detector

`thecmanager/detector.py` maps a directory to a run command. Adding support for
a new stack usually means one function and one entry.

1. Add a check that recognizes the project (a marker file — `Gemfile`,
   `go.mod`, `deno.json`).
2. Return the run command **and** the setup command, if there is one.
3. Return the default port the stack usually binds, so health probing works.
4. Explicit wins: a project's own `run.sh` or `Makefile` must always take
   precedence over a guess.
5. Test it on a real project of that type and say so in the PR.

## Security

Do not open a public issue for a security problem. See [SECURITY.md](SECURITY.md)
for private reporting.
