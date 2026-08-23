# GSO-1 + Ops Room — Runbook

How to start everything, and how to check each piece actually works.
Written 2026-08-23. Companion to `OPS_ROOM_PLAN.md` (the build log).

---

## The pieces

| Piece | What it is | Port |
|---|---|---|
| **GSO-1** | FastAPI app + dashboard. The thing you already had. | 8420 |
| **llama-server** | Local model, owned by GSO-1's `llm.py`. | 8080 |
| **supervisor** | Runs GSO-1 from a versioned release; health-checks and rolls back. | — |
| **Ops Room** | pi-based agent sidecar. One-shot CLI via `./ops`. | — |
| **GSO-1.app** | Electron shell in `~/Applications`. | — |

---

## Starting

### Option A — the desktop app (normal use)

Double-click **GSO-1** in `~/Applications`, or:

```bash
open ~/Applications/GSO-1.app
```

It adopts an already-running GSO-1 if there is one, otherwise starts the
supervisor itself. Closing the window hides to the menu bar; quit from the tray
icon or ⌘Q. Quitting stops only what it started.

### Option B — supervised, no GUI

```bash
cd ~/Projects/the-manager
python -m supervisor start --daemon      # runs var/current, restarts on crash
python -m supervisor status
python -m supervisor stop
```

### Option C — plain, the old way

```bash
cd ~/Projects/the-manager && ./run.sh    # working tree, no supervisor
```

Only one of these at a time — they all want port 8420.

### The local model

Start it from the dashboard's **Local LLM** view, or:

```bash
curl -X POST http://127.0.0.1:8420/api/llm/start \
  -H 'Content-Type: application/json' \
  -d '{"model_path":"'$HOME'/unsloth/GLM-4.7-Flash-GGUF/GLM-4.7-Flash-UD-Q4_K_XL.gguf",
       "ctx":65536,"alias":"glm-4.7-flash"}'
```

**GLM-4.7-Flash @ ctx 65536 is the working configuration.** Ops Room needs the
room: at ctx 8192 it cannot read a file and edit it in the same turn.

---

## Testing each piece

### 1. Dashboard

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8420/api/apps
```

`200`. Note it scans ~270 projects, so it can take several seconds.

### 2. Ops Room — read-only

```bash
cd ~/Projects/the-manager
./ops "which repos have uncommitted changes?"
```

Expect a count and a ranked list (~98 dirty of 250 repos). Roughly 40 s: 12 s of
git, the rest inference. `--verbose` shows tool calls and the active policy.

### 3. Web search

```bash
./ops "search the web for the latest llama.cpp release"
```

Needs `TAVILY_API_KEY` in `.env`. Without it the tool silently disappears from
the list rather than erroring — check with `./ops --verbose "hi"`.

### 4. Sandbox policy — the guards

```bash
./ops "write the text hello into /tmp/should_not_work.txt"   # must be REFUSED
ls /tmp/should_not_work.txt                                   # must not exist
```

Writes only work under `~/Projects/the-manager`, and never in `supervisor/`,
`var/`, or `ops`.

### 5. Human approval

```bash
OPSROOM_ENABLE_BASH=1 ./ops "run the shell command: echo hi"
```

A Telegram prompt appears with Allow/Deny and **blocks until you answer**. No
answer = denied. `bash` is off unless `OPSROOM_ENABLE_BASH=1`.

### 6. The self-build loop

```bash
./ops "Add a tool called ping_info to yourself that reports the hostname.
       Work out which files to change, then build_release and verify_release.
       Do not promote."
```

Takes ~15 min. Then promote (Telegram approval required):

```bash
./ops "promote_release with stamp <the stamp it printed>"
```

**Verify that it refuses a broken build too** — that matters more than the
happy path:

```bash
printf '\nexport const X: = ;\n' >> opsroom/src/tools.ts     # deliberate break
python -m supervisor release create                          # note the stamp
python -m supervisor verify <stamp>; echo "exit=$?"          # must be 1
git checkout opsroom/src/tools.ts                            # undo
```

### 7. Releases

```bash
python -m supervisor release list
python -m supervisor prune --keep 5 --dry-run
python -m supervisor rollback          # back to previous
```

Never delete releases by hand with `ls -t` — `prune` protects
`current`/`previous`, hand-deletion does not.

### 8. Scheduler

```bash
curl -s http://127.0.0.1:8420/api/schedule | python3 -m json.tool
curl -X POST 'http://127.0.0.1:8420/api/schedule/git-sweep/run?notify=false'
```

`notify=false` runs it without messaging Telegram. Defaults: git sweep 09:00,
disk report 09:05, health digest every 6 h (disabled). Edit
`data/schedule.json`; changes are picked up within 30 s, no restart.

To prove unattended firing, set a job's `daily_at` a minute ahead and wait.

---

## Gotchas

- **A running GSO-1 does not pick up code changes.** Restart it, or build and
  promote a release. An instance started before a change simply does not have it.
- **`./ops` runs `var/current/opsroom`, not your working tree.** That is
  deliberate: it means a broken edit cannot kill the running agent. Use
  `./ops --dev` to run the working tree while iterating on the sidecar.
- **Ops Room does not own llama-server.** GSO-1's `llm.py` does. If you swap
  models, Ops Room now fails loudly on mismatch instead of silently talking to
  whatever is loaded.
- **Secrets live in `.env`** (gitignored, excluded from releases):
  `TAVILY_API_KEY`, `MANAGER_APPROVAL_CHAT`.
- **Only one thing on 8420.** The desktop app adopts an existing server rather
  than fighting it.

## When something is wrong

```bash
tail -f var/supervisor.log        # supervisor + child lifecycle
tail -f var/desktop.log           # Electron
tail -f data/logs/<app>.log       # a managed app
python -m supervisor status       # release, health, dangling-link warnings
cd opsroom && node src/selfcheck.ts   # sidecar integrity + behavioural checks
```

`status` warns when `current`/`previous` point at a release that no longer
exists — worth checking before relying on `rollback`.
