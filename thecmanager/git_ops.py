"""Git inspection and update helpers."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Where command-line tools actually live, beyond the four directories launchd
# hands a background agent. GSO-1 is normally started by a LaunchAgent with
# PATH=/usr/bin:/bin:/usr/sbin:/sbin, so git cannot find the helpers it shells
# out to: with `commit.gpgsign = true` set, every commit made from the app
# failed with "cannot run gpg: No such file or directory" while the identical
# commit worked in a terminal. Git needs a PATH resembling a login shell's.
_EXTRA_PATH = (
    "/opt/homebrew/bin", "/opt/homebrew/sbin",   # Apple Silicon Homebrew
    "/usr/local/bin", "/usr/local/sbin",         # Intel Homebrew, and most else
    "/opt/local/bin",                            # MacPorts
    str(Path.home() / ".local/bin"),
)

_env_cache: dict | None = None


def _git_env() -> dict:
    """The environment git runs in: inherited, with a usable PATH."""
    global _env_cache
    if _env_cache is None:
        env = dict(os.environ)
        parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
        for extra in _EXTRA_PATH:
            if extra not in parts and Path(extra).is_dir():
                parts.append(extra)
        env["PATH"] = os.pathsep.join(parts)
        # Never let a signing prompt or a credential prompt block the request:
        # a GUI subprocess has no terminal to answer on, so it would hang until
        # the timeout instead of failing with something readable.
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        _env_cache = env
    return _env_cache


def _run(path: Path, *args: str, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_git_env(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, "git command timed out"
    except FileNotFoundError:
        return 127, "git not installed"


def _run_raw(path: Path, *args: str, timeout: int = 30) -> tuple[int, str]:
    """Like `_run`, but keeps the output exactly as git wrote it.

    `--porcelain` encodes the staged/unstaged state in two leading columns, and
    for an unstaged change the first of those is a space. Stripping the output
    eats that space on the first line only, so its path silently loses one
    character: ` M .DS_Store` became `M .DS_Store`, and everything that reads
    the path from a fixed offset then saw `DS_Store`.
    """
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(path),
            capture_output=True, text=True, timeout=timeout,
            env=_git_env(),
        )
        return proc.returncode, (proc.stdout or "")
    except subprocess.TimeoutExpired:
        return 124, ""
    except FileNotFoundError:
        return 127, ""


def is_repo(path: Path) -> bool:
    return (Path(path) / ".git").exists()


def status(path: Path) -> dict:
    path = Path(path)
    if not is_repo(path):
        return {"is_repo": False}

    _, branch = _run(path, "rev-parse", "--abbrev-ref", "HEAD")
    rc, porcelain = _run_raw(path, "status", "--porcelain")
    # Every entry is "XY<space>path" with X/Y possibly blank. Callers slice at
    # fixed offsets, so the shape has to survive intact, including the leading
    # space of an unstaged change.
    changed = [ln.rstrip("\n") for ln in porcelain.splitlines() if ln.strip()]

    ahead = behind = 0
    rc2, counts = _run(path, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if rc2 == 0 and "\t" in counts:
        try:
            behind_s, ahead_s = counts.split("\t")
            behind, ahead = int(behind_s), int(ahead_s)
        except ValueError:
            pass

    _, last_commit = _run(path, "log", "-1", "--pretty=%h %s (%cr)")
    _, remote = _run(path, "remote", "get-url", "origin")

    return {
        "is_repo": True,
        "branch": branch,
        "dirty": len(changed) > 0,
        "changed_count": len(changed),
        "changed_files": changed[:50],
        "ahead": ahead,
        "behind": behind,
        "last_commit": last_commit,
        "remote": remote if "fatal" not in remote.lower() else None,
    }


def branches(path: Path, limit: int = 40) -> dict:
    """Local branches, plus remote-only ones you could switch to.

    One `for-each-ref` rather than a `git branch` per ring: this runs every
    time the drawer opens, and the drawer opens constantly.

    Remote-only branches are included because "the branches of this repo" means
    the ones on the remote too. Checking one out is the common reason to look
    at this list at all, and `git switch` creates the local tracking branch on
    demand, so offering them costs nothing.
    """
    path = Path(path)
    if not is_repo(path):
        return {"is_repo": False, "branches": [], "current": None}

    fmt = "%(refname:short)%09%(upstream:short)%09%(committerdate:unix)%09%(HEAD)"
    out: list[dict] = []
    seen: set[str] = set()

    rc, raw = _run(path, "for-each-ref", "--format=" + fmt,
                   "--sort=-committerdate", "refs/heads")
    if rc == 0:
        for line in raw.splitlines():
            parts = line.split("\t")
            if not parts or not parts[0]:
                continue
            name = parts[0]
            seen.add(name)
            out.append({
                "name": name,
                "upstream": (parts[1] if len(parts) > 1 else "") or None,
                "when": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
                "current": (parts[3].strip() == "*") if len(parts) > 3 else False,
                "remote_only": False,
            })

    rc, raw = _run(path, "for-each-ref", "--format=%(refname:short)%09%(committerdate:unix)",
                   "--sort=-committerdate", "refs/remotes")
    if rc == 0:
        for line in raw.splitlines():
            parts = line.split("\t")
            full = parts[0] if parts else ""
            # "origin/main" -> "main"; skip the "origin/HEAD" pointer.
            if not full or "/" not in full or full.endswith("/HEAD"):
                continue
            short = full.split("/", 1)[1]
            if short in seen:
                continue
            seen.add(short)
            out.append({
                "name": short,
                "upstream": full,
                "when": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
                "current": False,
                "remote_only": True,
            })

    current = next((b["name"] for b in out if b["current"]), None)
    if current is None:
        _, current = _run(path, "rev-parse", "--abbrev-ref", "HEAD")
        current = current or None

    # Newest first, but the branch you are on is never buried.
    out.sort(key=lambda b: (not b["current"], -b["when"]))
    return {
        "is_repo": True,
        "current": current,
        "total": len(out),
        "branches": out[:limit],
    }


def checkout(path: Path, branch: str) -> dict:
    """Switch branches, letting git refuse rather than second-guessing it.

    `git switch` declines when the working tree would be clobbered, and its
    message says exactly which files are in the way. Reimplementing that check
    here would only produce a worse version of the same refusal.
    """
    path = Path(path)
    if not is_repo(path):
        return {"ok": False, "output": "Not a git repository."}
    branch = (branch or "").strip()
    if not branch:
        return {"ok": False, "output": "No branch given."}
    rc, out = _run(path, "switch", branch, timeout=60)
    if rc != 0 and "invalid reference" in out.lower():
        # A remote-only branch: create the local tracking branch for it.
        rc, out = _run(path, "switch", "-c", branch, "--track",
                       f"origin/{branch}", timeout=60)
    return {"ok": rc == 0, "output": out or f"Switched to {branch}."}


def update(path: Path) -> dict:
    """git pull on the current branch."""
    path = Path(path)
    if not is_repo(path):
        return {"ok": False, "output": "Not a git repository."}
    rc, out = _run(path, "pull", "--ff-only", timeout=120)
    return {"ok": rc == 0, "output": out or "(no output)"}


def commit_all(path: Path, message: str) -> dict:
    """Stage everything and commit. No-op (ok) when there's nothing to commit."""
    path = Path(path)
    if not is_repo(path):
        return {"ok": False, "output": "Not a git repository."}
    if not (message or "").strip():
        return {"ok": False, "output": "Empty commit message."}
    _run(path, "add", "-A")
    rc, out = _run(path, "commit", "-m", message)
    low = out.lower()
    if rc != 0 and ("nothing to commit" in low or "no changes added" in low):
        return {"ok": True, "output": "Nothing to commit.", "nochange": True}
    return {"ok": rc == 0, "output": out or "(committed)"}


def push(path: Path) -> dict:
    """Push the current branch, setting upstream if needed."""
    path = Path(path)
    if not is_repo(path):
        return {"ok": False, "output": "Not a git repository."}
    rc, out = _run(path, "push", timeout=180)
    if rc != 0 and "no upstream" in out.lower():
        _, br = _run(path, "rev-parse", "--abbrev-ref", "HEAD")
        rc, out = _run(path, "push", "-u", "origin", br, timeout=180)
    return {"ok": rc == 0, "output": out or "(pushed)"}
