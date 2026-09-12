"""Ed25519 signature verification, in pure Python (RFC 8032).

A licence key has to be checked on a machine that may be offline, which means
the check happens here rather than on a server. Verification needs a public
key only, so the app can carry one openly; the private key that signs keys
never leaves the seller's machine.

This is verification alone, and deliberately not a general crypto library: it
is a few hundred microseconds once at startup, so there is no reason to take
on `cryptography` and a wheel per platform for it. GSO-1 ships with two
dependencies and this keeps it that way.

Correctness is not taken on trust: `selftest()` runs the RFC 8032 section 7.1
vectors, and the licence module refuses to accept any key if it fails.
"""
from __future__ import annotations

import hashlib

# The curve, as RFC 8032 defines it.
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

# Points are kept in extended coordinates (X, Y, Z, T), where x = X/Z, y = Y/Z
# and T = XY/Z. Addition is then complete: no special case for doubling, and
# no branch that depends on the secret, which there isn't one of here anyway.
_IDENTITY = (0, 1, 1, 0)


def _recover_x(y: int, sign: int) -> int | None:
    """The x that goes with this y on the curve, or None if there isn't one."""
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _add(p: tuple, q: tuple) -> tuple:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p: tuple) -> tuple:
    out = _IDENTITY
    while s > 0:
        if s & 1:
            out = _add(out, p)
        p = _add(p, p)
        s >>= 1
    return out


def _same(p: tuple, q: tuple) -> bool:
    # Projective: compare x and y as fractions rather than normalising.
    return ((p[0] * q[2] - q[0] * p[2]) % _P == 0
            and (p[1] * q[2] - q[1] * p[2]) % _P == 0)


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0) or 0
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _decompress(data: bytes) -> tuple | None:
    """Turn 32 encoded bytes back into a point, or None if they are not one."""
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True when `signature` is this public key's signature over `message`."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:                       # a malleable signature is not a valid one
        return False
    digest = hashlib.sha512(signature[:32] + public_key + message).digest()
    h = int.from_bytes(digest, "little") % _L
    return _same(_mul(s, _G), _add(r, _mul(h, a)))


# RFC 8032 section 7.1, test vectors 1, 2 and 3: public key, message, signature.
_VECTORS = (
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
)


def selftest() -> bool:
    """Check this implementation against the vectors in the standard."""
    for pub, msg, sig in _VECTORS:
        if not verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig)):
            return False
    # And reject a signature that has been tampered with.
    pub, msg, sig = _VECTORS[1]
    bad = bytearray(bytes.fromhex(sig))
    bad[0] ^= 1
    return not verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes(bad))
