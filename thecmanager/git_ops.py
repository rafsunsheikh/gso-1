"""Git inspection and update helpers."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _run(path: Path, *args: str, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, "git command timed out"
    except FileNotFoundError:
        return 127, "git not installed"


def is_repo(path: Path) -> bool:
    return (Path(path) / ".git").exists()


def status(path: Path) -> dict:
    path = Path(path)
    if not is_repo(path):
        return {"is_repo": False}

    _, branch = _run(path, "rev-parse", "--abbrev-ref", "HEAD")
    rc, porcelain = _run(path, "status", "--porcelain")
    changed = [ln for ln in porcelain.splitlines() if ln.strip()]

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


def update(path: Path) -> dict:
    """git pull on the current branch."""
    path = Path(path)
    if not is_repo(path):
        return {"ok": False, "output": "Not a git repository."}
    rc, out = _run(path, "pull", "--ff-only", timeout=120)
    return {"ok": rc == 0, "output": out or "(no output)"}
