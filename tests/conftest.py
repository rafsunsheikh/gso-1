"""Shared setup: every test runs against a throwaway data folder and project root."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="gso1-test-")
os.environ.setdefault("MANAGER_DATA_DIR", str(Path(_tmp) / "data"))
os.environ.setdefault("MANAGER_PROJECTS_DIRS", f"Tests:{_tmp}")
os.environ.setdefault("MANAGER_NO_BROWSER", "1")
# No add-ons unless a test brings its own: point the loader at an empty folder.
_empty = Path(_tmp) / "no-addons"
_empty.mkdir(exist_ok=True)
os.environ.setdefault("GSO1_ADDONS_DIR", str(_empty))
