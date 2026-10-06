"""
routes.py — All Flask routes for the URL Shortener & Link Management System.

Endpoints
---------
POST   /api/shorten                  Create a new short link
GET    /<short_code>                 Redirect to original URL  (302 / 410 / 404)
GET    /api/links                    List active links (paginated)
GET    /api/links/<short_code>       Get details of one link
PATCH  /api/links/<short_code>       Update URL or expiry
DELETE /api/links/<short_code>       Soft-delete a link
GET    /api/links/<short_code>/stats Click count + metadata
GET    /api/validate?url=…           Validate a URL without shortening
GET    /health                       Health-check JSON
GET    /                             HTML dashboard
"""

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from .database import get_db
from .utils import (
    expiry_from_days,
    format_link_row,
    generate_short_code,
    is_expired,
    utcnow_iso,
    validate_custom_code,
    validate_url,
)

bp = Blueprint("shortener", __name__)

# ---------------------------------------------------------------------------
# Helper — consistent error envelope
# ---------------------------------------------------------------------------

def _err(message: str, code: str, status: int):
    return jsonify({"error": message, "code": code}), status


def _base_url() -> str:
    """Build scheme://host[:port] from the incoming request."""
    return request.host_url.rstrip("/")


# ---------------------------------------------------------------------------
# Health-check
# ---------------------------------------------------------------------------

@bp.get("/health")
def health():
    """Simple liveness probe — always returns 200 when the app is running."""
    return jsonify({"status": "ok", "service": "url-shortener"}), 200


# ---------------------------------------------------------------------------
# POST /api/shorten — Create a short link
# ---------------------------------------------------------------------------

@bp.post("/api/shorten")
def shorten():
    """
    Create a new shortened URL.

    Request JSON body:
    {
        "url":            "https://...",   (required)
        "custom_code":    "my-link",       (optional, 3-20 chars [A-Za-z0-9-])
        "expires_in_days": 30              (optional, integer > 0)
    }

    Returns 201 on success, or an error envelope on failure.
    """
    body = request.get_json(silent=True) or {}

    # --- Validate original URL ---
    original_url = (body.get("url") or "").strip()
    if not original_url:
        return _err("Field 'url' is required.", "MISSING_URL", 400)
    if not validate_url(original_url):
        return _err(
            "Invalid URL. Must be an absolute http or https URL.",
            "INVALID_URL",
            422,
        )

    # --- Optional custom code ---
    custom_code = (body.get("custom_code") or "").strip()
    if custom_code:
        if not validate_custom_code(custom_code):
            return _err(
                "custom_code must be 3–20 characters: letters, digits, or hyphens only.",
                "INVALID_CUSTOM_CODE",
                400,
            )

    # --- Optional expiry ---
    expires_at = None
    expires_in_days = body.get("expires_in_days")
    if expires_in_days is not None:
        try:
            expires_in_days = int(expires_in_days)
            if expires_in_days <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return _err(
                "expires_in_days must be a positive integer.",
                "INVALID_EXPIRY",
                400,
            )
        expires_at = expiry_from_days(expires_in_days)

    db = get_db()
    created_at = utcnow_iso()

    # --- Resolve short code (custom or generated) ---
    if custom_code:
        short_code = custom_code
        existing = db.execute(
            "SELECT id, is_active FROM links WHERE short_code = ?", (short_code,)
        ).fetchone()
        if existing:
            return _err(
                f"Short code '{short_code}' is already taken.",
                "CODE_CONFLICT",
                409,
            )
    else:
        # Retry up to 10 times to avoid collisions (astronomically unlikely).
        for _ in range(10):
            short_code = generate_short_code()
            existing = db.execute(
                "SELECT id FROM links WHERE short_code = ?", (short_code,)
            ).fetchone()
            if not existing:
                break
        else:
            return _err(
                "Could not generate a unique short code. Please try again.",
                "CODE_GENERATION_FAILED",
                500,
            )

    db.execute(
        """
        INSERT INTO links (short_code, original_url, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (short_code, original_url, created_at, expires_at),
    )
    db.commit()

    row = db.execute(
        "SELECT * FROM links WHERE short_code = ?", (short_code,)
    ).fetchone()

    return jsonify(format_link_row(row, _base_url())), 201


# ---------------------------------------------------------------------------
# GET /<short_code> — Redirect
# ---------------------------------------------------------------------------

@bp.get("/<short_code>")
def redirect_to_url(short_code: str):
    """
    Look up the short code and redirect (302) to the original URL.

    Returns 404 if the code doesn't exist or has been deleted,
    and 410 Gone if the link has expired.
    """
    db = get_db()
    row = db.execute(
        "SELECT * FROM links WHERE short_code = ? AND is_active = 1",
        (short_code,),
    ).fetchone()

    if row is None:
        return _err(f"Short link '{short_code}' not found.", "NOT_FOUND", 404)

    if is_expired(row["expires_at"]):
        return _err(
            f"Short link '{short_code}' has expired.",
            "LINK_EXPIRED",
            410,
        )

    # Increment click counter atomically.
    db.execute(
        "UPDATE links SET click_count = click_count + 1 WHERE short_code = ?",
        (short_code,),
    )
    db.commit()

    return redirect(row["original_url"], code=302)


# ---------------------------------------------------------------------------
# GET /api/links — List all active links (paginated)
# ---------------------------------------------------------------------------

@bp.get("/api/links")
def list_links():
    """
    Return a paginated list of all active (non-deleted) links.

    Query params:
        page     (int, default 1)
        per_page (int, default 20, max 100)
    """
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    except ValueError:
        return _err("'page' and 'per_page' must be integers.", "INVALID_PARAMS", 400)

    offset = (page - 1) * per_page
    db = get_db()

    total = db.execute(
        "SELECT COUNT(*) FROM links WHERE is_active = 1"
    ).fetchone()[0]

    rows = db.execute(
        """
        SELECT * FROM links
        WHERE is_active = 1
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (per_page, offset),
    ).fetchall()

    base = _base_url()
    return jsonify(
        {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max(1, -(-total // per_page)),  # ceil division
            "links": [format_link_row(r, base) for r in rows],
        }
    ), 200


# ---------------------------------------------------------------------------
# GET /api/links/<short_code> — Get one link's details
# ---------------------------------------------------------------------------

@bp.get("/api/links/<short_code>")
def get_link(short_code: str):
    """Return full details for a single link (active or deleted)."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM links WHERE short_code = ?", (short_code,)
    ).fetchone()

    if row is None:
        return _err(f"Short link '{short_code}' not found.", "NOT_FOUND", 404)

    data = format_link_row(row, _base_url())
    data["is_expired"] = is_expired(row["expires_at"])
    return jsonify(data), 200


# ---------------------------------------------------------------------------
# PATCH /api/links/<short_code> — Update URL or expiry
# ---------------------------------------------------------------------------

@bp.patch("/api/links/<short_code>")
def update_link(short_code: str):
    """
    Update one or both of: original_url, expires_in_days.

    Request JSON body (at least one field required):
    {
        "url":            "https://new-destination.com",
        "expires_in_days": 7    // set to 0 to remove expiry
    }
    """
    db = get_db()
    row = db.execute(
        "SELECT * FROM links WHERE short_code = ? AND is_active = 1",
        (short_code,),
    ).fetchone()

    if row is None:
        return _err(f"Short link '{short_code}' not found.", "NOT_FOUND", 404)

    body = request.get_json(silent=True) or {}
    updates = {}

    if "url" in body:
        new_url = (body["url"] or "").strip()
        if not validate_url(new_url):
            return _err(
                "Invalid URL. Must be an absolute http or https URL.",
                "INVALID_URL",
                422,
            )
        updates["original_url"] = new_url

    if "expires_in_days" in body:
        days = body["expires_in_days"]
        if days == 0 or days is None:
            updates["expires_at"] = None          # remove expiry
        else:
            try:
                days = int(days)
                if days < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return _err(
                    "expires_in_days must be a non-negative integer (0 = remove expiry).",
                    "INVALID_EXPIRY",
                    400,
                )
            updates["expires_at"] = expiry_from_days(days)

    if not updates:
        return _err(
            "Provide at least one of 'url' or 'expires_in_days' to update.",
            "NO_FIELDS",
            400,
        )

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [short_code]
    db.execute(f"UPDATE links SET {set_clause} WHERE short_code = ?", values)
    db.commit()

    updated = db.execute(
        "SELECT * FROM links WHERE short_code = ?", (short_code,)
    ).fetchone()

    return jsonify(format_link_row(updated, _base_url())), 200


# ---------------------------------------------------------------------------
# DELETE /api/links/<short_code> — Soft-delete
# ---------------------------------------------------------------------------

@bp.delete("/api/links/<short_code>")
def delete_link(short_code: str):
    """
    Soft-delete a link by setting is_active = 0.
    Stats are preserved; the short code will no longer redirect.
    """
    db = get_db()
    row = db.execute(
        "SELECT id FROM links WHERE short_code = ? AND is_active = 1",
        (short_code,),
    ).fetchone()

    if row is None:
        return _err(f"Short link '{short_code}' not found.", "NOT_FOUND", 404)

    db.execute(
        "UPDATE links SET is_active = 0 WHERE short_code = ?", (short_code,)
    )
    db.commit()

    return jsonify({"message": f"Short link '{short_code}' has been deleted."}), 200


# ---------------------------------------------------------------------------
# GET /api/links/<short_code>/stats — Click stats & metadata
# ---------------------------------------------------------------------------

@bp.get("/api/links/<short_code>/stats")
def link_stats(short_code: str):
    """Return click count and metadata for any link (active or deleted)."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM links WHERE short_code = ?", (short_code,)
    ).fetchone()

    if row is None:
        return _err(f"Short link '{short_code}' not found.", "NOT_FOUND", 404)

    data = format_link_row(row, _base_url())
    data["is_expired"] = is_expired(row["expires_at"])
    return jsonify(
        {
            "short_code": data["short_code"],
            "short_url": data.get("short_url", ""),
            "original_url": data["original_url"],
            "click_count": data["click_count"],
            "created_at": data["created_at"],
            "expires_at": data["expires_at"],
            "is_expired": data["is_expired"],
            "is_active": data["is_active"],
        }
    ), 200


# ---------------------------------------------------------------------------
# GET /api/validate?url=… — Validate a URL
# ---------------------------------------------------------------------------

@bp.get("/api/validate")
def validate():
    """
    Validate a URL without creating a short link.

    Query param: url (required)
    Returns: { "url": "...", "valid": true/false, "reason": "..." }
    """
    url = (request.args.get("url") or "").strip()
    if not url:
        return _err("Query parameter 'url' is required.", "MISSING_URL", 400)

    valid = validate_url(url)
    reason = "OK" if valid else "Must be an absolute http or https URL with a valid host."
    return jsonify({"url": url, "valid": valid, "reason": reason}), 200


# ---------------------------------------------------------------------------
# GET / — HTML Dashboard
# ---------------------------------------------------------------------------

@bp.get("/")
def dashboard():
    """Serve the HTML link-management dashboard."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM links WHERE is_active = 1 ORDER BY id DESC LIMIT 50"
    ).fetchall()
    base = _base_url()
    links = [format_link_row(r, base) for r in rows]
    # Annotate each link with expiry status for the template.
    for link in links:
        link["is_expired"] = is_expired(link.get("expires_at"))
    return render_template("index.html", links=links, base_url=base), 200
