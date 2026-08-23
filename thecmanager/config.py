"""Configuration and paths for GSO-1."""
import json
import os
from pathlib import Path

# Where GSO-1 itself lives (this package's parent of parent).
APP_DIR = Path(__file__).resolve().parent.parent


def _repo_root() -> Path:
    """The real checkout, even when running from a release snapshot.

    Releases deliberately do not carry `.env` — secrets stay in one place — so
    a release reads the canonical file through the `source` recorded at build
    time, the same way the Ops Room launcher does.
    """
    try:
        src = json.loads((APP_DIR / ".release.json").read_text()).get("source")
        if src and Path(src).is_dir():
            return Path(src)
    except (OSError, ValueError):
        pass
    return APP_DIR


def _load_env_file() -> None:
    """Load `<repo>/.env` into the environment. Real env vars always win."""
    path = _repo_root() / ".env"
    try:
        text = path.read_text()
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


_load_env_file()

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

DATA_DIR = APP_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
REGISTRY_FILE = DATA_DIR / "registry.json"

# Server. The default binds loopback only: reaching GSO-1 from another device
# is opt-in, and opting in requires a token (see MOBILE_TOKEN below).
HOST = os.environ.get("MANAGER_HOST", "127.0.0.1")
PORT = int(os.environ.get("MANAGER_PORT", "8420"))

# Shared secret for the phone companion. Requests that do not come from
# loopback must present it; with no token set, they are refused outright.
MOBILE_TOKEN = os.environ.get("MANAGER_MOBILE_TOKEN", "").strip()

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
