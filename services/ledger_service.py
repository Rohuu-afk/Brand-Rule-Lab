"""
services/ledger_service.py
--------------------------
Insight queries and audit-ledger reads.
All counts come from real stored data — no fabricated analytics.
"""

from __future__ import annotations

from db.connection import get_connection


def get_insights() -> dict:
    """
    Return real counts from stored findings and review events.

    Returns
    -------
    {
        "total_drafts": int,
        "total_findings": int,
        "findings_by_result": {result: count},
        "findings_by_severity": {severity: count},
        "top_violated_rules": [{rule_name, count}],
        "top_dismissed_rules": [{rule_name, count}],
        "top_escalated_findings": int,
        "approved_claims_by_drift": {drift_status: count},
        "total_approved_claims": int,
        "fact_expiry_flags": int,
    }
    """
    with get_connection() as conn:
        total_drafts = conn.execute("SELECT COUNT(*) AS n FROM drafts").fetchone()["n"]
        total_findings = conn.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"]

        # Findings by result
        rows = conn.execute(
            "SELECT result, COUNT(*) AS n FROM findings GROUP BY result"
        ).fetchall()
        findings_by_result = {r["result"]: r["n"] for r in rows}

        # Findings by severity
        rows = conn.execute(
            "SELECT severity, COUNT(*) AS n FROM findings GROUP BY severity"
        ).fetchall()
        findings_by_severity = {r["severity"]: r["n"] for r in rows}

        # Top violated rules (FAIL findings)
        rows = conn.execute(
            """
            SELECT br.name AS rule_name, COUNT(*) AS n
            FROM findings f
            JOIN brand_rules br ON br.id = f.rule_id
            WHERE f.result = 'FAIL'
            GROUP BY f.rule_id
            ORDER BY n DESC
            LIMIT 10
            """
        ).fetchall()
        top_violated_rules = [dict(r) for r in rows]

        # Rules most often dismissed (creator dismissed findings linked to a rule)
        rows = conn.execute(
            """
            SELECT br.name AS rule_name, COUNT(*) AS n
            FROM findings f
            JOIN brand_rules br ON br.id = f.rule_id
            WHERE f.status = 'DISMISSED'
            GROUP BY f.rule_id
            ORDER BY n DESC
            LIMIT 10
            """
        ).fetchall()
        top_dismissed_rules = [dict(r) for r in rows]

        # Escalated count
        escalated = conn.execute(
            "SELECT COUNT(*) AS n FROM findings WHERE status = 'ESCALATED'"
        ).fetchone()["n"]

        # Approved claims by drift
        rows = conn.execute(
            "SELECT drift_status, COUNT(*) AS n FROM approved_claims GROUP BY drift_status"
        ).fetchall()
        approved_by_drift = {r["drift_status"]: r["n"] for r in rows}
        total_approved = conn.execute(
            "SELECT COUNT(*) AS n FROM approved_claims"
        ).fetchone()["n"]

        # Drafts by status
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM drafts GROUP BY status"
        ).fetchall()
        drafts_by_status = {r["status"]: r["n"] for r in rows}

        # Review outcomes by status
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM reviews GROUP BY status"
        ).fetchall()
        reviews_by_status = {r["status"]: r["n"] for r in rows}

        # Fact expiry flags (approved_claims with drift_status = EXPIRED)
        expiry_flags = conn.execute(
            "SELECT COUNT(*) AS n FROM approved_claims WHERE drift_status = 'EXPIRED'"
        ).fetchone()["n"]

    return {
        "total_drafts": total_drafts,
        "total_findings": total_findings,
        "drafts_by_status": drafts_by_status,
        "reviews_by_status": reviews_by_status,
        "findings_by_result": findings_by_result,
        "findings_by_severity": findings_by_severity,
        "top_violated_rules": top_violated_rules,
        "top_dismissed_rules": top_dismissed_rules,
        "top_escalated_findings": escalated,
        "approved_claims_by_drift": approved_by_drift,
        "total_approved_claims": total_approved,
        "fact_expiry_flags": expiry_flags,
    }


def get_review_audit_trail(draft_id: int) -> list[dict]:
    """Return all review events for a draft."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT re.*, r.reviewer
            FROM review_events re
            JOIN reviews r ON r.id = re.review_id
            WHERE r.draft_id = ?
            ORDER BY re.created_at
            """,
            (draft_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_drafts() -> list[dict]:
    """Return all drafts with product name and latest status."""
    with get_connection() as conn:
        return [
            dict(r)
            for r in conn.execute(
                """
                SELECT d.*, p.name AS product_name
                FROM drafts d
                LEFT JOIN products p ON p.id = d.product_id
                ORDER BY d.updated_at DESC
                """
            ).fetchall()
        ]


def get_review_queue() -> list[dict]:
    """Return drafts that have been submitted and are waiting for a reviewer."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.title, d.status, d.market, d.channel, d.updated_at,
                   p.name AS product_name,
                   (SELECT dv.version_number
                    FROM draft_versions dv
                    WHERE dv.draft_id = d.id
                    ORDER BY dv.version_number DESC
                    LIMIT 1) AS latest_version
            FROM drafts d
            LEFT JOIN products p ON p.id = d.product_id
            WHERE d.status = 'READY_FOR_REVIEW'
            ORDER BY d.updated_at ASC, d.id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_draft_with_versions(draft_id: int) -> dict | None:
    """Return a draft and all its versions."""
    with get_connection() as conn:
        draft = conn.execute(
            "SELECT d.*, p.name AS product_name FROM drafts d "
            "LEFT JOIN products p ON p.id = d.product_id WHERE d.id = ?",
            (draft_id,),
        ).fetchone()
        if not draft:
            return None
        versions = conn.execute(
            "SELECT * FROM draft_versions WHERE draft_id = ? ORDER BY version_number",
            (draft_id,),
        ).fetchall()
        return {
            **dict(draft),
            "versions": [dict(v) for v in versions],
        }
