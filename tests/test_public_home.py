from __future__ import annotations

from pathlib import Path
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from streamlit.testing.v1 import AppTest

from db.connection import apply_schema, get_connection
from models.enums import BrandRole, UserRole
from services.auth_service import create_user, login, resolve_session
from services.brand_service import create_brand

def test_public_landing_and_sign_in_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "public-home.sqlite"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()

    assert not app.exception
    assert [title.value for title in app.title] == [
        "Every claim checked. Every brand protected."
    ]
    button_labels = [button.label for button in app.button]
    assert {
        "Sign in", "Sign in to your workspace", "Open demo", "Enter demo",
        "Create owner account",
    }.issubset(button_labels)
    assert "Send reset link" not in button_labels
    assert "One review, from draft to decision" in [item.value for item in app.subheader]

    next(button for button in app.button if button.label == "Sign in").click().run()

    assert not app.exception
    assert "Sign in" in [item.value for item in app.subheader]
    assert "Username or email" in [item.label for item in app.text_input]
    assert any("Ask your Brand Owner to invite your work email" in item.value for item in app.info)
    assert "Send reset link" in [button.label for button in app.button]


def test_demo_entry_requires_isolated_database_and_explicit_enablement(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "regular.sqlite"))
    monkeypatch.setenv("BRL_DEMO_ACCESS", "1")

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    next(button for button in app.button if button.label == "Open demo").click().run()

    assert not app.exception
    assert "Choose a demo role" in [item.value for item in app.subheader]
    assert any("isolated judge-demo database" in item.value for item in app.error)


def test_isolated_demo_role_login_shows_only_synthetic_configured_account(
    monkeypatch, tmp_path
):
    database_path = tmp_path / "judge_demo.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("BRL_DEMO_ACCESS", "1")
    monkeypatch.setenv("DEMO_CREATOR_PASSWORD", "creator-demo-password")
    monkeypatch.setenv("DEMO_REVIEWER_PASSWORD", "reviewer-demo-password")
    monkeypatch.setenv("DEMO_FACT_OWNER_PASSWORD", "fact-owner-demo-password")
    monkeypatch.setenv("DEMO_OWNER_PASSWORD", "owner-demo-password")
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    next(button for button in app.button if button.label == "Open demo").click().run()

    assert not app.exception
    assert [item.label for item in app.selectbox if item.label == "Demo role"]
    assert "Username  demo_creator\nPassword  creator-demo-password" in [
        item.value for item in app.code
    ]
    next(button for button in app.button if button.label == "Enter as Creator").click().run()

    assert not app.exception
    assert any(item.value.startswith("Good ") for item in app.title)
    with get_connection() as conn:
        membership = conn.execute(
            """
            SELECT bm.role FROM brand_memberships bm
            JOIN users u ON u.id = bm.user_id
            WHERE u.username = 'demo_creator'
            """
        ).fetchone()
    assert membership["role"] == "Creator"


def test_sso_required_hides_password_sign_in_and_recovery(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "sso-required.sqlite"))
    monkeypatch.setenv("BRL_REQUIRE_SSO", "1")

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    next(button for button in app.button if button.label == "Sign in").click().run()

    assert not app.exception
    assert not any(item.label == "Password" for item in app.text_input)
    assert "Send reset link" not in [button.label for button in app.button]
    assert "Create owner account" not in [button.label for button in app.button]


def test_existing_invitee_can_switch_to_sign_in_without_losing_invitation(
    monkeypatch, tmp_path
):
    database_path = tmp_path / "existing-invitee.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    apply_schema()
    create_user("workspace-owner", "owner-password-123", UserRole.REVIEWER)
    owner = resolve_session(login("workspace-owner", "owner-password-123"))
    brand = create_brand(owner, "Existing Invite Brand")
    raw_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO brand_invitations "
            "(brand_id, email, role, token_hash, invited_by_user_id, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                brand["id"], "already-registered@example.com", BrandRole.REVIEWER.value,
                hashlib.sha256(raw_token.encode("utf-8")).hexdigest(), owner.user_id,
                expires_at,
            ),
        )
        conn.commit()

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.query_params["invite"] = raw_token
    app.run()

    assert not app.exception
    assert "Join your invited workspace" in [item.value for item in app.subheader]
    next(button for button in app.button if button.label == "Already have an account? Sign in").click().run()

    assert not app.exception
    assert "Username or email" in [item.label for item in app.text_input]
    assert app.query_params["invite"] == raw_token
    assert "Sign in with the invited account (already-registered@example.com) to join this workspace." in [
        item.value for item in app.caption
    ]


def test_new_invitee_can_choose_configured_sso_or_password_registration(
    monkeypatch, tmp_path
):
    database_path = tmp_path / "new-invitee-sso.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    apply_schema()
    create_user("workspace-owner", "owner-password-123", UserRole.REVIEWER)
    owner = resolve_session(login("workspace-owner", "owner-password-123"))
    brand = create_brand(owner, "New Invite SSO Brand")
    raw_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO brand_invitations "
            "(brand_id, email, role, token_hash, invited_by_user_id, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                brand["id"], "new-invitee@example.com", BrandRole.REVIEWER.value,
                hashlib.sha256(raw_token.encode("utf-8")).hexdigest(), owner.user_id,
                expires_at,
            ),
        )
        conn.commit()
    monkeypatch.setattr("ui.public_home._oidc_configured", lambda: True)

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()
    app.query_params["invite"] = raw_token
    app.run()

    assert not app.exception
    assert "Join your invited workspace" in [item.value for item in app.subheader]
    assert "Continue with Google" in [button.label for button in app.button]
    assert "Create account and join" in [button.label for button in app.button]


def test_new_google_user_can_create_own_brand_when_demo_database_exists(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "google-brand-owner.sqlite"))
    apply_schema()
    create_user("existing-demo-owner", "owner-password-123", UserRole.REVIEWER)
    existing_owner = resolve_session(
        login("existing-demo-owner", "owner-password-123")
    )
    create_brand(existing_owner, "Existing Demo Brand")
    monkeypatch.setattr("ui.public_home._oidc_configured", lambda: True)
    monkeypatch.setattr(
        "ui.public_home._oidc_identity_data",
        lambda: {
            "sub": "new-google-owner-subject",
            "email": "new-google-owner@example.com",
            "email_verified": True,
        },
    )

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run()

    assert not app.exception
    assert "Start your brand workspace with Google" in [
        item.value for item in app.subheader
    ]
    next(item for item in app.text_input if item.label == "Choose a username").set_value(
        "new-google-owner"
    )
    next(item for item in app.text_input if item.label == "Brand workspace name").set_value(
        "My Google Brand"
    )
    next(
        item for item in app.button if item.label == "Create my brand workspace"
    ).click().run()

    assert not app.exception
    with get_connection() as conn:
        membership = conn.execute(
            "SELECT bm.role FROM brand_memberships bm "
            "JOIN brands b ON b.id = bm.brand_id "
            "JOIN users u ON u.id = bm.user_id "
            "WHERE b.name = 'My Google Brand' AND u.username = 'new-google-owner'"
        ).fetchone()
    assert membership["role"] == BrandRole.OWNER.value
