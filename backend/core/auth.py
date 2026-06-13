"""Authentication primitives: password hashing + JWT, stdlib-only.

We deliberately avoid `bcrypt`/`passlib`/`PyJWT` dependencies. The crypto used
here is mainstream and audited (HMAC-SHA256, PBKDF2-HMAC-SHA256, constant-time
comparisons via :func:`hmac.compare_digest`).

Password storage format
-----------------------
    pbkdf2_sha256$<iterations>$<base64_salt>$<base64_hash>

JWT format (HS256, RFC 7519)
----------------------------
    <base64url(header)>.<base64url(payload)>.<base64url(signature)>

Both pieces are intentionally compact and easy to inspect by hand for a PFE
defence.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any


# ---------- Password hashing ----------

PBKDF2_ITERATIONS = 200_000  # OWASP 2024 floor for PBKDF2-SHA256
SALT_BYTES = 16


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Return a PBKDF2-SHA256 hash of ``password`` using a fresh random salt."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return (
        f"pbkdf2_sha256${iterations}$"
        f"{base64.b64encode(salt).decode('ascii')}$"
        f"{base64.b64encode(digest).decode('ascii')}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time verify a password against a stored hash."""
    if not isinstance(password, str) or not isinstance(stored_hash, str):
        return False
    try:
        scheme, iters_str, salt_b64, hash_b64 = stored_hash.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iters_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(candidate, expected)


# ---------- JWT (HS256) ----------


@dataclass(frozen=True)
class TokenPayload:
    """Decoded JWT payload."""

    sub: str       # subject (= username)
    iat: int       # issued at (epoch seconds)
    exp: int       # expiry (epoch seconds)
    extra: dict[str, Any]


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_token(
    subject: str,
    *,
    secret: str,
    expires_in_minutes: int = 480,
    extra: dict[str, Any] | None = None,
) -> str:
    """Issue a signed HS256 JWT for ``subject``.

    Args:
        subject: Identifier embedded in ``sub`` (e.g. the username).
        secret: Shared secret used to sign the token (>= 32 chars recommended).
        expires_in_minutes: Lifetime in minutes.
        extra: Optional claims merged into the payload.
    """
    if not subject or not isinstance(subject, str):
        raise ValueError("subject must be a non-empty string")
    if not secret or not isinstance(secret, str):
        raise ValueError("secret must be a non-empty string")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + max(60, expires_in_minutes * 60),
    }
    if extra:
        payload.update(extra)
    header_b64 = _b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


class TokenError(ValueError):
    """Raised for any token verification failure (invalid/expired/tampered)."""


def decode_token(token: str, *, secret: str) -> TokenPayload:
    """Verify and decode a JWT. Raises :class:`TokenError` on any problem."""
    if not isinstance(token, str) or token.count(".") != 2:
        raise TokenError("Token mal formé")
    header_b64, payload_b64, signature_b64 = token.split(".")
    try:
        header = json.loads(_b64url_decode(header_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise TokenError("En-tête JWT illisible") from exc
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        # Defence against algorithm-confusion attacks (alg=none, alg=RS256).
        raise TokenError("Algorithme JWT non supporté")
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        f"{header_b64}.{payload_b64}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        given_sig = _b64url_decode(signature_b64)
    except ValueError as exc:
        raise TokenError("Signature JWT illisible") from exc
    if not hmac.compare_digest(expected_sig, given_sig):
        raise TokenError("Signature JWT invalide")
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise TokenError("Payload JWT illisible") from exc
    sub = payload.get("sub")
    exp = payload.get("exp")
    iat = payload.get("iat")
    if not isinstance(sub, str) or not isinstance(exp, int) or not isinstance(iat, int):
        raise TokenError("Claims JWT invalides")
    if exp < int(time.time()):
        raise TokenError("Token expiré")
    extra = {k: v for k, v in payload.items() if k not in {"sub", "exp", "iat"}}
    return TokenPayload(sub=sub, iat=iat, exp=exp, extra=extra)


# ---------- Convenience for ad-hoc password generation ----------

if __name__ == "__main__":  # pragma: no cover
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m backend.core.auth <password>", file=sys.stderr)
        sys.exit(1)
    print(hash_password(sys.argv[1]))
