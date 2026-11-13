from __future__ import annotations

import sqlite3

import pytest

from db.connection import apply_schema, get_connection
from db.connection import _apply_migration_17 as _apply_migration_8
from services.audit_chain_service import GENESIS_HASH, verify_chain
from services.audit_export_service import verify_audit_export
from services.approval_service import request_draft_changes
from services.ledger_service import get_review_audit_trail
from services.review_audit_service import (
    PersistedAuditError,
    create_draft_audit_export,
    get_verified_review_chain,
)
from services.review_event_writer import append_review_event
from services.review_service import record_review_event


@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    database_path = tmp_path / "review-audit.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("BRL_ENABLE_LEGACY_TEST_ACTORS", "1")
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_schema(conn)
    conn.close()
    return database_path


def _create_review(draft_status: str = "DRAFT") -> tuple[int, int, int]:
    with get_connection() as conn:
        draft_id = conn.execute(
            "INSERT INTO drafts (title, status) VALUES ('Audit test draft', ?)",
            (draft_status,),
        ).lastrowid
        version_id = conn.execute(
            """
            INSERT INTO draft_versions (draft_id, version_number, raw_text)
            VALUES (?, 1, 'Test copy')
            """,
            (draft_id,),
        ).lastrowid
        review_id = conn.execute(
            "INSERT INTO reviews (draft_id) VALUES (?)", (draft_id,)
        ).lastrowid
        conn.commit()
    return int(draft_id), int(version_id), int(review_id)


def test_persisted_events_have_stable_chain_and_explicit_role_provenance(
    isolated_database,
):
    draft_id, _, review_id = _create_review()

    record_review_event(review_id, "COMMENT", "reviewer", "First note")
    record_review_event(
        review_id,
        "COMMENT",
        "reviewer",
        "Second note",
        actor_role="Reviewer",
    )

    entries = get_verified_review_chain(draft_id)
    assert [entry["payload"]["sequence"] for entry in entries] == [1, 2]
    assert [entry["payload"]["note"] for entry in entries] == [
        "First note",
        "Second note",
    ]
    assert entries[0]["payload"]["actor_role_provenance"] == "NO_TRUSTED_CONTEXT"
    assert entries[1]["payload"]["actor_role_at_event"] == "Reviewer"
    assert entries[1]["payload"]["actor_role_provenance"] == "DEMO_SIMULATED"

    trail = get_review_audit_trail(draft_id)
    assert [event["chain_sequence"] for event in trail] == [1, 2]
    assert all(event["event_hash"] for event in trail)


def test_writer_rejects_finding_from_another_draft(isolated_database):
    draft_a_id, _, review_a_id = _create_review()
    draft_b_id, version_b_id, _ = _create_review()
    with get_connection() as conn:
        finding_b_id = conn.execute(
            """
            INSERT INTO findings (draft_version_id, finding_type, result, severity)
            VALUES (?, 'rule_check', 'FAIL', 'MEDIUM')
            """,
            (version_b_id,),
        ).lastrowid
        conn.commit()

    with get_connection() as conn:
        with pytest.raises(ValueError, match="does not belong"):
            append_review_event(
                conn,
                review_id=review_a_id,
                finding_id=finding_b_id,
                event_type="OVERRIDE",
                actor="reviewer",
            )

    with get_connection() as conn:
        event_count = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM review_events re
            JOIN reviews r ON r.id = re.review_id
            WHERE r.draft_id IN (?, ?)
            """,
            (draft_a_id, draft_b_id),
        ).fetchone()["n"]
        chain_count = conn.execute(
            "SELECT COUNT(*) AS n FROM review_event_chain WHERE draft_id = ?",
            (draft_a_id,),
        ).fetchone()["n"]
    assert event_count == 0
    assert chain_count == 0


def test_legacy_migration_backfills_stable_order_without_inventing_roles():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE drafts (id INTEGER PRIMARY KEY);
        CREATE TABLE reviews (
            id INTEGER PRIMARY KEY,
            draft_id INTEGER NOT NULL REFERENCES drafts(id)
        );
        CREATE TABLE review_events (
            id INTEGER PRIMARY KEY,
            review_id INTEGER NOT NULL REFERENCES reviews(id),
            finding_id INTEGER,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY);
        INSERT INTO schema_migrations(version) VALUES (7);
        INSERT INTO drafts(id) VALUES (1), (2);
        INSERT INTO reviews(id, draft_id) VALUES (11, 1), (12, 1), (13, 2);
        INSERT INTO review_events
            (id, review_id, event_type, actor, note, created_at)
        VALUES
            (101, 11, 'COMMENT', 'legacy-a', 'First', '2026-11-13T00:00:00Z'),
            (102, 12, 'COMMENT', 'legacy-b', 'Second', '2026-11-13T00:00:00Z'),
            (103, 13, 'COMMENT', 'legacy-c', 'Other draft', '2026-11-13T00:00:00Z');
        """
    )

    _apply_migration_8(conn, 7)
    rows = conn.execute(
        "SELECT draft_id, sequence, payload_json, event_hash "
        "FROM review_event_chain ORDER BY draft_id, sequence"
    ).fetchall()
    assert [(row["draft_id"], row["sequence"]) for row in rows] == [
        (1, 1),
        (1, 2),
        (2, 1),
    ]

    entries = get_chain_entries(rows)
    assert all(
        entry["payload"]["actor_role_provenance"] == "LEGACY_UNRECORDED"
        and entry["payload"]["actor_role_at_event"] is None
        for entry in entries
    )
    result = verify_chain(
        entries[:2],
        expected_draft_id="1",
        expected_event_count=2,
        expected_final_chain_hash=entries[1]["event_hash"],
    )
    assert result.overall_status == "PASS"
    conn.close()


def get_chain_entries(rows):
    import json

    return [
        {"payload": json.loads(row["payload_json"]), "event_hash": row["event_hash"]}
        for row in rows
    ]


def test_chain_failure_rolls_back_business_decision_and_event(isolated_database):
    draft_id, version_id, _ = _create_review("READY_FOR_REVIEW")
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_review_chain_insert
            BEFORE INSERT ON review_event_chain
            BEGIN SELECT RAISE(ABORT, 'forced chain insertion failure'); END
            """
        )
        conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced chain insertion failure"):
        request_draft_changes(
            draft_id,
            version_id,
            "Please revise the claim.",
            "reviewer",
            actor_role="Reviewer",
        )

    with get_connection() as conn:
        draft_status = conn.execute(
            "SELECT status FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()["status"]
        event_count = conn.execute(
            "SELECT COUNT(*) AS n FROM review_events"
        ).fetchone()["n"]
    assert draft_status == "READY_FOR_REVIEW"
    assert event_count == 0


def test_persisted_export_verifies_and_database_tampering_is_detected(
    isolated_database,
):
    draft_id, _, review_id = _create_review()
    record_review_event(review_id, "COMMENT", "reviewer", "A verifiable note")

    bundle = create_draft_audit_export(draft_id)
    assert verify_audit_export(bundle).overall_status == "PASS"

    with get_connection() as conn:
        conn.execute("DROP TRIGGER prevent_review_event_update")
        conn.execute(
            "UPDATE review_events SET note = 'tampered' WHERE review_id = ?",
            (review_id,),
        )
        conn.commit()

    with pytest.raises(PersistedAuditError, match="does not match"):
        get_verified_review_chain(draft_id)


def test_orphaned_chain_entry_is_not_omitted_from_verification(isolated_database):
    draft_id, _, review_id = _create_review()
    record_review_event(review_id, "COMMENT", "reviewer", "A verifiable note")

    with get_connection() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            INSERT INTO review_event_chain
                (event_id, draft_id, sequence, payload_json, event_hash)
            VALUES (999, ?, 2, '{}', ?)
            """,
            (draft_id, "a" * 64),
        )
        conn.commit()

    with pytest.raises(PersistedAuditError, match="counts do not match"):
        get_verified_review_chain(draft_id)


def test_chain_and_event_rows_reject_updates_and_deletes(isolated_database):
    draft_id, _, review_id = _create_review()
    record_review_event(review_id, "COMMENT", "reviewer", "Append-only")
    with get_connection() as conn:
        event_id = conn.execute(
            "SELECT id FROM review_events WHERE review_id = ?", (review_id,)
        ).fetchone()["id"]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE review_events SET note = 'changed' WHERE id = ?", (event_id,)
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM review_event_chain WHERE event_id = ?", (event_id,)
            )
        conn.rollback()

    assert get_verified_review_chain(draft_id)[0]["payload"]["previous_hash"] == GENESIS_HASH
