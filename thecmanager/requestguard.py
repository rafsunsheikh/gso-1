"""Refuses requests that a web page made, rather than you.

GSO-1 trusts callers on this machine (see remoteauth.py). A browser is one: any
page you have open can ask it to load http://127.0.0.1:8420/... . Two tricks
follow from that, and this middleware closes both:

1. Commands from another site. A page on example.com can send a POST (a form,
   or fetch with mode "no-cors") to start a project or run the agent. It never
   sees the answer, but the command still runs. Browsers say where a request
   came from (the Origin and Sec-Fetch-Site headers), so a request that a
   browser marks as coming from another site, or another port, is refused.

2. DNS rebinding. A site points its own name at 127.0.0.1 and then reads
   GSO-1's answers as if they were its own. The browser then sends that site's
   name as Host, so only Host names that mean "this machine" are accepted (plus
   any in MANAGER_ALLOWED_HOSTS, for a phone on the local network or Tailscale).

Programs on this machine (curl, the Ops Room sidecar, Claude Code through the
proxy) send none of the browser headers and are not affected.
"""

from __future__ import annotations

import ipaddress
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOCAL_NAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def allowed_hostnames() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("MANAGER_ALLOWED_HOSTS", "").split(",") if h.strip()}
    bind = os.environ.get("MANAGER_HOST", "").strip().lower()
    if bind and bind not in ("0.0.0.0", "::"):
        extra.add(bind)
    return set(LOCAL_NAMES) | extra


def hostname_of(host: str) -> str:
    """'127.0.0.1:8420' -> '127.0.0.1', '[::1]:8420' -> '[::1]'."""
    h = host.strip().lower()
    if h.startswith("["):
        return h[: h.find("]") + 1]
    return h.split(":")[0]


def host_ok(host: str | None) -> bool:
    """This machine's names, configured names, or a plain IP address. A rebinding
    attack needs a domain name, so an address like 192.168.1.5 (the phone
    companion on the local network) is safe to accept."""
    if not host:
        return False
    name = hostname_of(host)
    if name in allowed_hostnames():
        return True
    try:
        ipaddress.ip_address(name.strip("[]"))
        return True
    except ValueError:
        return False


def verdict(
    method: str,
    host: str | None,
    origin: str | None,
    fetch_site: str | None,
    path: str = "/",
    fetch_mode: str | None = None,
    fetch_dest: str | None = None,
) -> str | None:
    """None when the request may go ahead, else the reason it is refused."""
    if not host_ok(host):
        return "Unknown host"
    site = (fetch_site or "").lower()
    # Browsers mark where a request came from. "same-site" covers another port on
    # this machine (another local app), which is still not GSO-1's own page.
    if site in ("cross-site", "same-site"):
        # Opening GSO-1 from a link, a bookmark or a new tab is a plain page visit:
        # allowed, as long as it is a whole page (not inside another site's frame)
        # and not an API route (a link must never run a command).
        page_visit = (
            method.upper() == "GET"
            and (fetch_mode or "").lower() == "navigate"
            and (fetch_dest or "").lower() == "document"
            and not path.startswith(("/api/", "/v1/"))
        )
        if not page_visit:
            return "Request from another site"
    if method.upper() in SAFE_METHODS:
        return None
    # Older browsers send Origin but not Sec-Fetch-Site on a POST.
    if origin and origin != "null":
        own = host.strip().lower()
        if origin.lower().split("://", 1)[-1].rstrip("/") != own:
            return "Request from another site"
    elif origin == "null":
        return "Request from another site"
    return None


class RequestGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        reason = verdict(
            request.method,
            request.headers.get("host"),
            request.headers.get("origin"),
            request.headers.get("sec-fetch-site"),
            request.url.path,
            request.headers.get("sec-fetch-mode"),
            request.headers.get("sec-fetch-dest"),
        )
        if reason:
            return JSONResponse({"error": "forbidden", "detail": reason}, status_code=403)
        return await call_next(request)
