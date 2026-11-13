"""Brand workspaces, membership authorization, and single-use invitations."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from db.connection import get_connection
from models.enums import BrandRole
from services.audit_service import append_audit_event, _append_system_audit_event
from services.auth_service import (
    AuthenticatedPrincipal,
    _hash_password,
    require_principal,
    sso_required,
)
from services.email_service import application_url, send_email


class BrandAccessError(PermissionError):
    """Raised when an authenticated user lacks access to a brand."""


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_OWNER_ROLES = (BrandRole.OWNER,)
_STAFF_ROLES = (BrandRole.OWNER, BrandRole.REVIEWER, BrandRole.FACT_OWNER)
_SCOPED_TABLES = {
    "products", "guidelines", "brand_assets", "facts", "drafts", "audit_events",
    "brand_rules", "campaign_briefs", "knowledge_documents", "knowledge_entries",
}


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().casefold()).strip("-")
    if not slug:
        raise ValueError("Brand name is required.")
    return slug[:80]


def _email(value: str) -> str:
    normalised = value.strip().casefold()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalised):
        raise ValueError("A valid email address is required.")
    return normalised


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def can_register_initial_owner() -> bool:
    """Allow self-service only before any account or workspace exists."""
    with get_connection() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        brands = conn.execute("SELECT COUNT(*) FROM brands").fetchone()[0]
    return users == 0 and brands == 0


def register_initial_owner(
    username: str, email: str, password: str | None, brand_name: str,
    *, oidc_subject: str | None = None, oidc_email_verified: bool = False,
) -> dict:
    """Atomically establish the first email-verified Brand Owner account."""
    if sso_required() and not oidc_subject:
        raise PermissionError("Initial account registration requires configured SSO.")
    username = username.strip().casefold()
    invite_email = _email(email)
    clean_brand_name = brand_name.strip()
    slug = _slugify(clean_brand_name)
    if len(username) < 3:
        raise ValueError("Username must contain at least 3 characters.")
    oidc_subject = str(oidc_subject or "").strip() or None
    if oidc_subject and not oidc_email_verified:
        raise ValueError("A verified OIDC email is required.")
    if oidc_subject:
        password = password or secrets.token_urlsafe(48)
    elif not password or len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")
    password_hash = _hash_password(password)
    token = secrets.token_urlsafe(32) if not oidc_subject else None
    now = _now()
    stamp = _timestamp(now)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() or conn.execute(
            "SELECT 1 FROM brands LIMIT 1"
        ).fetchone():
            raise ValueError("Initial owner registration is closed. Sign in or request an invitation.")
        cursor = conn.execute(
            """
            INSERT INTO users
                (username, password_hash, role, is_active, email, email_verified_at,
                 registration_pending, oidc_subject)
            VALUES (?, ?, 'Reviewer', ?, ?, ?, ?, ?)
            """,
            (
                username, password_hash, int(bool(oidc_subject)), invite_email,
                stamp if oidc_subject else None, int(not bool(oidc_subject)), oidc_subject,
            ),
        )
        user_id = int(cursor.lastrowid)
        brand = conn.execute(
            "INSERT INTO brands (name, slug, created_by_user_id) VALUES (?, ?, ?) RETURNING id",
            (clean_brand_name, slug, user_id),
        ).fetchone()
        brand_id = int(brand["id"])
        conn.execute(
            "INSERT INTO brand_memberships (brand_id, user_id, role) VALUES (?, ?, 'Brand Owner')",
            (brand_id, user_id),
        )
        _append_system_audit_event(
            conn,
            action="BRAND_CREATED",
            object_type="brand",
            actor_user_id=user_id,
            actor_username=username,
            object_id=brand_id,
            brand_id=brand_id,
            details={"name": clean_brand_name, "registration_method": "google" if oidc_subject else "email"},
        )
        if token:
            conn.execute(
                """
                INSERT INTO auth_tokens (user_id, purpose, token_hash, expires_at)
                VALUES (?, 'email_verification', ?, ?)
                """,
                (user_id, _token_hash(token), _timestamp(now + timedelta(hours=24))),
            )
        conn.commit()
    return {"user_id": user_id, "brand_id": brand_id, "token": token}


def get_invitation_details(token: str) -> dict | None:
    if not token or len(token) < 20:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT i.email, i.role, i.expires_at, b.name AS brand_name, i.accepted_at
            FROM brand_invitations i JOIN brands b ON b.id = i.brand_id
            WHERE i.token_hash = ?
            """, (_token_hash(token),)
        ).fetchone()
    if row is None or row["accepted_at"] or row["expires_at"] <= _timestamp(_now()):
        return None
    return dict(row)


def get_pending_invitations_for_email(email: str) -> list[dict]:
    invite_email = _email(email)
    with get_connection() as conn:
        return [
            dict(row) for row in conn.execute(
                """
                SELECT i.id, i.email, i.role, i.expires_at, b.name AS brand_name
                FROM brand_invitations i JOIN brands b ON b.id = i.brand_id
                WHERE i.email = ? COLLATE NOCASE AND i.accepted_at IS NULL
                  AND i.expires_at > ?
                ORDER BY i.created_at, i.id
                """, (invite_email, _timestamp(_now()))
            ).fetchall()
        ]


def register_invited_user_with_oidc(
    invitation_id: int, username: str, subject: str, email: str,
    *, email_verified: bool,
) -> int:
    """Register an invitee from a verified OIDC claim and a live email invite."""
    if not email_verified or not subject.strip():
        raise ValueError("A verified OIDC identity is required.")
    username = username.strip().casefold()
    invite_email = _email(email)
    subject = subject.strip()
    if len(username) < 3:
        raise ValueError("Username must contain at least 3 characters.")
    stamp = _timestamp(_now())
    random_password_hash = _hash_password(secrets.token_urlsafe(48))
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        invite = conn.execute(
            """
            SELECT email, role, expires_at, accepted_at FROM brand_invitations
            WHERE id = ?
            """, (invitation_id,)
        ).fetchone()
        if (
            invite is None or invite["accepted_at"]
            or invite["email"].casefold() != invite_email
            or invite["expires_at"] <= stamp
        ):
            raise ValueError("Invitation is invalid, expired, or issued to another email.")
        cursor = conn.execute(
            """
            INSERT INTO users
                (username, password_hash, role, is_active, email, email_verified_at, oidc_subject)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            """,
            (username, random_password_hash, invite["role"], invite_email, stamp, subject),
        )
        conn.commit()
        return int(cursor.lastrowid)


def accept_invitation_for_verified_email(
    principal: AuthenticatedPrincipal, invitation_id: int
) -> dict:
    actor = require_principal(principal, action="accept a brand invitation")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        invitation = conn.execute(
            """
            SELECT i.*, i.email AS invite_email, b.name, b.slug,
                   u.email AS user_email, u.email_verified_at
            FROM brand_invitations i
            JOIN brands b ON b.id = i.brand_id
            JOIN users u ON u.id = ?
            WHERE i.id = ? AND i.accepted_at IS NULL
            """, (actor.user_id, invitation_id)
        ).fetchone()
        if (
            invitation is None or invitation["expires_at"] <= _timestamp(_now())
            or not invitation["email_verified_at"]
            or invitation["invite_email"].casefold() != invitation["user_email"].casefold()
        ):
            raise BrandAccessError("Invitation is invalid, expired, or belongs to another email.")
        conn.execute(
            "INSERT INTO brand_memberships (brand_id, user_id, role) VALUES (?, ?, ?)",
            (invitation["brand_id"], actor.user_id, invitation["role"]),
        )
        conn.execute(
            "UPDATE brand_invitations SET accepted_at = ?, accepted_by_user_id = ? WHERE id = ?",
            (_timestamp(_now()), actor.user_id, invitation_id),
        )
        append_audit_event(
            conn, actor, action="BRAND_INVITATION_ACCEPTED",
            object_type="brand_membership", object_id=actor.user_id,
            brand_id=invitation["brand_id"], details={"role": invitation["role"]},
        )
        conn.commit()
        return {"id": invitation["brand_id"], "name": invitation["name"], "slug": invitation["slug"]}


def _role(role: BrandRole | str, *, allow_owner: bool = False) -> str:
    try:
        value = role.value if isinstance(role, BrandRole) else BrandRole(str(role)).value
    except ValueError as exc:
        raise ValueError("Invalid brand role.") from exc
    if not allow_owner and value == BrandRole.OWNER.value:
        raise ValueError("Brand Owner cannot be assigned through an invitation.")
    return value


def _membership(
    conn: sqlite3.Connection, principal: AuthenticatedPrincipal, brand_id: int,
    roles: tuple[BrandRole, ...] = (),
) -> dict:
    require_principal(principal, action="access this brand")
    row = conn.execute(
        """
        SELECT b.id AS brand_id, b.name, b.slug, b.is_active, bm.user_id, bm.role
        FROM brands b JOIN brand_memberships bm ON bm.brand_id = b.id
        WHERE b.id = ? AND bm.user_id = ? AND b.is_active = 1
        """,
        (brand_id, principal.user_id),
    ).fetchone()
    if row is None:
        raise BrandAccessError("You do not have access to this brand.")
    if roles and row["role"] not in {item.value for item in roles}:
        raise BrandAccessError("Your brand role cannot perform this action.")
    return dict(row)


def create_brand(principal: AuthenticatedPrincipal, name: str) -> dict:
    actor = require_principal(principal, action="create a brand")
    clean_name = name.strip()
    slug = _slugify(clean_name)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        memberships = conn.execute(
            "SELECT bm.role FROM brand_memberships bm "
            "JOIN brands b ON b.id = bm.brand_id "
            "WHERE bm.user_id = ? AND b.is_active = 1",
            (actor.user_id,),
        ).fetchall()
        if memberships and not any(
            row["role"] == BrandRole.OWNER.value for row in memberships
        ):
            raise BrandAccessError(
                "Only a current Brand Owner can create an additional workspace."
            )
        try:
            row = conn.execute(
                "INSERT INTO brands (name, slug, created_by_user_id) VALUES (?, ?, ?) RETURNING *",
                (clean_name, slug, actor.user_id),
            ).fetchone()
            conn.execute(
                "INSERT INTO brand_memberships (brand_id, user_id, role) VALUES (?, ?, ?)",
                (row["id"], actor.user_id, BrandRole.OWNER.value),
            )
            append_audit_event(
                conn, actor, action="BRAND_CREATED", object_type="brand",
                object_id=row["id"], brand_id=row["id"], details={"name": clean_name},
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            if "slug" in str(exc).lower():
                raise ValueError("A brand with this name already exists.") from exc
            raise
    return dict(row)


def list_brands(principal: AuthenticatedPrincipal) -> list[dict]:
    actor = require_principal(principal, action="list brands")
    with get_connection() as conn:
        return [
            dict(row) for row in conn.execute(
                """
                SELECT b.id, b.name, b.slug, bm.role
                FROM brands b JOIN brand_memberships bm ON bm.brand_id = b.id
                WHERE bm.user_id = ? AND b.is_active = 1 ORDER BY b.name
                """, (actor.user_id,)
            ).fetchall()
        ]


def require_brand_membership(
    principal: AuthenticatedPrincipal, brand_id: int,
    roles: tuple[BrandRole, ...] = (),
) -> dict:
    with get_connection() as conn:
        return _membership(conn, principal, brand_id, roles)


def require_record_brand(
    conn: sqlite3.Connection,
    principal: AuthenticatedPrincipal,
    brand_id: int,
    table: str,
    record_id: int,
    *,
    roles: tuple[BrandRole, ...] = (),
) -> dict:
    """Require membership and prove that an object belongs to the selected brand."""
    if table not in _SCOPED_TABLES:
        raise ValueError("Unsupported brand-scoped table.")
    membership = _membership(conn, principal, brand_id, roles)
    row = conn.execute(
        f"SELECT * FROM {table} WHERE id = ? AND brand_id = ?",
        (record_id, brand_id),
    ).fetchone()
    if row is None:
        raise BrandAccessError("That record is not part of the selected brand.")
    return {"membership": membership, "record": dict(row)}


def list_members(principal: AuthenticatedPrincipal, brand_id: int) -> list[dict]:
    with get_connection() as conn:
        _membership(conn, principal, brand_id, _OWNER_ROLES)
        return [
            dict(row) for row in conn.execute(
                """
                SELECT u.id AS user_id, u.username, u.email, bm.role, bm.created_at
                FROM brand_memberships bm JOIN users u ON u.id = bm.user_id
                WHERE bm.brand_id = ? ORDER BY bm.role, u.username
                """, (brand_id,)
            ).fetchall()
        ]


def invite_member(
    principal: AuthenticatedPrincipal, brand_id: int, email: str,
    role: BrandRole | str, *, expires_in: timedelta = timedelta(days=7),
) -> dict:
    actor = require_principal(principal, action="invite a brand member")
    invitation_role = _role(role)
    invite_email = _email(email)
    if expires_in <= timedelta(0):
        raise ValueError("Invitation expiry must be in the future.")
    raw_token = secrets.token_urlsafe(32)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _membership(conn, actor, brand_id, _OWNER_ROLES)
        user = conn.execute("SELECT id FROM users WHERE email = ?", (invite_email,)).fetchone()
        if user and conn.execute(
            "SELECT 1 FROM brand_memberships WHERE brand_id = ? AND user_id = ?",
            (brand_id, user["id"]),
        ).fetchone():
            raise ValueError("That user is already a member of this brand.")
        row = conn.execute(
            """
            INSERT INTO brand_invitations
                (brand_id, email, role, token_hash, invited_by_user_id, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id, brand_id, email, role, expires_at
            """,
            (brand_id, invite_email, invitation_role, _token_hash(raw_token),
             actor.user_id, _timestamp(_now() + expires_in)),
        ).fetchone()
        append_audit_event(
            conn, actor, action="BRAND_INVITATION_CREATED",
            object_type="brand_invitation", object_id=row["id"], brand_id=brand_id,
            details={"email": invite_email, "role": invitation_role},
        )
        conn.commit()
    result = dict(row)
    result["token"] = raw_token
    invite_url = f"{application_url()}?invite={raw_token}"
    delivered = send_email(
        invite_email,
        "Brand Rule Lab workspace invitation",
        (
            f"You have been invited to a Brand Rule Lab workspace as {invitation_role}.\n\n"
            f"Open this secure invitation link to accept: {invite_url}\n\n"
            f"This link expires at {row['expires_at']} and can be used once."
        ),
    )
    result["delivery_status"] = (
        "Invitation email sent." if delivered
        else "Email delivery is unavailable; provide the one-time token securely."
    )
    with get_connection() as conn:
        append_audit_event(
            conn, actor,
            action="BRAND_INVITATION_DELIVERY",
            object_type="brand_invitation", object_id=row["id"], brand_id=brand_id,
            details={"delivered": delivered},
        )
        conn.commit()
    return result


def accept_invitation(principal: AuthenticatedPrincipal, token: str) -> dict:
    actor = require_principal(principal, action="accept a brand invitation")
    if not token or len(token) < 20:
        raise ValueError("Invitation token is invalid.")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM brand_invitations
            WHERE token_hash = ? AND accepted_at IS NULL
            """, (_token_hash(token),)
        ).fetchone()
        if row is None:
            raise ValueError("Invitation is invalid or has already been used.")
        if row["expires_at"] <= _timestamp(_now()):
            raise ValueError("Invitation has expired.")
        user = conn.execute(
            "SELECT email, email_verified_at FROM users WHERE id = ?", (actor.user_id,)
        ).fetchone()
        if (
            not user or not user["email"] or not user["email_verified_at"]
            or user["email"].casefold() != row["email"].casefold()
        ):
            raise BrandAccessError("This invitation was issued to a different email address.")
        conn.execute(
            "INSERT INTO brand_memberships (brand_id, user_id, role) VALUES (?, ?, ?)",
            (row["brand_id"], actor.user_id, row["role"]),
        )
        conn.execute(
            "UPDATE brand_invitations SET accepted_at = ?, accepted_by_user_id = ? WHERE id = ?",
            (_timestamp(_now()), actor.user_id, row["id"]),
        )
        append_audit_event(
            conn, actor, action="BRAND_INVITATION_ACCEPTED",
            object_type="brand_membership", object_id=actor.user_id,
            brand_id=row["brand_id"], details={"role": row["role"]},
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT id, name, slug FROM brands WHERE id = ?", (row["brand_id"],)
        ).fetchone())


def change_member_role(
    principal: AuthenticatedPrincipal, brand_id: int, user_id: int, role: BrandRole | str,
) -> None:
    actor = require_principal(principal, action="change a member role")
    new_role = _role(role)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _membership(conn, actor, brand_id, _OWNER_ROLES)
        target = conn.execute(
            "SELECT role FROM brand_memberships WHERE brand_id = ? AND user_id = ?",
            (brand_id, user_id),
        ).fetchone()
        if target is None:
            raise ValueError("Brand member was not found.")
        if target["role"] == BrandRole.OWNER.value:
            raise ValueError("Brand Owner ownership requires an explicit transfer process.")
        conn.execute(
            "UPDATE brand_memberships SET role = ?, updated_at = ? WHERE brand_id = ? AND user_id = ?",
            (new_role, _timestamp(_now()), brand_id, user_id),
        )
        append_audit_event(
            conn, actor, action="BRAND_MEMBER_ROLE_CHANGED",
            object_type="brand_membership", object_id=user_id, brand_id=brand_id,
            details={"role": new_role},
        )
        conn.commit()


def transfer_brand_ownership(
    principal: AuthenticatedPrincipal,
    brand_id: int,
    new_owner_user_id: int,
    *,
    outgoing_owner_role: BrandRole | str = BrandRole.REVIEWER,
) -> None:
    """Atomically transfer a brand to an active, verified existing member.

    The recipient must already have access to the brand. The current owner is
    retained as a non-owner member, and the target is promoted before that
    demotion so the transaction never leaves the brand without an owner.
    """
    actor = require_principal(principal, action="transfer brand ownership")
    retained_role = _role(outgoing_owner_role)
    if new_owner_user_id == actor.user_id:
        raise ValueError("Choose another existing brand member as the new owner.")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _membership(conn, actor, brand_id, _OWNER_ROLES)
        target = conn.execute(
            """
            SELECT bm.role, u.is_active, u.email_verified_at
            FROM brand_memberships bm JOIN users u ON u.id = bm.user_id
            WHERE bm.brand_id = ? AND bm.user_id = ?
            """,
            (brand_id, new_owner_user_id),
        ).fetchone()
        if target is None:
            raise ValueError("The new owner must already be a member of this brand.")
        if target["role"] == BrandRole.OWNER.value:
            raise ValueError("That member is already a Brand Owner.")
        if not target["is_active"] or not target["email_verified_at"]:
            raise ValueError("The new owner must have an active, email-verified account.")
        stamp = _timestamp(_now())
        conn.execute(
            "UPDATE brand_memberships SET role = ?, updated_at = ? "
            "WHERE brand_id = ? AND user_id = ?",
            (BrandRole.OWNER.value, stamp, brand_id, new_owner_user_id),
        )
        conn.execute(
            "UPDATE brand_memberships SET role = ?, updated_at = ? "
            "WHERE brand_id = ? AND user_id = ?",
            (retained_role, stamp, brand_id, actor.user_id),
        )
        append_audit_event(
            conn, actor, action="BRAND_OWNERSHIP_TRANSFERRED",
            object_type="brand_membership", object_id=new_owner_user_id,
            brand_id=brand_id,
            details={"new_owner_user_id": new_owner_user_id,
                     "outgoing_owner_role": retained_role},
        )
        conn.commit()


def remove_member(principal: AuthenticatedPrincipal, brand_id: int, user_id: int) -> None:
    actor = require_principal(principal, action="remove a brand member")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _membership(conn, actor, brand_id, _OWNER_ROLES)
        target = conn.execute(
            "SELECT role FROM brand_memberships WHERE brand_id = ? AND user_id = ?",
            (brand_id, user_id),
        ).fetchone()
        if target is None:
            raise ValueError("Brand member was not found.")
        if target["role"] == BrandRole.OWNER.value:
            owners = conn.execute(
                "SELECT COUNT(*) AS n FROM brand_memberships WHERE brand_id = ? AND role = ?",
                (brand_id, BrandRole.OWNER.value),
            ).fetchone()["n"]
            if owners <= 1:
                raise ValueError("The last Brand Owner cannot be removed.")
        conn.execute(
            "DELETE FROM brand_memberships WHERE brand_id = ? AND user_id = ?",
            (brand_id, user_id),
        )
        append_audit_event(
            conn, actor, action="BRAND_MEMBER_REMOVED",
            object_type="brand_membership", object_id=user_id, brand_id=brand_id,
        )
        conn.commit()
