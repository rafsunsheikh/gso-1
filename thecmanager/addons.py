"""Optional add-ons: extra tabs that live outside this repository.

An add-on is a Python package in the `pro/` folder next to `thecmanager/` (or in
GSO1_ADDONS_DIR). It describes itself with a module-level `GSO1_ADDON` dict:

    GSO1_ADDON = {
        "id": "notes",                 # url-safe, unique
        "label": "Notes",              # sidebar group name
        "views": [{"id": "inbox", "label": "Inbox"}],
        "feature": "notes",            # licence feature; every add-on is Pro
        "router": APIRouter(...),      # served under /api/addons/<id>
        "pages": APIRouter(...),       # optional: plain pages under /x/<id> (for example the
                                       # address a sign-in with Google returns to)
        "static": Path(...),           # served under /addons/<id>/
        "scripts": ["app.js"], "styles": ["app.css"],  # loaded by the page
        "startup": callable,           # optional background work, only when licensed
    }

Every add-on is a Pro feature: its API answers 402 and its scripts are not
loaded until a licence is active. Nothing here knows what any add-on does, so
this file is the whole of what the public repository says about them.
"""

from __future__ import annotations

import importlib
import os
import sys
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from . import licensing

_loaded: list[dict[str, Any]] = []
_errors: list[dict[str, str]] = []
_started: set[str] = set()
_lock = threading.Lock()


def addon_dirs() -> list[Path]:
    """GSO1_ADDONS_DIR when set (it replaces the default), else `pro/` beside the app."""
    chosen = [Path(d) for d in os.environ.get("GSO1_ADDONS_DIR", "").split(os.pathsep) if d]
    if chosen:
        return [d for d in chosen if d.is_dir()]
    # A frozen build (PyInstaller) unpacks bundled files under sys._MEIPASS.
    frozen = getattr(sys, "_MEIPASS", None)
    base = Path(frozen) if frozen else Path(__file__).resolve().parent.parent
    return [d for d in [base / "pro"] if d.is_dir()]


def discover() -> list[dict[str, Any]]:
    """Import every add-on package found. A broken add-on is reported, never fatal."""
    found: list[dict[str, Any]] = []
    for base in addon_dirs():
        if str(base) not in sys.path:
            sys.path.insert(0, str(base))
        # A frozen (Pro) build carries the add-ons as compiled code, named in pro/ADDONS;
        # a source checkout has them as package folders.
        listed = base / "ADDONS"
        if listed.is_file():
            names = [n.strip() for n in listed.read_text().splitlines() if n.strip() and not n.startswith("#")]
        else:
            names = sorted(p.name for p in base.iterdir() if (p / "__init__.py").is_file())
        for name in names:
            try:
                module = importlib.import_module(name)
            except Exception as e:  # noqa: BLE001
                _errors.append({"addon": name, "error": f"{type(e).__name__}: {e}"})
                continue
            spec = getattr(module, "GSO1_ADDON", None)
            if isinstance(spec, dict) and spec.get("id") and not any(a["id"] == spec["id"] for a in found):
                found.append(spec)
    return found


def _gate(feature: str):
    def check() -> None:
        if not licensing.allows(feature):
            raise HTTPException(status_code=402, detail="This is part of GSO-1 Pro. Add your licence key in Settings.")
    return check


def load(app: FastAPI) -> list[dict[str, Any]]:
    """Mount every add-on's API and files. Called once, at import of the server."""
    for spec in discover():
        aid = spec["id"]
        feature = spec.get("feature") or aid
        licensing.register_pro_feature(feature)
        router = spec.get("router")
        if isinstance(router, APIRouter):
            app.include_router(router, prefix=f"/api/addons/{aid}", dependencies=[Depends(_gate(feature))])
        pages = spec.get("pages")
        if isinstance(pages, APIRouter):
            app.include_router(pages, prefix=f"/x/{aid}", dependencies=[Depends(_gate(feature))])
        static = spec.get("static")
        if static and Path(static).is_dir():
            app.mount(f"/addons/{aid}", StaticFiles(directory=str(static)), name=f"addon-{aid}")
        _loaded.append(spec)
    return _loaded


def start_licensed() -> None:
    """Start each licensed add-on's background work, once. Safe to call again later
    (after a licence is entered)."""
    for spec in _loaded:
        aid = spec["id"]
        startup = spec.get("startup")
        if not callable(startup) or aid in _started:
            continue
        if not licensing.allows(spec.get("feature") or aid):
            continue
        with _lock:
            if aid in _started:
                continue
            _started.add(aid)
        try:
            startup()
        except Exception as e:  # noqa: BLE001
            _errors.append({"addon": aid, "error": f"startup failed: {type(e).__name__}: {e}"})


def manifest() -> dict[str, Any]:
    """What the page needs to add the tabs: names, views, files, and whether they are unlocked."""
    out = []
    for spec in _loaded:
        aid = spec["id"]
        unlocked = licensing.allows(spec.get("feature") or aid)
        out.append({
            "id": aid,
            "label": spec.get("label", aid),
            "views": spec.get("views", []),
            "licensed": unlocked,
            "scripts": [f"/addons/{aid}/{s}" for s in spec.get("scripts", [])] if unlocked else [],
            "styles": [f"/addons/{aid}/{s}" for s in spec.get("styles", [])] if unlocked else [],
        })
    return {"addons": out, "errors": list(_errors)}
