from __future__ import annotations

import json
import base64
import sqlite3

import pytest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from db.connection import apply_schema, get_connection
from models.enums import UserRole
from services.audit_service import (
    AuditIntegrityError,
    export_audit_json,
    export_audit_pdf,
    record_audit_event,
    verify_audit_chain,
    verify_audit_export,
)
from services.auth_service import create_user, login, resolve_session
from services.cms_adapters import CMSConfigurationError, get_cms_adapter


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "audit.sqlite"))
    apply_schema()


def _reviewer():
    create_user("reviewer", "reviewer-password", UserRole.REVIEWER)
    return resolve_session(login("reviewer", "reviewer-password"))


def test_audit_chain_detects_payload_removal_and_reordering():
    principal = _reviewer()
    record_audit_event(
        principal, action="DRAFT_CREATED", object_type="draft", object_id=1,
        details={"title": "Synthetic test"},
    )
    record_audit_event(
        principal, action="SUBMITTED", object_type="draft", object_id=1,
        details={"version": 1},
    )
    package = json.loads(export_audit_json(principal))

    assert verify_audit_chain(package["records"])["valid"] is True
    modified = json.loads(json.dumps(package["records"]))
    modified[0]["details"]["title"] = "Tampered"
    assert verify_audit_chain(modified)["valid"] is False
    assert verify_audit_chain(list(reversed(package["records"])))["valid"] is False
    assert verify_audit_export(json.dumps(package).encode())["valid"] is True


def test_audit_export_verifier_rejects_tampered_manifest_and_malformed_records():
    principal = _reviewer()
    record_audit_event(
        principal, action="DRAFT_CREATED", object_type="draft", object_id=1,
        details={"title": "Synthetic test"},
    )
    package = json.loads(export_audit_json(principal))
    package["verification"]["head_hash"] = "0" * 64

    assert verify_audit_export(json.dumps(package)) == {
        "valid": False,
        "reason": "Export verification metadata does not match its records.",
    }

    package = json.loads(export_audit_json(principal))
    package["records"] = [None]
    assert verify_audit_export(json.dumps(package)) == {
        "valid": False,
        "reason": "Invalid record at index 0.",
    }


def test_audit_events_cannot_be_updated_or_deleted():
    principal = _reviewer()
    event_id = record_audit_event(
        principal, action="DRAFT_CREATED", object_type="draft", object_id=1,
        details={"title": "Synthetic test"},
    )

    with get_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="audit events are immutable"):
            conn.execute(
                "UPDATE audit_events SET actor_username = 'forged' WHERE id = ?",
                (event_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="audit events are immutable"):
            conn.execute("DELETE FROM audit_events WHERE id = ?", (event_id,))


def test_migrations_15_and_16_install_audit_triggers_and_session_auth_method():
    with get_connection() as conn:
        conn.execute("DROP TRIGGER prevent_audit_event_update")
        conn.execute("DROP TRIGGER prevent_audit_event_delete")
        conn.execute("DELETE FROM schema_migrations WHERE version >= 15")
        conn.commit()

    from db.connection import _apply_migrations

    with get_connection() as conn:
        _apply_migrations(conn)
        version = conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        triggers = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        session_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(sessions)")
        }

    assert version == 17
    assert {
        "prevent_audit_event_update",
        "prevent_audit_event_delete",
    }.issubset(triggers)
    assert "auth_method" in session_columns


def test_audit_export_fails_closed_when_database_chain_is_invalid():
    principal = _reviewer()
    record_audit_event(
        principal, action="DRAFT_CREATED", object_type="draft", object_id=1,
        details={"title": "Synthetic test"},
    )
    with get_connection() as conn:
        conn.execute("DROP TRIGGER prevent_audit_event_update")
        conn.execute(
            "UPDATE audit_events SET details_json = ?",
            (json.dumps({"title": "Tampered"}),),
        )
        conn.commit()

    with pytest.raises(AuditIntegrityError, match="integrity check failed"):
        export_audit_json(principal)


def test_empty_audit_chain_is_valid_and_pdf_export_works():
    principal = _reviewer()
    package = json.loads(export_audit_json(principal))
    assert package["verification"]["valid"] is True
    assert package["verification"]["record_count"] == 0
    assert export_audit_pdf(principal).startswith(b"%PDF")


def test_audit_pdf_paginates_and_includes_each_record_hash():
    import fitz

    principal = _reviewer()
    for index in range(80):
        record_audit_event(
            principal,
            action=f"WORKFLOW_EVENT_{index:03d}",
            object_type="draft",
            object_id=index,
            details={"index": index},
        )

    document = fitz.open(
        stream=export_audit_pdf(principal), filetype="pdf"
    )
    exported_text = "\n".join(page.get_text() for page in document)

    assert document.page_count > 1
    assert "WORKFLOW_EVENT_000" in exported_text
    assert "WORKFLOW_EVENT_079" in exported_text
    assert "Event hash:" in exported_text


def test_audit_export_requires_reviewer_or_fact_owner():
    create_user("creator", "creator-password", UserRole.CREATOR)
    principal = resolve_session(login("creator", "creator-password"))
    with pytest.raises(PermissionError):
        export_audit_json(principal)


@pytest.mark.parametrize("provider", ["wordpress", "shopify", "contentful"])
def test_mock_cms_adapters_create_draft_only(provider):
    adapter = get_cms_adapter(provider)
    result = adapter.create_draft({"title": "Demo", "content": "Safe draft content"})
    assert result["provider"] == provider
    assert result["status"] == "DRAFT"
    assert result["published"] is False


def test_cms_validation_and_live_mode_are_explicitly_safe():
    adapter = get_cms_adapter("wordpress")
    with pytest.raises(ValueError, match="content"):
        adapter.create_draft({"title": "Missing content"})
    with pytest.raises(CMSConfigurationError, match="not configured"):
        get_cms_adapter("wordpress", mode="live")


def test_live_wordpress_adapter_posts_only_as_draft(monkeypatch):
    import services.cms_adapters as cms

    monkeypatch.setenv("WORDPRESS_URL", "https://brand.example")
    monkeypatch.setenv("WORDPRESS_USERNAME", "editor")
    monkeypatch.setenv("WORDPRESS_APPLICATION_PASSWORD", "secret app password")
    monkeypatch.setattr(
        cms,
        "_open_without_redirect",
        lambda request, timeout: _FakeWordPressResponse(
            request, timeout, {"id": 41, "status": "draft", "link": "https://brand.example/?p=41"},
            "secret app password",
        ),
    )

    result = get_cms_adapter("wordpress", mode="live").create_draft(
        {"title": "Review me", "content": "A measured product statement."}
    )

    assert result == {
        "provider": "wordpress", "mode": "live", "draft_id": "41",
        "status": "DRAFT", "published": False,
        "edit_url": "https://brand.example/?p=41",
    }


def test_live_shopify_adapter_creates_only_a_draft_product(monkeypatch):
    import services.cms_adapters as cms

    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "test-store.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "shopify-secret")
    captured = {}

    def fake_open(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        body = json.loads(request.data)
        assert body["variables"]["product"] == {
            "title": "Review me",
            "descriptionHtml": "A measured product statement.",
            "status": "DRAFT",
        }
        return _FakeCMSResponse({
            "data": {
                "productCreate": {
                    "product": {
                        "id": "gid://shopify/Product/123",
                        "status": "DRAFT",
                    },
                    "userErrors": [],
                }
            }
        })

    monkeypatch.setattr(cms, "_open_without_redirect", fake_open)
    result = get_cms_adapter("shopify", mode="live").create_draft(
        {"title": "Review me", "content": "A measured product statement."}
    )

    assert captured["request"].full_url == (
        "https://test-store.myshopify.com/admin/api/2026-10/graphql.json"
    )
    assert captured["request"].headers["X-shopify-access-token"] == "shopify-secret"
    assert captured["timeout"] == 15
    assert result == {
        "provider": "shopify",
        "mode": "live",
        "draft_id": "123",
        "status": "DRAFT",
        "published": False,
        "edit_url": "https://admin.shopify.com/store/test-store/products/123",
    }


def test_shopify_adapter_rejects_graphql_errors_and_non_draft_responses(monkeypatch):
    import services.cms_adapters as cms

    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "test-store.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "shopify-secret")
    monkeypatch.setattr(
        cms, "_open_without_redirect",
        lambda request, timeout: _FakeCMSResponse({
            "data": {
                "productCreate": {
                    "product": {
                        "id": "gid://shopify/Product/123",
                        "status": "ACTIVE",
                    },
                    "userErrors": [],
                }
            }
        }),
    )

    with pytest.raises(CMSConfigurationError, match="unpublished draft"):
        get_cms_adapter("shopify", mode="live").create_draft(
            {"title": "Review me", "content": "Content"}
        )


def test_live_contentful_adapter_creates_only_an_unpublished_entry(monkeypatch):
    import services.cms_adapters as cms

    monkeypatch.setenv("CONTENTFUL_SPACE_ID", "space-1")
    monkeypatch.setenv("CONTENTFUL_MANAGEMENT_TOKEN", "contentful-secret")
    monkeypatch.setenv("CONTENTFUL_CONTENT_TYPE_ID", "article")
    monkeypatch.setenv("CONTENTFUL_ENVIRONMENT_ID", "staging")
    monkeypatch.setenv("CONTENTFUL_LOCALE", "en-GB")
    captured = {}

    def fake_open(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        assert json.loads(request.data)["fields"] == {
            "title": {"en-GB": "Review me"},
            "content": {"en-GB": "A measured product statement."},
        }
        return _FakeCMSResponse({"sys": {"id": "entry-1", "version": 1}})

    monkeypatch.setattr(cms, "_open_without_redirect", fake_open)
    result = get_cms_adapter("contentful", mode="live").create_draft(
        {"title": "Review me", "content": "A measured product statement."}
    )

    assert captured["request"].full_url == (
        "https://api.contentful.com/spaces/space-1/"
        "environments/staging/entries"
    )
    assert captured["request"].headers["X-contentful-content-type"] == "article"
    assert captured["request"].headers["Authorization"] == "Bearer contentful-secret"
    assert captured["timeout"] == 15
    assert result == {
        "provider": "contentful",
        "mode": "live",
        "draft_id": "entry-1",
        "status": "DRAFT",
        "published": False,
        "edit_url": (
            "https://app.contentful.com/spaces/space-1/"
            "environments/staging/entries/entry-1"
        ),
    }


def test_live_cms_adapters_report_missing_configuration(monkeypatch):
    import ai.config as config

    monkeypatch.setattr(
        config, "_read_streamlit_secret", lambda name, default="": default
    )
    for name in (
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_ACCESS_TOKEN",
        "CONTENTFUL_SPACE_ID",
        "CONTENTFUL_MANAGEMENT_TOKEN",
        "CONTENTFUL_CONTENT_TYPE_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(CMSConfigurationError, match="Live Shopify is not configured"):
        get_cms_adapter("shopify", mode="live")
    with pytest.raises(CMSConfigurationError, match="Live Contentful is not configured"):
        get_cms_adapter("contentful", mode="live")


class _FakeCMSResponse:
    status = 201

    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.response).encode("utf-8")


class _FakeWordPressResponse:
    status = 201

    def __init__(self, request, timeout, response, password="secret app password"):
        self.request = request
        self.timeout = timeout
        self.response = response
        self.password = password

    def __enter__(self):
        payload = json.loads(self.request.data)
        assert payload["status"] == "draft"
        assert payload["title"] == "Review me"
        assert self.request.full_url == "https://brand.example/wp-json/wp/v2/posts"
        assert self.request.get_header("Authorization") == "Basic " + base64.b64encode(
            f"editor:{self.password}".encode()
        ).decode("ascii")
        assert 1 <= self.timeout <= 60
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.response).encode("utf-8")


def test_wordpress_adapter_rejects_non_draft_response(monkeypatch):
    import services.cms_adapters as cms

    monkeypatch.setenv("WORDPRESS_URL", "https://brand.example")
    monkeypatch.setenv("WORDPRESS_USERNAME", "editor")
    monkeypatch.setenv("WORDPRESS_APPLICATION_PASSWORD", "secret")
    monkeypatch.setattr(
        cms, "_open_without_redirect",
        lambda request, timeout: _FakeWordPressResponse(
            request, timeout, {"id": 42, "status": "publish"}
            , "secret"
        ),
    )
    with pytest.raises(CMSConfigurationError, match="unpublished draft"):
        get_cms_adapter("wordpress", mode="live").create_draft(
            {"title": "Review me", "content": "Content"}
        )


def test_wordpress_adapter_requires_https_off_localhost(monkeypatch):
    monkeypatch.setenv("WORDPRESS_URL", "http://brand.example")
    monkeypatch.setenv("WORDPRESS_USERNAME", "editor")
    monkeypatch.setenv("WORDPRESS_APPLICATION_PASSWORD", "secret")
    with pytest.raises(CMSConfigurationError, match="HTTPS"):
        get_cms_adapter("wordpress", mode="live")


def test_wordpress_adapter_drops_off_site_edit_link(monkeypatch):
    import services.cms_adapters as cms

    monkeypatch.setenv("WORDPRESS_URL", "https://brand.example")
    monkeypatch.setenv("WORDPRESS_USERNAME", "editor")
    monkeypatch.setenv("WORDPRESS_APPLICATION_PASSWORD", "secret app password")
    monkeypatch.setattr(
        cms, "_open_without_redirect",
        lambda request, timeout: _FakeWordPressResponse(
            request, timeout, {"id": 43, "status": "draft", "link": "https://phishing.example/"}
        ),
    )

    result = get_cms_adapter("wordpress", mode="live").create_draft(
        {"title": "Review me", "content": "Content"}
    )

    assert result["draft_id"] == "43"
    assert result["edit_url"] is None


def test_wordpress_redirect_does_not_forward_credentials_or_content(monkeypatch):
    requests = []

    class _RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get("Authorization"), self.rfile.read(
                int(self.headers.get("Content-Length", "0"))
            )))
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/capture")
            self.end_headers()

        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization"), b""))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "WORDPRESS_URL", f"http://127.0.0.1:{server.server_port}"
    )
    monkeypatch.setenv("WORDPRESS_USERNAME", "editor")
    monkeypatch.setenv("WORDPRESS_APPLICATION_PASSWORD", "secret app password")
    try:
        with pytest.raises(CMSConfigurationError, match="HTTP 307"):
            get_cms_adapter("wordpress", mode="live").create_draft(
                {"title": "Private draft", "content": "Confidential launch copy"}
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(requests) == 1
    assert requests[0][0] == "/wp-json/wp/v2/posts"
    assert requests[0][1].startswith("Basic ")
    assert b"Confidential launch copy" in requests[0][2]
