"""
test_api.py — Pytest suite for the URL Shortener REST API.

Uses Flask's built-in test client with an in-memory SQLite database so tests
are fast, isolated, and leave no files on disk.

Run with:
    cd <project-root>
    python -m pytest url_shortener/tests/test_api.py -v
"""

import json
import pytest

from url_shortener import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    """Return a Flask app configured for testing (in-memory SQLite)."""
    test_app = create_app(
        {
            "TESTING": True,
            "DATABASE": ":memory:",   # no disk writes during tests
        }
    )
    yield test_app


@pytest.fixture()
def client(app):
    """Return a Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def post_shorten(client, url, custom_code=None, expires_in_days=None):
    """POST /api/shorten and return (response, json_body)."""
    body = {"url": url}
    if custom_code is not None:
        body["custom_code"] = custom_code
    if expires_in_days is not None:
        body["expires_in_days"] = expires_in_days
    res = client.post(
        "/api/shorten",
        data=json.dumps(body),
        content_type="application/json",
    )
    return res, res.get_json()


# ---------------------------------------------------------------------------
# Health-check
# ---------------------------------------------------------------------------

class TestHealth:
    def test_returns_200(self, client):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.get_json()["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /api/shorten
# ---------------------------------------------------------------------------

class TestShorten:
    def test_creates_link(self, client):
        res, data = post_shorten(client, "https://www.example.com")
        assert res.status_code == 201
        assert data["short_code"]
        assert data["original_url"] == "https://www.example.com"
        assert data["click_count"] == 0
        assert "short_url" in data

    def test_custom_code(self, client):
        res, data = post_shorten(client, "https://www.example.com", custom_code="mylink")
        assert res.status_code == 201
        assert data["short_code"] == "mylink"

    def test_custom_code_conflict(self, client):
        post_shorten(client, "https://www.example.com", custom_code="dup")
        res, data = post_shorten(client, "https://www.other.com", custom_code="dup")
        assert res.status_code == 409
        assert data["code"] == "CODE_CONFLICT"

    def test_invalid_url_missing_scheme(self, client):
        res, data = post_shorten(client, "not-a-url")
        assert res.status_code == 422
        assert data["code"] == "INVALID_URL"

    def test_invalid_url_ftp_scheme(self, client):
        res, data = post_shorten(client, "ftp://files.example.com")
        assert res.status_code == 422

    def test_missing_url_field(self, client):
        res = client.post(
            "/api/shorten",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert res.status_code == 400
        assert res.get_json()["code"] == "MISSING_URL"

    def test_expiry_set(self, client):
        res, data = post_shorten(client, "https://www.example.com", expires_in_days=7)
        assert res.status_code == 201
        assert data["expires_at"] is not None

    def test_invalid_expiry(self, client):
        res, data = post_shorten(client, "https://www.example.com", expires_in_days=-1)
        assert res.status_code == 400

    def test_invalid_custom_code_too_short(self, client):
        res, data = post_shorten(client, "https://www.example.com", custom_code="ab")
        assert res.status_code == 400
        assert data["code"] == "INVALID_CUSTOM_CODE"

    def test_invalid_custom_code_special_chars(self, client):
        res, data = post_shorten(client, "https://www.example.com", custom_code="bad code!")
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# GET /<short_code> — Redirect
# ---------------------------------------------------------------------------

class TestRedirect:
    def test_redirect(self, client):
        _, created = post_shorten(client, "https://www.google.com")
        code = created["short_code"]
        res = client.get(f"/{code}")
        assert res.status_code == 302
        assert res.headers["Location"] == "https://www.google.com"

    def test_click_count_increments(self, client):
        _, created = post_shorten(client, "https://www.google.com")
        code = created["short_code"]
        client.get(f"/{code}")
        client.get(f"/{code}")
        stats = client.get(f"/api/links/{code}/stats").get_json()
        assert stats["click_count"] == 2

    def test_not_found(self, client):
        res = client.get("/nonexistent123")
        assert res.status_code == 404

    def test_expired_link_returns_410(self, client):
        _, created = post_shorten(
            client, "https://www.example.com", expires_in_days=1
        )
        code = created["short_code"]
        # Manually expire the link by setting expires_at in the past.
        with client.application.app_context():
            from url_shortener.database import get_db
            db = get_db()
            db.execute(
                "UPDATE links SET expires_at = '2000-01-01T00:00:00' WHERE short_code = ?",
                (code,),
            )
            db.commit()
        res = client.get(f"/{code}")
        assert res.status_code == 410


# ---------------------------------------------------------------------------
# GET /api/links — List
# ---------------------------------------------------------------------------

class TestListLinks:
    def test_returns_list(self, client):
        post_shorten(client, "https://www.example.com")
        post_shorten(client, "https://www.python.org")
        res = client.get("/api/links")
        assert res.status_code == 200
        data = res.get_json()
        assert "links" in data
        assert data["total"] >= 2

    def test_pagination(self, client):
        for i in range(5):
            post_shorten(client, f"https://www.example{i}.com")
        res = client.get("/api/links?page=1&per_page=2")
        data = res.get_json()
        assert len(data["links"]) == 2
        assert data["per_page"] == 2

    def test_deleted_links_excluded(self, client):
        _, created = post_shorten(client, "https://www.example.com")
        code = created["short_code"]
        client.delete(f"/api/links/{code}")
        res = client.get("/api/links")
        codes = [l["short_code"] for l in res.get_json()["links"]]
        assert code not in codes


# ---------------------------------------------------------------------------
# GET /api/links/<code>
# ---------------------------------------------------------------------------

class TestGetLink:
    def test_returns_link_details(self, client):
        _, created = post_shorten(client, "https://www.example.com")
        code = created["short_code"]
        res = client.get(f"/api/links/{code}")
        assert res.status_code == 200
        assert res.get_json()["short_code"] == code

    def test_not_found(self, client):
        res = client.get("/api/links/doesnotexist")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/links/<code>
# ---------------------------------------------------------------------------

class TestUpdateLink:
    def test_update_url(self, client):
        _, created = post_shorten(client, "https://www.old.com")
        code = created["short_code"]
        res = client.patch(
            f"/api/links/{code}",
            data=json.dumps({"url": "https://www.new.com"}),
            content_type="application/json",
        )
        assert res.status_code == 200
        assert res.get_json()["original_url"] == "https://www.new.com"

    def test_update_expiry(self, client):
        _, created = post_shorten(client, "https://www.example.com")
        code = created["short_code"]
        res = client.patch(
            f"/api/links/{code}",
            data=json.dumps({"expires_in_days": 14}),
            content_type="application/json",
        )
        assert res.status_code == 200
        assert res.get_json()["expires_at"] is not None

    def test_remove_expiry(self, client):
        _, created = post_shorten(client, "https://www.example.com", expires_in_days=7)
        code = created["short_code"]
        res = client.patch(
            f"/api/links/{code}",
            data=json.dumps({"expires_in_days": 0}),
            content_type="application/json",
        )
        assert res.status_code == 200
        assert res.get_json()["expires_at"] is None

    def test_no_fields_returns_400(self, client):
        _, created = post_shorten(client, "https://www.example.com")
        code = created["short_code"]
        res = client.patch(
            f"/api/links/{code}",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert res.status_code == 400

    def test_invalid_url_update(self, client):
        _, created = post_shorten(client, "https://www.example.com")
        code = created["short_code"]
        res = client.patch(
            f"/api/links/{code}",
            data=json.dumps({"url": "not-a-url"}),
            content_type="application/json",
        )
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/links/<code>
# ---------------------------------------------------------------------------

class TestDeleteLink:
    def test_soft_delete(self, client):
        _, created = post_shorten(client, "https://www.example.com")
        code = created["short_code"]
        res = client.delete(f"/api/links/{code}")
        assert res.status_code == 200
        # Redirect should now 404
        assert client.get(f"/{code}").status_code == 404

    def test_delete_not_found(self, client):
        res = client.delete("/api/links/ghost")
        assert res.status_code == 404

    def test_stats_preserved_after_delete(self, client):
        _, created = post_shorten(client, "https://www.example.com")
        code = created["short_code"]
        client.get(f"/{code}")   # register a click
        client.delete(f"/api/links/{code}")
        stats = client.get(f"/api/links/{code}/stats").get_json()
        assert stats["click_count"] == 1  # stat preserved
        assert stats["is_active"] is False


# ---------------------------------------------------------------------------
# GET /api/links/<code>/stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_returns_stats(self, client):
        _, created = post_shorten(client, "https://www.example.com")
        code = created["short_code"]
        res = client.get(f"/api/links/{code}/stats")
        assert res.status_code == 200
        data = res.get_json()
        assert "click_count" in data
        assert "created_at" in data
        assert "is_expired" in data

    def test_not_found(self, client):
        res = client.get("/api/links/ghost/stats")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/validate
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_url(self, client):
        res = client.get("/api/validate?url=https://www.example.com")
        assert res.status_code == 200
        assert res.get_json()["valid"] is True

    def test_invalid_url(self, client):
        res = client.get("/api/validate?url=not-a-url")
        assert res.status_code == 200
        assert res.get_json()["valid"] is False

    def test_missing_param(self, client):
        res = client.get("/api/validate")
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# GET / — Dashboard
# ---------------------------------------------------------------------------

class TestDashboard:
    def test_returns_html(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert b"URL Shortener" in res.data
