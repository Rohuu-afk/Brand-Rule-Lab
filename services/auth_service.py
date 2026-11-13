"""Provisioned user accounts and password verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db.connection import get_connection
from models.enums import UserRole


class AuthenticationError(ValueError):
    """Raised when credentials cannot authenticate an active account."""


class UnlinkedOIDCIdentityError(AuthenticationError):
    """Raised when a verified identity has no matching account yet."""


class SessionError(AuthenticationError):
    """Raised when a session token cannot resolve to an active account."""


def require_legacy_test_actor(action: str) -> None:
    """Restrict role-string compatibility to explicitly opted-in pytest runs.

    A role name supplied by a caller is not authentication. The compatibility
    path exists only for legacy regression fixtures while they are migrated to
    session-backed principals; it is unavailable to normal application runs.
    """
    test_actor_enabled = (
        os.getenv("BRL_ENABLE_LEGACY_TEST_ACTORS") == "1"
        and bool(os.getenv("PYTEST_CURRENT_TEST"))
    )
    demo_builder = Path(__file__).resolve().parents[1] / "db" / "create_judge_demo.py"
    synthetic_demo_actor_enabled = (
        os.getenv("BRL_ENABLE_SYNTHETIC_DEMO_ACTORS") == "1"
        and Path(sys.argv[0]).resolve() == demo_builder.resolve()
    )
    if not (test_actor_enabled or synthetic_demo_actor_enabled):
        raise PermissionError(f"A valid authenticated session is required to {action}.")


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Immutable identity resolved from a valid server-side session."""

    user_id: int
    username: str
    role: str
    _session_token: str = field(default="", repr=False, compare=False)


_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_SESSION_EXPIRY = timedelta(hours=8)


def _normalise_username(username: str) -> str:
    value = username.strip()
    if not value:
        raise ValueError("Username is required.")
    return value.casefold()


def _validate_password(password: str) -> None:
    if not password:
        raise ValueError("Password is required.")


def sso_required() -> bool:
    """Return whether password-based account access is disabled for this deployment."""
    return os.getenv("BRL_REQUIRE_SSO", "0").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def _validate_role(role: UserRole | str) -> str:
    role_value = role.value if isinstance(role, UserRole) else str(role).strip()
    try:
        return UserRole(role_value).value
    except ValueError as exc:
        raise ValueError("Invalid user role.") from exc


def _hash_password(password: str) -> str:
    _validate_password(password)
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
    )
    encode = lambda value: base64.b64encode(value).decode("ascii")
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${encode(salt)}${encode(digest)}"


# Equalize the expensive password-verification work for missing or ambiguous
# identifiers so login timing does not reveal whether an account exists.
_DUMMY_PASSWORD_HASH = _hash_password(secrets.token_urlsafe(32))


def _verify_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, n, r, p, salt_text, digest_text = encoded_hash.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(digest_text, validate=True)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_user(
    username: str,
    password: str,
    role: UserRole | str,
    *,
    is_active: bool = True,
    email: str | None = None,
    email_verified: bool = True,
) -> int:
    """Provision an account; this service has no public self-registration path."""
    normalised_username = _normalise_username(username)
    role_value = _validate_role(role)
    password_hash = _hash_password(password)
    normalised_email = email.strip().casefold() if email and email.strip() else None
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users
                    (username, password_hash, role, is_active, email, email_verified_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalised_username, password_hash, role_value, int(is_active),
                    normalised_email,
                    _format_timestamp(_utc_now()) if normalised_email and email_verified else None,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        if "users.username" in str(exc).lower() or "unique" in str(exc).lower():
            raise ValueError("Username is already registered.") from exc
        raise


def get_user(username: str) -> dict | None:
    """Return public account fields without exposing the password hash."""
    normalised_username = _normalise_username(username)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, username, role, is_active, created_at, updated_at "
            "FROM users WHERE username = ?",
            (normalised_username,),
        ).fetchone()
    return dict(row) if row else None


def authenticate_user(username: str, password: str) -> dict:
    """Verify credentials and return public account fields for active users."""
    normalised_username = _normalise_username(username)
    _validate_password(password)
    with get_connection() as conn:
        matches = conn.execute(
            "SELECT id, username, password_hash, role, is_active, email, email_verified_at, created_at, updated_at "
            "FROM users WHERE username = ? COLLATE NOCASE "
            "OR email = ? COLLATE NOCASE LIMIT 2",
            (normalised_username, normalised_username),
        ).fetchall()
    # A username may equal another account's email. Reject that ambiguous
    # identifier instead of authenticating whichever row SQLite returns first.
    if len(matches) != 1:
        _verify_password(password, _DUMMY_PASSWORD_HASH)
        raise AuthenticationError("Invalid username or password.")
    row = matches[0]
    password_matches = _verify_password(password, row["password_hash"])
    email_requires_verification = bool(
        row["email"]
        and not row["email_verified_at"]
        and (
            not row["email"].casefold().endswith(".invalid")
            or normalised_username == row["email"].casefold()
        )
    )
    if (
        row is None or not row["is_active"]
        or email_requires_verification
        or not password_matches
    ):
        raise AuthenticationError("Invalid username or password.")
    return {
        key: row[key]
        for key in ("id", "username", "role", "is_active", "created_at", "updated_at")
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def login(username: str, password: str) -> str:
    """Authenticate an active account and return a new opaque session token."""
    if sso_required():
        raise AuthenticationError("Password sign-in is disabled. Use your configured SSO provider.")
    user = authenticate_user(username, password)
    token = secrets.token_urlsafe(32)
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO sessions (user_id, token_hash, expires_at, created_at, auth_method)
            VALUES (?, ?, ?, ?, 'PASSWORD')
            """,
            (
                user["id"],
                _hash_session_token(token),
                _format_timestamp(now + _SESSION_EXPIRY),
                _format_timestamp(now),
            ),
        )
        conn.commit()
    return token


def login_oidc_identity(subject: str, email: str, email_verified: bool) -> str:
    """Bind a verified OIDC identity to an existing provisioned account."""
    subject = str(subject or "").strip()
    normalised_email = str(email or "").strip().casefold()
    verified = email_verified is True or str(email_verified).casefold() == "true"
    if not subject or not normalised_email or not verified:
        raise AuthenticationError("A verified OIDC identity is required.")
    now = _utc_now()
    stamp = _format_timestamp(now)
    raw_token = secrets.token_urlsafe(32)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, email, email_verified_at, oidc_subject, is_active "
            "FROM users WHERE oidc_subject = ? OR email = ? COLLATE NOCASE",
            (subject, normalised_email),
        ).fetchall()
        if not row:
            raise UnlinkedOIDCIdentityError(
                "This verified OIDC account is not linked to a workspace account."
            )
        if len(row) != 1:
            raise AuthenticationError("This OIDC account cannot access the linked workspace account.")
        user = row[0]
        if (
            not user["is_active"] or not user["email_verified_at"]
            or (user["email"] or "").casefold() != normalised_email
            or (user["oidc_subject"] and user["oidc_subject"] != subject)
        ):
            raise AuthenticationError("This OIDC account cannot access the linked workspace account.")
        if not user["oidc_subject"]:
            conn.execute("UPDATE users SET oidc_subject = ? WHERE id = ?", (subject, user["id"]))
        conn.execute(
            "INSERT INTO sessions "
            "(user_id, token_hash, expires_at, created_at, auth_method) "
            "VALUES (?, ?, ?, ?, 'OIDC')",
            (
                user["id"], _hash_session_token(raw_token),
                _format_timestamp(now + _SESSION_EXPIRY), stamp,
            ),
        )
        conn.commit()
    return raw_token


def _issue_auth_token(user_id: int, purpose: str, lifetime: timedelta) -> str:
    if purpose not in {"email_verification", "password_reset"}:
        raise ValueError("Invalid authentication token purpose.")
    raw_token = secrets.token_urlsafe(32)
    now = _utc_now()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            "SELECT id, email, email_verified_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            raise ValueError("Account does not exist.")
        if purpose == "email_verification" and (
            not user["email"] or user["email_verified_at"]
        ):
            raise ValueError("Account email does not need verification.")
        conn.execute(
            "UPDATE auth_tokens SET consumed_at = ? "
            "WHERE user_id = ? AND purpose = ? AND consumed_at IS NULL",
            (_format_timestamp(now), user_id, purpose),
        )
        conn.execute(
            "INSERT INTO auth_tokens (user_id, purpose, token_hash, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (
                user_id, purpose, _hash_session_token(raw_token),
                _format_timestamp(now + lifetime),
            ),
        )
        conn.commit()
    return raw_token


def create_email_verification_token(user_id: int) -> str:
    return _issue_auth_token(user_id, "email_verification", timedelta(hours=24))


def create_email_verification_token_for_email(email: str) -> str | None:
    normalised_email = email.strip().casefold()
    if not normalised_email:
        return None
    with get_connection() as conn:
        user = conn.execute(
            "SELECT id FROM users WHERE email = ? COLLATE NOCASE "
            "AND email_verified_at IS NULL",
            (normalised_email,),
        ).fetchone()
    return _issue_auth_token(user["id"], "email_verification", timedelta(hours=24)) if user else None


def verify_email(token: str) -> bool:
    if not token or len(token) < 20:
        return False
    now = _utc_now()
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT t.id, t.user_id, t.expires_at, t.consumed_at,
                   u.email_verified_at, u.registration_pending
            FROM auth_tokens t JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = ? AND t.purpose = 'email_verification'
            """,
            (_hash_session_token(token),),
        ).fetchone()
        if (
            row is None or row["consumed_at"] or row["email_verified_at"]
            or _parse_timestamp(row["expires_at"]) <= now
        ):
            return False
        stamp = _format_timestamp(now)
        conn.execute(
            "UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?), "
            "is_active = CASE WHEN registration_pending = 1 THEN 1 ELSE is_active END, "
            "registration_pending = 0, updated_at = ? WHERE id = ?",
            (stamp, stamp, row["user_id"]),
        )
        conn.execute(
            "UPDATE auth_tokens SET consumed_at = ? WHERE id = ?", (stamp, row["id"])
        )
        from services.audit_service import _append_system_audit_event

        user = conn.execute(
            "SELECT username FROM users WHERE id = ?", (row["user_id"],)
        ).fetchone()
        owner_brand = conn.execute(
            "SELECT brand_id FROM brand_memberships WHERE user_id = ? AND role = 'Brand Owner' "
            "ORDER BY brand_id LIMIT 1",
            (row["user_id"],),
        ).fetchone()
        _append_system_audit_event(
            conn,
            action="ACCOUNT_EMAIL_VERIFIED",
            object_type="user",
            actor_user_id=row["user_id"],
            actor_username=user["username"],
            object_id=row["user_id"],
            brand_id=owner_brand["brand_id"] if owner_brand else None,
        )
        conn.commit()
    return True


def create_password_reset_token(email: str) -> str | None:
    if sso_required():
        return None
    normalised_email = email.strip().casefold()
    if not normalised_email:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE email = ? COLLATE NOCASE "
            "AND email_verified_at IS NOT NULL AND is_active = 1",
            (normalised_email,),
        ).fetchone()
    return _issue_auth_token(row["id"], "password_reset", timedelta(hours=1)) if row else None


def reset_password(token: str, new_password: str) -> bool:
    if sso_required():
        return False
    if not token or len(token) < 20 or len(new_password) < 12:
        return False
    password_hash = _hash_password(new_password)
    now = _utc_now()
    stamp = _format_timestamp(now)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, user_id, expires_at, consumed_at FROM auth_tokens
            WHERE token_hash = ? AND purpose = 'password_reset'
            """,
            (_hash_session_token(token),),
        ).fetchone()
        if row is None or row["consumed_at"] or _parse_timestamp(row["expires_at"]) <= now:
            return False
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, stamp, row["user_id"]),
        )
        conn.execute(
            "UPDATE sessions SET revoked_at = COALESCE(revoked_at, ?) WHERE user_id = ?",
            (stamp, row["user_id"]),
        )
        conn.execute(
            "UPDATE auth_tokens SET consumed_at = ? WHERE id = ?", (stamp, row["id"])
        )
        from services.audit_service import _append_system_audit_event

        user = conn.execute(
            "SELECT username FROM users WHERE id = ?", (row["user_id"],)
        ).fetchone()
        _append_system_audit_event(
            conn,
            action="PASSWORD_RESET",
            object_type="user",
            actor_user_id=row["user_id"],
            actor_username=user["username"],
            object_id=row["user_id"],
        )
        conn.commit()
    return True


def register_invited_user(token: str, username: str, password: str) -> int:
    """Create a verified account only when a live invite proves its email address."""
    if sso_required():
        raise PermissionError("Invited accounts must be created with configured SSO.")
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")
    normalised_username = _normalise_username(username)
    password_hash = _hash_password(password)
    if not token or len(token) < 20:
        raise ValueError("Invitation token is invalid.")
    now = _utc_now()
    stamp = _format_timestamp(now)
    try:
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            invite = conn.execute(
                """
                SELECT id, email, role, expires_at, accepted_at FROM brand_invitations
                WHERE token_hash = ?
                """,
                (_hash_session_token(token),),
            ).fetchone()
            if (
                invite is None or invite["accepted_at"]
                or _parse_timestamp(invite["expires_at"]) <= now
            ):
                raise ValueError("Invitation is invalid, expired, or already used.")
            existing = conn.execute(
                "SELECT id FROM users WHERE email = ? COLLATE NOCASE", (invite["email"],)
            ).fetchone()
            if existing:
                raise ValueError("This email already has an account. Sign in to accept the invitation.")
            cursor = conn.execute(
                """
                INSERT INTO users
                    (username, password_hash, role, is_active, email, email_verified_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (
                    normalised_username, password_hash, invite["role"],
                    invite["email"], stamp,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise ValueError("Username or email is already registered.") from exc


def resolve_session(token: str) -> AuthenticatedPrincipal:
    """Resolve a token against current session and user state."""
    if not token or not isinstance(token, str):
        raise SessionError("Invalid session.")
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT s.expires_at, s.revoked_at, s.auth_method,
                   u.id, u.username, u.role, u.is_active
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (_hash_session_token(token),),
        ).fetchone()
    expired = False
    if row is not None:
        try:
            expired = _parse_timestamp(row["expires_at"]) <= _utc_now()
        except (TypeError, ValueError):
            expired = True
    if (
        row is None
        or row["revoked_at"] is not None
        or not row["is_active"]
        or (sso_required() and row["auth_method"] != "OIDC")
        or expired
    ):
        raise SessionError("Invalid session.")
    return AuthenticatedPrincipal(
        user_id=row["id"],
        username=row["username"],
        role=row["role"],
        _session_token=token,
    )


def require_principal(
    principal: AuthenticatedPrincipal | None,
    *,
    roles: tuple[UserRole | str, ...] = (),
    action: str = "perform this action",
) -> AuthenticatedPrincipal:
    """Revalidate a session-backed principal and enforce its current role."""
    if principal is None or not principal._session_token:
        raise PermissionError(f"Authenticated user required to {action}.")
    current = resolve_session(principal._session_token)
    if (
        current.user_id != principal.user_id
        or current.username != principal.username
        or current.role != principal.role
    ):
        raise PermissionError("Authenticated identity does not match the session.")
    allowed = {
        role.value if isinstance(role, UserRole) else str(role)
        for role in roles
    }
    if allowed and current.role not in allowed:
        raise PermissionError(f"Required role to {action}.")
    return current


def logout(token: str) -> None:
    """Revoke a session token without storing or returning its raw value."""
    if not token or not isinstance(token, str):
        return
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE sessions
            SET revoked_at = COALESCE(revoked_at, ?)
            WHERE token_hash = ?
            """,
            (_format_timestamp(_utc_now()), _hash_session_token(token)),
        )
        conn.commit()
