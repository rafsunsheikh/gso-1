"""Gate for requests that do not come from this machine.

GSO-1 starts and stops processes, edits files and runs an agent. On loopback
that is fine, you are the only caller. The phone companion means binding a
port the rest of the network can reach, and every one of those capabilities
would come with it.

So: loopback is unchanged and unauthenticated (the desktop app must keep
working exactly as it did), and everything else must present a shared secret.
With no secret configured the answer is no: a misconfigured bind cannot
silently expose the machine.

The token travels either as `Authorization: Bearer <token>` or as the
`gso_token` cookie. The cookie exists because `EventSource` cannot set
headers, and the Ops Room stream is server-sent events.
"""

from __future__ import annotations

import secrets
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from . import config

COOKIE = "gso_token"

LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# Reachable without a token from anywhere: the phone's shell and what it needs
# to paint a login screen. None of these read or change anything.
#
# Matched on a path boundary, never as a bare string prefix: "/m" must cover
# "/m" and "/m/login" while leaving a future "/models" or "/metrics" behind the
# gate. A plain startswith here would hand out any route that happened to begin
# with the same letters.
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/m",
    "/static",
    "/api/remote/login",
    "/health",
)

# Single files served from the root, where there is no boundary to match on.
PUBLIC_FILES: frozenset[str] = frozenset({
    "/manifest.webmanifest",
    "/favicon.ico",
    "/favicon.svg",
    "/favicon.png",
})


def is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in LOOPBACK


def _presented(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(COOKIE, "")


def token_ok(presented: str) -> bool:
    """Constant-time compare; an unset token can never match."""
    if not config.MOBILE_TOKEN:
        return False
    return secrets.compare_digest(presented, config.MOBILE_TOKEN)


def _public(path: str, prefixes: Iterable[str]) -> bool:
    if path in PUBLIC_FILES:
        return True
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes)


class RemoteAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if is_loopback(request) or _public(request.url.path, PUBLIC_PREFIXES):
            return await call_next(request)

        if token_ok(_presented(request)):
            return await call_next(request)

        if request.url.path.startswith("/api/") or request.url.path.startswith("/v1/"):
            return JSONResponse(
                {"error": "unauthorized",
                 "detail": "This GSO-1 requires a token for access from another device."},
                status_code=401,
            )
        # A browser that wandered in gets the phone's login screen, not a 401.
        return RedirectResponse("/m", status_code=307)
