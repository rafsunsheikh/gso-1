"""What this copy of GSO-1 is allowed to do.

GSO-1 runs on your machine and talks to nothing, which is the point of it, so
a licence cannot be checked against a server: the key carries its own proof.
It is a short signed statement - who bought it, which tier, when - signed with
a private key that never leaves the seller, and checked here against the public
key below. No network, no phoning home, works on a plane.

This is an honest lock, not a strong one. The check runs on the buyer's machine
in source they can read, and anyone determined can remove it. That is accepted:
the purpose is to make paying the obvious path for people who intend to pay,
not to fight the people who don't. Nothing here is obfuscated for that reason.

The free tier is a real tier, not a trial. It does not expire, it does not nag
on a timer, and everything in FREE_FEATURES keeps working forever.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from typing import Any

from . import config, ed25519

# The public half of the signing key. Replacing this with your own public key
# invalidates every key issued under the old one, so it changes only when the
# signing key is rotated.
PUBLIC_KEY_HEX = "0e480e12b0c1c6369e9b258976eb01c5a39526dd7456abb8be05ec28157e7cf5"

TIERS = ("free", "pro")

# Which tier each part of the app needs. This table is the whole of the
# free/paid split: moving a view between the two lists is the entire change.
FREE_FEATURES = (
    "favourites",     # the dashboard: find your apps and run them
    "vscode",         # the Library: folders, and opening them in an editor
    "planner",        # boards and tasks
    "uncommitted",    # what you have not committed
    "failed",         # what stopped working
    "site",           # the project site
    "settings",
)
PRO_FEATURES = (
    "opsroom",        # the agent sidecar
    "world",          # the 3D worlds
    "llm",            # local model setup
    "remote",         # the phone companion and the Telegram bridge
)

_lock = threading.Lock()
_LICENCE_FILE = config.DATA_DIR / "license.json"
_KEY_PREFIX = "GSO1"


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse(key: str) -> dict[str, Any] | None:
    """Read a key and check its signature. None means it is not a valid key.

    Refuses outright if the verifier fails its own test vectors: a broken
    implementation that says yes is worse than one that says no.
    """
    if not ed25519.selftest():
        return None
    # Split on "." and not "-": base64url spends "-" as a character, which an
    # earlier format separator did too, and keys containing one did not parse.
    parts = (key or "").strip().split(".")
    if len(parts) != 3 or parts[0] != _KEY_PREFIX:
        return None
    try:
        payload, signature = _b64d(parts[1]), _b64d(parts[2])
        if not ed25519.verify(bytes.fromhex(PUBLIC_KEY_HEX), payload, signature):
            return None
        claim = json.loads(payload.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(claim, dict) or claim.get("t") not in TIERS:
        return None
    return claim


def _expired(claim: dict) -> bool:
    exp = claim.get("x")
    return bool(exp) and time.time() > float(exp)


def _stored() -> str:
    if not _LICENCE_FILE.exists():
        return ""
    try:
        return json.loads(_LICENCE_FILE.read_text(encoding="utf-8")).get("key", "")
    except Exception:
        return ""


def status() -> dict[str, Any]:
    """The tier in force, and enough about the licence to show in Settings."""
    with _lock:
        key = _stored()
    if not key:
        return {"tier": "free", "licensed": False, "reason": "no key"}
    claim = parse(key)
    if claim is None:
        return {"tier": "free", "licensed": False, "reason": "key not valid"}
    if _expired(claim):
        return {"tier": "free", "licensed": False, "reason": "licence expired",
                "email": claim.get("e"), "expires": claim.get("x")}
    return {
        "tier": claim.get("t", "free"),
        "licensed": True,
        "email": claim.get("e"),
        "name": claim.get("n"),
        "issued": claim.get("i"),
        "expires": claim.get("x"),
    }


def tier() -> str:
    return status().get("tier", "free")


def allows(feature: str) -> bool:
    """Whether this copy may use `feature`. Anything unlisted is free."""
    if feature in PRO_FEATURES:
        return tier() != "free"
    return True


def activate(key: str) -> dict[str, Any]:
    """Record a key, if it is a real one. Returns the resulting status."""
    claim = parse(key)
    if claim is None:
        return {"ok": False, "error": "That key is not valid.", **status()}
    if _expired(claim):
        return {"ok": False, "error": "That licence has expired.", **status()}
    with _lock:
        config.ensure_dirs()
        tmp = _LICENCE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"key": key.strip(), "activated_at": time.time()},
                                  indent=2), encoding="utf-8")
        tmp.replace(_LICENCE_FILE)
    return {"ok": True, **status()}


def deactivate() -> dict[str, Any]:
    """Forget the key on this machine, so it can be moved to another."""
    with _lock:
        _LICENCE_FILE.unlink(missing_ok=True)
    return {"ok": True, **status()}


def snapshot() -> dict[str, Any]:
    """Everything the UI needs to decide what to show."""
    s = status()
    s["free_features"] = list(FREE_FEATURES)
    s["pro_features"] = list(PRO_FEATURES)
    s["locked"] = [] if s["tier"] != "free" else list(PRO_FEATURES)
    return s
