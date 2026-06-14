"""Configuration and paths for GSO-1."""
import os
from pathlib import Path

# Root folder that holds all your projects.
PROJECTS_DIR = Path(
    os.environ.get("MANAGER_PROJECTS_DIR", str(Path.home() / "Projects"))
).expanduser()

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
