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
        # SQLite doesn't support executescript inside a transaction; commit first
        conn.commit()
        conn.executescript(sql)
        conn.commit()
        _apply_migrations(conn)
    finally:
        if close_after:
            conn.close()
