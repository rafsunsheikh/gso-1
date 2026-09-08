"""Scheduled jobs for GSO-1.

A background thread that runs recurring reports and delivers them to Telegram.
Stdlib only, GSO-1 has exactly two dependencies (fastapi, uvicorn) and this
does not need a third.

Design notes:

* Jobs are **deterministic Python**, not agent prompts. A git sweep is a fact,
  not a judgement, and local inference costs ~30 s per turn, far too slow and
  too unreliable for something that runs unattended. Ops Room decides *when*
  things are interesting; code decides *what is true*. (An `opsroom_prompt`
  kind exists for jobs that genuinely need judgement; it runs detached.)
* Definitions live in ``data/schedule.json``. ``data/`` is shared across
  releases by symlink, so promoting a release never resets the schedule or
  re-fires jobs that already ran.
* Every job is wrapped: one failing job logs and never kills the thread.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from . import config, events, git_ops, health, scanner

SCHEDULE_FILE = config.DATA_DIR / "schedule.json"
STATE_FILE = config.DATA_DIR / "schedule_state.json"

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_wake = threading.Event()
_lock = threading.Lock()

# The longest the loop will sleep when nothing is due. It used to wake every 30
# seconds around the clock to notice that two daily reports were still hours
# away: 2,880 wakeups a day to start two jobs. It now sleeps until the next job
# is actually due, so this is only a backstop against a schedule edited outside
# the app, or a clock that jumped. Edits made through save_jobs wake it at once.
MAX_SLEEP_SECONDS = 900

# Fallback cadence for a job that says it is due but never records having run.
# Deliberately the old fixed tick: that failure used to be harmless and should
# stay harmless.
RETRY_SECONDS = 30
# A daily job that missed its slot (machine asleep, app restarting) still fires
# if we are within this window; beyond that it waits for tomorrow rather than
# firing a stale report at a random hour.
CATCHUP_MINUTES = 90

DEFAULT_JOBS: list[dict] = [
    {
        "id": "git-sweep",
        "name": "Daily git sweep",
        "kind": "git_sweep",
        "daily_at": "09:00",
        "enabled": True,
    },
    {
        "id": "disk-report",
        "name": "Disk space report",
        "kind": "disk_report",
        "daily_at": "09:05",
        "enabled": True,
    },
    {
        "id": "health-digest",
        "name": "App health digest",
        "kind": "health_digest",
        "every": "6h",
        "enabled": False,
    },
]


# ------------------------------------------------------------------ storage

def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic; a crash mid-write cannot corrupt the schedule


def load_jobs() -> list[dict]:
    jobs = _read_json(SCHEDULE_FILE, None)
    if not isinstance(jobs, list):
        _write_json(SCHEDULE_FILE, DEFAULT_JOBS)
        return list(DEFAULT_JOBS)
    return jobs


def save_jobs(jobs: list[dict]) -> None:
    _write_json(SCHEDULE_FILE, jobs)
    _wake.set()   # the loop may be asleep until tomorrow; this is now its cue


def _state() -> dict:
    s = _read_json(STATE_FILE, {})
    return s if isinstance(s, dict) else {}


def _mark_run(job_id: str, ok: bool, detail: str = "") -> None:
    s = _state()
    s[job_id] = {
        "last_run": datetime.now().isoformat(timespec="seconds"),
        "ok": ok,
        "detail": detail[:400],
    }
    _write_json(STATE_FILE, s)
    # The Ops Room timeline shows scheduled work alongside everything else.
    events.record("job" if ok else "fail", job_id,
                  (detail.strip().splitlines() or ["ran"])[0][:110])


# ----------------------------------------------------------------- schedule

def _parse_every(spec: str) -> Optional[timedelta]:
    """'30m', '6h', '90s' -> timedelta."""
    spec = (spec or "").strip().lower()
    if len(spec) < 2 or not spec[:-1].isdigit():
        return None
    n, unit = int(spec[:-1]), spec[-1]
    return {"s": timedelta(seconds=n), "m": timedelta(minutes=n), "h": timedelta(hours=n)}.get(unit)


def due(job: dict, now: Optional[datetime] = None) -> bool:
    if not job.get("enabled", True):
        return False
    now = now or datetime.now()
    last_raw = _state().get(job.get("id", ""), {}).get("last_run")
    last = None
    if last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
        except ValueError:
            last = None

    if job.get("every"):
        delta = _parse_every(job["every"])
        if not delta:
            return False
        return last is None or now - last >= delta

    at = job.get("daily_at")
    if at:
        try:
            hh, mm = (int(x) for x in at.split(":", 1))
        except ValueError:
            return False
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < slot:
            return False
        # Already ran during this slot today?
        if last and last >= slot:
            return False
        # Too long past the slot: wait for tomorrow rather than fire a stale one.
        return now - slot <= timedelta(minutes=CATCHUP_MINUTES)

    return False


def next_due(job: dict, now: Optional[datetime] = None) -> Optional[datetime]:
    """When this job wants to run next, or None if it never will.

    The counterpart to `due`, and it has to agree with it: the loop sleeps until
    the earliest time this returns, so a job whose moment is not reported here
    is a job that does not run. Both read the same last_run and the same specs.
    """
    if not job.get("enabled", True):
        return None
    now = now or datetime.now()
    last_raw = _state().get(job.get("id", ""), {}).get("last_run")
    last = None
    if last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
        except ValueError:
            last = None

    if job.get("every"):
        delta = _parse_every(job["every"])
        if not delta:
            return None
        return now if last is None else last + delta

    at = job.get("daily_at")
    if at:
        try:
            hh, mm = (int(x) for x in at.split(":", 1))
        except ValueError:
            return None
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < slot:
            return slot
        # Past today's slot: due now if it has not run and is still inside the
        # catch-up window, otherwise tomorrow.
        if (last is None or last < slot) and now - slot <= timedelta(minutes=CATCHUP_MINUTES):
            return now
        return slot + timedelta(days=1)

    return None


def _sleep_seconds(now: Optional[datetime] = None) -> float:
    """How long the loop may sleep before some job needs attention."""
    now = now or datetime.now()
    soonest: Optional[datetime] = None
    try:
        for job in load_jobs():
            nxt = next_due(job, now)
            if nxt is not None and (soonest is None or nxt < soonest):
                soonest = nxt
    except Exception:  # noqa: BLE001
        return float(MAX_SLEEP_SECONDS)
    if soonest is None:
        return float(MAX_SLEEP_SECONDS)          # nothing enabled
    delta = (soonest - now).total_seconds()
    if delta <= 0:
        # Due jobs are run immediately before this is called, so a job still
        # wanting "now" is one whose last_run did not advance, a state file
        # that could not be written, say. Retry at the old fixed tick instead
        # of spinning at one second for the whole catch-up window.
        return float(RETRY_SECONDS)
    return max(1.0, min(float(MAX_SLEEP_SECONDS), delta))


# --------------------------------------------------------------- job bodies

def _git_sweep() -> str:
    """Which repositories have uncommitted or unpushed work."""
    dirty, ahead, scanned, repos = [], [], 0, 0
    for name in scanner.list_app_names():
        scanned += 1
        path = scanner.app_path(name)
        if not git_ops.is_repo(path):
            continue
        repos += 1
        try:
            st = git_ops.status(path)
        except Exception:
            continue
        if st.get("dirty"):
            dirty.append((name, st.get("changed_count", 0), st.get("branch", "?")))
        if st.get("ahead") or st.get("behind"):
            ahead.append((name, st.get("ahead", 0), st.get("behind", 0)))

    dirty.sort(key=lambda r: -r[1])
    lines = [f"🔍 Git sweep, {repos} repos of {scanned} apps"]
    if dirty:
        lines.append(f"\n📝 {len(dirty)} with uncommitted changes:")
        lines += [f"  • {n}, {c} file(s) on {b}" for n, c, b in dirty[:12]]
        if len(dirty) > 12:
            lines.append(f"  …and {len(dirty) - 12} more")
    else:
        lines.append("\n✅ Nothing uncommitted.")
    if ahead:
        lines.append(f"\n🔀 {len(ahead)} out of sync with remote:")
        lines += [f"  • {n}: ahead {a}, behind {b}" for n, a, b in ahead[:8]]
    return "\n".join(lines)


def _disk_report() -> str:
    total, used, free = shutil.disk_usage("/")
    gb = 1024 ** 3
    pct = used / total * 100
    lines = [
        "💾 Disk",
        f"  {free / gb:.0f} GB free of {total / gb:.0f} GB ({pct:.0f}% used)",
    ]
    if free / gb < 50:
        lines.append("  ⚠️ Under 50 GB free.")
    for label, root in config.PROJECT_ROOTS:
        try:
            out = subprocess.run(
                ["du", "-sh", "-x", str(root)], capture_output=True, text=True, timeout=300
            )
            size = out.stdout.split("\t")[0].strip()
            if size:
                lines.append(f"  {label}: {size}")
        except (OSError, subprocess.SubprocessError):
            pass
    return "\n".join(lines)


def _health_digest() -> str:
    running, unhealthy = [], []
    for name in scanner.list_app_names():
        cfg = scanner.effective_config(name)
        port = cfg.get("port")
        if not port:
            continue
        st = health.check(name, port)
        state = st.get("state") or st.get("status")
        if state in ("healthy", "running"):
            running.append(name)
        elif state in ("crashed", "unhealthy"):
            unhealthy.append((name, state))
    lines = [f"❤️ Health, {len(running)} up"]
    if unhealthy:
        lines.append(f"\n⚠️ {len(unhealthy)} unhealthy:")
        lines += [f"  • {n}, {s}" for n, s in unhealthy[:10]]
    return "\n".join(lines)


def _opsroom_prompt(job: dict) -> str:
    """Run an Ops Room prompt detached.

    Local inference is ~30 s per turn before tool time, so this never blocks the
    scheduler thread; it fires and reports only that it started.
    """
    prompt = (job.get("prompt") or "").strip()
    if not prompt:
        return "opsroom_prompt job has no prompt"
    ops = config.APP_DIR / "ops"
    if not ops.exists():
        return f"launcher not found at {ops}"
    log = config.LOG_DIR / f"job-{job.get('id', 'opsroom')}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        subprocess.Popen(
            [str(ops), prompt], cwd=str(config.APP_DIR),
            stdout=fh, stderr=subprocess.STDOUT, start_new_session=True,
        )
    return f"🤖 Started Ops Room job '{job.get('name')}', output: {log}"


KINDS: dict[str, Callable[..., str]] = {
    "git_sweep": lambda job: _git_sweep(),
    "disk_report": lambda job: _disk_report(),
    "health_digest": lambda job: _health_digest(),
    "opsroom_prompt": _opsroom_prompt,
}


# ------------------------------------------------------------------ runner

def _notify(text: str) -> None:
    """Deliver to every authorised Telegram chat. Import is local so the
    scheduler still works (logging only) when the bot is not configured."""
    try:
        from . import telegrambot
        for chat_id in telegrambot.ALLOWED:
            telegrambot._send(chat_id, text)
    except Exception:
        pass


def run_job(job: dict, notify: bool = True) -> dict:
    """Run one job now. Never raises: a bad job must not kill the thread."""
    kind = job.get("kind", "")
    fn = KINDS.get(kind)
    if not fn:
        _mark_run(job.get("id", "?"), False, f"unknown kind: {kind}")
        return {"ok": False, "error": f"unknown kind: {kind}"}
    started = time.time()
    try:
        text = fn(job)
        if notify and text:
            _notify(text)
        _mark_run(job.get("id", "?"), True, text)
        return {"ok": True, "text": text, "seconds": round(time.time() - started, 1)}
    except Exception as exc:  # noqa: BLE001
        detail = f"{exc.__class__.__name__}: {exc}"
        _mark_run(job.get("id", "?"), False, detail)
        print(f"[scheduler] job {job.get('id')} failed: {detail}\n{traceback.format_exc()}")
        return {"ok": False, "error": detail}


def _loop() -> None:
    print("[scheduler] started")
    while not _stop.is_set():
        try:
            for job in load_jobs():
                if _stop.is_set():
                    break
                if due(job):
                    print(f"[scheduler] running {job.get('id')}")
                    run_job(job)
        except Exception:  # noqa: BLE001
            print(f"[scheduler] tick failed:\n{traceback.format_exc()}")
        # Sleep until the next job is due rather than ticking at a fixed rate.
        # `_wake` is set by save_jobs and by stop, so an edited schedule or a
        # shutdown is picked up immediately instead of at the end of the wait.
        _wake.clear()
        _wake.wait(_sleep_seconds())
    print("[scheduler] stopped")


def start() -> None:
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        load_jobs()  # materialise defaults on first run
        _stop.clear()
        _wake.clear()
        _thread = threading.Thread(target=_loop, name="scheduler", daemon=True)
        _thread.start()


def stop() -> None:
    _stop.set()
    _wake.set()


def status() -> dict:
    st = _state()
    jobs = []
    for job in load_jobs():
        jobs.append({**job, "last": st.get(job.get("id", ""), {}), "due_now": due(job)})
    return {
        "running": bool(_thread and _thread.is_alive()),
        "next_check_seconds": round(_sleep_seconds()),
        "jobs": jobs,
    }
