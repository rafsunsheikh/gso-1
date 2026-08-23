"""Configuration and paths for GSO-1."""
import os
from pathlib import Path

# Project root(s). MANAGER_PROJECTS_DIRS is comma-separated and each entry may
# carry an optional display label as "Label:/path" (e.g.
# "Personal Projects:~/Projects,AUDD Work:~/work"). Falls back to the
# single MANAGER_PROJECTS_DIR, then to ~/Projects.
def _parse_roots() -> list[tuple[str, Path]]:
    raw = os.environ.get("MANAGER_PROJECTS_DIRS")
    if raw:
        entries = [e.strip() for e in raw.split(",") if e.strip()]
    else:
        single = os.environ.get("MANAGER_PROJECTS_DIR")
        entries = [single] if single else [str(Path.home() / "Projects")]

    roots: list[tuple[str, Path]] = []
    for e in entries:
        label, path_str = None, e
        # "Label:/path" — only split when the right side looks like a path.
        if ":" in e:
            maybe_label, maybe_path = e.split(":", 1)
            if maybe_path.strip().startswith(("/", "~", ".")):
                label, path_str = maybe_label.strip(), maybe_path.strip()
        p = Path(path_str).expanduser()
        roots.append((label or p.name or str(p), p))
    return roots


PROJECT_ROOTS = _parse_roots()                       # [(label, Path), ...]
PROJECTS_DIRS = [p for _, p in PROJECT_ROOTS]
# Primary root — kept for back-compat with code/UI that expects a single dir.
PROJECTS_DIR = PROJECTS_DIRS[0]


def under_any_root(path: str) -> bool:
    """True if `path` is inside any configured projects root."""
    try:
        rp = str(Path(path).resolve())
    except OSError:
        rp = str(path)
    for root in PROJECTS_DIRS:
        try:
            root_r = str(root.resolve())
        except OSError:
            continue
        if rp == root_r or rp.startswith(root_r + "/"):
            return True
    return False

# Where GSO-1 itself lives (this package's parent of parent).
APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
REGISTRY_FILE = DATA_DIR / "registry.json"

# Server.
HOST = os.environ.get("MANAGER_HOST", "127.0.0.1")
PORT = int(os.environ.get("MANAGER_PORT", "8420"))

# Directory names we never list as "apps".
IGNORED_NAMES = {
    "node_modules",
    "__pycache__",
    ".git",
    "plans",
    "book-assets",
    "notebooks",
}

# File extensions / suffixes that mean "not a project dir worth managing".
IGNORED_PREFIXES = (".",)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
