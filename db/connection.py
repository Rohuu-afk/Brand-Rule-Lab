"""
db/connection.py
----------------
Opens and configures the SQLite connection.
Call get_connection() wherever you need a DB handle.
The connection enables:
  - Foreign-key enforcement (PRAGMA foreign_keys = ON)
  - WAL journal mode for concurrent reads
  - Row factory so rows behave like dicts

Usage
-----
    from db.connection import get_connection

    with get_connection() as conn:
        conn.execute("SELECT 1")
        # the context manager auto-commits on success, rolls back on exception
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Resolve DB path from environment (default: data/brand_rule_lab.db)
_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "brand_rule_lab.db"


def get_db_path() -> Path:
    """Return the currently configured SQLite database path."""
    return Path(os.getenv("DATABASE_PATH", str(_DEFAULT_DB)))


def get_connection() -> sqlite3.Connection:
    """
    Return a new SQLite connection with:
      - Foreign keys ON
      - WAL journal mode
      - Row factory returning sqlite3.Row (access columns by name)

    The connection is *not* a context manager by default; wrap in `with conn:` or
    use the returned connection in a `with get_connection() as conn:` block.
    """
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply additive schema upgrades once; never perform DDL on ordinary connections."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        )
        """
    )
    current = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
    ).fetchone()["version"]
    if current >= 1:
        _apply_migration_2(conn, current)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        additions = {
            "findings": [("evidence_relationship", "TEXT")],
            "draft_versions": [("visual_metadata", "TEXT NOT NULL DEFAULT '{}'")],
            "guidelines": [
                ("source_type", "TEXT NOT NULL DEFAULT 'manual'"),
                ("section_ref", "TEXT"),
            ],
            "brand_rules": [("review_status", "TEXT NOT NULL DEFAULT 'PROPOSED'")],
            "brand_assets": [("owner_approved", "INTEGER NOT NULL DEFAULT 0")],
            "approved_claims": [("current_snapshot_id", "INTEGER")],
        }
        for table, columns in additions.items():
            existing = _column_names(conn, table)
            for name, definition in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

        conn.execute(
            """
            UPDATE brand_rules SET review_status =
                CASE WHEN owner_approved = 1 THEN 'APPROVED' ELSE 'PROPOSED' END
            """
        )
        conn.execute(
            """
            UPDATE brand_assets SET owner_approved = 1
            WHERE is_active = 1 AND owner_approved = 0
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approved_claim_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                approved_claim_id INTEGER NOT NULL
                    REFERENCES approved_claims(id) ON DELETE RESTRICT,
                stable_claim_id TEXT NOT NULL,
                approved_text TEXT NOT NULL,
                approved_text_hash TEXT NOT NULL,
                product_id INTEGER REFERENCES products(id) ON DELETE RESTRICT,
                market TEXT,
                reviewer TEXT NOT NULL,
                approved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshot_fact_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL
                    REFERENCES approved_claim_snapshots(id) ON DELETE RESTRICT,
                fact_version_id INTEGER NOT NULL
                    REFERENCES fact_versions(id) ON DELETE RESTRICT,
                UNIQUE(snapshot_id, fact_version_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshot_claim "
            "ON approved_claim_snapshots(approved_claim_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshot_fact_version "
            "ON snapshot_fact_links(fact_version_id)"
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_claim_snapshot_update
            BEFORE UPDATE ON approved_claim_snapshots
            BEGIN SELECT RAISE(ABORT, 'claim approval snapshots are immutable'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_claim_snapshot_delete
            BEFORE DELETE ON approved_claim_snapshots
            BEGIN SELECT RAISE(ABORT, 'claim approval snapshots are immutable'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_snapshot_fact_link_update
            BEFORE UPDATE ON snapshot_fact_links
            BEGIN SELECT RAISE(ABORT, 'snapshot fact links are immutable'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_snapshot_fact_link_delete
            BEFORE DELETE ON snapshot_fact_links
            BEGIN SELECT RAISE(ABORT, 'snapshot fact links are immutable'); END
            """
        )

        # Preserve the exact lineage of claims created before snapshot support.
        conn.execute(
            """
            INSERT INTO approved_claim_snapshots
                (approved_claim_id, stable_claim_id, approved_text,
                 approved_text_hash, product_id, market, reviewer, approved_at)
            SELECT ac.id, ac.stable_claim_id, ac.approved_text,
                   ac.approved_text_hash, d.product_id, d.market,
                   ac.approved_by, ac.approved_at
            FROM approved_claims ac
            JOIN drafts d ON d.id = ac.draft_id
            WHERE NOT EXISTS (
                SELECT 1 FROM approved_claim_snapshots s
                WHERE s.approved_claim_id = ac.id
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO snapshot_fact_links (snapshot_id, fact_version_id)
            SELECT s.id, cfl.fact_version_id
            FROM approved_claim_snapshots s
            JOIN claim_fact_links cfl ON cfl.approved_claim_id = s.approved_claim_id
            """
        )
        conn.execute(
            """
            UPDATE approved_claims
            SET current_snapshot_id = (
                SELECT MAX(s.id) FROM approved_claim_snapshots s
                WHERE s.approved_claim_id = approved_claims.id
            )
            WHERE current_snapshot_id IS NULL
              AND EXISTS (
                SELECT 1 FROM approved_claim_snapshots s
                WHERE s.approved_claim_id = approved_claims.id
              )
            """
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (1)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_2(conn, 1)


def _apply_migration_2(conn: sqlite3.Connection, current: int) -> None:
    """Add reviewer decisions linked to dismissed findings."""
    if current >= 2:
        _apply_migration_3(conn, current)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        if "finding_id" not in _column_names(conn, "review_events"):
            conn.execute(
                "ALTER TABLE review_events ADD COLUMN finding_id INTEGER "
                "REFERENCES findings(id) ON DELETE SET NULL"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finding_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                finding_id INTEGER NOT NULL REFERENCES findings(id) ON DELETE RESTRICT,
                reviewer TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('ACCEPT', 'REJECT')),
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_finding_overrides_finding "
            "ON finding_overrides(finding_id, id)"
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_finding_override_update
            BEFORE UPDATE ON finding_overrides
            BEGIN SELECT RAISE(ABORT, 'finding override decisions are append-only'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_finding_override_delete
            BEFORE DELETE ON finding_overrides
            BEGIN SELECT RAISE(ABORT, 'finding override decisions are append-only'); END
            """
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (2)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_3(conn, 2)


def _apply_migration_3(conn: sqlite3.Connection, current: int) -> None:
    """Add source-preserving brand knowledge onboarding records."""
    if current >= 3:
        _apply_migration_4(conn, current)
        return

    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS brand_profiles (
                brand TEXT PRIMARY KEY,
                industry TEXT NOT NULL DEFAULT '',
                audience TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                personality TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            );
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                document_type TEXT NOT NULL,
                purpose TEXT NOT NULL,
                product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                market TEXT,
                mime_type TEXT NOT NULL,
                original_bytes BLOB NOT NULL,
                extracted_text TEXT NOT NULL DEFAULT '',
                page_records TEXT NOT NULL DEFAULT '[]',
                extraction_status TEXT NOT NULL DEFAULT 'PROCESSED'
                    CHECK(extraction_status IN ('PROCESSED','NEEDS_ATTENTION','FAILED')),
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            );
            CREATE TABLE IF NOT EXISTS knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER REFERENCES knowledge_documents(id) ON DELETE SET NULL,
                category TEXT NOT NULL CHECK(category IN
                    ('BRAND_RULE','TONE','TERMINOLOGY','PRODUCT_FACT','EVIDENCE',
                     'RESTRICTION','ASSET')),
                content TEXT NOT NULL,
                explanation TEXT NOT NULL DEFAULT '',
                product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                market TEXT,
                channel TEXT,
                source_page TEXT,
                source_quote TEXT,
                confidence REAL,
                warnings TEXT NOT NULL DEFAULT '[]',
                potential_duplicates TEXT NOT NULL DEFAULT '[]',
                potential_conflicts TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL'
                    CHECK(status IN ('PENDING_APPROVAL','APPROVED','REJECTED',
                                     'NEEDS_CLARIFICATION','UNRESOLVED')),
                approved_by TEXT,
                approved_at TEXT,
                approved_version INTEGER,
                linked_record_type TEXT,
                linked_record_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            );
            CREATE TABLE IF NOT EXISTS knowledge_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE RESTRICT,
                decision TEXT NOT NULL,
                actor TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            );
            CREATE TABLE IF NOT EXISTS knowledge_entry_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE RESTRICT,
                version_number INTEGER NOT NULL,
                content TEXT NOT NULL,
                explanation TEXT NOT NULL,
                actor TEXT NOT NULL,
                source_quote TEXT,
                source_page TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                UNIQUE(entry_id, version_number)
            );
            CREATE TABLE IF NOT EXISTS knowledge_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE RESTRICT,
                second_entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE RESTRICT,
                conflict_type TEXT NOT NULL CHECK(conflict_type IN ('DUPLICATE','CONFLICT')),
                status TEXT NOT NULL DEFAULT 'UNRESOLVED'
                    CHECK(status IN ('UNRESOLVED','KEEP_BOTH','FIRST','SECOND','DISMISSED')),
                resolution_note TEXT NOT NULL DEFAULT '',
                resolved_by TEXT,
                resolved_at TEXT,
                UNIQUE(first_entry_id, second_entry_id, conflict_type),
                CHECK(first_entry_id < second_entry_id)
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_entries_status
                ON knowledge_entries(status, category);
            CREATE INDEX IF NOT EXISTS idx_knowledge_documents_created
                ON knowledge_documents(created_at DESC);
            """
        )
        scoped_columns = {
            "guidelines": [
                ("market_scope", "TEXT"),
                ("channel_scope", "TEXT"),
                ("content_type", "TEXT"),
            ],
            "drafts": [("content_type", "TEXT")],
            "knowledge_documents": [
                ("channel", "TEXT"),
                ("content_type", "TEXT"),
            ],
            "knowledge_entries": [("content_type", "TEXT")],
        }
        for table, columns in scoped_columns.items():
            existing = _column_names(conn, table)
            for name, definition in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        conn.execute("INSERT INTO schema_migrations(version) VALUES (3)")
        conn.commit()
    except Exception:
        raise
    _apply_migration_4(conn, 3)


def _apply_migration_4(conn: sqlite3.Connection, current: int) -> None:
    """Track AI extraction outcomes and structured visual identity notes."""
    if current >= 4:
        _apply_migration_5(conn, current)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        additions = {
            "brand_profiles": [("visual_identity", "TEXT NOT NULL DEFAULT ''")],
            "knowledge_documents": [
                (
                    "ai_extraction_status",
                    "TEXT NOT NULL DEFAULT 'NOT_REQUESTED' "
                    "CHECK(ai_extraction_status IN "
                    "('NOT_REQUESTED','RUNNING','SUCCEEDED','PARTIAL','FAILED'))",
                ),
                ("ai_extraction_error", "TEXT"),
                ("ai_extraction_at", "TEXT"),
            ],
        }
        for table, columns in additions.items():
            existing = _column_names(conn, table)
            for name, definition in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        conn.execute("INSERT INTO schema_migrations(version) VALUES (4)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_5(conn, 4)


def _apply_migration_5(conn: sqlite3.Connection, current: int) -> None:
    """Add source-backed rule readiness, audit history, and approved AI guidance metadata."""
    if current >= 5:
        _apply_migration_6(conn, current)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        additions = {
            "guidelines": [
                ("readiness_class", "TEXT"),
                ("source_document_id", "INTEGER REFERENCES knowledge_documents(id) ON DELETE SET NULL"),
                ("source_page", "TEXT"),
                ("source_quote", "TEXT"),
            ],
            "brand_rules": [
                ("readiness_class", "TEXT NOT NULL DEFAULT 'CODE_CHECKABLE'"),
                ("checking_method", "TEXT"),
                ("source_document_id", "INTEGER REFERENCES knowledge_documents(id) ON DELETE SET NULL"),
                ("source_page", "TEXT"),
                ("source_quote", "TEXT"),
            ],
            "knowledge_entries": [
                ("rule_readiness_classification", "TEXT"),
                ("rule_checking_method", "TEXT"),
                ("rule_type", "TEXT"),
                ("rule_parameters", "TEXT NOT NULL DEFAULT '{}'"),
                ("rule_scope", "TEXT NOT NULL DEFAULT '{}'"),
                ("rule_severity", "TEXT NOT NULL DEFAULT 'MEDIUM'"),
                ("rule_positive_tests", "TEXT NOT NULL DEFAULT '[]'"),
                ("rule_negative_tests", "TEXT NOT NULL DEFAULT '[]'"),
                ("clarification_note", "TEXT"),
            ],
        }
        for table, columns in additions.items():
            existing = _column_names(conn, table)
            for name, definition in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rule_readiness_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE RESTRICT,
                version_number INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                readiness_classification TEXT NOT NULL,
                checking_method TEXT NOT NULL,
                rule_type TEXT,
                rule_parameters TEXT NOT NULL DEFAULT '{}',
                rule_scope TEXT NOT NULL DEFAULT '{}',
                rule_severity TEXT NOT NULL DEFAULT 'MEDIUM',
                positive_tests TEXT NOT NULL DEFAULT '[]',
                negative_tests TEXT NOT NULL DEFAULT '[]',
                source_document_id INTEGER,
                source_page TEXT,
                source_quote TEXT,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                UNIQUE(entry_id, version_number)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS brand_rule_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL REFERENCES brand_rules(id) ON DELETE RESTRICT,
                version_number INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                rule_snapshot TEXT NOT NULL,
                source_document_id INTEGER,
                source_page TEXT,
                source_quote TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                UNIQUE(rule_id, version_number)
            )
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_rule_readiness_history_update
            BEFORE UPDATE ON rule_readiness_history
            BEGIN SELECT RAISE(ABORT, 'rule readiness history is append-only'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_rule_readiness_history_delete
            BEFORE DELETE ON rule_readiness_history
            BEGIN SELECT RAISE(ABORT, 'rule readiness history is append-only'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_brand_rule_history_update
            BEFORE UPDATE ON brand_rule_history
            BEGIN SELECT RAISE(ABORT, 'brand rule history is append-only'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_brand_rule_history_delete
            BEFORE DELETE ON brand_rule_history
            BEGIN SELECT RAISE(ABORT, 'brand rule history is append-only'); END
            """
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (5)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_6(conn, 5)


def _apply_migration_6(conn: sqlite3.Connection, current: int) -> None:
    """Store source page and character spans for review findings."""
    if current >= 6:
        _apply_migration_7(conn, current)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = _column_names(conn, "findings")
        for name in ("source_page", "start_char", "end_char"):
            if name not in existing:
                conn.execute(f"ALTER TABLE findings ADD COLUMN {name} INTEGER")
        conn.execute("INSERT INTO schema_migrations(version) VALUES (6)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_7(conn, 6)


def _apply_migration_7(conn: sqlite3.Connection, current: int) -> None:
    """Persist source verification, reviewer confirmation, and proposal-version lineage."""
    if current >= 7:
        _apply_migration_8(conn, current)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        additions = {
            "knowledge_entries": [
                ("source_quote_verified", "INTEGER NOT NULL DEFAULT 0"),
                ("source_support_confirmed", "INTEGER NOT NULL DEFAULT 0"),
                ("source_support_confirmed_by", "TEXT"),
                ("source_support_confirmed_at", "TEXT"),
            ],
            "knowledge_entry_versions": [
                ("source_document_id", "INTEGER REFERENCES knowledge_documents(id) ON DELETE SET NULL"),
                ("product_id", "INTEGER REFERENCES products(id) ON DELETE SET NULL"),
                ("market", "TEXT"),
                ("channel", "TEXT"),
                ("content_type", "TEXT"),
                ("source_quote_verified", "INTEGER NOT NULL DEFAULT 0"),
                ("source_support_confirmed", "INTEGER NOT NULL DEFAULT 0"),
                ("source_support_confirmed_by", "TEXT"),
                ("source_support_confirmed_at", "TEXT"),
            ],
            "rule_readiness_history": [
                ("source_quote_verified", "INTEGER NOT NULL DEFAULT 0"),
                ("source_support_confirmed", "INTEGER NOT NULL DEFAULT 0"),
                ("source_support_confirmed_by", "TEXT"),
                ("source_support_confirmed_at", "TEXT"),
            ],
        }
        for table, columns in additions.items():
            existing = _column_names(conn, table)
            for name, definition in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_conflict_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edited_entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE RESTRICT,
                other_entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE RESTRICT,
                first_entry_id INTEGER NOT NULL,
                second_entry_id INTEGER NOT NULL,
                conflict_type TEXT NOT NULL,
                previous_status TEXT NOT NULL,
                resolution_note TEXT NOT NULL DEFAULT '',
                resolved_by TEXT,
                resolved_at TEXT,
                edited_entry_version INTEGER NOT NULL,
                other_entry_version INTEGER NOT NULL,
                actor TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_conflict_history_entry "
            "ON knowledge_conflict_history(edited_entry_id, id)"
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_knowledge_conflict_history_update
            BEFORE UPDATE ON knowledge_conflict_history
            BEGIN SELECT RAISE(ABORT, 'knowledge conflict history is append-only'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_knowledge_conflict_history_delete
            BEFORE DELETE ON knowledge_conflict_history
            BEGIN SELECT RAISE(ABORT, 'knowledge conflict history is append-only'); END
            """
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (7)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_8(conn, 7)


def _apply_migration_8(conn: sqlite3.Connection, current: int) -> None:
    """Add and record the authentication user account foundation."""
    if current >= 8:
        _apply_migration_9(conn, current)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('Creator', 'Reviewer', 'Fact Owner')),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_active_username "
            "ON users(is_active, username)"
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (8)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_9(conn, 8)


def _apply_migration_9(conn: sqlite3.Connection, current: int) -> None:
    """Add and record server-side authentication sessions."""
    if current >= 9:
        _apply_migration_10(conn, current)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user "
            "ON sessions(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_expiry "
            "ON sessions(expires_at)"
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (9)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_10(conn, 9)


def _apply_migration_10(conn: sqlite3.Connection, current: int) -> None:
    """Associate new drafts and reviews with provisioned users."""
    if current >= 10:
        _apply_migration_11(conn, current)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        for table, column in (
            ("drafts", "submitted_by_user_id"),
            ("reviews", "reviewer_user_id"),
        ):
            if column not in _column_names(conn, table):
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_drafts_submitted_by_user "
            "ON drafts(submitted_by_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reviews_reviewer_user "
            "ON reviews(reviewer_user_id)"
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (10)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_11(conn, 10)


def _apply_migration_11(conn: sqlite3.Connection, current: int) -> None:
    """Add a tamper-evident audit event chain without changing legacy history."""
    if current >= 11:
        _apply_migration_12(conn, current)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                actor_username TEXT NOT NULL,
                action TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_id TEXT,
                details_json TEXT NOT NULL DEFAULT '{}',
                previous_hash TEXT,
                event_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_events_created "
            "ON audit_events(created_at, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_events_object "
            "ON audit_events(object_type, object_id)"
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (11)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_12(conn, 11)


def _apply_migration_12(conn: sqlite3.Connection, current: int) -> None:
    """Add brand workspaces, memberships, invitations, and scoped audit links."""
    if current >= 12:
        _apply_migration_13(conn, current)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        if "email" not in _column_names(conn, "users"):
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT COLLATE NOCASE")
        for table in ("products", "guidelines", "facts", "brand_assets", "drafts"):
            if "brand_id" not in _column_names(conn, table):
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN brand_id INTEGER "
                    "REFERENCES brands(id) ON DELETE SET NULL"
                )
        if "brand_id" not in _column_names(conn, "audit_events"):
            conn.execute(
                "ALTER TABLE audit_events ADD COLUMN brand_id INTEGER "
                "REFERENCES brands(id) ON DELETE SET NULL"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS brands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL COLLATE NOCASE UNIQUE,
                created_by_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS brand_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('Brand Owner','Creator','Reviewer','Fact Owner')),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                UNIQUE(brand_id, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS brand_invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
                email TEXT NOT NULL COLLATE NOCASE,
                role TEXT NOT NULL CHECK(role IN ('Creator','Reviewer','Fact Owner')),
                token_hash TEXT NOT NULL UNIQUE,
                invited_by_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                expires_at TEXT NOT NULL,
                accepted_at TEXT,
                accepted_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
            """
        )
        for statement in (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_brand_memberships_user ON brand_memberships(user_id, brand_id)",
            "CREATE INDEX IF NOT EXISTS idx_brand_memberships_brand ON brand_memberships(brand_id, role)",
            "CREATE INDEX IF NOT EXISTS idx_brand_invitations_lookup ON brand_invitations(brand_id, email, expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_brand_products ON products(brand_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_brand_drafts ON drafts(brand_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_brand_audit ON audit_events(brand_id, created_at, id)",
        ):
            conn.execute(statement)
        conn.execute("INSERT INTO schema_migrations(version) VALUES (12)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_13(conn, 12)


def _apply_migration_13(conn: sqlite3.Connection, current: int) -> None:
    """Complete tenant columns for rules, briefs, and knowledge records."""
    if current >= 13:
        _apply_migration_14(conn, current)
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table in ("brand_rules", "campaign_briefs", "knowledge_documents", "knowledge_entries"):
            if "brand_id" not in _column_names(conn, table):
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN brand_id INTEGER "
                    "REFERENCES brands(id) ON DELETE SET NULL"
                )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_brand ON {table}(brand_id, id)"
            )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (13)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_14(conn, 13)


def _apply_migration_14(conn: sqlite3.Connection, current: int) -> None:
    """Add verified email, OIDC identity, and single-use auth token storage."""
    if current >= 14:
        _apply_migration_15(conn, current)
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if "email_verified_at" not in _column_names(conn, "users"):
            conn.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")
        if "oidc_subject" not in _column_names(conn, "users"):
            conn.execute("ALTER TABLE users ADD COLUMN oidc_subject TEXT")
        if "registration_pending" not in _column_names(conn, "users"):
            conn.execute(
                "ALTER TABLE users ADD COLUMN registration_pending INTEGER NOT NULL DEFAULT 0 "
                "CHECK(registration_pending IN (0, 1))"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                purpose TEXT NOT NULL CHECK(purpose IN ('email_verification','password_reset')),
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
            """
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (14)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_15(conn, 14)


def _apply_migration_15(conn: sqlite3.Connection, current: int) -> None:
    """Prevent normal SQL updates and deletions of audit events."""
    if current >= 15:
        _apply_migration_16(conn, current)
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_audit_event_update
            BEFORE UPDATE ON audit_events
            BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_audit_event_delete
            BEFORE DELETE ON audit_events
            BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END
            """
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (15)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_16(conn, 15)


def _apply_migration_16(conn: sqlite3.Connection, current: int) -> None:
    """Track whether each session was authenticated by password or OIDC."""
    if current >= 16:
        _apply_migration_17(conn, current)
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if "auth_method" not in _column_names(conn, "sessions"):
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN auth_method TEXT NOT NULL "
                "DEFAULT 'PASSWORD' CHECK(auth_method IN ('PASSWORD','OIDC'))"
            )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (16)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _apply_migration_17(conn, 16)


def _apply_migration_17(conn: sqlite3.Connection, current: int) -> None:
    """Backfill and persist immutable per-draft review-event hash chains."""
    if current >= 17:
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_event_chain (
                event_id INTEGER PRIMARY KEY
                    REFERENCES review_events(id) ON DELETE RESTRICT,
                draft_id INTEGER NOT NULL
                    REFERENCES drafts(id) ON DELETE RESTRICT,
                sequence INTEGER NOT NULL CHECK(sequence > 0),
                payload_json TEXT NOT NULL,
                event_hash TEXT NOT NULL CHECK(length(event_hash) = 64),
                UNIQUE(draft_id, sequence)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_event_chain_draft_sequence "
            "ON review_event_chain(draft_id, sequence)"
        )

        from services.audit_chain_service import (
            CHAIN_FORMAT,
            GENESIS_HASH,
            build_chain_entry,
            canonicalize_json,
        )

        rows = conn.execute(
            """
            SELECT re.id, re.review_id, re.finding_id, re.event_type, re.actor,
                   re.note, re.created_at, r.draft_id
            FROM review_events re
            LEFT JOIN reviews r ON r.id = re.review_id
            ORDER BY r.draft_id, re.id
            """
        ).fetchall()
        next_sequence: dict[int, int] = {}
        previous_hash: dict[int, str] = {}
        for row in rows:
            if row["draft_id"] is None:
                continue
            draft_id = int(row["draft_id"])
            sequence = next_sequence.get(draft_id, 0) + 1
            previous = previous_hash.get(draft_id, GENESIS_HASH)
            payload = {
                "actor": row["actor"],
                "actor_role_at_event": None,
                "actor_role_provenance": "LEGACY_UNRECORDED",
                "created_at": row["created_at"],
                "draft_id": str(draft_id),
                "event_id": str(row["id"]),
                "event_type": row["event_type"],
                "finding_id": (
                    str(row["finding_id"]) if row["finding_id"] is not None else None
                ),
                "format": CHAIN_FORMAT,
                "note": row["note"],
                "previous_hash": previous,
                "review_id": str(row["review_id"]),
                "sequence": sequence,
            }
            entry = build_chain_entry(payload)
            conn.execute(
                """
                INSERT OR IGNORE INTO review_event_chain
                    (event_id, draft_id, sequence, payload_json, event_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    draft_id,
                    sequence,
                    canonicalize_json(entry["payload"]).decode("utf-8"),
                    entry["event_hash"],
                ),
            )
            next_sequence[draft_id] = sequence
            previous_hash[draft_id] = entry["event_hash"]

        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_review_event_update
            BEFORE UPDATE ON review_events
            BEGIN SELECT RAISE(ABORT, 'review events are append-only'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_review_event_delete
            BEFORE DELETE ON review_events
            BEGIN SELECT RAISE(ABORT, 'review events are append-only'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_review_event_chain_update
            BEFORE UPDATE ON review_event_chain
            BEGIN SELECT RAISE(ABORT, 'review event chain is append-only'); END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_review_event_chain_delete
            BEFORE DELETE ON review_event_chain
            BEGIN SELECT RAISE(ABORT, 'review event chain is append-only'); END
            """
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (17)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _schema_statements(sql: str) -> list[str]:
    """Return complete SQLite statements from a schema script."""
    statements: list[str] = []
    pending: list[str] = []
    for line in sql.splitlines(keepends=True):
        pending.append(line)
        candidate = "".join(pending)
        if sqlite3.complete_statement(candidate):
            statements.append(candidate)
            pending.clear()

    remainder = "".join(pending)
    if re.sub(r"/\*.*?\*/|--[^\r\n]*", "", remainder, flags=re.S).strip():
        raise sqlite3.OperationalError("Incomplete SQL statement in schema.sql")
    return statements


def apply_schema(conn: sqlite3.Connection | None = None) -> None:
    """
    Read db/schema.sql and execute it against *conn* (or a fresh connection).
    Safe to call multiple times — all CREATE statements use IF NOT EXISTS.
    """
    schema_path = Path(__file__).resolve().parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")

    close_after = conn is None
    if conn is None:
        conn = get_connection()

    try:
        # Create tables first, but defer indexes because older databases may
        # not have columns added by migrations yet (for example users.email).
        statements = _schema_statements(sql)
        base_statements: list[str] = []
        index_statements: list[str] = []
        for statement in statements:
            without_comments = re.sub(
                r"/\*.*?\*/|--[^\r\n]*", "", statement, flags=re.S
            ).lstrip()
            if re.match(r"CREATE\s+(?:UNIQUE\s+)?INDEX\b", without_comments, re.I):
                index_statements.append(statement)
            else:
                base_statements.append(statement)

        conn.commit()
        conn.executescript("".join(base_statements))
        conn.commit()
        _apply_migrations(conn)
        conn.executescript("".join(index_statements))
        conn.commit()
    finally:
        if close_after:
            conn.close()
