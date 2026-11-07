-- =============================================================
-- db/schema.sql
-- Brand Rule Lab — SQLite schema
-- =============================================================
-- Design rules
--   • Integer primary keys throughout (rowid aliases).
--   • Foreign keys explicitly declared; run "PRAGMA foreign_keys = ON" at
--     connection time (enforced in connection.py).
--   • Status columns are TEXT with CHECK constraints matching enums.py.
--   • Timestamps stored as ISO-8601 TEXT (UTC).
--   • Fact versions are IMMUTABLE — never UPDATE a fact_versions row.
-- =============================================================

PRAGMA journal_mode = WAL;

-- ---------------------------------------------------------------
-- products
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    brand       TEXT    NOT NULL,
    sku_aliases TEXT    NOT NULL DEFAULT '[]',   -- JSON array of alias strings
    markets     TEXT    NOT NULL DEFAULT '[]',   -- JSON array e.g. ["IN","US"]
    description TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- guidelines
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS guidelines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER REFERENCES products(id) ON DELETE SET NULL,
    title       TEXT    NOT NULL,
    content     TEXT    NOT NULL,               -- full guideline text
    source_ref  TEXT,                           -- e.g. "Brand Bible v3 p.12"
    version     TEXT    NOT NULL DEFAULT '1.0',
    is_active   INTEGER NOT NULL DEFAULT 1,     -- 1 = active
    readiness_class TEXT,
    source_document_id INTEGER REFERENCES knowledge_documents(id) ON DELETE SET NULL,
    source_page TEXT,
    source_quote TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- brand_rules  (deterministic predicates)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS brand_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guideline_id    INTEGER REFERENCES guidelines(id) ON DELETE SET NULL,
    product_id      INTEGER REFERENCES products(id)   ON DELETE SET NULL,
    name            TEXT    NOT NULL,
    description     TEXT,
    rule_type       TEXT    NOT NULL CHECK(rule_type IN (
                        'term_list','regex','numeric_range',
                        'date_window','required_text','wcag_contrast','asset_hash'
                    )),
    scope           TEXT    NOT NULL DEFAULT '{}',   -- JSON: {channel, market, ...}
    parameters      TEXT    NOT NULL DEFAULT '{}',   -- JSON predicate parameters
    severity        TEXT    NOT NULL DEFAULT 'MEDIUM' CHECK(severity IN
                        ('LOW','MEDIUM','HIGH','CRITICAL')),
    positive_tests  TEXT    NOT NULL DEFAULT '[]',   -- JSON array of strings that should PASS
    negative_tests  TEXT    NOT NULL DEFAULT '[]',   -- JSON array of strings that should FAIL
    is_active       INTEGER NOT NULL DEFAULT 0,      -- 0 until owner approves
    owner_approved  INTEGER NOT NULL DEFAULT 0,
    readiness_class TEXT    NOT NULL DEFAULT 'CODE_CHECKABLE'
                    CHECK(readiness_class IN ('CODE_CHECKABLE','AI_ASSISTED','HUMAN_JUDGEMENT','UNCLEAR')),
    checking_method TEXT,
    source_document_id INTEGER REFERENCES knowledge_documents(id) ON DELETE SET NULL,
    source_page     TEXT,
    source_quote    TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- brand_assets
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS brand_assets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER REFERENCES products(id) ON DELETE SET NULL,
    name        TEXT    NOT NULL,
    asset_type  TEXT    NOT NULL,               -- e.g. 'logo','color_palette'
    file_path   TEXT,                           -- relative path under data/
    perceptual_hash TEXT,                       -- imagehash value as hex string
    sha256_hash TEXT,                           -- exact file hash
    metadata    TEXT    NOT NULL DEFAULT '{}',  -- JSON for colour values, etc.
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- facts  (parent records; identity stays stable across versions)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    name        TEXT    NOT NULL,
    category    TEXT    NOT NULL,              -- e.g. 'ingredient','claim','stat'
    markets     TEXT    NOT NULL DEFAULT '[]',
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- fact_versions  (IMMUTABLE — never UPDATE)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id         INTEGER NOT NULL REFERENCES facts(id) ON DELETE RESTRICT,
    version_number  INTEGER NOT NULL DEFAULT 1,
    statement       TEXT    NOT NULL,          -- the approved fact text
    evidence_ref    TEXT,                      -- e.g. "Clinical Study BRL-2024-01"
    valid_from      TEXT,                      -- ISO-8601 date or NULL
    valid_until     TEXT,                      -- ISO-8601 date or NULL
    approval_status TEXT    NOT NULL DEFAULT 'PENDING_APPROVAL'
                    CHECK(approval_status IN (
                        'PENDING_APPROVAL','APPROVED','REJECTED','EXPIRED'
                    )),
    approved_by     TEXT,                      -- demo username string
    approved_at     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(fact_id, version_number)
);

-- ---------------------------------------------------------------
-- campaign_briefs
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campaign_briefs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER REFERENCES products(id) ON DELETE SET NULL,
    name            TEXT    NOT NULL,
    objectives      TEXT,
    tone_guidance   TEXT,
    target_audience TEXT,
    key_messages    TEXT,                      -- JSON array of approved messages
    markets         TEXT    NOT NULL DEFAULT '[]',
    channels        TEXT    NOT NULL DEFAULT '[]',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- drafts  (parent; each submission is a new draft_version)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER REFERENCES products(id) ON DELETE SET NULL,
    brief_id    INTEGER REFERENCES campaign_briefs(id) ON DELETE SET NULL,
    title       TEXT    NOT NULL,
    channel     TEXT,
    market      TEXT,
    submitted_by TEXT   NOT NULL DEFAULT 'demo_creator',
    status      TEXT    NOT NULL DEFAULT 'DRAFT'
                CHECK(status IN (
                    'DRAFT','CHECKED','READY_FOR_REVIEW',
                    'CHANGES_REQUESTED','APPROVED','REJECTED'
                )),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- draft_versions  (immutable snapshots of submitted text)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS draft_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id        INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL DEFAULT 1,
    raw_text        TEXT    NOT NULL,
    source_type     TEXT    NOT NULL DEFAULT 'paste',  -- 'paste','pdf','docx','txt','image'
    page_map        TEXT    NOT NULL DEFAULT '{}',     -- JSON: char_offset -> page
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(draft_id, version_number)
);

-- ---------------------------------------------------------------
-- claim_mentions  (AI-extracted claim spans from a draft version)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS claim_mentions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_version_id    INTEGER NOT NULL REFERENCES draft_versions(id) ON DELETE CASCADE,
    claim_text          TEXT    NOT NULL,
    start_char          INTEGER NOT NULL,
    end_char            INTEGER NOT NULL,
    claim_type_hint     TEXT,                  -- AI hint only; not used for filtering
    page                INTEGER,
    paragraph           INTEGER,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- findings  (one row per rule/claim check)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS findings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_version_id    INTEGER NOT NULL REFERENCES draft_versions(id) ON DELETE CASCADE,
    claim_mention_id    INTEGER REFERENCES claim_mentions(id) ON DELETE SET NULL,
    rule_id             INTEGER REFERENCES brand_rules(id) ON DELETE SET NULL,
    fact_version_id     INTEGER REFERENCES fact_versions(id) ON DELETE SET NULL,
    finding_type        TEXT    NOT NULL,      -- 'rule_check','fact_match','tone'
    result              TEXT    NOT NULL DEFAULT 'OPEN'
                        CHECK(result IN (
                            'PASS','FAIL','WARNING','UNLINKED',
                            'REVIEW_REQUIRED','NOT_OBSERVED','ERROR'
                        )),
    status              TEXT    NOT NULL DEFAULT 'OPEN'
                        CHECK(status IN (
                            'OPEN','ACCEPTED','EDITED','DISMISSED','ESCALATED'
                        )),
    severity            TEXT    NOT NULL DEFAULT 'MEDIUM'
                        CHECK(severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    evidence_relationship TEXT,                -- 'SUPPORTS','CONTRADICTS','RELATED_NOT_EVIDENCE','NO_MATCH'
    quoted_text         TEXT,
    source_page         INTEGER,
    start_char          INTEGER,
    end_char            INTEGER,
    explanation         TEXT,
    suggested_correction TEXT,
    creator_note        TEXT,                  -- reason for DISMISSED or EDITED text
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- reviews
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id        INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    reviewer        TEXT    NOT NULL DEFAULT 'demo_reviewer',
    status          TEXT    NOT NULL DEFAULT 'OPEN'
                    CHECK(status IN (
                        'OPEN','APPROVED','CHANGES_REQUESTED','ESCALATED'
                    )),
    reviewer_note   TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- review_events  (audit trail; append-only)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS review_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id   INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    finding_id  INTEGER REFERENCES findings(id) ON DELETE SET NULL,
    event_type  TEXT    NOT NULL CHECK(event_type IN (
                    'SUBMITTED','APPROVED','CHANGES_REQUESTED',
                    'ESCALATED','OVERRIDE','COMMENT'
                )),
    actor       TEXT    NOT NULL,
    note        TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS finding_overrides (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id  INTEGER NOT NULL REFERENCES findings(id) ON DELETE RESTRICT,
    reviewer    TEXT    NOT NULL,
    decision    TEXT    NOT NULL CHECK(decision IN ('ACCEPT','REJECT')),
    reason      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- approved_claims
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approved_claims (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_claim_id     TEXT    NOT NULL UNIQUE,   -- 12-char SHA-256 hex prefix
    draft_id            INTEGER NOT NULL REFERENCES drafts(id) ON DELETE RESTRICT,
    claim_mention_id    INTEGER REFERENCES claim_mentions(id) ON DELETE SET NULL,
    approved_text       TEXT    NOT NULL,
    approved_text_hash  TEXT    NOT NULL,          -- SHA-256 of normalised text
    drift_status        TEXT    NOT NULL DEFAULT 'CURRENT'
                        CHECK(drift_status IN (
                            'CURRENT','REVIEW_REQUIRED','EXPIRED'
                        )),
    approved_by         TEXT    NOT NULL DEFAULT 'demo_reviewer',
    approved_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------
-- claim_fact_links  (many-to-many: approved_claims <-> fact_versions)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS claim_fact_links (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    approved_claim_id   INTEGER NOT NULL REFERENCES approved_claims(id) ON DELETE CASCADE,
    fact_version_id     INTEGER NOT NULL REFERENCES fact_versions(id) ON DELETE RESTRICT,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(approved_claim_id, fact_version_id)
);

-- ---------------------------------------------------------------
-- Indexes for common query patterns
-- ---------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_findings_draft_version  ON findings(draft_version_id);
CREATE INDEX IF NOT EXISTS idx_findings_status         ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_result         ON findings(result);
CREATE INDEX IF NOT EXISTS idx_fact_versions_fact      ON fact_versions(fact_id);
CREATE INDEX IF NOT EXISTS idx_fact_versions_status    ON fact_versions(approval_status);
CREATE INDEX IF NOT EXISTS idx_claim_mentions_draft    ON claim_mentions(draft_version_id);
CREATE INDEX IF NOT EXISTS idx_approved_claims_drift   ON approved_claims(drift_status);
CREATE INDEX IF NOT EXISTS idx_claim_fact_links_claim  ON claim_fact_links(approved_claim_id);
CREATE INDEX IF NOT EXISTS idx_claim_fact_links_fv     ON claim_fact_links(fact_version_id);
CREATE INDEX IF NOT EXISTS idx_finding_overrides_finding ON finding_overrides(finding_id, id);
