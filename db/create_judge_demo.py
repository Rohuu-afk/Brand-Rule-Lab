"""Create a clean synthetic judge database without touching existing data."""

from __future__ import annotations

import json
import os
import sys
import gc
import hashlib
import io
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEMO_DB = PROJECT_ROOT / "data" / "judge_demo_round2_20261106.db"


class SyntheticDemoProvider:
    """Deterministic fixture provider; its output is never a live model result."""

    provider_name = "SYNTHETIC MOCK"
    last_model_used = "synthetic-demo-mock"

    def __init__(self, claim: str, fact_version_id: int, draft_text: str) -> None:
        self.claim = claim
        self.fact_version_id = fact_version_id
        self.draft_text = draft_text

    def generate_text(self, prompt: str, **kwargs: object) -> str:
        from ai.prompts import (
            CLAIM_EXTRACTION_MARKER,
            FACT_RANKING_MARKER,
            TONE_ASSESSMENT_MARKER,
        )

        if prompt.startswith(CLAIM_EXTRACTION_MARKER):
            try:
                candidate_json = prompt.split(
                    "--- SOURCE CANDIDATES JSON START ---\n", 1
                )[1].split("\n--- SOURCE CANDIDATES JSON END ---", 1)[0]
                candidates = json.loads(candidate_json)["candidates"]
            except (IndexError, KeyError, json.JSONDecodeError, TypeError) as exc:
                raise ValueError("Synthetic candidate list could not be read.") from exc
            matches = [
                candidate for candidate in candidates
                if candidate.get("text") == self.claim
            ]
            if len(matches) != 1:
                raise ValueError("Synthetic claim is not present in the draft text.")
            return json.dumps(
                {
                    "claims": [
                        {
                            "candidate_id": matches[0]["candidate_id"],
                            "claim_type_hint": (
                                "ingredient"
                                if "ascorbic acid" in self.claim.casefold()
                                else "efficacy"
                            ),
                        }
                    ]
                }
            )
        if prompt.startswith(FACT_RANKING_MARKER):
            return json.dumps(
                {
                    "best_relationship": "SUPPORTS",
                    "matched_fact_version_id": self.fact_version_id,
                    "confidence": 1.0,
                    "explanation": "[SYNTHETIC MOCK] Matches the seeded approved demo fact.",
                    "evaluations": [
                        {
                            "fact_version_id": self.fact_version_id,
                            "relationship": "SUPPORTS",
                            "confidence": 1.0,
                            "relevance_note": "[SYNTHETIC MOCK] Demo fixture only.",
                        }
                    ],
                }
            )
        if prompt.startswith(TONE_ASSESSMENT_MARKER):
            return '{"tone_findings":[]}'
        raise ValueError("Unexpected AI operation in the synthetic demo builder.")


class OfflineDemoProvider:
    def generate_text(self, prompt: str, **kwargs: object) -> str:
        from ai.base import AIProviderError

        raise AIProviderError(
            "[SYNTHETIC DEMO] AI intentionally unavailable; deterministic checks only."
        )


def _create_reviewed_demo_claim(
    *,
    draft_title: str,
    product_id: int,
    market: str,
    text: str,
    claim: str,
    fact_version_id: int,
    brief_id: int,
) -> int:
    from db.connection import get_connection
    from services.approval_service import approve_submitted_draft
    from services.review_service import run_review

    conn = get_connection()
    try:
        draft_id = conn.execute(
            """
            INSERT INTO drafts
                (title, product_id, brief_id, market, channel, submitted_by)
            VALUES (?, ?, ?, ?, 'website', 'synthetic_demo_creator')
            """,
            (draft_title, product_id, brief_id, market),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    review = run_review(
        draft_id=draft_id,
        raw_text=text,
        source_type="paste",
        page_map={},
        product_id=product_id,
        market=market,
        channel="website",
        brief_id=brief_id,
        ai_provider=SyntheticDemoProvider(claim, fact_version_id, text),
        ai_consent=True,
    )

    conn = get_connection()
    try:
        review_id = conn.execute(
            "SELECT id FROM reviews WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if review_id is None:
            review_id = conn.execute(
                "INSERT INTO reviews (draft_id) VALUES (?)", (draft_id,)
            ).lastrowid
        else:
            review_id = review_id["id"]
        conn.execute(
            "INSERT INTO review_events (review_id, event_type, actor, note) "
            "VALUES (?, 'SUBMITTED', 'synthetic_demo_creator', ?)",
            (review_id, "[SYNTHETIC DEMO] Submitted fixture for review."),
        )
        conn.execute(
            "UPDATE drafts SET status = 'READY_FOR_REVIEW' WHERE id = ?",
            (draft_id,),
        )
        conn.commit()
    finally:
        conn.close()

    approved = approve_submitted_draft(
        draft_id=draft_id,
        draft_version_id=review["draft_version_id"],
        reviewer_note="[SYNTHETIC DEMO] Mock-reviewed fixture; not a real customer approval.",
        acknowledge_not_observed=True,
        approved_by="synthetic_demo_reviewer",
        actor_role="Reviewer",
    )
    if approved != 1:
        raise RuntimeError(f"Expected one synthetic approved claim; created {approved}.")
    return draft_id


def main() -> None:
    demo_db = Path(os.getenv("JUDGE_DEMO_DATABASE_PATH", str(DEFAULT_DEMO_DB))).resolve()
    protected_names = {
        "brand_rule_lab.db",
        "brand_rule_lab_backup.db",
        "judge_demo_2026.db",
        "judge_demo_final.db",
        "judge_demo_complete.db",
    }
    if demo_db.name.casefold() in protected_names:
        raise SystemExit(f"Refusing to use protected database filename: {demo_db.name}")
    if demo_db.exists():
        raise SystemExit(
            f"Refusing to overwrite existing judge database: {demo_db}"
        )
    uploads_path = demo_db.parent / f"{demo_db.stem}_uploads"
    if uploads_path.exists():
        raise SystemExit(
            f"Refusing to reuse an existing demo upload directory: {uploads_path}"
        )

    os.environ["DATABASE_PATH"] = str(demo_db)
    os.environ["UPLOADS_PATH"] = str(uploads_path)
    # This standalone builder creates only synthetic fixtures in a new database.
    # Service authorization recognizes this exact process, never the app server.
    os.environ["BRL_ENABLE_SYNTHETIC_DEMO_ACTORS"] = "1"
    sys.path.insert(0, str(PROJECT_ROOT))

    from db.connection import apply_schema, get_connection
    from db.seed import seed
    from services.fact_service import approve_fact_version, create_fact_version
    from services.asset_service import decide_asset, register_image_asset
    from services.review_service import run_review
    from PIL import Image

    apply_schema()
    seed(force=False)

    conn = get_connection()
    try:
        monsoon = conn.execute(
            """
            SELECT f.id AS fact_id, fv.id AS fact_version_id
            FROM facts f JOIN fact_versions fv ON fv.fact_id = f.id
            WHERE f.name = 'F-112: Clear Skin Clinical Trial Timeline'
              AND fv.approval_status = 'APPROVED'
            ORDER BY fv.version_number DESC LIMIT 1
            """
        ).fetchone()
        vitamin_c = conn.execute(
            """
            SELECT f.id AS fact_id, fv.id AS fact_version_id
            FROM facts f JOIN fact_versions fv ON fv.fact_id = f.id
            WHERE f.name = 'Vitamin C Concentration'
              AND fv.approval_status = 'APPROVED'
            ORDER BY fv.version_number DESC LIMIT 1
            """
        ).fetchone()
        monsoon_brief = conn.execute(
            "SELECT id FROM campaign_briefs WHERE product_id = 4 AND is_active = 1 "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        vitamin_brief = conn.execute(
            "SELECT id FROM campaign_briefs WHERE product_id = 1 AND is_active = 1 "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        if any(value is None for value in (monsoon, vitamin_c, monsoon_brief, vitamin_brief)):
            raise RuntimeError("Required synthetic seed facts or campaign briefs were not found.")
        monsoon = dict(monsoon)
        vitamin_c = dict(vitamin_c)
        monsoon_brief_id = monsoon_brief["id"]
        vitamin_brief_id = vitamin_brief["id"]
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    monsoon_claim = (
        "Monsoon Clear Skin Face Wash reduces blemishes and excess sebum "
        "in 4 weeks with twice-daily use."
    )
    monsoon_text = monsoon_claim
    _create_reviewed_demo_claim(
        draft_title="[SYNTHETIC DEMO] Monsoon four-week efficacy claim",
        product_id=4,
        market="IN",
        text=monsoon_text,
        claim=monsoon_claim,
        fact_version_id=monsoon["fact_version_id"],
        brief_id=monsoon_brief_id,
    )

    vitamin_claim = (
        "GlowUp Vitamin-C Serum contains 15% stabilised L-Ascorbic Acid "
        "(Vitamin C)."
    )
    vitamin_text = vitamin_claim
    _create_reviewed_demo_claim(
        draft_title="[SYNTHETIC DEMO] GlowUp ingredient specification",
        product_id=1,
        market="IN",
        text=vitamin_text,
        claim=vitamin_claim,
        fact_version_id=vitamin_c["fact_version_id"],
        brief_id=vitamin_brief_id,
    )

    image = Image.new("RGB", (48, 48), (35, 122, 91))
    image_buffer = io.BytesIO()
    image.save(image_buffer, format="PNG")
    image_bytes = image_buffer.getvalue()
    asset_id = register_image_asset(
        "[SYNTHETIC DEMO] Monsoon package mock image",
        "packaging",
        "synthetic-monsoon-packaging.png",
        image_bytes,
        product_id=4,
    )
    decide_asset(asset_id, approved=True, actor_role="Fact Owner")
    conn = get_connection()
    try:
        conn_draft_id = conn.execute(
            """
            INSERT INTO drafts (title, product_id, market, channel, submitted_by)
            VALUES ('[SYNTHETIC DEMO] Approved image identity check', 4, 'IN',
                    'website', 'synthetic_demo_creator')
            """
        ).lastrowid
        conn.commit()
    finally:
        conn.close()
    asset_draft_id = conn_draft_id
    asset_review = run_review(
        draft_id=asset_draft_id,
        raw_text="[SYNTHETIC DEMO IMAGE] Mock package artwork.",
        source_type="image",
        page_map={},
        product_id=4,
        market="IN",
        channel="website",
        brief_id=monsoon_brief_id,
        ai_provider=OfflineDemoProvider(),
        visual_metadata={
            "asset_id": asset_id,
            "submitted_hash": hashlib.sha256(image_bytes).hexdigest(),
            "alt_text": "Synthetic green Monsoon face wash package mock image",
        },
    )
    if not any(
        finding["finding_type"] == "asset_identity"
        and finding["result"] == "PASS"
        for finding in asset_review["findings"]
    ):
        raise RuntimeError("The synthetic approved image did not pass exact identity comparison.")

    flawed_text = (
        "Say goodbye to acne forever! Our 2.5% BPO wash clears breakouts in 3 days. "
        "It's the best solution for everyone."
    )
    with get_connection() as conn:
        flawed_draft_id = conn.execute(
            """
            INSERT INTO drafts (title, product_id, market, channel, submitted_by)
            VALUES ('[SYNTHETIC DEMO] Deterministic policy violations', 4, 'IN',
                    'instagram', 'synthetic_demo_creator')
            """
        ).lastrowid
        conn.commit()
    flawed_review = run_review(
        draft_id=flawed_draft_id,
        raw_text=flawed_text,
        source_type="paste",
        page_map={},
        product_id=4,
        market="IN",
        channel="instagram",
        brief_id=monsoon_brief_id,
        ai_provider=OfflineDemoProvider(),
    )
    required_rules = ("V-04", "T-12", "F-112", "Superlatives")
    with get_connection() as conn:
        flawed_rule_names = {
            row["rule_name"]
            for row in conn.execute(
                """
                SELECT br.name AS rule_name FROM findings f
                JOIN brand_rules br ON br.id = f.rule_id
                WHERE f.draft_version_id = ?
                """,
                (flawed_review["draft_version_id"],),
            )
        }
    if not all(any(token.casefold() in name.casefold() for name in flawed_rule_names) for token in required_rules):
        raise RuntimeError(f"Expected deterministic findings not present: {sorted(flawed_rule_names)}")

    new_version = create_fact_version(
        monsoon["fact_id"],
        "[DEMO] Updated clinical wording: reduce visible blemishes and excess "
        "sebum in 4 weeks of twice-daily use.",
        "Clinical Study MS-2024-F112 (synthetic updated fixture)",
        actor_role="Fact Owner",
    )
    approve_fact_version(
        new_version,
        approved_by="synthetic_demo_fact_owner",
        actor_role="Fact Owner",
    )

    conn = get_connection()
    try:
        statuses = {
            row["title"]: row["status"]
            for row in conn.execute(
                "SELECT title, status FROM drafts ORDER BY id"
            ).fetchall()
        }
        claim_states = {
            row["approved_text"]: row["drift_status"]
            for row in conn.execute(
                "SELECT approved_text, drift_status FROM approved_claims ORDER BY id"
            ).fetchall()
        }
        monsoon_lineage = conn.execute(
            """
            SELECT s.stable_claim_id, s.approved_text, s.reviewer,
                   s.approved_at, l.fact_version_id
            FROM approved_claim_snapshots s
            JOIN snapshot_fact_links l ON l.snapshot_id = s.id
            JOIN approved_claims c ON c.current_snapshot_id = s.id
            WHERE s.product_id = 4
            """
        ).fetchone()
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "products", "guidelines", "brand_rules", "facts", "fact_versions",
                "campaign_briefs", "drafts", "draft_versions", "findings",
                "brand_assets", "approved_claims", "approved_claim_snapshots",
                "snapshot_fact_links",
            )
        }
        demo_findings = [
            dict(row)
            for row in conn.execute(
                """
                SELECT br.name AS rule_name, f.result, f.status, f.explanation
                FROM findings f JOIN brand_rules br ON br.id = f.rule_id
                WHERE f.draft_version_id = ?
                ORDER BY br.name
                """,
                (flawed_review["draft_version_id"],),
            )
        ]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    if not monsoon_lineage or claim_states.get(monsoon_claim) != "REVIEW_REQUIRED":
        raise RuntimeError("Synthetic dependent-claim drift was not propagated.")
    if claim_states.get(vitamin_claim) != "CURRENT":
        raise RuntimeError("An unrelated synthetic claim was unexpectedly drifted.")
    if integrity != "ok" or foreign_key_issues:
        raise RuntimeError(f"SQLite integrity check failed: {integrity}; {foreign_key_issues}")
    if statuses.get("[SYNTHETIC DEMO] Deterministic policy violations") == "APPROVED":
        raise RuntimeError("The deliberately flawed synthetic draft must not be approved.")

    gc.collect()
    print(f"Created clean synthetic judge database: {demo_db}")
    print(f"Created synthetic judge uploads directory: {uploads_path}")
    print(f"SQLite integrity_check: {integrity}")
    print(f"Foreign key violations: {len(foreign_key_issues)}")
    print(f"Row counts: {json.dumps(counts, sort_keys=True)}")
    print(f"Draft statuses: {json.dumps(statuses, sort_keys=True)}")
    print(f"Deterministic demo findings: {json.dumps(demo_findings, sort_keys=True)}")
    print(
        "Ledger fixture: "
        f"stable_claim_id={monsoon_lineage['stable_claim_id']}, "
        f"fact_version_id={monsoon_lineage['fact_version_id']}, "
        f"state={claim_states[monsoon_claim]}"
    )
    print("Mock AI only; all approvals and facts are synthetic demo fixtures.")


if __name__ == "__main__":
    main()
