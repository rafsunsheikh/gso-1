"""Edit a Jekyll site (your-site.github.io) from the dashboard, CMS-style.

Each Markdown file in a collection is an editable item: YAML front matter
(rendered as form fields in the UI) plus a Markdown body. Save writes the file;
Publish stages everything, commits, and pushes to the GitHub remote.
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path

import yaml

from . import git_ops


def _jsonable(v):
    """Make YAML-parsed front matter JSON-serializable (dates -> ISO strings)."""
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    return v

SITE_DIR = Path(
    os.environ.get("MANAGER_SITE_DIR", str(Path.home() / "Projects" / "your-site.github.io"))
).expanduser()

# Jekyll collections to expose (folder -> label); trimmed to those that exist.
COLLECTIONS = [
    ("_pages", "Pages"),
    ("_posts", "Blog Posts"),
    ("_portfolio", "Portfolio"),
    ("_publications", "Publications"),
    ("_talks", "Talks"),
    ("_teaching", "Teaching"),
]
_VALID = {folder for folder, _ in COLLECTIONS}
_LABELS = dict(COLLECTIONS)

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


# ---- helpers --------------------------------------------------------------
def configured() -> bool:
    return SITE_DIR.is_dir() and (SITE_DIR / ".git").exists()


def _coll_dir(coll: str) -> Path:
    if coll not in _VALID:
        raise ValueError(f"unknown collection: {coll}")
    return SITE_DIR / coll


def _safe_file(coll: str, name: str) -> Path:
    d = _coll_dir(coll)
    p = (d / name).resolve()
    if not str(p).startswith(str(d.resolve()) + os.sep):
        raise ValueError("invalid path")
    return p


def _md_files(d: Path) -> list[Path]:
    return sorted([*d.glob("*.md"), *d.glob("*.markdown")])


def _parse(text: str) -> tuple[dict, str]:
    """Split YAML front matter from the Markdown body."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return (fm if isinstance(fm, dict) else {}), m.group(2)


def _compose(frontmatter: dict, body: str) -> str:
    y = yaml.safe_dump(
        frontmatter or {}, sort_keys=False, allow_unicode=True,
        default_flow_style=False,
    ).strip()
    return f"---\n{y}\n---\n\n{(body or '').lstrip(chr(10))}\n"


# ---- read -----------------------------------------------------------------
def collections() -> list[dict]:
    out = []
    for folder, label in COLLECTIONS:
        d = SITE_DIR / folder
        if d.is_dir():
            out.append({"id": folder, "label": label, "count": len(_md_files(d))})
    return out


def list_items(coll: str) -> list[dict]:
    out = []
    for f in _md_files(_coll_dir(coll)):
        fm, _ = _parse(f.read_text(errors="ignore"))
        out.append({"file": f.name, "title": fm.get("title") or f.stem})
    return out


def read_item(coll: str, name: str) -> dict:
    f = _safe_file(coll, name)
    fm, body = _parse(f.read_text(errors="ignore"))
    return {"collection": coll, "file": f.name, "frontmatter": _jsonable(fm), "body": body}


# ---- write ----------------------------------------------------------------
def save_item(coll: str, name: str, frontmatter: dict, body: str) -> dict:
    f = _safe_file(coll, name)
    if not f.exists():
        return {"ok": False, "output": "file not found"}
    f.write_text(_compose(frontmatter, body))
    return {"ok": True, "file": f.name}


def create_item(coll: str, filename: str, frontmatter: dict, body: str) -> dict:
    if not filename.endswith((".md", ".markdown")):
        filename += ".md"
    f = _safe_file(coll, filename)
    if f.exists():
        return {"ok": False, "output": "a file with that name already exists"}
    f.write_text(_compose(frontmatter or {"title": "Untitled"}, body or ""))
    return {"ok": True, "collection": coll, "file": f.name}


# ---- publish --------------------------------------------------------------
def status() -> dict:
    st = git_ops.status(SITE_DIR)
    return {
        "branch": st.get("branch"),
        "dirty": st.get("dirty", False),
        "changed_count": st.get("changed_count", 0),
        "changed_files": st.get("changed_files", []),
        "ahead": st.get("ahead", 0),
        "remote": st.get("remote"),
    }


def publish(message: str) -> dict:
    msg = (message or "").strip() or "Update site content via GSO-1"
    c = git_ops.commit_all(SITE_DIR, msg)
    if not c["ok"]:
        return {"ok": False, "step": "commit", "output": c["output"]}
    p = git_ops.push(SITE_DIR)
    return {
        "ok": p["ok"],
        "committed": c["output"],
        "pushed": p["output"],
        "nochange": c.get("nochange", False),
    }


def overview() -> dict:
    if not configured():
        return {"configured": False, "dir": str(SITE_DIR)}
    return {
        "configured": True,
        "dir": str(SITE_DIR),
        "collections": collections(),
        "status": status(),
    }
