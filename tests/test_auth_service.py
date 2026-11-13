from __future__ import annotations

import pytest

from db.connection import apply_schema, get_connection
from models.enums import UserRole
from services.auth_service import (
    AuthenticatedPrincipal,
    AuthenticationError,
    SessionError,
    authenticate_user,
    create_user,
    create_email_verification_token,
    create_password_reset_token,
    get_user,
    login,
    login_oidc_identity,
    logout,
    reset_password,
    resolve_session,
    verify_email,
)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "auth.sqlite"))
    apply_schema()


def test_successful_account_creation():
    user_id = create_user("Rohit", "correct horse battery staple", UserRole.CREATOR)
    user = get_user("rohit")

    assert user_id == user["id"]
    assert user["username"] == "rohit"
    assert user["role"] == UserRole.CREATOR.value
    assert user["is_active"] == 1


def test_duplicate_username_rejection():
    create_user("Reviewer", "first-password", UserRole.REVIEWER)

    with pytest.raises(ValueError, match="already registered"):
        create_user("reviewer", "second-password", UserRole.CREATOR)


def test_password_is_never_stored_in_plaintext():
    password = "never-store-me"
    create_user("creator", password, UserRole.CREATOR)

    with get_connection() as conn:
        stored = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'creator'"
        ).fetchone()["password_hash"]

    assert stored != password
    assert stored.startswith("scrypt$")
    assert password not in stored


def test_correct_password_verification():
    create_user("creator", "valid-password", UserRole.CREATOR)

    authenticated = authenticate_user("creator", "valid-password")

    assert authenticated["username"] == "creator"
    assert "password_hash" not in authenticated


def test_unknown_login_performs_dummy_password_verification(monkeypatch):
    from services import auth_service

    verified_hashes = []
    real_verify = auth_service._verify_password

    def record_verification(password, encoded_hash):
        verified_hashes.append(encoded_hash)
        return real_verify(password, encoded_hash)

    monkeypatch.setattr(auth_service, "_verify_password", record_verification)
    with pytest.raises(AuthenticationError, match="Invalid username or password"):
        authenticate_user("unknown-user", "candidate-password")

    assert verified_hashes == [auth_service._DUMMY_PASSWORD_HASH]


def test_email_can_be_used_as_login_identifier():
    create_user(
        "creator", "valid-password", UserRole.CREATOR, email="Creator@example.com"
    )

    authenticated = authenticate_user("CREATOR@example.com", "valid-password")

    assert authenticated["username"] == "creator"


def test_reserved_invalid_demo_email_allows_username_only_login():
    create_user(
        "demo-user", "valid-password", UserRole.CREATOR,
        email="demo@example.invalid", email_verified=False,
    )
    assert authenticate_user("demo-user", "valid-password")["username"] == "demo-user"
    with pytest.raises(AuthenticationError):
        authenticate_user("demo@example.invalid", "valid-password")


def test_ambiguous_username_and_email_identifier_is_rejected():
    create_user("first", "valid-password", UserRole.CREATOR, email="same@example.com")
    create_user("same@example.com", "valid-password", UserRole.CREATOR, email="other@example.com")

    with pytest.raises(AuthenticationError, match="Invalid username or password"):
        authenticate_user("same@example.com", "valid-password")


def test_unverified_email_cannot_login_until_single_use_verification():
    user_id = create_user(
        "pending", "valid-password", UserRole.CREATOR,
        email="pending@example.com", email_verified=False, is_active=False,
    )
    with get_connection() as conn:
        conn.execute("UPDATE users SET registration_pending = 1 WHERE id = ?", (user_id,))
        conn.commit()
    token = create_email_verification_token(user_id)
    with pytest.raises(AuthenticationError):
        login("pending", "valid-password")

    assert verify_email(token) is True
    assert verify_email(token) is False
    assert resolve_session(login("pending@example.com", "valid-password")).username == "pending"


def test_email_verification_does_not_reactivate_an_administratively_disabled_user():
    user_id = create_user(
        "disabled", "valid-password", UserRole.CREATOR,
        email="disabled@example.com", email_verified=False, is_active=False,
    )
    with get_connection() as conn:
        conn.execute("UPDATE users SET registration_pending = 0 WHERE id = ?", (user_id,))
        conn.commit()
    token = create_email_verification_token(user_id)

    assert verify_email(token)
    with pytest.raises(AuthenticationError):
        login("disabled", "valid-password")


def test_password_reset_is_single_use_and_revokes_existing_sessions():
    create_user("reset", "valid-password", UserRole.CREATOR, email="reset@example.com")
    session = login("reset", "valid-password")
    token = create_password_reset_token("reset@example.com")

    assert token
    assert reset_password(token, "a-new-long-password") is True
    assert reset_password(token, "another-new-password") is False
    with pytest.raises(SessionError):
        resolve_session(session)
    with pytest.raises(AuthenticationError):
        login("reset", "valid-password")
    assert login("reset", "a-new-long-password")


def test_password_reset_does_not_issue_for_unknown_email():
    assert create_password_reset_token("unknown@example.com") is None


def test_oidc_identity_links_only_to_a_verified_matching_email():
    create_user("google-user", "valid-password", UserRole.CREATOR, email="google@example.com")
    token = login_oidc_identity("google-subject-1", "Google@example.com", True)
    assert resolve_session(token).username == "google-user"

    with get_connection() as conn:
        subject = conn.execute(
            "SELECT oidc_subject FROM users WHERE username = 'google-user'"
        ).fetchone()["oidc_subject"]
    assert subject == "google-subject-1"
    with pytest.raises(AuthenticationError, match="verified OIDC identity"):
        login_oidc_identity("google-subject-2", "google@example.com", False)
    with pytest.raises(AuthenticationError, match="cannot access"):
        login_oidc_identity("google-subject-2", "google@example.com", True)


def test_sso_required_disables_password_sessions_and_recovery(monkeypatch):
    from services.auth_service import register_invited_user

    create_user("sso-only", "valid-password", UserRole.REVIEWER, email="sso@example.com")
    password_session = login("sso-only", "valid-password")
    monkeypatch.setenv("BRL_REQUIRE_SSO", "1")

    with pytest.raises(AuthenticationError, match="Password sign-in is disabled"):
        login("sso-only", "valid-password")
    with pytest.raises(SessionError):
        resolve_session(password_session)
    oidc_session = login_oidc_identity("sso-subject", "sso@example.com", True)
    assert resolve_session(oidc_session).username == "sso-only"
    assert create_password_reset_token("sso@example.com") is None
    assert reset_password("x" * 32, "a-new-long-password") is False
    with pytest.raises(PermissionError, match="must be created with configured SSO"):
        register_invited_user("x" * 32, "invitee", "a-valid-long-password")


def test_incorrect_password_rejection():
    create_user("creator", "valid-password", UserRole.CREATOR)

    with pytest.raises(AuthenticationError, match="Invalid username or password"):
        authenticate_user("creator", "wrong-password")


def test_inactive_account_login_rejection():
    create_user("creator", "valid-password", UserRole.CREATOR, is_active=False)

    with pytest.raises(AuthenticationError, match="Invalid username or password"):
        authenticate_user("creator", "valid-password")


def test_existing_migration_compatibility_preserves_historical_data():
    with get_connection() as conn:
        draft_id = conn.execute(
            "INSERT INTO drafts (title, submitted_by) VALUES ('Historical draft', 'legacy_creator')"
        ).lastrowid
        conn.execute("INSERT INTO reviews (draft_id, reviewer) VALUES (?, 'legacy_reviewer')", (draft_id,))
        conn.commit()

    with get_connection() as conn:
        draft = conn.execute(
            "SELECT title, submitted_by FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        review = conn.execute(
            "SELECT reviewer FROM reviews WHERE draft_id = ?", (draft_id,)
        ).fetchone()

    assert dict(draft) == {"title": "Historical draft", "submitted_by": "legacy_creator"}
    assert review["reviewer"] == "legacy_reviewer"


def test_migration_idempotency():
    user_id = create_user("preserved-user", "valid-password", UserRole.REVIEWER)
    with get_connection() as conn:
        draft_id = conn.execute(
            "INSERT INTO drafts (title, submitted_by) VALUES ('Preserved draft', 'legacy_creator')"
        ).lastrowid
        review_id = conn.execute(
            "INSERT INTO reviews (draft_id, reviewer) VALUES (?, 'legacy_reviewer')",
            (draft_id,),
        ).lastrowid
        conn.commit()

    apply_schema()
    apply_schema()

    with get_connection() as conn:
        versions = [
            row["version"]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        migration_9_count = conn.execute(
            "SELECT COUNT(*) AS count FROM schema_migrations WHERE version = 9"
        ).fetchone()["count"]
        user = conn.execute(
            "SELECT id, username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        draft = conn.execute(
            "SELECT title, submitted_by FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        review = conn.execute(
            "SELECT id, reviewer FROM reviews WHERE id = ?", (review_id,)
        ).fetchone()

    assert versions == list(range(1, 18))
    assert migration_9_count == 1
    assert dict(user) == {"id": user_id, "username": "preserved-user"}
    assert dict(draft) == {"title": "Preserved draft", "submitted_by": "legacy_creator"}
    assert dict(review) == {"id": review_id, "reviewer": "legacy_reviewer"}


def test_successful_login_creates_a_valid_session():
    user_id = create_user("creator", "valid-password", UserRole.CREATOR)

    token = login("creator", "valid-password")

    assert token
    with get_connection() as conn:
        session = conn.execute(
            "SELECT user_id, token_hash, revoked_at FROM sessions"
        ).fetchone()
    assert session["user_id"] == user_id
    assert session["token_hash"] != token
    assert session["revoked_at"] is None


def test_incorrect_password_creates_no_session():
    create_user("creator", "valid-password", UserRole.CREATOR)

    with pytest.raises(AuthenticationError):
        login("creator", "wrong-password")

    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_raw_session_token_is_never_stored():
    create_user("creator", "valid-password", UserRole.CREATOR)
    token = login("creator", "valid-password")

    with get_connection() as conn:
        stored_values = [
            row["token_hash"]
            for row in conn.execute("SELECT token_hash FROM sessions")
        ]
    assert token not in stored_values


def test_valid_session_resolves_to_principal():
    user_id = create_user("creator", "valid-password", UserRole.CREATOR)
    token = login("creator", "valid-password")

    principal = resolve_session(token)

    assert principal == AuthenticatedPrincipal(user_id, "creator", UserRole.CREATOR.value)


def test_invalid_token_is_rejected():
    with pytest.raises(SessionError, match="Invalid session"):
        resolve_session("not-a-real-token")


def test_expired_session_is_rejected():
    create_user("creator", "valid-password", UserRole.CREATOR)
    token = login("creator", "valid-password")
    with get_connection() as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = datetime('now', '-1 second')"
        )
        conn.commit()

    with pytest.raises(SessionError, match="Invalid session"):
        resolve_session(token)


def test_revoked_session_is_rejected():
    create_user("creator", "valid-password", UserRole.CREATOR)
    token = login("creator", "valid-password")
    logout(token)

    with pytest.raises(SessionError, match="Invalid session"):
        resolve_session(token)


def test_inactive_account_cannot_resolve_session():
    user_id = create_user("creator", "valid-password", UserRole.CREATOR)
    token = login("creator", "valid-password")
    with get_connection() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
        conn.commit()

    with pytest.raises(SessionError, match="Invalid session"):
        resolve_session(token)


def test_role_changes_are_reflected_when_resolving_existing_session():
    user_id = create_user("creator", "valid-password", UserRole.CREATOR)
    token = login("creator", "valid-password")
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (UserRole.REVIEWER.value, user_id),
        )
        conn.commit()

    assert resolve_session(token).role == UserRole.REVIEWER.value


def test_logout_invalidates_session():
    create_user("creator", "valid-password", UserRole.CREATOR)
    token = login("creator", "valid-password")

    logout(token)

    with pytest.raises(SessionError):
        resolve_session(token)


def test_migration_9_is_recorded_exactly_once():
    apply_schema()
    apply_schema()

    with get_connection() as conn:
        versions = [
            row["version"]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        migration_9_count = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 9"
        ).fetchone()[0]
    assert versions == list(range(1, 18))
    assert migration_9_count == 1


def test_existing_users_and_historical_records_survive_session_migration():
    user_id = create_user("legacy-user", "valid-password", UserRole.REVIEWER)
    with get_connection() as conn:
        draft_id = conn.execute(
            "INSERT INTO drafts (title, submitted_by) VALUES ('Legacy draft', 'legacy_creator')"
        ).lastrowid
        review_id = conn.execute(
            "INSERT INTO reviews (draft_id, reviewer) VALUES (?, 'legacy_reviewer')",
            (draft_id,),
        ).lastrowid
        conn.commit()

    apply_schema()

    with get_connection() as conn:
        user = conn.execute(
            "SELECT id, username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        draft = conn.execute(
            "SELECT title, submitted_by FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        review = conn.execute(
            "SELECT id, reviewer FROM reviews WHERE id = ?", (review_id,)
        ).fetchone()
    assert dict(user) == {"id": user_id, "username": "legacy-user"}
    assert dict(draft) == {"title": "Legacy draft", "submitted_by": "legacy_creator"}
    assert dict(review) == {"id": review_id, "reviewer": "legacy_reviewer"}
