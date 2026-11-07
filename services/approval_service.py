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
from models.enums import ClaimDriftStatus, DraftStatus, FactApprovalStatus, ReviewEventType, UserRole


def _require_reviewer(actor_role: UserRole | str | None, action: str) -> None:
    role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
    if role_value.casefold() != UserRole.REVIEWER.value.casefold():
        raise PermissionError(f"Reviewer role required to {action}.")


def _require_creator(actor_role: UserRole | str | None, action: str) -> None:
    role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
    if role_value.casefold() != UserRole.CREATOR.value.casefold():
        raise PermissionError(f"Creator role required to {action}.")


def submit_draft_for_review(
    draft_id: int,
    draft_version_id: int,
    submitted_by: str,
    *,
    actor_role: UserRole | str | None = None,
) -> int:
    """Atomically transition the latest checked version into the Reviewer queue."""
    _require_creator(actor_role, "submit drafts for review")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        draft = conn.execute(
            "SELECT status FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        latest = conn.execute(
            "SELECT id FROM draft_versions WHERE draft_id = ? "
            "ORDER BY version_number DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if draft is None or latest is None:
            raise ValueError("Draft or draft version does not exist.")
        if draft["status"] not in {
            DraftStatus.CHECKED.value, DraftStatus.CHANGES_REQUESTED.value
        }:
            raise ValueError("Only a checked draft can be submitted for review.")
        if latest["id"] != draft_version_id:
            raise ValueError("Only the latest checked draft version can be submitted.")
        review = conn.execute(
            "SELECT id FROM reviews WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if review is None:
            review_id = int(conn.execute(
                "INSERT INTO reviews (draft_id) VALUES (?)", (draft_id,)
            ).lastrowid)
        else:
            review_id = int(review["id"])
        conn.execute(
            "INSERT INTO review_events (review_id, event_type, actor) VALUES (?, 'SUBMITTED', ?)",
            (review_id, submitted_by.strip() or "demo_creator"),
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
    reviewer: str,
    *,
    actor_role: UserRole | str | None = None,
) -> None:
    """Atomically record a Reviewer change request for the latest submitted version."""
    _require_reviewer(actor_role, "request changes to drafts")
    if not note.strip():
        raise ValueError("Provide a note explaining the requested changes.")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        draft = conn.execute(
            "SELECT status FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        latest = conn.execute(
            "SELECT id FROM draft_versions WHERE draft_id = ? "
            "ORDER BY version_number DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if draft is None or draft["status"] != DraftStatus.READY_FOR_REVIEW.value:
            raise ValueError("Only a submitted draft can receive a change request.")
        if latest is None or latest["id"] != draft_version_id:
            raise ValueError("Only the latest submitted draft version can receive a decision.")
        review = conn.execute(
            "SELECT id FROM reviews WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if review is None:
            raise ValueError("Submitted draft has no review record.")
        conn.execute(
            "INSERT INTO review_events (review_id, event_type, actor, note) "
            "VALUES (?, 'CHANGES_REQUESTED', ?, ?)",
            (review["id"], reviewer.strip() or "demo_reviewer", note.strip()),
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
    _require_reviewer(actor_role, "mint approved claims")
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
) -> int:
    """Atomically approve a submitted version and create all claims and lineage snapshots."""
    _require_reviewer(actor_role, "approve submitted drafts")
    with get_connection() as conn:
        draft = conn.execute(
            "SELECT status FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if draft is None or draft["status"] != "READY_FOR_REVIEW":
            raise ValueError("Only a draft submitted as READY_FOR_REVIEW can be approved.")
        latest_version = conn.execute(
            "SELECT id FROM draft_versions WHERE draft_id = ? ORDER BY version_number DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if latest_version is None or latest_version["id"] != draft_version_id:
            raise ValueError("Only the latest submitted draft version can be approved.")

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

        conn.execute(
            "INSERT INTO review_events (review_id, event_type, actor, note) VALUES (?, 'APPROVED', ?, ?)",
            (review["id"], approved_by, reviewer_note),
        )
        conn.execute(
            """
            UPDATE reviews SET status = 'APPROVED', reviewer_note = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (reviewer_note, review["id"]),
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


def get_approved_claims(drift_status: str | None = None) -> list[dict]:
    """Return approved claims, optionally filtered by drift status."""
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
    if drift_status:
        query += " WHERE ac.drift_status = ?"
        params.append(drift_status)
    query += " ORDER BY ac.approved_at DESC"

    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_claim_fact_links(approved_claim_id: int) -> list[dict]:
    """Return fact versions from the current immutable approval snapshot."""
    with get_connection() as conn:
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


def get_claim_approval_snapshots(approved_claim_id: int) -> list[dict]:
    """Return all immutable approval snapshots, newest first."""
    with get_connection() as conn:
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


def get_snapshot_fact_links(snapshot_id: int) -> list[dict]:
    """Return the exact evidence versions recorded in one approval snapshot."""
    with get_connection() as conn:
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
