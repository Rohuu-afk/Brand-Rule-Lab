"""
services/approval_service.py
-----------------------------
Handles the human approval step: minting stable claim IDs and storing
approved claims with their fact-version links.
Includes strict defensive validation for data integrity and claim ledger.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata

from db.connection import get_connection
from models.enums import BrandRole, ClaimDriftStatus, DraftStatus, FactApprovalStatus, ReviewEventType, UserRole
from services.auth_service import (
    AuthenticatedPrincipal,
    require_legacy_test_actor,
    require_principal,
)
from services.audit_service import append_audit_event
from services.brand_service import require_record_brand
from services.review_event_writer import append_review_event, simulated_role_context


def _require_reviewer(actor_role: UserRole | str | None, action: str) -> None:
    require_legacy_test_actor(action)
    role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
    if role_value.casefold() != UserRole.REVIEWER.value.casefold():
        raise PermissionError(f"Reviewer role required to {action}.")


def _require_creator(actor_role: UserRole | str | None, action: str) -> None:
    require_legacy_test_actor(action)
    role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
    if role_value.casefold() != UserRole.CREATOR.value.casefold():
        raise PermissionError(f"Creator role required to {action}.")


def submit_draft_for_review(
    draft_id: int,
    draft_version_id: int,
    submitted_by: str | None = None,
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> int:
    """Atomically transition the latest checked version into the Reviewer queue."""
    actor = require_principal(principal, action="submit drafts for review") if principal else None
    if principal is None:
        _require_creator(actor_role, "submit drafts for review")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if actor is not None:
            if brand_id is None:
                raise ValueError("A brand is required to submit this draft.")
            require_record_brand(
                conn, actor, brand_id, "drafts", draft_id,
                roles=(BrandRole.OWNER, BrandRole.CREATOR),
            )
        draft = conn.execute(
            "SELECT status, submitted_by_user_id, brand_id FROM drafts WHERE id = ?"
            + (" AND brand_id = ?" if brand_id is not None else ""),
            (draft_id, brand_id) if brand_id is not None else (draft_id,),
        ).fetchone()
        latest = conn.execute(
            "SELECT id FROM draft_versions WHERE draft_id = ? "
            "ORDER BY version_number DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if draft is None or latest is None:
            raise ValueError("Draft or draft version does not exist.")
        if actor is not None and brand_id is None and draft["brand_id"] is not None:
            raise PermissionError("A selected brand is required for this draft.")
        if draft["status"] not in {
            DraftStatus.CHECKED.value, DraftStatus.CHANGES_REQUESTED.value
        }:
            raise ValueError("Only a checked draft can be submitted for review.")
        if latest["id"] != draft_version_id:
            raise ValueError("Only the latest checked draft version can be submitted.")
        if actor is not None and draft["submitted_by_user_id"] not in (None, actor.user_id):
            raise PermissionError("Only the draft creator can submit this draft.")
        review = conn.execute(
            "SELECT id FROM reviews WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if review is None:
            review_id = int(conn.execute(
                "INSERT INTO reviews (draft_id, reviewer_user_id) VALUES (?, NULL)",
                (draft_id,),
            ).lastrowid)
        else:
            review_id = int(review["id"])
        if actor is not None:
            conn.execute(
                "UPDATE drafts SET submitted_by_user_id = ?, submitted_by = ? "
                "WHERE id = ? AND submitted_by_user_id IS NULL",
                (actor.user_id, actor.username, draft_id),
            )
        role_at_event, provenance = (
            (actor.role.value if hasattr(actor.role, "value") else str(actor.role), "AUTHENTICATED")
            if actor is not None
            else simulated_role_context(actor_role)
        )
        append_review_event(
            conn,
            review_id=review_id,
            event_type="SUBMITTED",
            actor=actor.username if actor else (submitted_by or "").strip() or "demo_creator",
            actor_role_at_event=role_at_event,
            actor_role_provenance=provenance,
        )
        if actor is not None:
            append_audit_event(
                conn,
                actor,
                action="REVIEW_SUBMITTED",
                object_type="draft",
                object_id=draft_id,
                brand_id=brand_id,
                details={"version_id": draft_version_id},
            )
        conn.execute(
            "UPDATE drafts SET status = 'READY_FOR_REVIEW', "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (draft_id,),
        )
        conn.commit()
        return review_id


def request_draft_changes(
    draft_id: int,
    draft_version_id: int,
    note: str,
    reviewer: str | None = None,
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    """Atomically record a Reviewer change request for the latest submitted version."""
    actor = require_principal(principal, action="request changes to drafts") if principal else None
    if principal is None:
        _require_reviewer(actor_role, "request changes to drafts")
    if not note.strip():
        raise ValueError("Provide a note explaining the requested changes.")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if actor is not None:
            if brand_id is None:
                raise ValueError("A brand is required to review this draft.")
            require_record_brand(
                conn, actor, brand_id, "drafts", draft_id,
                roles=(BrandRole.OWNER, BrandRole.REVIEWER),
            )
        draft = conn.execute(
            "SELECT status, brand_id FROM drafts WHERE id = ?"
            + (" AND brand_id = ?" if brand_id is not None else ""),
            (draft_id, brand_id) if brand_id is not None else (draft_id,),
        ).fetchone()
        latest = conn.execute(
            "SELECT id FROM draft_versions WHERE draft_id = ? "
            "ORDER BY version_number DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if draft is None or draft["status"] != DraftStatus.READY_FOR_REVIEW.value:
            raise ValueError("Only a submitted draft can receive a change request.")
        if actor is not None and brand_id is None and draft["brand_id"] is not None:
            raise PermissionError("A selected brand is required for this draft.")
        if latest is None or latest["id"] != draft_version_id:
            raise ValueError("Only the latest submitted draft version can receive a decision.")
        review = conn.execute(
            "SELECT id FROM reviews WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if review is None:
            raise ValueError("Submitted draft has no review record.")
        role_at_event, provenance = (
            (actor.role.value if hasattr(actor.role, "value") else str(actor.role), "AUTHENTICATED")
            if actor is not None
            else simulated_role_context(actor_role)
        )
        append_review_event(
            conn,
            review_id=review["id"],
            event_type="CHANGES_REQUESTED",
            actor=actor.username if actor else (reviewer or "").strip() or "demo_reviewer",
            note=note.strip(),
            actor_role_at_event=role_at_event,
            actor_role_provenance=provenance,
        )
        if actor is not None:
            append_audit_event(
                conn,
                actor,
                action="REVIEW_CHANGES_REQUESTED",
                object_type="draft",
                object_id=draft_id,
                brand_id=brand_id,
                details={"version_id": draft_version_id},
            )
        conn.execute(
            "UPDATE reviews SET status = 'CHANGES_REQUESTED', reviewer_note = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (note.strip(), review["id"]),
        )
        conn.execute(
            "UPDATE drafts SET status = 'CHANGES_REQUESTED', "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (draft_id,),
        )
        conn.commit()


def _normalise_text(text: str) -> str:
    """Unicode-normalise, strip, and lower-case a claim text for hashing."""
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.strip().lower().split())


def _make_stable_claim_id(draft_id: int, claim_ordinal: int, claim_text: str) -> str:
    """
    Compute a 12-character hex stable claim ID.
    Formula: SHA-256( str(draft_id) | '|' | str(claim_ordinal) | '|' | normalised_claim_text )
    Collisions are handled by appending a suffix in mint_approved_claim().
    """
    normalised = _normalise_text(claim_text)
    payload = f"{draft_id}|{claim_ordinal}|{normalised}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def mint_approved_claim(
    draft_id: int,
    claim_ordinal: int,
    approved_text: str,
    claim_mention_id: int | None,
    fact_version_ids: list[int] | None,
    approved_by: str = "demo_reviewer",
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
) -> str:
    """
    Mint a stable claim ID, persist the approved claim, and link fact versions.

    Defensive validations performed before minting:
      1. draft_id exists in drafts.
      2. Every fact_version_id in fact_version_ids exists in fact_versions.
      3. Every fact_version_id has approval_status == 'APPROVED' (cannot link PENDING or EXPIRED).
      4. If draft has a product_id, fact's product_id MUST match draft's product_id.
      5. If draft has a market specified, fact MUST be eligible for that market.
      6. Any validation failure raises ValueError and aborts the entire transaction.

    Returns the stable_claim_id string.
    """
    actor = (
        require_principal(
            principal, roles=(UserRole.REVIEWER,), action="mint approved claims"
        )
        if principal is not None
        else None
    )
    if principal is None:
        _require_reviewer(actor_role, "mint approved claims")
    if actor is not None:
        approved_by = actor.username
    if fact_version_ids is None:
        fact_version_ids = []

    with get_connection() as conn:
        stable_id = _mint_claim_in_transaction(
            conn,
            draft_id,
            claim_ordinal,
            approved_text,
            claim_mention_id,
            fact_version_ids,
            approved_by,
        )
        conn.commit()
    return stable_id


def _mint_claim_in_transaction(
    conn,
    draft_id: int,
    claim_ordinal: int,
    approved_text: str,
    claim_mention_id: int | None,
    fact_version_ids: list[int],
    approved_by: str,
) -> str:
    if not approved_text.strip():
        raise ValueError("Cannot mint an empty approved claim.")

    normalised = _normalise_text(approved_text)
    text_hash = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    base_id = _make_stable_claim_id(draft_id, claim_ordinal, approved_text)
    draft = conn.execute(
        "SELECT id, product_id, market FROM drafts WHERE id = ?", (draft_id,)
    ).fetchone()
    if draft is None:
        raise ValueError(f"Cannot mint claim: Draft id {draft_id} does not exist.")
    from datetime import date
    today = date.today().isoformat()
    for fv_id in sorted(set(fact_version_ids)):
        row = conn.execute(
            """
            SELECT fv.id, fv.approval_status, fv.version_number,
                   fv.valid_from, fv.valid_until, f.name AS fact_name,
                   f.product_id, f.markets
            FROM fact_versions fv JOIN facts f ON f.id = fv.fact_id
            WHERE fv.id = ?
            """,
            (fv_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Fact version id {fv_id} does not exist.")
        if row["approval_status"] != "APPROVED":
            raise ValueError(
                f"Only 'APPROVED' fact versions can be linked; version {fv_id} is {row['approval_status']}."
            )
        if row["valid_from"] and row["valid_from"] > today:
            raise ValueError(f"Fact version {fv_id} is not valid until {row['valid_from']}.")
        if row["valid_until"] and row["valid_until"] < today:
            raise ValueError(f"Fact version {fv_id} expired on {row['valid_until']}.")
        if row["product_id"] != draft["product_id"]:
            raise ValueError(f"Product mismatch: fact version {fv_id} belongs to a different product.")
        if draft["product_id"] is None or not draft["market"]:
            raise ValueError("Product and market are required to link approved evidence.")
        try:
            markets = {str(value).strip().casefold() for value in json.loads(row["markets"] or "[]")}
        except (json.JSONDecodeError, TypeError):
            markets = set()
        if draft["market"].strip().casefold() not in markets and "all" not in markets:
            raise ValueError(f"Fact version {fv_id} is not eligible for market {draft['market']}.")

    stable_id = base_id
    suffix = 0
    while True:
        existing = conn.execute(
            "SELECT id, draft_id FROM approved_claims WHERE stable_claim_id = ?",
            (stable_id,),
        ).fetchone()
        if existing is None or existing["draft_id"] == draft_id:
            break
        suffix += 1
        stable_id = f"{base_id}{suffix:02x}"

    conn.execute(
        """
        INSERT INTO approved_claims
            (stable_claim_id, draft_id, claim_mention_id, approved_text,
             approved_text_hash, drift_status, approved_by)
        VALUES (?, ?, ?, ?, ?, 'CURRENT', ?)
        ON CONFLICT(stable_claim_id) DO UPDATE SET
            claim_mention_id = excluded.claim_mention_id,
            approved_text = excluded.approved_text,
            approved_text_hash = excluded.approved_text_hash,
            drift_status = 'CURRENT',
            approved_by = excluded.approved_by,
            approved_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
        """,
        (stable_id, draft_id, claim_mention_id, approved_text, text_hash, approved_by),
    )
    approved_claim_id = conn.execute(
        "SELECT id FROM approved_claims WHERE stable_claim_id = ?", (stable_id,)
    ).fetchone()["id"]
    snapshot = conn.execute(
        """
        INSERT INTO approved_claim_snapshots
            (approved_claim_id, stable_claim_id, approved_text, approved_text_hash,
             product_id, market, reviewer)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            approved_claim_id, stable_id, approved_text, text_hash,
            draft["product_id"], draft["market"], approved_by,
        ),
    )
    snapshot_id = snapshot.lastrowid
    for fv_id in sorted(set(fact_version_ids)):
        conn.execute(
            "INSERT INTO snapshot_fact_links (snapshot_id, fact_version_id) VALUES (?, ?)",
            (snapshot_id, fv_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO claim_fact_links (approved_claim_id, fact_version_id) VALUES (?, ?)",
            (approved_claim_id, fv_id),
        )
    conn.execute(
        "UPDATE approved_claims SET current_snapshot_id = ? WHERE id = ?",
        (snapshot_id, approved_claim_id),
    )
    return stable_id


def approve_submitted_draft(
    draft_id: int,
    draft_version_id: int,
    reviewer_note: str,
    acknowledge_not_observed: bool = False,
    approved_by: str = "demo_reviewer",
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> int:
    """Atomically approve a submitted version and create all claims and lineage snapshots."""
    actor = require_principal(principal, action="approve submitted drafts") if principal else None
    if principal is None:
        _require_reviewer(actor_role, "approve submitted drafts")
    with get_connection() as conn:
        # Serialize the status check and approval writes so concurrent review
        # submissions cannot both mint claims for the same draft version.
        conn.execute("BEGIN IMMEDIATE")
        if actor is not None:
            if brand_id is None:
                raise ValueError("A brand is required to approve this draft.")
            require_record_brand(
                conn, actor, brand_id, "drafts", draft_id,
                roles=(BrandRole.OWNER, BrandRole.REVIEWER),
            )
        draft = conn.execute(
            "SELECT status, submitted_by, submitted_by_user_id, brand_id FROM drafts WHERE id = ?"
            + (" AND brand_id = ?" if brand_id is not None else ""),
            (draft_id, brand_id) if brand_id is not None else (draft_id,),
        ).fetchone()
        if draft is None or draft["status"] != "READY_FOR_REVIEW":
            raise ValueError("Only a draft submitted as READY_FOR_REVIEW can be approved.")
        if actor is not None and brand_id is None and draft["brand_id"] is not None:
            raise PermissionError("A selected brand is required for this draft.")
        latest_version = conn.execute(
            "SELECT id FROM draft_versions WHERE draft_id = ? ORDER BY version_number DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if latest_version is None or latest_version["id"] != draft_version_id:
            raise ValueError("Only the latest submitted draft version can be approved.")
        if actor is not None and (
            draft["submitted_by_user_id"] == actor.user_id
            or (
                draft["submitted_by_user_id"] is None
                and draft["submitted_by"].strip().casefold() == actor.username.casefold()
            )
        ):
            raise PermissionError("A creator cannot approve their own submission.")
        if actor is not None:
            approved_by = actor.username

        blockers = conn.execute(
            """
            SELECT f.id FROM findings f
            WHERE f.draft_version_id = ? AND f.severity IN ('HIGH','CRITICAL')
              AND f.result IN ('FAIL','REVIEW_REQUIRED','WARNING')
              AND NOT (
                  f.status = 'DISMISSED'
                  AND f.finding_type != 'fact_match'
                  AND COALESCE((
                      SELECT fo.decision FROM finding_overrides fo
                      WHERE fo.finding_id = f.id
                      ORDER BY fo.id DESC LIMIT 1
                  ), '') = 'ACCEPT'
              )
            LIMIT 1
            """,
            (draft_version_id,),
        ).fetchone()
        if blockers:
            raise ValueError("Resolve open HIGH/CRITICAL findings before approval.")
        unlinked = conn.execute(
            """
            SELECT id FROM findings
            WHERE draft_version_id = ? AND finding_type = 'fact_match'
              AND (evidence_relationship IS NULL OR evidence_relationship != 'SUPPORTS'
                   OR fact_version_id IS NULL)
            LIMIT 1
            """,
            (draft_version_id,),
        ).fetchone()
        if unlinked:
            raise ValueError("Unlinked or contradictory claims must be resolved before approval.")
        not_observed = conn.execute(
            "SELECT id FROM findings WHERE draft_version_id = ? AND result = 'NOT_OBSERVED' LIMIT 1",
            (draft_version_id,),
        ).fetchone()
        if not_observed and not acknowledge_not_observed:
            raise ValueError("Acknowledge checks marked NOT_OBSERVED before approval.")

        review = conn.execute(
            "SELECT id FROM reviews WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if review is None:
            raise ValueError("Submitted draft has no review record.")

        claims = conn.execute(
            "SELECT * FROM claim_mentions WHERE draft_version_id = ? ORDER BY id",
            (draft_version_id,),
        ).fetchall()
        for ordinal, claim in enumerate(claims):
            links = conn.execute(
                """
                SELECT DISTINCT fact_version_id FROM findings
                WHERE draft_version_id = ? AND claim_mention_id = ?
                  AND evidence_relationship = 'SUPPORTS'
                  AND fact_version_id IS NOT NULL
                """,
                (draft_version_id, claim["id"]),
            ).fetchall()
            _mint_claim_in_transaction(
                conn, draft_id, ordinal, claim["claim_text"], claim["id"],
                [row["fact_version_id"] for row in links], approved_by,
            )

        role_at_event, provenance = (
            (actor.role.value if hasattr(actor.role, "value") else str(actor.role), "AUTHENTICATED")
            if actor is not None
            else simulated_role_context(actor_role)
        )
        append_review_event(
            conn,
            review_id=review["id"],
            event_type="APPROVED",
            actor=approved_by,
            note=reviewer_note,
            actor_role_at_event=role_at_event,
            actor_role_provenance=provenance,
        )
        if actor is not None:
            append_audit_event(
                conn,
                actor,
                action="DRAFT_APPROVED",
                object_type="draft",
                object_id=draft_id,
                brand_id=brand_id,
                details={"version_id": draft_version_id},
            )
        conn.execute(
            """
            UPDATE reviews SET status = 'APPROVED', reviewer_note = ?,
                reviewer = COALESCE(?, reviewer),
                reviewer_user_id = COALESCE(?, reviewer_user_id),
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (
                reviewer_note,
                actor.username if actor is not None else None,
                actor.user_id if actor is not None else None,
                review["id"],
            ),
        )
        conn.execute(
            """
            UPDATE drafts SET status = 'APPROVED',
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (draft_id,),
        )
        conn.commit()
        return len(claims)


def get_approved_claims(
    drift_status: str | None = None,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return approved claims, optionally filtered by drift status."""
    if principal is None:
        require_legacy_test_actor("read unscoped approved claims")
    query = """
        SELECT ac.*,
               d.title AS draft_title,
               d.product_id,
               p.name  AS product_name
        FROM approved_claims ac
        JOIN drafts d ON d.id = ac.draft_id
        LEFT JOIN products p ON p.id = d.product_id
    """
    params: list = []
    if principal is not None:
        if brand_id is None:
            raise ValueError("A brand is required for authenticated claim lookup.")
        from services.brand_service import require_brand_membership

        require_brand_membership(principal, brand_id)
        query += " WHERE d.brand_id = ?"
        params.append(brand_id)
    if drift_status:
        query += " AND " if " WHERE " in query else " WHERE "
        query += "ac.drift_status = ?"
        params.append(drift_status)
    query += " ORDER BY ac.approved_at DESC"

    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_claim_fact_links(
    approved_claim_id: int,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return fact versions from the current immutable approval snapshot."""
    if principal is None:
        require_legacy_test_actor("read unscoped claim evidence")
    with get_connection() as conn:
        if principal is not None:
            _require_claim_brand(conn, principal, brand_id, approved_claim_id)
        rows = conn.execute(
            """
            SELECT sfl.fact_version_id, sfl.snapshot_id, fv.statement,
                   fv.version_number, fv.approval_status, fv.evidence_ref,
                   f.name AS fact_name
            FROM approved_claims ac
            JOIN approved_claim_snapshots s ON s.id = ac.current_snapshot_id
            JOIN snapshot_fact_links sfl ON sfl.snapshot_id = s.id
            JOIN fact_versions fv ON fv.id = sfl.fact_version_id
            JOIN facts f ON f.id = fv.fact_id
            WHERE ac.id = ?
            """,
            (approved_claim_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_claim_approval_snapshots(
    approved_claim_id: int,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return all immutable approval snapshots, newest first."""
    if principal is None:
        require_legacy_test_actor("read unscoped claim snapshots")
    with get_connection() as conn:
        if principal is not None:
            _require_claim_brand(conn, principal, brand_id, approved_claim_id)
        rows = conn.execute(
            """
            SELECT s.*, p.name AS product_name
            FROM approved_claim_snapshots s
            LEFT JOIN products p ON p.id = s.product_id
            WHERE s.approved_claim_id = ?
            ORDER BY s.id DESC
            """,
            (approved_claim_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_snapshot_fact_links(
    snapshot_id: int,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return the exact evidence versions recorded in one approval snapshot."""
    if principal is None:
        require_legacy_test_actor("read unscoped snapshot evidence")
    with get_connection() as conn:
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for authenticated snapshot lookup.")
            from services.brand_service import require_brand_membership

            require_brand_membership(principal, brand_id)
            allowed = conn.execute(
                """
                SELECT 1 FROM approved_claim_snapshots s
                JOIN approved_claims ac ON ac.id = s.approved_claim_id
                JOIN drafts d ON d.id = ac.draft_id
                WHERE s.id = ? AND d.brand_id = ?
                """, (snapshot_id, brand_id)
            ).fetchone()
            if allowed is None:
                from services.brand_service import BrandAccessError

                raise BrandAccessError("That snapshot is not part of the selected brand.")
        rows = conn.execute(
            """
            SELECT sfl.fact_version_id, fv.version_number, fv.statement,
                   fv.evidence_ref, fv.approval_status, f.name AS fact_name
            FROM snapshot_fact_links sfl
            JOIN fact_versions fv ON fv.id = sfl.fact_version_id
            JOIN facts f ON f.id = fv.fact_id
            WHERE sfl.snapshot_id = ?
            ORDER BY sfl.fact_version_id
            """,
            (snapshot_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def _require_claim_brand(conn, principal, brand_id, approved_claim_id) -> None:
    if brand_id is None:
        raise ValueError("A brand is required for authenticated claim lookup.")
    from services.brand_service import BrandAccessError, require_brand_membership

    require_brand_membership(principal, brand_id)
    allowed = conn.execute(
        """
        SELECT 1 FROM approved_claims ac JOIN drafts d ON d.id = ac.draft_id
        WHERE ac.id = ? AND d.brand_id = ?
        """, (approved_claim_id, brand_id)
    ).fetchone()
    if allowed is None:
        raise BrandAccessError("That claim is not part of the selected brand.")
