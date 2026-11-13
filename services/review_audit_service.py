"""Database adapter for verifying and exporting persisted review-event chains."""

from __future__ import annotations

import json
from typing import Any, Mapping

from db.connection import get_connection
from services.audit_chain_service import (
    AuditChainError,
    GENESIS_HASH,
    canonicalize_json,
    verify_chain,
)
class PersistedAuditError(ValueError):
    """Raised when persisted review events and their chain entries disagree."""


def get_verified_review_chain(draft_id: int) -> list[dict[str, Any]]:
    """Load and verify every persisted event for a draft in stable chain order."""
    if type(draft_id) is not int or draft_id < 1:
        raise ValueError("draft_id must be a positive integer.")

    with get_connection() as conn:
        conn.execute("BEGIN")
        rows = conn.execute(
            """
            SELECT re.id, re.review_id, re.finding_id, re.event_type, re.actor,
                   re.note, re.created_at, r.draft_id,
                   c.draft_id AS chain_draft_id, c.sequence AS chain_sequence,
                   c.payload_json, c.event_hash
            FROM review_events re
            JOIN reviews r ON r.id = re.review_id
            LEFT JOIN review_event_chain c ON c.event_id = re.id
            WHERE r.draft_id = ?
            ORDER BY c.sequence, re.id
            """,
            (draft_id,),
        ).fetchall()
        chain_count = conn.execute(
            "SELECT COUNT(*) AS n FROM review_event_chain WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()["n"]

    if chain_count != len(rows):
        raise PersistedAuditError(
            "Persisted review-event and chain-entry counts do not match."
        )

    entries: list[dict[str, Any]] = []
    for row in rows:
        if row["payload_json"] is None or row["event_hash"] is None:
            raise PersistedAuditError(
                f"Review event {row['id']} has no persisted chain entry."
            )
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise PersistedAuditError(
                f"Review event {row['id']} has malformed chain payload JSON."
            ) from exc

        if type(payload) is not dict:
            raise PersistedAuditError(
                f"Review event {row['id']} chain payload must be an object."
            )
        expected = {
            "actor": row["actor"],
            "created_at": row["created_at"],
            "draft_id": str(row["draft_id"]),
            "event_id": str(row["id"]),
            "event_type": row["event_type"],
            "finding_id": (
                str(row["finding_id"]) if row["finding_id"] is not None else None
            ),
            "note": row["note"],
            "review_id": str(row["review_id"]),
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise PersistedAuditError(
                f"Review event {row['id']} does not match its stored chain payload."
            )
        if (
            row["chain_draft_id"] != row["draft_id"]
            or row["chain_sequence"] != payload.get("sequence")
        ):
            raise PersistedAuditError(
                f"Review event {row['id']} chain index does not match its payload."
            )
        try:
            canonical_payload = canonicalize_json(payload).decode("utf-8")
        except (AuditChainError, UnicodeDecodeError) as exc:
            raise PersistedAuditError(
                f"Review event {row['id']} has an invalid canonical payload."
            ) from exc
        if canonical_payload != row["payload_json"]:
            raise PersistedAuditError(
                f"Review event {row['id']} payload is not stored canonically."
            )
        entries.append(
            {"payload": payload, "event_hash": row["event_hash"]}
        )

    final_hash = entries[-1]["event_hash"] if entries else GENESIS_HASH
    result = verify_chain(
        entries,
        expected_draft_id=str(draft_id),
        expected_event_count=len(rows),
        expected_final_chain_hash=final_hash,
    )
    if result.overall_status != "PASS":
        raise PersistedAuditError(
            f"Persisted review-event chain verification failed: {result.to_dict()}"
        )
    return entries


def create_draft_audit_export(
    draft_id: int,
    *,
    asset_identity: Mapping[str, Any] | None = None,
) -> bytes:
    """Create a self-contained export only after verifying the persisted chain."""
    entries = get_verified_review_chain(draft_id)
    from services.audit_export_service import create_audit_export

    return create_audit_export(
        entries,
        draft_id=str(draft_id),
        asset_identity=asset_identity,
    )
