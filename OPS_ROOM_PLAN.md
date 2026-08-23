# Ops Room — Build Plan

> A self-extending agent sidecar for GSO-1, built on the pi agent harness.
> This is a living document. Update the **Progress log** at the bottom after every session.

---

## 1. North star

GSO-1 becomes a packaged desktop application (Electron) with an embedded agent
sidecar called **Ops Room**. Ops Room can:

- answer questions and run tasks about the work on this machine (repos, apps, logs, git),
- run scheduled jobs (cleanup, reporting, git sweeps),
- **modify, rebuild, and relaunch the application — including itself** — so the
  product improves incrementally without a full manual dev loop.

The point of the self-build loop is compounding: each session leaves the tool
slightly more capable than the last.

---

## 2. Verified current state

All facts below were checked on **2026-08-22**. Re-verify before relying on them
in a much later session.

### GSO-1 (`~/Projects/the-manager`)

| | |
|---|---|
| Stack | FastAPI + uvicorn (only two deps), Python |
| Entry | `./run.sh` → `python -m thecmanager` → `127.0.0.1:8420` |
| Frontend | single `thecmanager/static/index.html`, 1,427 lines, no build step |
| Size | 78 M |
| API | 40 REST endpoints (see §6) |

Modules: `scanner` `registry` `git_ops` `runner` `health` `detector` `llm`
`llmproxy` `planner` `sysmon` `telegrambot` `vscode` `claudebridge`
`claude_perm_mcp` `chat` `site` `config` `server`

Already solved, **do not rebuild in the agent**:
- repo scan + registry + per-app overrides
- git status / `git pull --ff-only`
- app start/stop with process groups, port health probing, log streaming
- `llama-server` lifecycle: model picker, ctx size, GPU layers, port
- Anthropic-shaped `/v1/messages` shim → llama.cpp
- human Allow/Deny approval flow (`claude_perm_mcp.py`, answered via Telegram)

### pi (`~/Projects/example-assistant/source_codes/pi`)

| | |
|---|---|
| Remote | `github.com/earendil-works/pi.git` |
| Pinned at | `v0.84.2` / `c49906ec7` (2026-08-22) |
| Requires | node ≥ 22.19.0 — host has v24.14.1 ✅ |

Packages: `pi-agent-core` (embeddable Agent + tool calling + state),
`pi-ai` (multi-provider LLM), `pi-coding-agent` (CLI), `pi-tui`.

Built-in tools: **`bash`**, `read`, `write`, `edit`, `edit-diff`, `find`,
`grep`, `ls`. **No web search / fetch — this is the one real gap.**

Two properties that matter architecturally:
- Tools are defined with TypeBox schemas and exported as `AgentTool`.
- Tools expose **pluggable operation interfaces** (e.g. `LsOperations` with
  `exists` / `stat` / `readdir`). We constrain tools by supplying our own
  operations, **not by forking pi**.

pi speaks llama.cpp natively (`/login llama.cpp`, `/llama`) and `pi-ai`
supports any OpenAI-compatible endpoint.

### Host

MacBook Pro M1 Max, 32 GB unified memory, macOS.
`llama-server` at `~/llama.cpp/build/bin/llama-server` (matches `llm.py`).
Local models: `GLM-4.7-Flash` 16 G (**recommended**), `Qwen3.6-35B-A3B` 21 G
(too tight for comfortable KV cache), `DeepSeek-R1-1.5B` 1 G (too weak).

---

## 3. Architecture

```
supervisor            tiny, hand-written, STABLE. Agent may NOT edit.
  │                   starts children, health-checks, swaps versions, rolls back
  ├── gso1            FastAPI + UI          (agent MAY edit)
  ├── opsroom         pi sidecar, Node      (agent MAY edit)
  └── llama-server    owned by gso1 llm.py; both others connect to it
```

**Central invariant: the thing that relaunches must never be the thing being
replaced.** A broken self-edit must leave a working process able to roll back.

Electron shell wraps this later (M5). Renderer keeps talking to
`127.0.0.1:8420` plus a websocket to Ops Room.

### Version layout

```
var/releases/<timestamp>/     built candidate
var/current -> releases/<ts>  symlink the supervisor flips
var/previous -> releases/<ts> rollback target
```

Build to a **new directory**, verify, then flip. Never build in place.

---

## 4. Safety invariants

Non-negotiable. Every milestone must preserve all six.

1. **Supervisor is immutable to the agent.** Its path is deny-listed in the
   agent's file tools. Keep it small and boring.
2. **Every self-edit is a git commit** on a branch. Rollback is `git reset`;
   history is auditable.
3. **Health gate.** A new release must answer `/health` within 30 s or the
   supervisor flips `current` back to `previous` automatically.
4. **Destructive operations are Python functions, not shell strings.** The
   agent decides *when*, `git_ops.py`-style code decides *how*. No open `rm`.
5. **Path allowlist on write/bash.** Implemented via pi's pluggable tool
   operations. Default allow: the `the-manager` checkout and `var/`. Everything
   else — including `~/Projects/*` — is read-only unless explicitly granted.
6. **Human approval for the risky class**, reusing `claude_perm_mcp.py`:
   `bash`, `write`/`edit` outside the sandbox root, and any release flip.

### Standing hazards on this machine

- `data/corpus`'s only copy lives on the Toshiba (exFAT, unjournaled, no backup).
- auudrey's Docker stack is live with 10 data volumes.
- `~/Projects` is 143 G of real work.

A confused cleanup task is a worse outcome than a broken build. Bias toward
read-only and narrow tools.

---

## 5. Milestones

Each milestone ships something runnable. Do not start the next until the
acceptance criteria pass.

### M0 — Skeleton and supervisor
**Deliverable:** `supervisor/` (Python, stdlib only) that starts/stops/health-checks
GSO-1, plus `var/` release layout with `current`/`previous` symlinks.
**Accept:** `supervisor start` boots GSO-1; killing the child auto-restarts it;
`supervisor rollback` flips the symlink and the old version serves again.

### M1 — Ops Room sidecar, read-only
**Deliverable:** Node sidecar embedding `pi-agent-core`, pointed at the running
`llama-server`. Tools: **only** two custom ones wrapping existing GSO-1
endpoints — `list_apps` (`GET /api/apps`) and `git_status` (`GET /api/apps/{name}/git`).
No bash, no write.
**Accept:** ask it "which repos have uncommitted changes?" and get a correct
answer derived from real endpoint data.

### M2 — Constrained file + shell access
**Deliverable:** enable `read`, `grep`, `find`, `ls` unrestricted within an
allowlist; enable `write`/`edit`/`bash` **only** under the sandbox root, via
pi's pluggable tool operations. Wire `claude_perm_mcp.py` for the approval class.
**Accept:** agent edits a file inside the root unattended; an attempt to write
outside it is refused; a `bash` call raises a Telegram Allow/Deny that blocks
until answered.

### M3 — Web search tool
**Deliverable:** one custom tool filling pi's only real gap. Start with a single
provider behind an env var; no scraping stack.
**Accept:** agent answers a question requiring current information, with the
source URL in its reply.

### M4 — The self-build loop ⭐
**Deliverable:** `build_release`, `verify_release`, `promote_release`,
`rollback_release` as agent tools, implemented as Python/Node functions calling
the supervisor. Sequence: branch → edit → build to new dir → typecheck+tests →
boot on a scratch port → `/health` → promote → supervisor restarts child.
**Accept, both required:**
- *"Add a `disk_free` tool to yourself, rebuild, restart"* works end to end and
  the new tool is callable afterwards.
- A **deliberately broken** edit is caught at verify, never promoted, and the
  running version is untouched.

### M5 — Electron shell
**Deliverable:** Electron main spawns supervisor; renderer loads the existing
`index.html` unchanged. Real icon, tray, window chrome, single-click launch.
Bundle Python (PyInstaller or bundled venv).
**Accept:** double-click launches everything with no terminal; quitting stops
all children cleanly.

### M6 — Scheduling
**Deliverable:** scheduled jobs (git sweep, disk/cleanup report, app health
digest) delivered to Telegram. GSO-1 currently has **no scheduler** — add one
(APScheduler or a stdlib thread-timer loop).
**Accept:** a daily git sweep across `~/Projects` posts a digest without manual
triggering.

---

## 6. Interfaces

### GSO-1 endpoints worth exposing as agent tools

```
GET  /api/apps                        list + status
GET  /api/apps/{name}                 detail
GET  /api/apps/{name}/git             branch, dirty, ahead/behind, last commit
GET  /api/apps/{name}/health          health probe
GET  /api/apps/{name}/logs            log tail
POST /api/apps/{name}/start|stop      lifecycle          [approval]
POST /api/apps/{name}/update          git pull --ff-only [approval]
GET  /api/llm/status|models|metrics   local model state
GET  /api/planner                     kanban boards
POST /api/claude/permission/request   existing approval flow — reuse
GET  /api/claude/permission/poll/{aid}
```

Prefer wrapping these over giving the agent bash to rediscover the same facts.

### pi tool contract

```typescript
import type { AgentTool } from "@earendil-works/pi-agent-core";
import { type Static, Type } from "typebox";

const schema = Type.Object({
  name: Type.String({ description: "App name from the registry" }),
});
```

Constrain built-ins by supplying custom operation objects, not by forking.

---

## 7. Risks and open decisions

| Risk | Mitigation |
|---|---|
| Self-edit bricks the app | Supervisor is immutable + health gate + auto-rollback (M0, M4) |
| Agent damages unrelated files | Path allowlist, read-only default, no open `rm` (§4) |
| pi churn — 1,242 commits in ~2 months | Pin `v0.84.2`; upgrade deliberately, never track `main` |
| RAM: Electron + Node + 16 G model on 32 GB | Use GLM-4.7-Flash; skip Docker sandboxing; watch `sysmon` |
| Local model too weak for reliable tool calls | Fall back to a hosted model for the self-build loop specifically |
| Two managers fighting over `llama-server` | `llm.py` owns the lifecycle; Ops Room only connects |

**Open decisions**

1. Is the Electron move for packaging/polish only, or for native capability
   (global hotkeys, notifications, menu bar)? Changes how much the frontend is
   restructured in M5.
2. Sidecar language: Node embedding `pi-agent-core` (assumed) vs shelling out to
   the `pi` CLI. Embedding gives event streaming and custom tools; the CLI is
   less code. **Assumed: embed.**
3. Which model drives the self-build loop — local GLM-4.7-Flash, or hosted for
   reliability on multi-step tool chains?

---

## 8. Progress log

| Date | Milestone | What happened |
|---|---|---|
| 2026-08-22 | — | Plan written. pi pulled to v0.84.2. Premise corrected: pi **does** have bash; only web search is missing. |
| 2026-08-22 | **M0 ✅** | `supervisor/` built (`core.py`, `__main__.py`, stdlib only). All three acceptance criteria pass — see notes below. |
| 2026-08-22 | **M1 ✅** | `opsroom/` pi sidecar on local llama-server; 3 read-only tools; answer cross-checked against raw `git`. |
| 2026-08-22 | **M2 ✅** | Path rings + immutable supervisor + approval-gated bash. Guards verified to actually refuse, not just to allow. |
| 2026-08-22 | **M3 ✅** | `web_search` via Tavily, after surveying 6 agents. Live query returned next-day-fresh data with a cited URL. |
| 2026-08-22 | **§9 1-4 ✅** | GLM @ 64K loaded. Unassisted self-build works. Agent hallucinated an API and the gate missed it — behavioural checks added. |
| 2026-08-23 | **Redesign ✅** | All 7 phases of the Decara handoff: tokens + light/dark, sidebar shell, Ops Room view, Library, docks, view polish, backend gaps. |
| 2026-08-23 | **M6 ✅** | Scheduler in GSO-1 (stdlib, no new deps). Unattended git sweep fired and delivered to Telegram. |
| 2026-08-23 | **M5 ✅** | Electron shell: `.app` bundle, tray, real icon, adopts-or-spawns lifecycle, clean 3 s teardown with no orphans. |
| 2026-08-22 | **M4 ✅** | Self-build loop complete. Both acceptance criteria pass: agent wrote `disk_free` into itself, built, verified, and promoted it through a human gate; a broken build was refused. |

### M0 notes

**Shipped:** `supervisor/core.py` + `supervisor/__main__.py`, stdlib only.
Commands: `release create|list`, `promote`, `verify`, `start [--daemon]`,
`stop`, `status`, `rollback`. `var/` added to `.gitignore`.

**Two design facts discovered during the build — both now handled:**

1. GSO-1 resolves `DATA_DIR = Path(__file__).parent.parent / "data"`, i.e.
   **relative to its own source**. Naively, every release would get its own
   `registry.json` / `planner.json` / logs and state would reset on promote.
   Fixed with a symlink chain: `release/data -> var/data -> <repo>/data`, so
   supervised and manual `./run.sh` runs share one state directory.
   *Follow-up worth doing:* add a `MANAGER_DATA_DIR` env override to
   `config.py` so this stops depending on symlinks.
2. **There is no `/health` route**, and `GET /api/apps` scans 165+ repos, so it
   is far too expensive to poll. Health is therefore TCP-connect + an HTTP GET
   that treats *any* response (404 included) as "serving". Adding a real cheap
   `/health` endpoint is a good early task for Ops Room itself.

**Verified on 2026-08-22** (test ran on port 8421; the live instance on 8420 was
left untouched and confirmed healthy afterwards):

| Criterion | Result |
|---|---|
| `start` boots GSO-1 | ✅ health ok |
| Child crash auto-restarts | ✅ SIGKILL pid 74344 → respawned 74400 |
| `rollback` serves the old release | ✅ B → A, confirmed by the live child's cwd |

`verify <stamp>` also works: boots a release on a scratch port, health-checks,
never promotes. This is the health gate M4 will reuse.

**Not yet done / deferred:** release pruning (releases accumulate in `var/`);
promote does not auto-restart the supervisor (it prints the command instead —
deliberate for now, will become automatic in M4).

### M1 notes

**Shipped:** `opsroom/` — Node sidecar embedding `pi-agent-core`, talking to the
local `llama-server`. Node 24 runs the TypeScript natively, so there is **no
build step** yet (M4 will need one).

```
opsroom/src/model.ts   llama.cpp provider via createProvider + openAICompletionsApi
opsroom/src/tools.ts   list_apps, git_status, git_dirty_sweep
opsroom/src/ask.ts     one-shot CLI:  node src/ask.ts [--verbose] "question"
```

Pinned deps: `@earendil-works/pi-agent-core@0.84.2`, `@earendil-works/pi-ai@0.84.2`,
`typebox@1.3.7` (must match pi's own pin — `^0.34` does not exist).
`node_modules/` added to `.gitignore`.

**Model:** GLM-4.7-Flash-UD-Q4_K_XL, ctx 65536, `q8_0` KV cache, on
`127.0.0.1:8080`. Started **through GSO-1's `/api/llm/start`** so `llm.py`
remains the sole owner of the process. Ops Room only connects; it must never
start or stop llama-server itself.

**Deviation from plan — a third tool was necessary.** The plan specified two
tools. Answering *"which repos have uncommitted changes?"* with only
`list_apps` + `git_status` would need **270 sequential tool calls**, which is
impossible in a 64K window. `/api/apps` carries no git fields, so there was no
bulk source. Added `git_dirty_sweep`: it fans out over all apps in Node at
concurrency 8 and returns only the dirty ones. This follows the plan's own
principle — deterministic code decides *how*, the agent decides *when*.
**Rule going forward: any question spanning many repos needs a bulk tool, not a
loop.**

**Verified 2026-08-22:**

| Check | Result |
|---|---|
| Acceptance question answered | ✅ "98 repositories have uncommitted changes" + top-N list |
| Numbers correct | ✅ spot-checked vs raw `git status`: the-manager 21, example_lab 39, example_press 22 — all exact |
| Sweep cost | 270 apps → 250 repos → 98 dirty in **11.3 s**, ~3,400 tokens |
| Tool executed once, not looped | ✅ instrumented: 1 real execution |
| End-to-end latency | ~41 s (11 s sweep + ~30 s local inference) |

**Three traps hit during the build — worth remembering:**

1. `log()` wrote to stdout, so `$(release create)` captured log lines along with
   the stamp. Log to **stderr**; keep stdout for values.
2. `npm install --silent ... | tail` reported success while installing nothing —
   `$?` was `tail`'s status. **Never read `$?` through a pipe** (this same trap
   also hid a `docker rm` failure earlier in the session).
3. A failed model turn is **not thrown**: it arrives as a normal message with
   `stopReason: "error"`. The first run printed nothing and exited 0, hiding
   `"No API key for provider: llamacpp"`. `ask.ts` now inspects `stopReason` and
   exits non-zero. pi-ai needs an apiKey *or* an auth header even for keyless
   local servers — a placeholder string satisfies it.

**Observations for later:** local inference adds ~30 s per turn on top of tool
time, so M6's scheduled jobs should run detached rather than interactively.
`list_apps` alone is ~5,000 tokens for 270 apps — worth a filter argument before
the tool surface grows.

### M2 notes

**Shipped:**

```
opsroom/src/policy.ts    path rings: readable / writable / immutable
opsroom/src/approval.ts  human Allow-Deny via GSO-1's existing endpoints
opsroom/src/fstools.ts   pi's read/ls/grep/write/edit/bash, constrained
```

Tool surface is now 9: `list_apps` `git_status` `git_dirty_sweep` `read` `ls`
`grep` `write` `edit` (+ `bash` only when opted in). All schemas together cost
~1,300 tokens.

**Policy rings** (override via `OPSROOM_SANDBOX_ROOT`, `OPSROOM_READ_ROOTS`,
`OPSROOM_IMMUTABLE`):

| Ring | Default |
|---|---|
| writable | `~/Projects/the-manager` only |
| readable | the sandbox + `~/Projects` + `~/work` |
| immutable | `supervisor/`, `var/` — never writable (invariant #1) |

Checks resolve symlinks and `..` before comparing, so escape attempts fail
closed. 9/9 unit cases pass, including `sandbox/../evil.txt` and `/etc/passwd`.

**Approval** reuses `claude_perm_mcp.py`'s endpoints and env vars
(`MANAGER_APPROVAL_AUTO|URL|CHAT`), so there is one approval mechanism on this
machine, not two. It **fails closed**: unreachable approver or timeout = denied.

**bash is opt-in** (`OPSROOM_ENABLE_BASH=1`). With bash exposed, the model
answered "write X to <path>" with `echo -n 'X' > <path>` — routing an ordinary
file write through a human approval prompt and defeating the purpose of the
purpose-built tools. Default-off makes the safe path the default path.

**Verified 2026-08-22:**

| Criterion | Result |
|---|---|
| Edit inside sandbox, unattended | ✅ file written, no prompt |
| Write outside sandbox refused | ✅ `/tmp/...` never created |
| Immutable `supervisor/` refused | ✅ `core.py` sha unchanged under a forced attempt |
| bash denied → not run | ✅ no side effect |
| bash allowed → runs | ✅ side effect present |
| bash with no answer | ✅ blocks, then fails closed on timeout |

**The serious bug this milestone: my guards were silently inert.** Every pi tool
factory is `createXTool(cwd, options)`, and I called `createWriteTool({operations})`
— so the options object was passed as `cwd` and **my custom operations were
never installed**. It surfaced only as `normalized.startsWith is not a function`
from deep inside pi. Until fixed, the sole protection was the execute-level
param guard. *Lesson: test that a guard actually REFUSES something, never just
that the happy path works.* Both layers are now verified independently.

**Caveat on model attribution.** Partway through this milestone the operator
deliberately swapped the local `llama-server` from GLM-4.7-Flash @ ctx 65536 to
**Qwen3.6-35B-A3B @ ctx 8192**, to run a separate analysis (confirmed; GSO-1
reports `managed: false` because it was started outside GSO-1).

llama-server serves whichever model is loaded regardless of the `model` field in
the request, so the `glm-4.7-flash` id in `model.ts` is cosmetic — requests
silently hit whatever is loaded. The policy results above are code-level and
model-independent, so they stand. The behavioural observations ("prefers bash",
"asks for permission conversationally") cannot be firmly attributed to one
model; **re-check tool-choice once a model is pinned.**

**Design consequence — port 8080 is contended.** The operator uses the local
llama-server for their own work, so Ops Room cannot assume it owns it, nor that
the model or context size stays constant. Two follow-ups:

1. `model.ts` should **assert the served model matches what it requested**
   (`GET /v1/models`) and fail loudly rather than silently talking to whatever
   is loaded.
2. Ops Room should get **its own llama-server on a separate port** before M4, so
   a self-build loop is never competing with the operator's analysis. At ctx
   8192 Ops Room is already marginal: ~1,300 tokens of tool schemas plus a
   ~3,400-token `git_dirty_sweep` result leaves little room to reason.

### M3 notes

**Survey first.** Before writing anything, read how six agents in
`example-assistant/source_codes` do web search (2026-08-22):

| Agent | Search | Mechanism |
|---|---|---|
| codex | provider-hosted | `web_search.rs` is display formatting only; search runs at OpenAI |
| gemini-cli | provider-hosted | Google grounding → `groundingMetadata` chunks + citation spans |
| qwen-code | **none — removed** | Forked gemini-cli, lost grounding, dropped search rather than reimplement |
| opencode | local | Exa / Parallel MCP; caps 8/20 results, 10k/50k context chars |
| openclaw | local, 9 providers | brave, tavily, exa, perplexity (keyed); duckduckgo (scrape); searxng (self-host) |
| pi | none | the gap |

**Decisive finding: provider-hosted search is unavailable to us.** llama.cpp has
no grounding, so the harness must call a search API itself — the opencode /
openclaw family, not the codex / gemini-cli one. qwen-code is the cautionary
case: same situation, and they removed the feature instead.

**Chosen:** Tavily, search-only (no fetch tool). DuckDuckGo was rejected —
openclaw's reference implementation ships `isBotChallenge()` looking for
recaptcha and "are you a human", which is fatal for M6's unattended jobs.

**Provider pricing, verified 2026-08-22** (an earlier from-memory claim that
Brave had a "~2,000 query/month free tier" was wrong — always check):

| Provider | Free allowance | Card required |
|---|---|---|
| Brave | $5 credit/month ≈ 1,000 requests | yes, billing account |
| **Tavily** | **1,000 credits/month** | **no** |
| Exa | $20 initial (~2,800), then $10/month | unclear |
| SearXNG | unlimited, self-hosted | n/a |

Brave was the first pick but its allowance sits behind a billing account.
Tavily matches the volume with no card and returns pre-digested content, which
suits a small context. **SearXNG on the Ubuntu server remains the better
long-term answer** — no key, no quota, nothing leaving the network — but it is a
container to deploy and maintain, so it is deferred rather than dropped.

**Shipped:** `opsroom/src/websearch.ts` — one `web_search` tool.
Self-disables when `TAVILY_API_KEY` is unset, so the tool list shrinks rather
than the agent hitting a runtime error.

API shape verified against docs.tavily.com, not memory:
`POST https://api.tavily.com/search`, `Authorization: Bearer tvly-…`,
returning `{ results: [{title,url,content,score}], answer?, usage }`.

Three techniques adopted from the survey:

1. **`wrapUntrusted`** (from gemini-cli) — results are wrapped in
   `<untrusted_context>` and the closing tag is escaped, so content cannot break
   out. This matters more here than in gemini-cli: Ops Room can write to its own
   source and, at M4, rebuild and relaunch itself, so a search snippet is a live
   injection path. The system prompt also states that anything inside those tags
   is data, never instructions.
2. **Hard caps** — 5 results default / 10 max, 200-char snippets. Far tighter
   than opencode's 10,000-char default, because our context may be 8192.
3. **Do not trust the provider's limit** — the response array is sliced locally.
   A mock returning 20 results for a `count=5` request initially produced 10;
   now clamped.

**Verified 2026-08-22** (mock Brave server; no live key on this machine yet):

| Check | Result |
|---|---|
| Untrusted wrapper, breakout attempt | ✅ single closing tag, escaped form present |
| HTML stripped from titles/snippets | ✅ |
| Bearer auth + request body | ✅ `Bearer tvly-…`, `max_results: 5`, `include_answer: true` |
| Result cap enforced locally | ✅ mock returned 20, tool emitted 5 |
| Payload size | ✅ 465 tokens (answer + 5 results) |
| 401, 429, 5xx, missing key | ✅ each gives a distinct actionable message |

**Live verification 2026-08-22 — M3 COMPLETE.** Query *"what is the latest
llama.cpp release?"* returned build **b10549 dated 2026-08-21** (the day before)
with the source URL cited — information no training cutoff could supply. The
`<untrusted_context>` wrapper appeared in the real response path, and Tavily's
`include_answer` summary gave the model a digested starting point rather than
five raw snippets.

Getting the key: Tavily's plan chooser pushes Pay-as-you-go and Project, both of
which want a card. The free tier is behind the easily-missed **"Continue on
Free"** link in the bottom-right of that dialog.

**Key persistence is still open.** `export TAVILY_API_KEY=…` lives only in that
shell. Before M6's scheduled jobs — and before the supervisor manages the
sidecar — the key needs a persistent home that is not the repo. `.env` and
`.env.*` are now gitignored for this purpose; the supervisor will need to load
it and pass it through to the child.


### M4 notes

**Shipped:**

```
opsroom/src/buildtools.ts  build_release, verify_release, promote_release,
                           rollback_release, release_status
opsroom/src/selfcheck.ts   sidecar health gate
opsroom/src/env.ts         loads <repo>/.env via node's native loadEnvFile
supervisor/core.py         verify now checks BOTH halves; releases share node_modules
```

The loop: agent edits the **working tree** (its sandbox) → `build_release`
snapshots to `var/releases/<ts>` → `verify_release` → `promote_release`
(human-approved) → supervisor serves the new release. The agent never writes
into `var/`; only the supervisor does, and the supervisor is immutable to the
agent. That is what makes a bad self-edit undoable.

**`verify` now checks both halves.** Booting GSO-1 proves only the Python app.
A syntax error in `opsroom/` would previously have passed verification and
surfaced after promotion. `selfcheck.ts` imports every tool module, asserts the
required tool set, rejects duplicates, and — importantly — **asserts the policy
still refuses `/etc/passwd`**. A guard that only ever allows is indistinguishable
from no guard (see the M2 bug).

**Releases share `node_modules`** by symlink, mirroring the `data` symlink.
*Limitation:* a release whose `package.json` differs from the repo's will run
against the wrong dependency tree. Re-install before building such a release.

**Verified 2026-08-22:**

| Check | Result |
|---|---|
| Supervisor pipeline, healthy tree | ✅ build → verify PASS (both halves), exit 0 |
| Supervisor pipeline, broken sidecar | ✅ verify FAIL (sidecar), exit **1** |
| Agent writes a new tool from scratch | ✅ `disktool.ts` loads and returns real `df` output |
| Agent edits itself and rebuilds | ✅ build + verify PASS, **15 tools incl. `disk_free`** |
| **Acceptance 2** — broken build refused | ✅ app health FAILED, sidecar OK, no promote, live release untouched |
| **Acceptance 1** — promote | ✅ approved on Telegram; `current` → `20260822T112855Z`, live release carries `disk_free` and runs it |
| Approval fail-closed | ✅ no answer → not run, `current` unchanged |

**Two real problems found.**

1. **The running agent edits its own source, so a bad edit kills it outright.**
   The first acceptance-2 attempt broke `tools.ts`, which `ask.ts` imports — the
   agent died before verify could refuse. The live release survived by crash,
   not by the gate. **Fix: run the sidecar from `var/current/opsroom` while it
   edits the working tree**, so the running instance is insulated from
   in-progress edits. Today both are the same directory. This should be done
   before relying on the loop.
2. **ctx 8192 cannot drive this loop.** Two attempts died on *"Context size has
   been exceeded"*, both when the agent tried to `read` a large file before
   editing it. It succeeded only when given exact strings to `edit` without
   reading — i.e. a human compensating. The loop needs GLM at 64K or a hosted
   model. Qwen (21 G) and GLM (16 G) cannot co-reside in 32 GB, so it is one or
   the other.

**Three delivery bugs in the approval path, all fixed.** Chasing "nothing
arrives on my phone" cost most of the milestone and every cause was silent:

1. `MANAGER_APPROVAL_CHAT` was never set, so `chat_id` was empty.
   `claudebridge.create_approval` passes it straight to the Telegram notifier
   and swallows send failures in a bare `except: pass` — a misconfigured chat is
   therefore **indistinguishable from a human ignoring the prompt**. Both
   present as "approval timed out". Now persisted in `.env`, and
   `requireApproval` refuses up front naming the exact variable.
   *GSO-1 should surface notifier failures rather than swallowing them.*
2. The POST timeout was 10s, but GSO-1 sends the Telegram message
   **synchronously inside the request** with its own 15s budget. Measured
   latency: **7,984 ms** — so it worked intermittently. Raised to 45s, and the
   error now distinguishes "could not create the request" from "human did not
   answer".
3. **The health probe produced a false negative.** GSO-1 answers in ~7.8s under
   load (its Telegram long-poll shares the process) against a 3s probe, so
   `status` reported a perfectly healthy app as `down`. In a supervisor that
   **rolls back on failed health, a false negative can revert a good release** —
   the most dangerous of the three. `HEALTH_TIMEOUT` is now 20s (env-tunable),
   `tcp_open` 5s, `wait_healthy` 60s. Re-verified afterwards: healthy release
   still passes (exit 0), broken release still refused (exit 1).

**Also fixed:** appending to `.env` with `>>` merged onto the existing line
because the file had no trailing newline, corrupting `TAVILY_API_KEY` (58 → 90
chars). Repaired and re-verified with a live search. This was the second
no-trailing-newline append to bite in one session — the first was `.gitignore`.
**Use a read-modify-write, not `>>`, for these files.**

**Still outstanding — do before relying on the loop:**

1. **Run the sidecar from `var/current/opsroom`, not the working tree.** Today
   the agent edits the source it is executing, so a bad edit kills it mid-task
   instead of being caught by verify. Proven accidentally: breaking `tools.ts`
   killed `ask.ts` before it could refuse anything.
2. **Give Ops Room a context it can work in.** Two runs died on "Context size
   has been exceeded" at ctx 8192, both when reading a file before editing it.
   The loop only completed because exact edit strings were supplied by hand.
   Needs GLM at 64K or a hosted model; Qwen (21 G) + GLM (16 G) will not
   co-reside in 32 GB.

---

## 9. When the Qwen analysis finishes — switch Ops Room to GLM

The operator will stop the Qwen server; Ops Room then gets the context it needs.
Run these in order.

**1. Load GLM-4.7-Flash at 64K** (through GSO-1, so `llm.py` stays the owner —
never start llama-server behind its back):

```bash
curl -s -X POST http://127.0.0.1:8420/api/llm/start \
  -H 'Content-Type: application/json' \
  -d '{"model_path":"/Users/user/unsloth/GLM-4.7-Flash-GGUF/GLM-4.7-Flash-UD-Q4_K_XL.gguf",
       "ctx":65536,"alias":"glm-4.7-flash"}'
```

Takes ~30 s to load. Confirm with `GET /api/llm/status` → `ctx: 65536`.

**2. Re-run the self-build loop without hand-holding.** The M4 pass only
completed because exact `edit` strings were supplied by hand; at 8192 the agent
died whenever it read a file first. The honest test is a single instruction with
no pre-computed strings:

> "Add a tool called `uptime_info` to yourself that reports system uptime, then
> build, verify, and promote it."

If it can read `buildtools.ts`, work out the edits itself, and drive the loop
unaided, the self-build story is real. If it still cannot, the local model is
not the right driver and a hosted model should take the build loop while GLM
keeps everyday queries.

**3. Re-check the tool-choice observations.** "Prefers `bash` over `write`" and
"asks for permission conversationally" were recorded while the served model was
ambiguous (see the M2 caveat). Re-measure with a known model before treating
them as facts about GLM. `OPSROOM_ENABLE_BASH=1` to test.

**4. Make `model.ts` assert the served model.** It currently requests
`glm-4.7-flash` and silently accepts whatever llama-server has loaded — which is
how the Qwen swap went unnoticed mid-milestone. Query `GET /v1/models` on
startup and fail loudly on mismatch.

**5. Then the two M4 prerequisites**, in this order:
   - run the sidecar from `var/current/opsroom` rather than the working tree
   - only after that, trust the loop unattended

---

## 10. GLM switchover results (2026-08-22)

**Step 1 ✅** GLM-4.7-Flash @ ctx 65536, healthy in ~5 s.

**Step 2 ✅ — the unassisted loop works.** Given one instruction with no
pre-computed strings — *"Add a tool called uptime_info… work out for yourself
which files to create and edit, then build and verify"* — the agent ran
`read ×3 → write → edit ×3 → build_release → verify_release: PASSED`.
**Context was the blocker, not the model.** At 8192 it died reading any file; at
65536 it reads, reasons, and drives the whole sequence. Slower (~15 min for the
full loop) but unaided.

**Step 4 ✅** `model.ts` now calls `GET /v1/models` and refuses to run on a
mismatch, naming both the configured and the served model. The silent swap that
went unnoticed for hours can no longer happen.

### The important finding: a passing gate is not a working tool

The tool the agent wrote was well-structured — helper functions, human-readable
formatting, typed schema, sensible docs — and **wrong**. It fetched
`http://127.0.0.1:8420/api/system/uptime`, a route that does not exist, and its
own `catch { return 0 }` swallowed the 404:

```
machine uptime : 1 day 3 hours
tool reported  : {"uptime_seconds": 0, "uptime_human": "0s"}
selfcheck      : OK: 16 tools
verify_release : PASSED
```

The gate proved the module *loads*. It never proved it *works*. **A broken build
is easy to catch; a build that runs perfectly and lies is not** — and the second
is the failure mode a self-modifying system actually produces, because the code
it writes is fluent.

### Fix — `opsroom/src/smoke.ts`, wired into `selfcheck`

Two independent detectors:

1. **Endpoint validation.** Every quoted GSO-1 URL in the sidecar's source must
   resolve to a real route; a 404 fails the build. Comments are stripped first —
   the first version flagged a docstring *describing* the bug as the bug.
2. **Behavioural smoke tests.** Side-effect-free, parameterless tools are
   actually executed. A throw, empty content, or an all-zero numeric payload
   fails. `SMOKE_SAFE` deliberately excludes anything destructive
   (build/promote/rollback) or costly (`web_search`).

Verified: the release that previously PASSED is now **rejected** (`verify` exit
1); a deliberately planted fake endpoint is caught; a comment mentioning a fake
URL is not; and the corrected tool — using `os.uptime()`, no HTTP at all — now
reports `1d 3h 48m 46s` against a true `1d 3h`, and passes.

**Generalisable rule for future tools: prefer a local API over an HTTP call the
agent might invent.** `os.uptime()` needed no endpoint, no error handling, and
had nothing to hallucinate.

**§9 step 5 ✅ — the sidecar now runs from the promoted release.**

`./ops` at the repo root is the entry point. It runs
`var/current/opsroom/src/ask.ts` while the agent edits `opsroom/` in the working
tree, so **an in-progress edit can no longer touch the executing code**.
`./ops --dev` runs the working tree deliberately, for iterating on the sidecar
itself. The launcher is in `IMMUTABLE` for the same reason as the supervisor: if
the agent breaks what starts it, nothing starts.

Verified:

| Check | Result |
|---|---|
| `./ops` executes the release, not the worktree | ✅ |
| Worktree deliberately broken → `./ops` still answers | ✅ *(previously this killed the agent)* |
| …and `verify` refuses the broken tree | ✅ exit 1 |
| Agent writes reach the worktree | ✅ |
| …and do **not** appear in the running release | ✅ insulated |
| From inside the release: worktree writable, own code / `ops` / `supervisor` refused | ✅ |

**Secret leak found and fixed.** `.env` was not in `EXCLUDE`, so every release
built after the key was added contained a copy of `TAVILY_API_KEY` — five
snapshots on disk. `.env`/`.env.*` are now excluded, the existing copies were
scrubbed, and the launcher passes `OPSROOM_ENV_FILE` so a release reads the
canonical file instead of carrying its own.

**Operator error worth recording:** cleaning up a "test release" with
`rm -rf var/releases/$(ls -t | head -1)` deleted the **currently promoted**
release, because the run under test had produced no new one. `var/current` was
left dangling. Recovery was one rebuild-and-promote — releases are derived
artifacts, not state — but the lesson stands: **delete releases by exact stamp,
never by recency.**

**Fixed — `supervisor prune`.** The footgun is now gone:

```
python -m supervisor prune [--keep N] [--dry-run]     # default keep 5
```

`current` and `previous` are protected unconditionally — verified by running
`--keep 1`, which still refused to list either. `status` and `prune` also warn
when `current`/`previous` point at a release that no longer exists, which is how
the dangling `previous` left by the incident was spotted and repaired (it now
points at `20260822T112855Z`, the last known-good promoted release, so rollback
works again). First real run removed 3 stale releases, 1.7 MiB; 5 remain.

---

## 11. M5 — Electron shell (2026-08-23)

**Shipped:**

```
desktop/main.js            main process: lifecycle, window, tray
desktop/assets/            icon.png (1024), icon.icns, trayTemplate.png
~/Applications/GSO-1.app   double-clickable launcher (1.2 MB)
```

Electron **43.4.1**. My first attempt pinned `^34` from memory: nine majors stale
and carrying a high-severity *ASAR Integrity Bypass* advisory. Upgrading to
current cleared it — `npm audit` now reports 0 vulnerabilities. Electron bundles
Chromium, so a stale pin means a stale browser engine; check, do not recall.

**The renderer is unchanged.** It loads `http://127.0.0.1:8420` — the same
1,427-line `index.html` that already worked in a browser. No frontend rewrite,
no build step. `nodeIntegration: false`, `contextIsolation: true`, `sandbox:
true`; external links open in the real browser, never inside the shell.

**Adopt-or-spawn.** If something is already serving 8420 (a manual `./run.sh`),
the shell adopts it and records that it does **not** own it — so quitting does
not kill a server the user started. Otherwise it spawns the supervisor in its own
process group. Verified both ways: the adopted pid 922 survived an Electron quit
untouched.

**Closing the window hides to tray; quitting is explicit** (tray menu or Cmd-Q).
Children are torn down on `before-quit`, `will-quit`, `exit`, SIGINT and SIGTERM.

**Verified 2026-08-23:**

| Check | Result |
|---|---|
| `open GSO-1.app` — no terminal | ✅ Electron up in ~4 s |
| Spawns supervisor when port is free | ✅ electron → supervisor → GSO-1 |
| Adopts an existing server instead of fighting for the port | ✅ |
| Quit leaves no orphaned supervisor | ✅ |
| Quit releases the port | ✅ |
| Quit does **not** kill an adopted server | ✅ pid 922 survived, HTTP 200 |
| Release snapshots exclude Electron's node_modules | ✅ release still 1.7 MB |

**A false failure worth recording.** The first teardown test reported
`supervisor: ORPHANED`. It had not orphaned — the supervisor gave its child an
8 s grace period and I polled at 6 s. The log said `signal 15 — shutting down`
then `supervisor stopped` 8 s later. *Read the log before believing the probe.*
Grace is now 3 s (uvicorn stops promptly when it stops at all; a long grace only
delays SIGKILL for a wedged process), so full teardown went **9 s → 3 s**.

**Deliberately not done: full distribution packaging.** No electron-builder, no
bundled Python, no signing or notarisation. The `.app` is a thin launcher that
execs the repo's Electron and venv. For a personal tool on one machine that is
the right trade: it tracks the working copy and stays 1.2 MB instead of freezing
a ~200 MB runtime. Revisit only if this needs to run on another machine.

---

## 12. M6 — Scheduling (2026-08-23)

**Shipped:** `thecmanager/scheduler.py` + 3 endpoints. **Stdlib only** — GSO-1
still has exactly two dependencies (fastapi, uvicorn); a daily report does not
justify a third.

```
GET  /api/schedule                  jobs, last run, due-now
POST /api/schedule/{id}/run         trigger now (?notify=false to test quietly)
POST /api/schedule/{id}/enabled     enable/disable
```

Jobs live in `data/schedule.json`, state in `data/schedule_state.json`. `data/`
is shared across releases by symlink, so **promoting a release never resets the
schedule or re-fires jobs that already ran**. Writes are atomic (tmp + replace).

Defaults: `git-sweep` 09:00, `disk-report` 09:05, `health-digest` every 6h
(disabled).

**Jobs are deterministic Python, not agent prompts.** A git sweep is a fact, not
a judgement, and local inference costs ~30 s per turn — far too slow and too
unreliable to run unattended. The sweep takes 12.4 s and produces the same
numbers Ops Room reached through the LLM (250 repos, 98 dirty). An
`opsroom_prompt` kind exists for jobs that genuinely need judgement; it launches
`./ops` **detached** and reports only that it started, so a slow turn can never
block the scheduler thread.

**Scheduling rules**, unit-tested against a frozen clock (8/8):

| Case | Behaviour |
|---|---|
| Daily slot passed, never run | fires |
| Slot not yet reached | waits |
| Slot >90 min ago | **skips to tomorrow** — no stale report at a random hour |
| Already ran during today's slot | no double-fire |
| Ran yesterday | fires today |
| Interval `30m`, 10 min elapsed / 31 min elapsed | waits / fires |

Each job is wrapped: a failure is logged and recorded, and never kills the
thread.

**Acceptance verified 2026-08-23** — a job was scheduled 75 s out on a live
instance (port 8423) and left alone:

```
FIRED at 2026-08-23T01:14:20, ok=True, no manual trigger
🔍 Git sweep — 250 repos of 270 apps
📝 98 with uncommitted changes: …
```

Telegram was configured on that instance (`authorized_count: 1`), so the digest
was delivered to the authorised chat.

**A test bug worth recording:** the first no-double-fire check failed, but the
code was right — `_mark_run` stamps *real* now (~01:00) while `due()` was being
called with a fake 09:30, so "last run" landed before the slot and firing was
correct. Re-tested by writing the state timestamp explicitly. *When a frozen
clock meets code that reads the real one, the test is usually what is broken.*

---

## 13. UI redesign (2026-08-23)

Implemented the Decara-derived handoff in seven phases, each verified and
committed separately. The handoff assumed Electron + React with shadcn
primitives; this codebase is one vanilla file, so every primitive was built by
hand against CSS custom properties.

| Phase | Shipped |
|---|---|
| 1 | `tokens.css` — full light/dark token table, theme toggle, pre-paint apply |
| 2 | 216px sidebar with four groups, 60px icon rail, content header |
| 3 | Ops Room default view — LIVE / NEEDS YOU / ROUTINES / RECENT |
| 4 | Library — status strip, redesigned cards, one primary action each |
| 5 | Ops Room becomes a 322px dock column instead of an overlay |
| 6 | Details dock, per-view chrome |
| 7 | `overview.py` — live processes, git sweep, failed runs, port conflicts |

**The highest-leverage decision: do not rewrite the markup.** ~1,500 lines of
`slate-*`/`indigo-*` utilities became theme-aware by remapping Tailwind's own
palette to the tokens as channel triplets (`rgb(var(--c-card) / <alpha-value>)`),
which preserves the `/opacity` modifiers. Migrating class by class would have
been days of churn for the same result.

`overview.py` exists because the design needs data `/api/apps` never returned —
pid, uptime, per-repo git state, failed runs, port clashes. Per-repo from the
browser is ~270 round trips; aggregated and cached it is ~3s, refreshed every
45s or on demand.

**Bugs found and fixed during the phases:**

- A retry loop in the first Ops Room cut re-rendered on failure, which
  re-triggered the load, which failed again — an unbounded toast storm. Failure
  now records state and renders a retry panel.
- `body { background: #0c0a17 }` and the scrollbar colours were hard-coded and
  would have defeated light mode entirely.
- White repo names on white cards: `text-white` is used both as a label on
  purple buttons (must stay white) and as emphasis on dark surfaces (must follow
  the theme). The override is scoped to elements not on a coloured background.
- The dock refactor deleted `#close-drawer` and `#drawer-bg` while their
  `onclick` bindings remained, throwing a TypeError on every drawer open.
  Verified afterwards: nine views render with **zero console errors**.

**Deliberately not done.** The ⌘K palette — the handoff states the overlay is
not designed yet, so only its trigger exists (it focuses search). The iPhone
companion is a separate application, not part of the desktop redesign.

**Standing hazard, hit twice.** An orphaned `python -m thecmanager` holding 8420
makes the supervisor's child fail `rc=1` on every restart, forever, while the
stale process keeps serving old code. Both times it looked like broken code.
`supervisor status` should learn to distinguish "my child crashed" from
"someone else owns my port".
### 13b. Second pass — the views the first pass only recoloured (2026-08-23)

The first pass got the shell, the Ops Room view and the Library cards. Reviewing
it against the handoff showed the rest of the app was **the old layout wearing
new colours** — which is exactly what the review said. This pass rebuilt each
remaining screen against the handoff text, one at a time, screenshotting after
every step.

| Screen | Was | Now |
|---|---|---|
| Library | card grid | table: `34px 1fr 92px 112px 148px 100px`, filter chips, checkbox multi-select, bulk bar, skeleton rows, footer with pagination |
| Planner | 3 columns of slate cards | 4 tone-dotted columns (Backlog/Today/In progress/Done), spec cards, dashed `+ Add task` that becomes an input |
| Local LLM | one status box + a long form | live server card with a four-up stat grid, LAST HOUR histogram, models-on-disk rows with state pills and Load/Unload |
| Site | 230px list + editor pane | deploy header card (live pill, unpublished count, Preview/Publish) over a content table with state pills and edited times |
| In VSCode | cards | the Library's row language, one column narrower |
| Chat | slate bubbles | the Ops Room's bubble language at page width |
| Details dock | six stacked `slate-800/40` sections | four spec cards — identity+actions, git, run config with a 34×20 switch, and a failed-run card that hands the problem to Ops Room |
| Ops Room dock | plain text log | user/assistant bubbles, a live Machine card (CPU/RAM from sysmon), pill composer with a teal send button |
| Toasts | stack, bottom-right, two competing implementations | one pill, bottom-centre, 3200ms, with Undo |

**Backend work the design forced.** A design is a specification of what data the
app must have:

- `overview.py` now returns `repo_index` — branch/dirty/ahead/behind for **every**
  repo, not just the dirty fifty, because the table shows a git cell on every row.
- `llmusage.py` is new: the LAST HOUR histogram needs traffic history that
  llama.cpp does not keep, so the `/v1/messages` proxy records per-minute buckets
  and `/api/llm/usage` aggregates them into eight slices. The card shows real
  requests/tokens/errors or honest zeroes — it is never decoration.
- `site.list_items()` returns `permalink` and `modified`, so the content table
  can show a slug and an edited time without a request per row.
- `planner.STATUSES` gained `backlog` for the four-column board.
- Metrics sampling moved out of the Local LLM view: the dock's Machine card is
  visible everywhere, so one sampler runs for the whole session.

**Where the design was adapted rather than copied.** The handoff's Auto-start
toggle has no backend, so that switch drives `favourite`, which does. The
histogram, throughput and resident figures are wired to real sources; a stat
with no source was cut rather than faked. The `Start a server` form has no
artboard — it keeps every llama.cpp flag and adopts the design's field styling.

**Two bugs worth remembering:**

- `.lib-skel-bar` was a bare inline `<span>` with an inline `width` — which does
  nothing. Every loading bar in the git column was invisible, and it read as
  "no data" rather than "loading". `display: inline-block` fixed it.
- Distinguishing *unknown* from *empty* matters: before the overview loads,
  `gitFor()` returns null, and the first cut rendered that as "not a repo" for
  all 270 rows. Loading state and absent state must not share a rendering.

**Screenshots as the verification loop.** Chrome headless hangs on this page —
the polling timers keep virtual time from advancing — so an offscreen Electron
window (`BrowserWindow({show:false, webPreferences:{offscreen:true}})`) loads the
real app, runs a snippet to select the view, and captures with
`capturePage()`. Every table in this section was checked against the artboard
that way before moving on.

### 13c. Shell, identity and the missing band (2026-08-23)

Review after 13b: *"some sections are too compact and no gaps between them; the
icon is still the old one; we don't need the Details button; refresh and search
don't look nice; no top panel to hold the app"* — plus *"the LIVE section says
nothing is running"*.

**The gap bug was one line.** `.ops-view` sets `display:flex; gap:24px`, but
every other view assigned `gridEl.style.display = "block"` inline, and an inline
style beats a class. Whichever view ran first left `display:block` on `#grid`,
so the Ops Room's flex gap silently never applied — measured gaps were
`[0,0,0,0]`. Views now hand off through one `resetGrid(cls)` helper that clears
inline styles; measured `[24,24,24,24]`. Spacing was then widened across the
board: body padding `20 → 22/24/32`, table rows `11 → 13px`, section gaps
`16-19 → 22-24px`.

**Title bar.** The handoff specifies 38px of window chrome — traffic lights,
centred `GSO-1 · <view>`, theme toggle right — which did not exist because
Electron's `hiddenInset` hides the system bar and nothing replaced it. Added as
a `-webkit-app-region: drag` row, with 78px reserved for the lights and
`trafficLightPosition: {x:14, y:12}` in `main.js` to centre them in it. In a
browser tab `body[data-shell="web"]` drops the reserve.

**Identity.** The mark is now exactly the handoff's: 96×96 r23 in `#14121f`, 3px
inner stroke at 14% white, four r5 cells — purple, two at 50%, one teal.
Rasterised through an offscreen Electron window (no `rsvg`/ImageMagick on this
machine), padded to the macOS grid, and built into `icon.icns` with `iconutil`,
plus a black-alpha template for the tray. `~/Applications/GSO-1.app` was
re-stamped and `lsregister -f` run — macOS caches launcher icons hard.

**Header.** Details is gone: a row opens the dock, the dock closes itself, and
the button was a third way to do it that could open on nothing. `⌘K` and rescan
carry real inline SVG instead of the `⌲`/`⟳` glyphs, which rendered at different
weights per font fallback; rescan spins while a sweep is in flight.

**"Nothing running" was true and useless.** `overview.live` only listed apps
GSO-1 had started, so the strip claimed nothing was up while the dashboard you
were reading it in and the model answering you were both running. It now leads
with the real services — `gso-1` on :8420 (not stoppable) and `llama-server`
with its model, context and resident size — which is what the handoff's own
sample data shows.

**The band that was missing entirely.** Section 4 of the Ops Room spec — TODAY
and PORTS — had never been built, because nothing recorded history. Added
`events.py`: an append-only JSONL of `{at, kind, repo, text}`, trimmed at 400
entries, written from app start/stop, git pull, site publish, llm start/stop,
scheduled jobs and explicit rescans. `/api/events?since=<midnight>` feeds the
timeline; `overview.ports` feeds the card, marking a port contested when a
conflict names it.

Two judgement calls there. Scans are logged **only on an explicit rescan** — the
45-second cache refresh would have written "scanned 270 repos" every minute and
made the timeline unreadable. And LLM traffic is **rolled up per 15-minute
slot** rather than per request, so the feed reads
`llama-server · 42 requests · 318k tokens · 0 errors`, which is exactly the line
the handoff's sample data shows — arrived at from real counters.

## 14. iPhone companion (2026-08-23)

The desktop is frozen as shipped; this is additive. The handoff's mobile design
is deliberately *not* a reflow of the desktop shell — "check and unblock, not
manage" — so it is a separate page, `static/mobile.html`, served at `/m` by the
same FastAPI process. No App Store, no second stack, no API to keep in sync:
Add to Home Screen and iOS runs it standalone.

Four tabs, three of them from the artboards (Ops feed, repo sheet, Ops Room
chat) plus Planner, which the tab bar showed but no artboard specified — built
as one column at a time, where tapping a task advances it.

**The real design question was not layout, it was exposure.** GSO-1 starts
processes, edits files and runs an agent, and until now it only ever answered
on loopback. A phone means binding an interface the rest of the network can
reach, and every one of those capabilities comes along.

`remoteauth.py` is the answer, and the shape of it matters more than the code:

- **Loopback is untouched and unauthenticated.** The desktop app needed no
  change, which was the constraint.
- **Everything else needs a shared secret**, as a bearer header or the
  `gso_token` cookie. The cookie is not laziness: `EventSource` cannot set
  headers, and the Ops Room stream is SSE, so the token has to ride the cookie
  to make the chat work at all. HttpOnly, so page scripts cannot read it back.
- **It fails closed.** With no `MANAGER_MOBILE_TOKEN` set, non-loopback requests
  are refused outright rather than allowed — a mistyped `MANAGER_HOST` cannot
  quietly open the machine to the network.
- Only `/m`, `/static/*` and `/health` are public: enough to paint a login
  screen and nothing more.

Verified from a second address on the LAN: `/api/apps` 401 without the code,
200 with it; wrong code 401; the SSE stream 401 without the cookie and streaming
with it; mutating calls (favourite, commit, start/stop) all work remotely; and
with the token unset, LAN requests 401 while loopback still returns 200.

**Supporting work.** `config.py` learned to read `<repo>/.env` (real env vars
still win, and a release resolves the canonical file through `.release.json`)
so the token lives in one place. `POST /api/apps/{name}/commit` is new — the
repo sheet's "Commit all" had no endpoint behind it. `scripts/mobile-setup.sh`
generates the code, rewrites `.env` whole (never `>>` — appending onto a file
without a trailing newline once corrupted a key) and prints the LAN and
`.local` URLs.

**One bug worth naming.** `tokens.css` carries no CSS reset — the desktop gets
one from Tailwind's preflight, which the phone page does not load. Without
`box-sizing: border-box` every padded input was wider than its column and the
whole page scrolled sideways. Reusing a token file is not the same as reusing a
stylesheet.
