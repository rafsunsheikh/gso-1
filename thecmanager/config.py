"""Configuration and paths for GSO-1."""
import json
import os
import sys
from pathlib import Path

# True when running from a packaged build (PyInstaller sets this). A packaged
# app lives in a read-only bundle and must keep its state elsewhere; a source
# checkout keeps using ./data so existing installs are undisturbed.
PACKAGED = bool(getattr(sys, "frozen", False))

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

# ---------------------------------------------------------------- data dir

def _default_data_dir() -> Path:
    """Where a packaged app keeps its state, per platform convention."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "GSO-1"
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "GSO-1"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "gso-1"


def _resolve_data_dir() -> Path:
    """State lives beside the code in a checkout, and in the user's data
    directory once packaged — a bundle is read-only, and two installs must not
    fight over one registry."""
    override = os.environ.get("MANAGER_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return _default_data_dir() if PACKAGED else APP_DIR / "data"


DATA_DIR = _resolve_data_dir()
LOG_DIR = DATA_DIR / "logs"
REGISTRY_FILE = DATA_DIR / "registry.json"
SETTINGS_FILE = DATA_DIR / "settings.json"


def load_settings() -> dict:
    """User settings chosen in the app (as opposed to environment overrides)."""
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    """Write settings atomically — a half-written file would lose the user's
    project roots and send them back through onboarding."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(SETTINGS_FILE)


# ------------------------------------------------------------ project roots

def parse_root_entry(entry: str) -> tuple[str, Path]:
    """Parse one root, which may carry a display label as "Label:/path"."""
    label, path_str = None, entry
    # Only split on ":" when the right side actually looks like a path, so a
    # Windows drive letter or a stray colon in a folder name survives.
    if ":" in entry:
        maybe_label, maybe_path = entry.split(":", 1)
        if maybe_path.strip().startswith(("/", "~", ".")):
            label, path_str = maybe_label.strip(), maybe_path.strip()
    p = Path(path_str).expanduser()
    return (label or p.name or str(p), p)


def _env_roots() -> list[tuple[str, Path]]:
    """Roots forced by the environment, if any. These always win: a packaged
    app must still be scriptable."""
    raw = os.environ.get("MANAGER_PROJECTS_DIRS")
    if raw:
        entries = [e.strip() for e in raw.split(",") if e.strip()]
    else:
        single = os.environ.get("MANAGER_PROJECTS_DIR", "").strip()
        entries = [single] if single else []
    return [parse_root_entry(e) for e in entries]


def _saved_roots() -> list[tuple[str, Path]]:
    """Roots the user picked in the app, from settings.json."""
    roots: list[tuple[str, Path]] = []
    for item in load_settings().get("project_roots", []):
        if isinstance(item, dict) and item.get("path"):
            p = Path(str(item["path"])).expanduser()
            roots.append((str(item.get("label") or p.name or p), p))
        elif isinstance(item, str) and item.strip():
            roots.append(parse_root_entry(item.strip()))
    return roots


def _parse_roots() -> list[tuple[str, Path]]:
    """Environment first, then what the user chose, then the usual guess.

    The final fallback is deliberately unconditional: the scanner copes with a
    root that does not exist, and `needs_onboarding()` is what decides whether
    to ask. Returning an empty list here would break every caller that assumes
    at least one root.
    """
    return _env_roots() or _saved_roots() or [(  "Projects", Path.home() / "Projects")]


def needs_onboarding() -> bool:
    """True when nobody has told GSO-1 where the projects live.

    Only asked on a fresh install: an environment override, a saved choice, or
    an existing ~/Projects all count as an answer.
    """
    if _env_roots() or _saved_roots():
        return False
    return not (Path.home() / "Projects").is_dir()


def _apply_roots(roots: list[tuple[str, Path]]) -> None:
    """Point every root global at `roots` at once.

    Callers read `config.PROJECTS_DIRS` and friends at call time, so rebinding
    these three is enough to re-target a running scan — but they must move
    together or the UI tabs and the scanner will disagree.
    """
    global PROJECT_ROOTS, PROJECTS_DIRS, PROJECTS_DIR
    PROJECT_ROOTS = roots
    PROJECTS_DIRS = [p for _, p in roots]
    PROJECTS_DIR = PROJECTS_DIRS[0]


def set_project_roots(entries: list) -> list[tuple[str, Path]]:
    """Persist the user's chosen roots and apply them immediately.

    `entries` accepts either "Label:/path" strings or {"label", "path"} dicts.
    Raises ValueError if nothing usable is left after validation, so a bad
    request cannot leave the app with no roots at all.
    """
    roots: list[tuple[str, Path]] = []
    for item in entries:
        if isinstance(item, dict) and str(item.get("path", "")).strip():
            p = Path(str(item["path"]).strip()).expanduser()
            label = str(item.get("label") or "").strip() or p.name or str(p)
            roots.append((label, p))
        elif isinstance(item, str) and item.strip():
            roots.append(parse_root_entry(item.strip()))

    roots = [(label, p) for label, p in roots if p.is_dir()]
    if not roots:
        raise ValueError("no readable directories among the given project roots")

    settings = load_settings()
    settings["project_roots"] = [{"label": l, "path": str(p)} for l, p in roots]
    save_settings(settings)
    _apply_roots(roots)
    return roots


PROJECT_ROOTS: list[tuple[str, Path]] = []
PROJECTS_DIRS: list[Path] = []
PROJECTS_DIR: Path = Path.home() / "Projects"
_apply_roots(_parse_roots())


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
