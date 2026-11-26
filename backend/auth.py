"""HTTP Basic auth check, kept free of FastAPI/DB imports so it can be unit tested alone."""

from __future__ import annotations

import base64
import binascii
import secrets


def credentials_configured(user: str, password: str) -> bool:
    return bool(user and password)


def basic_auth_valid(header: str, user: str, password: str) -> bool:
    """True only if `header` is a well-formed Basic Authorization header matching user/password.
    Always False when either expected value is empty - an unset credential must never match."""
    if not credentials_configured(user, password) or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    given_user, _, given_password = decoded.partition(":")
    # Both compared unconditionally (no short-circuit) so timing doesn't reveal which was wrong.
    user_ok = secrets.compare_digest(given_user.encode(), user.encode())
    password_ok = secrets.compare_digest(given_password.encode(), password.encode())
    return user_ok and password_ok
