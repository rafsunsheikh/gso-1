"""Issue GSO-1 licence keys. Seller-side: this never ships with the app.

    python tools/licence.py keygen                 # once, ever
    python tools/licence.py issue --email a@b.com --tier pro

`keygen` writes the signing key to ~/.gso1/signing-key.json and prints the
public half to paste into thecmanager/licensing.py. Guard that file: anyone
holding it can mint keys, and rotating it invalidates every key already sold.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from thecmanager import ed25519 as ed                      # noqa: E402

KEY_FILE = Path.home() / ".gso1" / "signing-key.json"


def _clamp(seed: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8            # clear the low 3 bits
    a |= 1 << 254                  # set the high bit, clear the one above it
    return a, h[32:]


def _compress(point: tuple) -> bytes:
    zinv = pow(point[2], ed._P - 2, ed._P)
    x = point[0] * zinv % ed._P
    y = point[1] * zinv % ed._P
    return ((y | ((x & 1) << 255)).to_bytes(32, "little"))


def _public(seed: bytes) -> bytes:
    a, _ = _clamp(seed)
    return _compress(ed._mul(a, ed._G))


def _sign(seed: bytes, message: bytes) -> bytes:
    a, prefix = _clamp(seed)
    pub = _compress(ed._mul(a, ed._G))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % ed._L
    big_r = _compress(ed._mul(r, ed._G))
    h = int.from_bytes(hashlib.sha512(big_r + pub + message).digest(), "little") % ed._L
    s = (r + h * a) % ed._L
    return big_r + s.to_bytes(32, "little")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def keygen(force: bool) -> None:
    if KEY_FILE.exists() and not force:
        sys.exit(f"{KEY_FILE} already exists. Pass --force only if you mean to "
                 "invalidate every key you have ever issued.")
    seed = secrets.token_bytes(32)
    pub = _public(seed)
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(json.dumps({"seed": seed.hex(), "public": pub.hex()},
                                   indent=2), encoding="utf-8")
    os.chmod(KEY_FILE, 0o600)
    print(f"signing key written to {KEY_FILE} (mode 600) - back this up somewhere safe")
    print("\npaste this into thecmanager/licensing.py:\n")
    print(f'PUBLIC_KEY_HEX = "{pub.hex()}"')


def issue(email: str, tier: str, name: str | None, days: int | None) -> None:
    if not KEY_FILE.exists():
        sys.exit("No signing key. Run: python tools/licence.py keygen")
    seed = bytes.fromhex(json.loads(KEY_FILE.read_text(encoding="utf-8"))["seed"])
    claim: dict = {"e": email, "t": tier, "i": int(time.time())}
    if name:
        claim["n"] = name
    if days:
        claim["x"] = int(time.time() + days * 86400)
    # Sorted and tight, so the same claim always signs to the same bytes.
    payload = json.dumps(claim, sort_keys=True, separators=(",", ":")).encode("utf-8")
    print(f"GSO1.{_b64e(payload)}.{_b64e(_sign(seed, payload))}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("keygen", help="create the signing key (once)")
    g.add_argument("--force", action="store_true")
    i = sub.add_parser("issue", help="sign a key for a buyer")
    i.add_argument("--email", required=True)
    i.add_argument("--tier", default="pro", choices=["free", "pro"])
    i.add_argument("--name")
    i.add_argument("--days", type=int, help="expiring licence; omit for perpetual")
    args = ap.parse_args()
    if args.cmd == "keygen":
        keygen(args.force)
    else:
        issue(args.email, args.tier, args.name, args.days)


if __name__ == "__main__":
    main()
