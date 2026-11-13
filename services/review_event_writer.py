"""Transaction-aware persistence for immutable, hash-chained review events."""

from __future__ import annotations

import sqlite3

from models.enums import UserRole

from services.audit_chain_service import (
    CHAIN_FORMAT,
    GENESIS_HASH,
    build_chain_entry,
    canonicalize_json,
)


_NEW_EVENT_PROVENANCE = frozenset(
    {"AUTHENTICATED", "DEMO_SIMULATED", "NO_TRUSTED_CONTEXT"}
)


def simulated_role_context(actor_role: UserRole | str | None) -> tuple[str | None, str]:
    """Represent the app's simulated role without claiming authenticated identity."""
    if actor_role is None:
        return None, "NO_TRUSTED_CONTEXT"
    role = actor_role.value if isinstance(actor_role, UserRole) else actor_role
    if not isinstance(role, str) or not role.strip():
        raise ValueError("A simulated actor role must be a non-empty string.")
    return role.strip(), "DEMO_SIMULATED"


def append_review_event(
    conn: sqlite3.Connection,
    *,
    review_id: int,
    event_type: str,
    actor: str,
    note: str | None = None,
    finding_id: int | None = None,
    actor_role_at_event: UserRole | str | None = None,
    actor_role_provenance: str = "NO_TRUSTED_CONTEXT",
) -> int:
    """Write the event and its chain entry in the caller's business transaction."""
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")

        if isinstance(actor_role_at_event, UserRole):
            actor_role_at_event = actor_role_at_event.value
        if actor_role_provenance not in _NEW_EVENT_PROVENANCE:
            raise ValueError("New review events require an explicit non-legacy provenance.")
        if actor_role_provenance in {"AUTHENTICATED", "DEMO_SIMULATED"}:
            if not isinstance(actor_role_at_event, str) or not actor_role_at_event.strip():
                raise ValueError(f"{actor_role_provenance} requires an explicit actor role.")
        elif actor_role_at_event is not None:
            raise ValueError("NO_TRUSTED_CONTEXT events cannot claim an actor role.")

        normalized_actor = actor.strip() if isinstance(actor, str) else ""
        normalized_type = event_type.strip().upper() if isinstance(event_type, str) else ""
        if not normalized_actor:
            raise ValueError("Review event actor is required.")

        review = conn.execute(
            "SELECT draft_id FROM reviews WHERE id = ?", (review_id,)
        ).fetchone()
        if review is None:
            raise ValueError("Review does not exist.")
        if finding_id is not None:
            finding = conn.execute(
                """
                SELECT dv.draft_id
                FROM findings f
                JOIN draft_versions dv ON dv.id = f.draft_version_id
                WHERE f.id = ?
                """,
                (finding_id,),
            ).fetchone()
            if finding is None:
                raise ValueError("Finding does not exist.")
            if finding["draft_id"] != review["draft_id"]:
                raise ValueError("Finding does not belong to the review's draft.")

        cursor = conn.execute(
            """
            INSERT INTO review_events (review_id, finding_id, event_type, actor, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (review_id, finding_id, normalized_type, normalized_actor, note),
        )
        event_id = int(cursor.lastrowid)
        event = conn.execute(
            """
            SELECT re.id, re.review_id, re.finding_id, re.event_type, re.actor,
                   re.note, re.created_at, r.draft_id
            FROM review_events re
            JOIN reviews r ON r.id = re.review_id
            WHERE re.id = ?
            """,
            (event_id,),
        ).fetchone()
        if event is None:
            raise RuntimeError("Inserted review event could not be read back.")

        head = conn.execute(
            """
            SELECT sequence, event_hash
            FROM review_event_chain
            WHERE draft_id = ?
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (event["draft_id"],),
        ).fetchone()
        sequence = int(head["sequence"]) + 1 if head else 1
        payload = {
            "actor": event["actor"],
            "actor_role_at_event": actor_role_at_event,
            "actor_role_provenance": actor_role_provenance,
            "created_at": event["created_at"],
            "draft_id": str(event["draft_id"]),
            "event_id": str(event["id"]),
            "event_type": event["event_type"],
            "finding_id": (
                str(event["finding_id"]) if event["finding_id"] is not None else None
            ),
            "format": CHAIN_FORMAT,
            "note": event["note"],
            "previous_hash": head["event_hash"] if head else GENESIS_HASH,
            "review_id": str(event["review_id"]),
            "sequence": sequence,
        }
        entry = build_chain_entry(payload)
        conn.execute(
            """
            INSERT INTO review_event_chain
                (event_id, draft_id, sequence, payload_json, event_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event["draft_id"],
                sequence,
                canonicalize_json(entry["payload"]).decode("utf-8"),
                entry["event_hash"],
            ),
        )
        return event_id
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
