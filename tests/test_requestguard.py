"""Web pages must not be able to drive GSO-1; this machine's own tools still can."""
import pytest
from fastapi.testclient import TestClient

from thecmanager import requestguard
from thecmanager.requestguard import verdict
from thecmanager.server import app

OWN = "127.0.0.1:8420"


@pytest.fixture
def client():
    # Not used as a context manager: the app's startup work (scheduler, bot) stays off.
    return TestClient(app, base_url="http://127.0.0.1:8420", client=("127.0.0.1", 50000))


def test_own_page_and_local_tools_are_allowed():
    assert verdict("GET", OWN, None, "same-origin") is None
    assert verdict("POST", OWN, "http://127.0.0.1:8420", "same-origin") is None
    # curl, the Ops Room sidecar, Claude Code through the proxy: no browser headers.
    assert verdict("POST", OWN, None, None) is None
    # Typed into the address bar.
    assert verdict("GET", OWN, None, "none") is None


def test_other_sites_cannot_send_commands():
    assert verdict("POST", OWN, "https://evil.example", "cross-site") == "Request from another site"
    # Older browsers: Origin only.
    assert verdict("POST", OWN, "https://evil.example", None) == "Request from another site"
    assert verdict("POST", OWN, "null", None) == "Request from another site"
    # Another app on this machine (a different port) is not GSO-1's own page either.
    assert verdict("POST", OWN, "http://127.0.0.1:3200", "same-site") == "Request from another site"
    # Even a plain GET, like an <img> pointed at an API route.
    assert verdict("GET", OWN, None, "cross-site") == "Request from another site"


def test_opening_gso1_from_a_link_or_new_tab_works():
    assert verdict("GET", OWN, None, "cross-site", "/", "navigate", "document") is None
    # ...but not inside another site's frame, and a link never runs an API command.
    assert verdict("GET", OWN, None, "cross-site", "/", "navigate", "iframe") == "Request from another site"
    assert verdict("GET", OWN, None, "cross-site", "/api/llm/install", "navigate", "document") == "Request from another site"
    assert verdict("POST", OWN, "https://evil.example", "cross-site", "/", "navigate", "document") == "Request from another site"


def test_dns_rebinding_is_refused():
    assert verdict("GET", "evil.example:8420", None, "same-origin") == "Unknown host"
    assert verdict("GET", None, None, None) == "Unknown host"


def test_phone_on_the_network_and_configured_names(monkeypatch):
    assert verdict("GET", "192.168.1.5:8420", None, "same-origin") is None
    assert verdict("GET", "my-mac.tail1234.ts.net", None, None) == "Unknown host"
    monkeypatch.setenv("MANAGER_ALLOWED_HOSTS", "my-mac.tail1234.ts.net")
    assert requestguard.host_ok("my-mac.tail1234.ts.net")


def test_middleware_in_the_running_app(client):
    assert client.get("/api/settings/roots").status_code == 200
    r = client.get("/api/settings/roots", headers={"host": "evil.example:8420"})
    assert r.status_code == 403
    r = client.post("/api/llm/stop", headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"})
    assert r.status_code == 403
