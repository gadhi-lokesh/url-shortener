"""
utils.py — Shared helper functions for the URL Shortener.

Responsibilities
----------------
- generate_short_code   : produce a random, URL-safe slug
- validate_url          : check that a string is a well-formed http/https URL
- is_expired            : compare an ISO-8601 expiry string against now (UTC)
- format_link_row       : convert a sqlite3.Row to a plain dict for JSON responses
"""

import re
import secrets
import string
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Short-code generation
# ---------------------------------------------------------------------------

_ALPHABET = string.ascii_letters + string.digits   # A-Z a-z 0-9  (62 chars)
_CUSTOM_CODE_RE = re.compile(r"^[A-Za-z0-9\-]{3,20}$")


def generate_short_code(length: int = 6) -> str:
    """
    Return a random URL-safe slug of `length` characters drawn from
    [A-Za-z0-9].  Using `secrets` ensures cryptographic randomness.

    6 characters → 62^6 ≈ 56 billion unique codes before collision.
    """
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def validate_custom_code(code: str) -> bool:
    """
    Return True if `code` is a valid custom short code:
    - 3–20 characters
    - Only alphanumeric characters and hyphens
    """
    return bool(_CUSTOM_CODE_RE.fullmatch(code))


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

def validate_url(url: str) -> bool:
    """
    Return True when `url` is a well-formed absolute URL with an http or
    https scheme and a non-empty host.  Uses Python's urllib.parse so no
    third-party library is required.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    # Reject bare IPs without a dot (e.g. "http://localhost") if you want;
    # for maximum compatibility we allow them.
    return True


# ---------------------------------------------------------------------------
# Expiry helpers
# ---------------------------------------------------------------------------

def utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (no timezone suffix)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def expiry_from_days(days: int) -> str:
    """Return an ISO-8601 UTC datetime string `days` days from now."""
    from datetime import timedelta
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def is_expired(expires_at: Optional[str]) -> bool:
    """
    Return True if `expires_at` is a non-None ISO-8601 string that is
    earlier than the current UTC time.
    """
    if not expires_at:
        return False
    try:
        expiry_dt = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        return datetime.now(timezone.utc) > expiry_dt
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Row serialisation
# ---------------------------------------------------------------------------

def format_link_row(row, base_url: str = "") -> dict:
    """
    Convert a sqlite3.Row (or dict) representing a *links* table row into a
    plain Python dict suitable for JSON serialisation.

    Parameters
    ----------
    row      : sqlite3.Row or dict
    base_url : The scheme + host used to build the short URL
               (e.g. "http://localhost:5000").  Leave empty to omit short_url.
    """
    d = dict(row)
    d["is_active"] = bool(d.get("is_active", 1))
    if base_url:
        d["short_url"] = f"{base_url}/{d['short_code']}"
    return d
