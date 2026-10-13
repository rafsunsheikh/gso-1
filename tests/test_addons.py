"""Add-ons appear as Pro tabs: locked without a licence, served with one."""
import json
import textwrap

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thecmanager import addons, licensing


@pytest.fixture
def addon_dir(tmp_path, monkeypatch):
    pkg = tmp_path / "sample_addon"
    (pkg / "web").mkdir(parents=True)
    (pkg / "web" / "app.js").write_text("window.sampleLoaded = true;")
    (pkg / "__init__.py").write_text(textwrap.dedent('''
        from pathlib import Path
        from fastapi import APIRouter
        router = APIRouter()
        started = []

        @router.get("/hello")
        def hello():
            return {"hello": "world"}

        pages = APIRouter()

        @pages.get("/back")
        def back():
            return {"page": "back"}

        GSO1_ADDON = {
            "id": "sample", "label": "Sample", "feature": "sample-feature",
            "views": [{"id": "home", "label": "Home"}],
            "router": router, "pages": pages, "static": Path(__file__).parent / "web",
            "scripts": ["app.js"], "startup": lambda: started.append(1),
        }
    '''))
    monkeypatch.setenv("GSO1_ADDONS_DIR", str(tmp_path))
    monkeypatch.setattr(addons, "_loaded", [])
    monkeypatch.setattr(addons, "_started", set())
    return tmp_path


def _client(app):
    return TestClient(app, base_url="http://127.0.0.1:8420", client=("127.0.0.1", 50000))


def test_locked_without_a_licence(addon_dir, monkeypatch):
    monkeypatch.setattr(licensing, "tier", lambda: "free")
    app = FastAPI()
    addons.load(app)
    c = _client(app)
    assert c.get("/api/addons/sample/hello").status_code == 402
    assert c.get("/x/sample/back").status_code == 402
    m = addons.manifest()["addons"][0]
    assert m["licensed"] is False and m["scripts"] == []
    assert "sample-feature" in licensing.pro_features()
    addons.start_licensed()
    import sample_addon
    assert sample_addon.started == []


def test_unlocked_with_a_licence(addon_dir, monkeypatch):
    monkeypatch.setattr(licensing, "tier", lambda: "pro")
    app = FastAPI()
    addons.load(app)
    c = _client(app)
    assert c.get("/api/addons/sample/hello").json() == {"hello": "world"}
    assert c.get("/x/sample/back").json() == {"page": "back"}
    assert "sampleLoaded" in c.get("/addons/sample/app.js").text
    m = addons.manifest()["addons"][0]
    assert m["licensed"] is True and m["scripts"] == ["/addons/sample/app.js"]
    addons.start_licensed()
    addons.start_licensed()
    import sample_addon
    assert sample_addon.started == [1]


def test_a_broken_addon_is_reported_not_fatal(tmp_path, monkeypatch):
    (tmp_path / "broken_addon").mkdir()
    (tmp_path / "broken_addon" / "__init__.py").write_text("raise RuntimeError('nope')")
    monkeypatch.setenv("GSO1_ADDONS_DIR", str(tmp_path))
    monkeypatch.setattr(addons, "_loaded", [])
    monkeypatch.setattr(addons, "_errors", [])
    addons.load(FastAPI())
    assert addons.manifest()["errors"][0]["addon"] == "broken_addon"


def test_licence_routes_in_the_app():
    from thecmanager.server import app
    c = _client(app)
    assert c.get("/api/license").json()["tier"] in ("free", "pro")
    r = c.post("/api/license", json={"key": "GSO1.not.valid"}, headers={"origin": "http://127.0.0.1:8420"})
    assert r.status_code == 400 and r.json()["ok"] is False
    assert c.get("/api/addons").json() == {"addons": [], "errors": []}
