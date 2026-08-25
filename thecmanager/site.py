"""Edit a Jekyll site from the dashboard, CMS-style.

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

from . import git_ops, events


def _jsonable(v):
    """Make YAML-parsed front matter JSON-serializable (dates -> ISO strings)."""
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    return v

# Which Jekyll checkout to edit. There is no sensible default, it is one
# specific repo on one specific machine, so the CMS stays switched off until
# MANAGER_SITE_DIR names one.
_SITE_ENV = os.environ.get("MANAGER_SITE_DIR", "").strip()
SITE_DIR = Path(_SITE_ENV).expanduser() if _SITE_ENV else None

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
    """True only once MANAGER_SITE_DIR names a real Jekyll checkout.

    Every other entry point in this module goes through here first, so an
    unset SITE_DIR turns the CMS off rather than raising on a None path.
    """
    return SITE_DIR is not None and SITE_DIR.is_dir() and (SITE_DIR / ".git").exists()


def _require_site() -> Path:
    """The configured site, or a clear error instead of a None path.

    Every code path that touches the filesystem goes through here, so an
    unconfigured CMS answers 400 with a usable message rather than raising a
    TypeError deep in a path join.
    """
    if not configured():
        raise ValueError(
            "the site CMS is not configured, set MANAGER_SITE_DIR to a Jekyll checkout"
        )
    return SITE_DIR


def _coll_dir(coll: str) -> Path:
    root = _require_site()
    if coll not in _VALID:
        raise ValueError(f"unknown collection: {coll}")
    return root / coll


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
    _require_site()
    out = []
    for folder, label in COLLECTIONS:
        d = SITE_DIR / folder
        if d.is_dir():
            out.append({"id": folder, "label": label, "count": len(_md_files(d))})
    return out


def list_items(coll: str) -> list[dict]:
    """Every item in a collection, newest edit first.

    The content table shows a permalink and an edited time next to each row,
    so both come back with the listing rather than costing a request per row.
    """
    out = []
    for f in _md_files(_coll_dir(coll)):
        fm, _ = _parse(f.read_text(errors="ignore"))
        out.append({
            "file": f.name,
            "title": fm.get("title") or f.stem,
            "permalink": fm.get("permalink") or "",
            "modified": f.stat().st_mtime,
        })
    out.sort(key=lambda i: -i["modified"])
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
    _require_site()
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
    _require_site()
    msg = (message or "").strip() or "Update site content via GSO-1"
    c = git_ops.commit_all(SITE_DIR, msg)
    if not c["ok"]:
        return {"ok": False, "step": "commit", "output": c["output"]}
    p = git_ops.push(SITE_DIR)
    events.record("site" if p["ok"] else "fail", SITE_DIR.name,
                  "published, Pages will rebuild" if p["ok"] and not c.get("nochange")
                  else "nothing to publish" if c.get("nochange") else "publish failed")
    return {
        "ok": p["ok"],
        "committed": c["output"],
        "pushed": p["output"],
        "nochange": c.get("nochange", False),
    }


def overview() -> dict:
    if not configured():
        return {"configured": False, "dir": str(SITE_DIR) if SITE_DIR else ""}
    return {
        "configured": True,
        "dir": str(SITE_DIR),
        "collections": collections(),
        "status": status(),
    }
