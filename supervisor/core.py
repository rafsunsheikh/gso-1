"""Ops Room supervisor, core.

Small, boring, stdlib-only. This module is the one component the agent must
never edit: it is what survives a bad self-edit and rolls the app back.

Layout it owns:

    var/
      releases/<timestamp>/   snapshot of app source
      current  -> releases/X  what the supervisor runs
      previous -> releases/Y  rollback target
      data     -> <repo>/data shared state (registry, planner, logs)
      venv/                   fallback venv when the repo has none
      supervisor.pid          running supervisor
      supervisor.log          supervisor's own log
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- paths

REPO = Path(__file__).resolve().parent.parent
VAR = REPO / "var"
RELEASES = VAR / "releases"
CURRENT = VAR / "current"
PREVIOUS = VAR / "previous"
DATA_LINK = VAR / "data"
PIDFILE = VAR / "supervisor.pid"
LOGFILE = VAR / "supervisor.log"
STATEFILE = VAR / "state.json"

def _load_env() -> None:
    """Read <repo>/.env, without overriding variables already in the process.

    The supervisor stamps MANAGER_HOST into every child it spawns, so if it did
    not read this file the app could never see it: enabling the phone companion
    in .env would silently keep binding loopback. Deliberately a local copy, 
    the supervisor stays stdlib-only and independent of the app package.
    """
    try:
        text = (REPO / ".env").read_text()
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env()

HOST = os.environ.get("MANAGER_HOST", "127.0.0.1")
PORT = int(os.environ.get("MANAGER_PORT", "8420"))
HEALTH_PATH = os.environ.get("SUPERVISOR_HEALTH_PATH", "/health")

# 0.0.0.0 is a bind address, not a destination: probe over loopback.
PROBE_HOST = "127.0.0.1" if HOST in ("0.0.0.0", "::", "") else HOST

# Never copied into a release.
# Never copied into a release. Secrets in particular: a release is a snapshot
# that accumulates on disk, so copying .env would duplicate the API keys into
# every build. The running release reads the canonical <repo>/.env instead.
EXCLUDE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "var", "data", "__pycache__", "*.pyc",
    ".DS_Store", "node_modules", ".claude", ".pytest_cache",
    ".env", ".env.*",
)

# Restart backoff (seconds); the last value repeats.
BACKOFF = [1, 2, 4, 8, 15, 30]
# A child alive this long is considered stable; backoff resets.
STABLE_AFTER = 60


def log(msg: str) -> None:
    # stderr, so stdout stays clean for command values (release stamps, etc.)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, file=sys.stderr, flush=True)
    try:
        VAR.mkdir(parents=True, exist_ok=True)
        with LOGFILE.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def ensure_layout() -> None:
    """Create var/ and the shared data symlink. Idempotent."""
    RELEASES.mkdir(parents=True, exist_ok=True)
    repo_data = REPO / "data"
    repo_data.mkdir(parents=True, exist_ok=True)
    if not DATA_LINK.exists() and not DATA_LINK.is_symlink():
        DATA_LINK.symlink_to(repo_data)


def venv_python() -> Path:
    """Interpreter used to run a release. Prefers the repo venv."""
    repo_venv = REPO / ".venv" / "bin" / "python"
    if repo_venv.exists():
        return repo_venv
    own = VAR / "venv" / "bin" / "python"
    if not own.exists():
        log("creating supervisor venv")
        subprocess.run([sys.executable, "-m", "venv", str(VAR / "venv")], check=True)
        subprocess.run(
            [str(own), "-m", "pip", "install", "-q", "-r", str(REPO / "requirements.txt")],
            check=True,
        )
    return own


# ------------------------------------------------------------- releases

def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def create_release(source: Path | None = None) -> str:
    """Snapshot `source` (default: the repo) into var/releases/<stamp>."""
    ensure_layout()
    src = (source or REPO).resolve()
    stamp = _stamp()
    dest = RELEASES / stamp
    if dest.exists():
        raise FileExistsError(dest)

    shutil.copytree(src, dest, ignore=EXCLUDE, symlinks=True)

    # Point the release's data/ at shared state so promoting never resets it.
    (dest / "data").symlink_to(DATA_LINK)

    # node_modules is excluded from the snapshot (it is large and derived), but
    # the sidecar cannot run without it. Share the repo's install rather than
    # copying ~100 packages per release.
    ops = dest / "opsroom"
    repo_modules = src / "opsroom" / "node_modules"
    if ops.is_dir() and repo_modules.is_dir():
        (ops / "node_modules").symlink_to(repo_modules)

    meta = {
        "stamp": stamp,
        "source": str(src),
        "created": datetime.now(timezone.utc).isoformat(),
        "git": _git_describe(src),
    }
    (dest / ".release.json").write_text(json.dumps(meta, indent=2))
    log(f"release created: {stamp} ({meta['git'] or 'no git'})")
    return stamp


def _git_describe(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "log", "-1", "--format=%h %s"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def list_releases() -> list[str]:
    if not RELEASES.exists():
        return []
    return sorted(p.name for p in RELEASES.iterdir() if p.is_dir())


def _link_target(link: Path) -> str | None:
    if link.is_symlink():
        return Path(os.readlink(link)).name
    return None


def current_release() -> str | None:
    return _link_target(CURRENT)


def previous_release() -> str | None:
    return _link_target(PREVIOUS)


def _repoint(link: Path, stamp: str) -> None:
    """Atomically point `link` at releases/<stamp>."""
    tmp = link.with_name(link.name + ".tmp")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(RELEASES / stamp)
    os.replace(tmp, link)


def promote(stamp: str) -> None:
    """Make <stamp> current, remembering the outgoing release as previous."""
    if not (RELEASES / stamp).is_dir():
        raise FileNotFoundError(f"no such release: {stamp}")
    outgoing = current_release()
    if outgoing and outgoing != stamp:
        _repoint(PREVIOUS, outgoing)
    _repoint(CURRENT, stamp)
    log(f"promoted {stamp}" + (f" (previous: {outgoing})" if outgoing else ""))


def dangling_links() -> list[str]:
    """Names of current/previous that point at a release which no longer exists."""
    bad = []
    for name, link in (("current", CURRENT), ("previous", PREVIOUS)):
        if link.is_symlink() and not link.resolve().is_dir():
            bad.append(name)
    return bad


def prune_releases(keep: int = 5, dry_run: bool = False) -> dict:
    """Delete old releases, keeping the newest `keep`.

    `current` and `previous` are ALWAYS protected regardless of age, deleting
    the live release leaves a dangling symlink and deleting the rollback target
    removes the only way back. Both happened by hand on 2026-08-22 using
    `ls -t | head -1`, which is exactly the mistake this exists to prevent.
    """
    protected = {r for r in (current_release(), previous_release()) if r}
    all_rel = list_releases()          # sorted oldest -> newest
    keepers = set(all_rel[-keep:]) | protected
    doomed = [r for r in all_rel if r not in keepers]

    removed, freed = [], 0
    for stamp in doomed:
        path = RELEASES / stamp
        try:
            freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())
        except OSError:
            pass
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True)
        removed.append(stamp)

    if removed:
        verb = "would remove" if dry_run else "removed"
        log(f"prune: {verb} {len(removed)} release(s), {freed / 1048576:.1f} MiB")
    return {
        "removed": removed,
        "kept": [r for r in all_rel if r in keepers],
        "protected": sorted(protected),
        "freed_bytes": freed,
        "dry_run": dry_run,
    }


def rollback() -> str:
    """Swap current and previous. Returns the release now current."""
    cur, prev = current_release(), previous_release()
    if not prev:
        raise RuntimeError("no previous release to roll back to")
    _repoint(CURRENT, prev)
    if cur:
        _repoint(PREVIOUS, cur)
    log(f"rolled back to {prev}" + (f" (was {cur})" if cur else ""))
    return prev


# --------------------------------------------------------------- health

# GSO-1 answers in ~8s under load (its Telegram long-poll shares the process),
# so a tight probe reports a healthy app as down. That is dangerous here: this
# supervisor rolls back on failed health, and a false negative would revert a
# perfectly good release. Measured 7.8s on 2026-08-22; 20s leaves real margin.
HEALTH_TIMEOUT = float(os.environ.get("SUPERVISOR_HEALTH_TIMEOUT", "20"))


def tcp_open(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_responds(host: str, port: int, path: str = HEALTH_PATH,
                  timeout: float = HEALTH_TIMEOUT) -> bool:
    """True if the server answers HTTP at all.

    GSO-1 has no /health route yet, so a 404 still proves uvicorn is up and
    routing. Only a connection/protocol failure counts as unhealthy.
    """
    url = f"http://{host}:{port}{path}"
    try:
        urllib.request.urlopen(url, timeout=timeout).read(1)
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError):
        return False


def healthy(host: str = PROBE_HOST, port: int = PORT) -> bool:
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return tcp_open(host, port) and http_responds(host, port)


def wait_healthy(host: str, port: int, timeout: float = 60.0) -> bool:
    """Poll until the app answers. Each probe may itself take HEALTH_TIMEOUT."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if healthy(host, port):
            return True
        time.sleep(1.0)
    return False


# -------------------------------------------------------------- process

def spawn(release_dir: Path, port: int = PORT, host: str = HOST) -> subprocess.Popen:
    """Start GSO-1 from `release_dir` in its own process group."""
    env = dict(os.environ)
    env["MANAGER_HOST"] = host
    env["MANAGER_PORT"] = str(port)
    env["MANAGER_NO_BROWSER"] = "1"  # supervisor restarts must not spawn tabs
    return subprocess.Popen(
        [str(venv_python()), "-m", "thecmanager"],
        cwd=str(release_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def verify_sidecar(rel: Path) -> bool:
    """Run the Ops Room self-check inside a release.

    Booting GSO-1 proves the Python app works and says nothing about the Node
    sidecar. Without this, a syntax error or a broken import in opsroom/ passes
    verification and only surfaces after promotion, exactly when it is hardest
    to recover from.
    """
    check = rel / "opsroom" / "src" / "selfcheck.ts"
    if not check.exists():
        log("verify: no sidecar self-check in this release, skipping")
        return True

    node = shutil.which("node")
    if not node:
        log("verify: node not found; cannot self-check sidecar")
        return False

    env = dict(os.environ)
    # Point the release's sidecar at the canonical secrets file.
    env.setdefault("OPSROOM_ENV_FILE", str(REPO / ".env"))
    try:
        res = subprocess.run(
            [node, str(check)],
            cwd=str(rel / "opsroom"),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.SubprocessError as exc:
        log(f"verify: sidecar self-check errored: {exc}")
        return False

    out = (res.stdout or "").strip() or (res.stderr or "").strip()
    log(f"verify: sidecar {'OK' if res.returncode == 0 else 'FAILED'}, {out[-400:]}")
    return res.returncode == 0


def verify(stamp: str, port: int | None = None) -> bool:
    """Boot a release and check BOTH halves. Never promotes."""
    rel = RELEASES / stamp
    if not rel.is_dir():
        raise FileNotFoundError(stamp)

    # 1. The Node sidecar must load and its guards must still refuse.
    if not verify_sidecar(rel):
        log(f"verify {stamp}: FAIL (sidecar)")
        return False

    # 2. GSO-1 must boot and answer on a scratch port.
    port = port or _free_port()
    log(f"verifying {stamp} app on port {port}")
    proc = spawn(rel, port=port)
    try:
        ok = wait_healthy(PROBE_HOST, port, timeout=60.0)
        log(f"verify {stamp}: {'PASS' if ok else 'FAIL (app)'}")
        return ok
    finally:
        _terminate(proc)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _terminate(proc: subprocess.Popen, grace: float = 3.0) -> None:
    """Stop a child and its whole process group."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except OSError:
            return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


# ------------------------------------------------------------ supervise

class Supervisor:
    """Keeps one child alive, restarting it with backoff when it dies."""

    def __init__(self, host: str = HOST, port: int = PORT):
        self.host, self.port = host, port
        self.proc: subprocess.Popen | None = None
        self.stopping = False
        self.restarts = 0

    def _release_dir(self) -> Path:
        if not CURRENT.is_symlink():
            raise RuntimeError("var/current is not set, run `release create` then `promote`")
        return CURRENT.resolve()

    def start_child(self) -> None:
        rel = self._release_dir()
        self.proc = spawn(rel, port=self.port, host=self.host)
        log(f"child started pid={self.proc.pid} release={current_release()} port={self.port}")

    def run(self) -> int:
        ensure_layout()
        PIDFILE.write_text(str(os.getpid()))
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._on_signal)

        idx = 0
        try:
            while not self.stopping:
                started = time.time()
                self.start_child()
                assert self.proc is not None
                rc = self.proc.wait()
                if self.stopping:
                    break
                uptime = time.time() - started
                self.restarts += 1
                if uptime >= STABLE_AFTER:
                    idx = 0  # it was healthy for a while; treat as a fresh fault
                delay = BACKOFF[min(idx, len(BACKOFF) - 1)]
                idx += 1
                log(f"child exited rc={rc} after {uptime:.1f}s, restarting in {delay}s")
                self._sleep(delay)
        finally:
            self._cleanup()
        return 0

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not self.stopping:
            time.sleep(0.2)

    def _on_signal(self, signum, _frame) -> None:
        log(f"signal {signum}, shutting down")
        self.stopping = True
        if self.proc:
            _terminate(self.proc)

    def _cleanup(self) -> None:
        if self.proc:
            _terminate(self.proc)
        if PIDFILE.exists():
            try:
                PIDFILE.unlink()
            except OSError:
                pass
        log("supervisor stopped")


# ----------------------------------------------------------- pid helpers

def running_pid() -> int | None:
    if not PIDFILE.exists():
        return None
    try:
        pid = int(PIDFILE.read_text().strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def stop_supervisor(grace: float = 8.0) -> bool:
    pid = running_pid()
    if pid is None:
        return False
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + grace
    while time.time() < deadline:
        if running_pid() is None:
            return True
        time.sleep(0.3)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return True
