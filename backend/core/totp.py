"""TOTP (RFC 6238) — stdlib-only implementation.

Compatible with Google Authenticator, Microsoft Authenticator, Authy, 1Password
and any other RFC-6238-compliant TOTP app.

Defaults:
  * HMAC-SHA1 (the only algorithm those apps implement universally)
  * 30-second period
  * 6 digits
  * ±1 period tolerance on verification to accommodate clock drift
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse


DEFAULT_PERIOD = 30
DEFAULT_DIGITS = 6
DEFAULT_ALGORITHM = "SHA1"


# ---------- Secret management ----------


def generate_secret(num_bytes: int = 20) -> str:
    """Return a freshly random base32-encoded TOTP secret.

    20 bytes (160 bits) is the RFC-4226 recommended size for HMAC-SHA1.
    """
    if num_bytes < 16:
        raise ValueError("TOTP secret must be at least 16 bytes")
    raw = secrets.token_bytes(num_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    """Decode a base32 secret tolerantly (handles missing padding + spaces)."""
    if not isinstance(secret, str):
        raise ValueError("TOTP secret must be a string")
    cleaned = secret.replace(" ", "").replace("-", "").upper()
    if not cleaned:
        raise ValueError("TOTP secret is empty")
    padding = "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(cleaned + padding, casefold=True)
    except (ValueError, Exception) as exc:
        raise ValueError(f"Base32 invalide : {exc}") from None


# ---------- Code generation / verification ----------


def _hotp(secret_bytes: bytes, counter: int, digits: int) -> str:
    """RFC 4226 HOTP. Building block for TOTP."""
    counter_bytes = counter.to_bytes(8, "big")
    digest = hmac.new(secret_bytes, counter_bytes, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = (
        ((digest[offset] & 0x7F) << 24)
        | ((digest[offset + 1] & 0xFF) << 16)
        | ((digest[offset + 2] & 0xFF) << 8)
        | (digest[offset + 3] & 0xFF)
    )
    return str(truncated % (10 ** digits)).zfill(digits)


def current_code(
    secret: str,
    *,
    when: int | None = None,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
) -> str:
    """Compute the TOTP code for ``when`` (defaults to now)."""
    secret_bytes = _decode_secret(secret)
    timestamp = int(when if when is not None else time.time())
    counter = timestamp // period
    return _hotp(secret_bytes, counter, digits)


def verify_code(
    secret: str,
    code: str,
    *,
    when: int | None = None,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
    window: int = 1,
) -> bool:
    """Constant-time verify a 6-digit code against the secret.

    ``window`` allows ±N periods of clock drift (default ±30 s on each side).
    The comparison runs over every candidate to keep timing constant whether
    the match is on the first or the last counter.
    """
    if not isinstance(code, str):
        return False
    sanitized = code.strip().replace(" ", "").replace("-", "")
    if not sanitized.isdigit() or len(sanitized) != digits:
        return False
    try:
        secret_bytes = _decode_secret(secret)
    except ValueError:
        return False
    timestamp = int(when if when is not None else time.time())
    base_counter = timestamp // period

    matched = False
    for offset in range(-window, window + 1):
        candidate = _hotp(secret_bytes, base_counter + offset, digits)
        # `compare_digest` keeps the per-iteration cost constant.
        if hmac.compare_digest(candidate, sanitized):
            matched = True
    return matched


# ---------- Provisioning URI (otpauth://) ----------


def provisioning_uri(
    secret: str,
    *,
    account_name: str,
    issuer: str,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
) -> str:
    """Build an ``otpauth://totp/...`` URI suitable for QR-code scanning."""
    if not account_name:
        raise ValueError("account_name must not be empty")
    if not issuer:
        raise ValueError("issuer must not be empty")
    # Strip padding so the URI matches what Google Auth expects.
    secret_clean = secret.replace("=", "").upper()
    label = urllib.parse.quote(f"{issuer}:{account_name}", safe="")
    params = urllib.parse.urlencode(
        {
            "secret": secret_clean,
            "issuer": issuer,
            "algorithm": DEFAULT_ALGORITHM,
            "digits": digits,
            "period": period,
        }
    )
    return f"otpauth://totp/{label}?{params}"


# ---------- CLI helper ----------

if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="TOTP utilities")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("generate", help="Generate a new secret + URI")
    p_gen.add_argument("--account", default="admin")
    p_gen.add_argument("--issuer", default="SIA-CCM")

    p_code = sub.add_parser("code", help="Print the current code for a secret")
    p_code.add_argument("secret")

    args = parser.parse_args()
    if args.cmd == "generate":
        s = generate_secret()
        print(f"Secret (base32) : {s}")
        print(
            "Provisioning URI:",
            provisioning_uri(s, account_name=args.account, issuer=args.issuer),
        )
        print(f"Code actuel : {current_code(s)}")
    elif args.cmd == "code":
        print(current_code(args.secret))
