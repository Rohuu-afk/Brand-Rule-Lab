"""
services/ledger_service.py
--------------------------
Insight queries and audit-ledger reads.
All counts come from real stored data — no fabricated analytics.
"""

from __future__ import annotations

from db.connection import get_connection
from models.enums import BrandRole
from services.auth_service import AuthenticatedPrincipal, require_legacy_test_actor
from services.brand_service import require_brand_membership, require_record_brand


def get_insights(
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> dict:
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
    if principal is None:
        require_legacy_test_actor("read unscoped insights")
    with get_connection() as conn:
        scope = ""
        params: tuple = ()
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for authenticated insights.")
            require_brand_membership(principal, brand_id)
            scope = " WHERE d.brand_id = ?"
            params = (brand_id,)
        total_drafts = conn.execute(
            "SELECT COUNT(*) AS n FROM drafts d" + scope, params
        ).fetchone()["n"]
        finding_scope = " WHERE d.brand_id = ?" if principal is not None else ""
        total_findings = conn.execute(
            "SELECT COUNT(*) AS n FROM findings f JOIN draft_versions dv ON dv.id = f.draft_version_id "
            "JOIN drafts d ON d.id = dv.draft_id" + finding_scope, params
        ).fetchone()["n"]

        # Findings by result
        rows = conn.execute(
            "SELECT f.result, COUNT(*) AS n FROM findings f "
            "JOIN draft_versions dv ON dv.id = f.draft_version_id "
            "JOIN drafts d ON d.id = dv.draft_id"
            + (" WHERE d.brand_id = ?" if principal is not None else "")
            + " GROUP BY f.result",
            params,
        ).fetchall()
        findings_by_result = {r["result"]: r["n"] for r in rows}

        # Findings by severity
        rows = conn.execute(
            "SELECT f.severity, COUNT(*) AS n FROM findings f "
            "JOIN draft_versions dv ON dv.id = f.draft_version_id "
            "JOIN drafts d ON d.id = dv.draft_id"
            + (" WHERE d.brand_id = ?" if principal is not None else "")
            + " GROUP BY f.severity",
            params,
        ).fetchall()
        findings_by_severity = {r["severity"]: r["n"] for r in rows}

        # Top violated rules (FAIL findings)
        rows = conn.execute(
            """
            SELECT br.name AS rule_name, COUNT(*) AS n
            FROM findings f
            JOIN brand_rules br ON br.id = f.rule_id
            JOIN draft_versions dv ON dv.id = f.draft_version_id
            JOIN drafts d ON d.id = dv.draft_id
            WHERE f.result = 'FAIL'"""
            + (" AND d.brand_id = ?" if principal is not None else "")
            + """
            GROUP BY f.rule_id
            ORDER BY n DESC
            LIMIT 10
            """,
            params,
        ).fetchall()
        top_violated_rules = [dict(r) for r in rows]

        # Rules most often dismissed (creator dismissed findings linked to a rule)
        rows = conn.execute(
            """
            SELECT br.name AS rule_name, COUNT(*) AS n
            FROM findings f
            JOIN brand_rules br ON br.id = f.rule_id
            JOIN draft_versions dv ON dv.id = f.draft_version_id
            JOIN drafts d ON d.id = dv.draft_id
            WHERE f.status = 'DISMISSED'"""
            + (" AND d.brand_id = ?" if principal is not None else "")
            + """
            GROUP BY f.rule_id
            ORDER BY n DESC
            LIMIT 10
            """,
            params,
        ).fetchall()
        top_dismissed_rules = [dict(r) for r in rows]

        # Escalated count
        escalated = conn.execute(
            "SELECT COUNT(*) AS n FROM findings f JOIN draft_versions dv ON dv.id = f.draft_version_id "
            "JOIN drafts d ON d.id = dv.draft_id WHERE f.status = 'ESCALATED'"
            + (" AND d.brand_id = ?" if principal is not None else ""),
            params,
        ).fetchone()["n"]

        # Approved claims by drift
        claim_scope = " WHERE d.brand_id = ?" if principal is not None else ""
        rows = conn.execute(
            "SELECT ac.drift_status, COUNT(*) AS n FROM approved_claims ac "
            "JOIN drafts d ON d.id = ac.draft_id" + claim_scope +
            " GROUP BY ac.drift_status",
            params,
        ).fetchall()
        approved_by_drift = {r["drift_status"]: r["n"] for r in rows}
        total_approved = conn.execute(
            "SELECT COUNT(*) AS n FROM approved_claims ac "
            "JOIN drafts d ON d.id = ac.draft_id" + claim_scope,
            params,
        ).fetchone()["n"]

        # Drafts by status
        rows = conn.execute(
            "SELECT d.status, COUNT(*) AS n FROM drafts d"
            + (" WHERE d.brand_id = ?" if principal is not None else "")
            + " GROUP BY d.status",
            params,
        ).fetchall()
        drafts_by_status = {r["status"]: r["n"] for r in rows}

        # Review outcomes by status
        rows = conn.execute(
            "SELECT r.status, COUNT(*) AS n FROM reviews r JOIN drafts d ON d.id = r.draft_id"
            + (" WHERE d.brand_id = ?" if principal is not None else "")
            + " GROUP BY r.status",
            params,
        ).fetchall()
        reviews_by_status = {r["status"]: r["n"] for r in rows}

        # Fact expiry flags (approved_claims with drift_status = EXPIRED)
        expiry_flags = conn.execute(
            "SELECT COUNT(*) AS n FROM approved_claims ac JOIN drafts d ON d.id = ac.draft_id "
            "WHERE ac.drift_status = 'EXPIRED'"
            + (" AND d.brand_id = ?" if principal is not None else ""),
            params,
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


def get_review_audit_trail(
    draft_id: int,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return all review events for a draft."""
    if principal is None:
        require_legacy_test_actor("read unscoped review history")
    with get_connection() as conn:
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for authenticated review history.")
            require_record_brand(conn, principal, brand_id, "drafts", draft_id)
        rows = conn.execute(
            """
            SELECT re.*, r.reviewer, c.sequence AS chain_sequence,
                   c.event_hash, c.payload_json
            FROM review_events re
            JOIN reviews r ON r.id = re.review_id
            LEFT JOIN review_event_chain c ON c.event_id = re.id
            WHERE r.draft_id = ?
            ORDER BY c.sequence, re.id
            """,
            (draft_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_drafts(
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return all drafts with product name and latest status."""
    if principal is None:
        require_legacy_test_actor("read unscoped drafts")
    with get_connection() as conn:
        params = ()
        scope = ""
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for authenticated drafts.")
            require_brand_membership(principal, brand_id)
            scope = " WHERE d.brand_id = ?"
            params = (brand_id,)
        return [
            dict(r)
            for r in conn.execute(
                """
                SELECT d.*, p.name AS product_name
                FROM drafts d
                LEFT JOIN products p ON p.id = d.product_id
                """ + scope + """
                ORDER BY d.updated_at DESC
                """
            , params).fetchall()
        ]


def get_review_queue(
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return drafts that have been submitted and are waiting for a reviewer."""
    if principal is None:
        require_legacy_test_actor("read an unscoped review queue")
    with get_connection() as conn:
        params = ()
        scope = ""
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for the review queue.")
            require_brand_membership(
                principal, brand_id, (BrandRole.OWNER, BrandRole.REVIEWER)
            )
            scope = " AND d.brand_id = ?"
            params = (brand_id,)
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
            WHERE d.status = 'READY_FOR_REVIEW'""" + scope + """
            ORDER BY d.updated_at ASC, d.id ASC
            """
        , params).fetchall()
        return [dict(row) for row in rows]


def get_draft_with_versions(
    draft_id: int,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> dict | None:
    """Return a draft and all its versions."""
    if principal is None:
        require_legacy_test_actor("read an unscoped draft")
    with get_connection() as conn:
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for this draft.")
            require_record_brand(conn, principal, brand_id, "drafts", draft_id)
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


def get_review_events(
    principal: AuthenticatedPrincipal,
    brand_id: int,
    draft_id: int | None = None,
) -> list[dict]:
    """Return review events for one selected brand, optionally for one draft."""
    with get_connection() as conn:
        require_brand_membership(principal, brand_id)
        where = ["d.brand_id = ?"]
        params: list = [brand_id]
        if draft_id is not None:
            require_record_brand(conn, principal, brand_id, "drafts", draft_id)
            where.append("d.id = ?")
            params.append(draft_id)
        rows = conn.execute(
            """
            SELECT re.*, r.reviewer, d.id AS draft_id, d.title AS draft_title,
                   p.name AS product_name
            FROM review_events re
            JOIN reviews r ON r.id = re.review_id
            JOIN drafts d ON d.id = r.draft_id
            LEFT JOIN products p ON p.id = d.product_id
            WHERE """ + " AND ".join(where) + " ORDER BY re.created_at DESC, re.id DESC",
            params,
        ).fetchall()
        return [dict(row) for row in rows]
