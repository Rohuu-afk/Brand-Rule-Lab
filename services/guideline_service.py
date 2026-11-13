"""
services/guideline_service.py
------------------------------
Database operations for guidelines and brand assets.
"""

from __future__ import annotations

import json
import re

from db.connection import get_connection
from engines.rule_engine import run_self_tests
from models.enums import BrandRole, RuleType, Severity, UserRole
from services.auth_service import (
    AuthenticatedPrincipal,
    require_legacy_test_actor,
    require_principal,
)
from services.audit_service import append_audit_event
from services.brand_service import require_brand_membership, require_record_brand


def _require_reviewer(actor_role: UserRole | str | None, action: str) -> None:
    require_legacy_test_actor(f"{action} rule proposals")
    role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
    if role_value.strip().casefold() != UserRole.REVIEWER.value.casefold():
        raise PermissionError(f"Reviewer demo role required to {action} rule proposals.")


_WORKSPACE_EDITORS = (BrandRole.OWNER, BrandRole.REVIEWER, BrandRole.FACT_OWNER)


def _authorize_workspace(
    principal: AuthenticatedPrincipal | None, brand_id: int | None,
    action: str, *, roles: tuple[BrandRole, ...] = _WORKSPACE_EDITORS,
) -> None:
    if principal is None:
        require_legacy_test_actor(f"{action} without a workspace session")
        return
    if brand_id is None:
        raise ValueError(f"A brand is required to {action}.")
    require_brand_membership(principal, brand_id, roles)


def _require_related_record(
    conn, principal: AuthenticatedPrincipal | None, brand_id: int | None,
    table: str, record_id: int | None,
) -> None:
    if principal is not None and record_id is not None:
        require_record_brand(
            conn, principal, brand_id, table, record_id, roles=_WORKSPACE_EDITORS
        )


def _audit_workspace_change(
    conn, principal: AuthenticatedPrincipal | None, *, action: str,
    object_type: str, object_id: int | None, brand_id: int | None,
) -> None:
    if principal is not None:
        append_audit_event(
            conn, principal, action=action, object_type=object_type,
            object_id=object_id, brand_id=brand_id,
        )


def get_guidelines_summary(
    product_id: int | None = None,
    market: str | None = None,
    channel: str | None = None,
    content_type: str | None = None,
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> str:
    """
    Return a concatenated text summary of active guidelines for the product.
    Used to build AI prompts; never sent as instructions.
    """
    _authorize_workspace(principal, brand_id, "read guideline context", roles=())
    query = (
        "SELECT title, content, source_ref FROM guidelines "
        "WHERE is_active = 1 AND (readiness_class IS NULL OR readiness_class = 'AI_ASSISTED')"
    )
    params: list = []
    if principal is not None:
        if brand_id is None:
            raise ValueError("A brand is required for authenticated guideline context.")
        require_brand_membership(principal, brand_id)
        query = query.replace("SELECT title, content, source_ref FROM guidelines", "SELECT title, content, source_ref FROM guidelines g")
        query += " AND g.brand_id = ?"
        params.append(brand_id)
    if product_id is None:
        query += " AND product_id IS NULL"
    else:
        query += " AND (product_id = ? OR product_id IS NULL)"
        params.append(product_id)
    for column, value in (
        ("market_scope", market),
        ("channel_scope", channel),
        ("content_type", content_type),
    ):
        query += f" AND ({column} IS NULL OR trim({column}) = '' OR lower({column}) = 'all' OR lower({column}) = lower(?))"
        params.append(value or "")

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    parts = []
    for row in rows:
        parts.append(f"[{row['source_ref'] or 'Guideline'}] {row['title']}:\n{row['content']}")

    return "\n\n".join(parts)


def get_all_guidelines(
    product_id: int | None = None,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return all guidelines, optionally filtered by product."""
    _authorize_workspace(principal, brand_id, "read guidelines", roles=())
    query = "SELECT g.*, p.name AS product_name FROM guidelines g LEFT JOIN products p ON p.id = g.product_id"
    params: list = []
    if principal is not None:
        if brand_id is None:
            raise ValueError("A brand is required for authenticated guideline lookup.")
        require_brand_membership(principal, brand_id)
        query += " WHERE g.brand_id = ?"
        params.append(brand_id)
    if product_id is not None:
        query += " AND " if " WHERE " in query else " WHERE "
        query += "(g.product_id = ? OR g.product_id IS NULL)"
        params.append(product_id)
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_all_assets(
    product_id: int | None = None,
    include_inactive: bool = False,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return approved active assets by default, or all assets for governance."""
    _authorize_workspace(principal, brand_id, "read brand assets", roles=())
    query = "SELECT ba.*, p.name AS product_name FROM brand_assets ba LEFT JOIN products p ON p.id = ba.product_id"
    params: list = []
    where: list[str] = []
    if principal is not None:
        if brand_id is None:
            raise ValueError("A brand is required for authenticated asset lookup.")
        require_brand_membership(principal, brand_id)
        where.append("ba.brand_id = ?")
        params.append(brand_id)
    if not include_inactive:
        where.append("ba.is_active = 1 AND ba.owner_approved = 1")
    if product_id is not None:
        where.append("ba.product_id = ?")
        params.append(product_id)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY ba.created_at DESC, ba.id DESC"
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_all_products(
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return all products."""
    _authorize_workspace(principal, brand_id, "read products", roles=())
    with get_connection() as conn:
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for authenticated product lookup.")
            require_brand_membership(principal, brand_id)
            rows = conn.execute(
                "SELECT * FROM products WHERE brand_id = ? ORDER BY name", (brand_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM products ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def create_product(
    name: str,
    *,
    sku_aliases: list[str] | None = None,
    markets: list[str] | None = None,
    description: str | None = None,
    principal: AuthenticatedPrincipal,
    brand_id: int,
) -> int:
    """Create a product inside a workspace; only its Brand Owner may do so."""
    actor = require_principal(principal, action="create products")
    clean_name = str(name or "").strip()
    if not clean_name or len(clean_name) > 200:
        raise ValueError("Product name is required and must be 200 characters or fewer.")
    clean_aliases = list(dict.fromkeys(
        str(alias).strip() for alias in (sku_aliases or []) if str(alias).strip()
    ))
    if any(len(alias) > 100 for alias in clean_aliases):
        raise ValueError("Each product SKU or alias must be 100 characters or fewer.")
    clean_markets = list(dict.fromkeys(
        str(market).strip().upper() for market in (markets or []) if str(market).strip()
    ))
    if any(not re.fullmatch(r"[A-Z]{2}", market) for market in clean_markets):
        raise ValueError("Markets must use two-letter country codes, such as IN or US.")
    clean_description = str(description or "").strip()
    if len(clean_description) > 2000:
        raise ValueError("Product description must be 2,000 characters or fewer.")

    with get_connection() as conn:
        membership = require_brand_membership(
            actor, brand_id, (BrandRole.OWNER,)
        )
        duplicate = conn.execute(
            "SELECT 1 FROM products WHERE brand_id = ? AND name = ? COLLATE NOCASE",
            (brand_id, clean_name),
        ).fetchone()
        if duplicate:
            raise ValueError("A product with this name already exists in the workspace.")
        cursor = conn.execute(
            "INSERT INTO products (name, brand, sku_aliases, markets, description, brand_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                clean_name, membership["name"], json.dumps(clean_aliases),
                json.dumps(clean_markets), clean_description or None, brand_id,
            ),
        )
        append_audit_event(
            conn, actor, action="PRODUCT_CREATED", object_type="product",
            object_id=cursor.lastrowid, brand_id=brand_id,
            details={"name": clean_name, "markets": clean_markets},
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_all_briefs(
    product_id: int | None = None,
    include_inactive: bool = False,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return campaign briefs."""
    _authorize_workspace(principal, brand_id, "read campaign briefs", roles=())
    query = "SELECT cb.*, p.name AS product_name FROM campaign_briefs cb LEFT JOIN products p ON p.id = cb.product_id"
    params: list = []
    where: list[str] = []
    if principal is not None:
        if brand_id is None:
            raise ValueError("A brand is required for authenticated brief lookup.")
        require_brand_membership(principal, brand_id)
        where.append("cb.brand_id = ?")
        params.append(brand_id)
    if not include_inactive:
        where.append("cb.is_active = 1")
    if product_id is not None:
        where.append("cb.product_id = ?")
        params.append(product_id)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY cb.created_at DESC, cb.id DESC"
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_all_rules(
    product_id: int | None = None,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    """Return brand rules joined with guideline title."""
    _authorize_workspace(principal, brand_id, "read rules", roles=())
    query = (
        "SELECT br.*, g.title AS guideline_title, p.name AS product_name, "
        "d.filename AS source_filename "
        "FROM brand_rules br "
        "LEFT JOIN guidelines g ON g.id = br.guideline_id "
        "LEFT JOIN knowledge_documents d ON d.id = br.source_document_id "
        "LEFT JOIN products p ON p.id = br.product_id"
    )
    params: list = []
    if principal is not None:
        if brand_id is None:
            raise ValueError("A brand is required for authenticated rule lookup.")
        require_brand_membership(principal, brand_id)
        query += " WHERE br.brand_id = ?"
        params.append(brand_id)
    if product_id is not None:
        query += " AND " if " WHERE " in query else " WHERE "
        query += "(br.product_id = ? OR br.product_id IS NULL)"
        params.append(product_id)
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def create_guideline(
    title: str,
    content: str,
    source_ref: str,
    version: str = "1.0",
    product_id: int | None = None,
    source_type: str = "manual",
    section_ref: str | None = None,
    market_scope: str | None = None,
    channel_scope: str | None = None,
    content_type: str | None = None,
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> int:
    if not title.strip() or not content.strip():
        raise ValueError("Guideline title and content are required.")
    _authorize_workspace(principal, brand_id, "create guidelines")
    with get_connection() as conn:
        _require_related_record(conn, principal, brand_id, "products", product_id)
        cursor = conn.execute(
            """
            INSERT INTO guidelines
                (product_id, title, content, source_ref, version, is_active, source_type,
                 section_ref, market_scope, channel_scope, content_type, brand_id)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id, title.strip(), content.strip(), source_ref.strip() or None,
                version.strip() or "1.0", source_type,
                section_ref.strip() or None if section_ref else None,
                market_scope.strip() or None if market_scope else None,
                channel_scope.strip() or None if channel_scope else None,
                content_type.strip() or None if content_type else None,
                brand_id if principal is not None else None,
            ),
        )
        _audit_workspace_change(
            conn, principal, action="GUIDELINE_CREATED", object_type="guideline",
            object_id=cursor.lastrowid, brand_id=brand_id,
        )
        conn.commit()
        return cursor.lastrowid


def update_guideline(
    guideline_id: int,
    title: str,
    content: str,
    source_ref: str,
    version: str,
    product_id: int | None,
    source_type: str,
    section_ref: str | None,
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> None:
    if not title.strip() or not content.strip():
        raise ValueError("Guideline title and content are required.")
    with get_connection() as conn:
        _authorize_workspace(principal, brand_id, "edit guidelines")
        if principal is not None:
            require_record_brand(
                conn, principal, brand_id, "guidelines", guideline_id,
                roles=_WORKSPACE_EDITORS,
            )
        _require_related_record(conn, principal, brand_id, "products", product_id)
        cursor = conn.execute(
            """
            UPDATE guidelines
            SET title = ?, content = ?, source_ref = ?, version = ?,
                product_id = ?, source_type = ?, section_ref = ?
            WHERE id = ?""" + (" AND brand_id = ?" if principal is not None else ""),
            (
                title.strip(), content.strip(), source_ref.strip() or None,
                version.strip() or "1.0", product_id, source_type,
                section_ref.strip() if section_ref else None, guideline_id,
                *([brand_id] if principal is not None else []),
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Guideline {guideline_id} does not exist.")
        _audit_workspace_change(
            conn, principal, action="GUIDELINE_UPDATED", object_type="guideline",
            object_id=guideline_id, brand_id=brand_id,
        )
        conn.commit()


def set_guideline_active(
    guideline_id: int, active: bool, *, principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    with get_connection() as conn:
        _authorize_workspace(principal, brand_id, "change guideline status")
        if principal is not None:
            require_record_brand(
                conn, principal, brand_id, "guidelines", guideline_id,
                roles=_WORKSPACE_EDITORS,
            )
        cursor = conn.execute(
            "UPDATE guidelines SET is_active = ? WHERE id = ?" +
            (" AND brand_id = ?" if principal is not None else ""),
            (int(active), guideline_id, *([brand_id] if principal is not None else [])),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Guideline {guideline_id} does not exist.")
        _audit_workspace_change(
            conn, principal, action="GUIDELINE_STATUS_CHANGED", object_type="guideline",
            object_id=guideline_id, brand_id=brand_id,
        )
        conn.commit()


def create_rule_proposal(
    name: str,
    description: str,
    rule_type: str,
    parameters_text: str,
    positive_tests: list[str],
    negative_tests: list[str],
    product_id: int | None = None,
    guideline_id: int | None = None,
    scope_text: str = "{}",
    severity: str = "MEDIUM",
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> int:
    _authorize_workspace(principal, brand_id, "create rule proposals")
    try:
        kind = RuleType(rule_type).value
        severity = Severity(severity).value
        parameters = json.loads(parameters_text)
        scope = json.loads(scope_text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Rule type or JSON specification is invalid: {exc}") from exc
    if not isinstance(parameters, dict) or not isinstance(scope, dict):
        raise ValueError("Rule parameters and scope must be JSON objects.")
    if not name.strip() or not positive_tests or not negative_tests:
        raise ValueError("A rule needs a name and at least one positive and negative test.")

    tests = [item.strip() for item in positive_tests + negative_tests]
    if any(not item for item in tests):
        raise ValueError("Rule test cases cannot be empty.")
    proposed = {
        "name": name.strip(),
        "description": description.strip(),
        "rule_type": kind,
        "parameters": json.dumps(parameters),
        "scope": json.dumps(scope),
        "severity": severity,
        "positive_tests": json.dumps([item.strip() for item in positive_tests]),
        "negative_tests": json.dumps([item.strip() for item in negative_tests]),
        "is_active": 1,
        "owner_approved": 1,
    }
    results = run_self_tests(proposed)
    if not results or not all(item["passed"] for item in results):
        raise ValueError("Rule tests failed. Update the proposal until all positive and negative tests pass.")

    with get_connection() as conn:
        _require_related_record(conn, principal, brand_id, "products", product_id)
        _require_related_record(conn, principal, brand_id, "guidelines", guideline_id)
        cursor = conn.execute(
            """
            INSERT INTO brand_rules
                (guideline_id, product_id, name, description, rule_type, scope,
                 parameters, severity, positive_tests, negative_tests,
                 is_active, owner_approved, review_status, brand_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'PROPOSED', ?)
            """,
            (
                guideline_id, product_id, proposed["name"], proposed["description"],
                kind, proposed["scope"], proposed["parameters"], severity,
                proposed["positive_tests"], proposed["negative_tests"],
                brand_id if principal is not None else None,
            ),
        )
        _audit_workspace_change(
            conn, principal, action="RULE_PROPOSAL_CREATED", object_type="brand_rule",
            object_id=cursor.lastrowid, brand_id=brand_id,
        )
        conn.commit()
        return cursor.lastrowid


def update_rule_proposal(
    rule_id: int,
    name: str,
    description: str,
    rule_type: str,
    parameters_text: str,
    positive_tests: list[str],
    negative_tests: list[str],
    product_id: int | None = None,
    guideline_id: int | None = None,
    scope_text: str = "{}",
    severity: str = "MEDIUM",
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    if principal is None:
        _require_reviewer(actor_role, "edit")
    try:
        kind = RuleType(rule_type).value
        severity = Severity(severity).value
        parameters = json.loads(parameters_text)
        scope = json.loads(scope_text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Rule type or JSON specification is invalid: {exc}") from exc
    if not isinstance(parameters, dict) or not isinstance(scope, dict):
        raise ValueError("Rule parameters and scope must be JSON objects.")
    if not name.strip() or not positive_tests or not negative_tests:
        raise ValueError("A rule needs a name and at least one positive and negative test.")
    positive = [item.strip() for item in positive_tests]
    negative = [item.strip() for item in negative_tests]
    if any(not item for item in positive + negative):
        raise ValueError("Rule test cases cannot be empty.")
    candidate = {
        "name": name.strip(),
        "description": description.strip(),
        "rule_type": kind,
        "parameters": json.dumps(parameters),
        "scope": json.dumps(scope),
        "severity": severity,
        "positive_tests": json.dumps(positive),
        "negative_tests": json.dumps(negative),
        "is_active": 1,
        "owner_approved": 1,
    }
    results = run_self_tests(candidate)
    if not results or not all(item["passed"] for item in results):
        raise ValueError("Rule tests failed. Update the proposal until all positive and negative tests pass.")
    with get_connection() as conn:
        _authorize_workspace(principal, brand_id, "edit rule proposals", roles=(
            BrandRole.OWNER, BrandRole.REVIEWER,
        ))
        if principal is not None:
            require_record_brand(
                conn, principal, brand_id, "brand_rules", rule_id,
                roles=(BrandRole.OWNER, BrandRole.REVIEWER),
            )
        _require_related_record(conn, principal, brand_id, "products", product_id)
        _require_related_record(conn, principal, brand_id, "guidelines", guideline_id)
        cursor = conn.execute(
            """
            UPDATE brand_rules SET guideline_id = ?, product_id = ?, name = ?,
                description = ?, rule_type = ?, scope = ?, parameters = ?,
                severity = ?, positive_tests = ?, negative_tests = ?
            WHERE id = ? AND review_status = 'PROPOSED' AND is_active = 0""" +
            (" AND brand_id = ?" if principal is not None else ""),
            (
                guideline_id, product_id, candidate["name"], candidate["description"],
                kind, candidate["scope"], candidate["parameters"], severity,
                candidate["positive_tests"], candidate["negative_tests"], rule_id,
                *([brand_id] if principal is not None else []),
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("Only an inactive proposed rule can be edited.")
        _audit_workspace_change(
            conn, principal, action="RULE_PROPOSAL_UPDATED", object_type="brand_rule",
            object_id=rule_id, brand_id=brand_id,
        )
        conn.commit()


def get_rule_test_results(
    rule_id: int, *, principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    _authorize_workspace(
        principal, brand_id, "read rule tests", roles=(
            BrandRole.OWNER, BrandRole.REVIEWER, BrandRole.FACT_OWNER,
            BrandRole.CREATOR,
        ),
    )
    with get_connection() as conn:
        if principal is not None:
            row = conn.execute(
                "SELECT * FROM brand_rules WHERE id = ? AND brand_id = ?",
                (rule_id, brand_id),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM brand_rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise ValueError(f"Rule {rule_id} does not exist.")
    rule = dict(row)
    rule["is_active"] = 1
    rule["owner_approved"] = 1
    return run_self_tests(rule)


def approve_rule(
    rule_id: int,
    actor: str = "demo_reviewer",
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    if principal is None:
        _require_reviewer(actor_role, "approve")
    else:
        actor = require_principal(principal, action="approve rules")
        actor = actor.username
    results = get_rule_test_results(rule_id, principal=principal, brand_id=brand_id)
    if not results or not all(item["passed"] for item in results):
        raise ValueError("Rule cannot be activated until all positive and negative tests pass.")
    with get_connection() as conn:
        _authorize_workspace(principal, brand_id, "approve rule proposals", roles=(
            BrandRole.OWNER, BrandRole.REVIEWER,
        ))
        if principal is not None:
            require_record_brand(
                conn, principal, brand_id, "brand_rules", rule_id,
                roles=(BrandRole.OWNER, BrandRole.REVIEWER),
            )
        rule = conn.execute(
            """
            SELECT br.*, g.content AS guideline_content, d.page_records
            FROM brand_rules br
            LEFT JOIN guidelines g ON g.id = br.guideline_id
            LEFT JOIN knowledge_documents d ON d.id = br.source_document_id
            WHERE br.id = ?""" + (" AND br.brand_id = ?" if principal is not None else ""),
            (rule_id, *([brand_id] if principal is not None else [])),
        ).fetchone()
        if rule is None:
            raise ValueError(f"Rule {rule_id} does not exist.")
        if rule["guideline_id"] is None:
            _validate_rule_source(rule)
        cursor = conn.execute(
            """
            UPDATE brand_rules SET is_active = 1, owner_approved = 1,
                review_status = 'APPROVED'
            WHERE id = ? AND review_status = 'PROPOSED'""" +
            (" AND brand_id = ?" if principal is not None else ""),
            (rule_id, *([brand_id] if principal is not None else [])),
        )
        if cursor.rowcount != 1:
            raise ValueError("Only a pending rule proposal can be approved.")
        _record_brand_rule_history(conn, rule_id, "APPROVED", actor)
        if principal is not None:
            append_audit_event(
                conn, principal, action="RULE_APPROVED", object_type="brand_rule",
                object_id=rule_id, brand_id=brand_id,
            )
        conn.commit()


def reject_rule(
    rule_id: int,
    actor: str = "demo_reviewer",
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    if principal is None:
        _require_reviewer(actor_role, "reject")
    else:
        actor = require_principal(principal, action="reject rules").username
    with get_connection() as conn:
        _authorize_workspace(principal, brand_id, "reject rule proposals", roles=(
            BrandRole.OWNER, BrandRole.REVIEWER,
        ))
        if principal is not None:
            require_record_brand(
                conn, principal, brand_id, "brand_rules", rule_id,
                roles=(BrandRole.OWNER, BrandRole.REVIEWER),
            )
        row = conn.execute(
            "SELECT id FROM brand_rules WHERE id = ?" +
            (" AND brand_id = ?" if principal is not None else ""),
            (rule_id, *([brand_id] if principal is not None else [])),
        ).fetchone()
        if row is None:
            raise ValueError(f"Rule {rule_id} does not exist.")
        cursor = conn.execute(
            """
            UPDATE brand_rules SET is_active = 0, owner_approved = 0,
                review_status = 'REJECTED'
            WHERE id = ? AND review_status = 'PROPOSED'""" +
            (" AND brand_id = ?" if principal is not None else ""),
            (rule_id, *([brand_id] if principal is not None else [])),
        )
        if cursor.rowcount != 1:
            raise ValueError("Only a pending rule proposal can be rejected.")
        _record_brand_rule_history(conn, rule_id, "REJECTED", actor)
        if principal is not None:
            append_audit_event(
                conn, principal, action="RULE_REJECTED", object_type="brand_rule",
                object_id=rule_id, brand_id=brand_id,
            )
        conn.commit()


def deactivate_rule(
    rule_id: int,
    actor: str = "demo_reviewer",
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    if principal is None:
        _require_reviewer(actor_role, "deactivate")
    else:
        actor = require_principal(principal, action="deactivate rules").username
    with get_connection() as conn:
        _authorize_workspace(principal, brand_id, "deactivate rules", roles=(
            BrandRole.OWNER, BrandRole.REVIEWER,
        ))
        if principal is not None:
            require_record_brand(
                conn, principal, brand_id, "brand_rules", rule_id,
                roles=(BrandRole.OWNER, BrandRole.REVIEWER),
            )
        cursor = conn.execute(
            "UPDATE brand_rules SET is_active = 0 WHERE id = ?" +
            (" AND brand_id = ?" if principal is not None else ""),
            (rule_id, *([brand_id] if principal is not None else [])),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Rule {rule_id} does not exist.")
        _record_brand_rule_history(conn, rule_id, "DEACTIVATED", actor)
        if principal is not None:
            append_audit_event(
                conn, principal, action="RULE_DEACTIVATED", object_type="brand_rule",
                object_id=rule_id, brand_id=brand_id,
            )
        conn.commit()


def _validate_rule_source(rule: dict) -> None:
    if rule["source_document_id"] is None or not rule["source_page"] or not rule["source_quote"]:
        raise ValueError("A rule needs a linked source guideline or an exact source quotation and page.")
    try:
        pages = json.loads(rule["page_records"] or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("The linked source document has invalid page data.") from exc
    page = next(
        (item for item in pages if str(item.get("page_number")) == str(rule["source_page"])),
        None,
    )
    if page is None or rule["source_quote"] not in page.get("text", ""):
        raise ValueError("The rule quotation must exactly match its linked source page.")


def _record_brand_rule_history(conn, rule_id: int, event_type: str, actor: str) -> None:
    row = conn.execute("SELECT * FROM brand_rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise ValueError(f"Rule {rule_id} does not exist.")
    version = conn.execute(
        "SELECT COALESCE(MAX(version_number), 0) + 1 AS next "
        "FROM brand_rule_history WHERE rule_id = ?",
        (rule_id,),
    ).fetchone()["next"]
    snapshot = {key: row[key] for key in row.keys()}
    conn.execute(
        """
        INSERT INTO brand_rule_history
            (rule_id, version_number, event_type, actor, rule_snapshot,
             source_document_id, source_page, source_quote)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rule_id, version, event_type, actor, json.dumps(snapshot, ensure_ascii=False),
            row["source_document_id"], row["source_page"], row["source_quote"],
        ),
    )


def create_campaign_brief(
    name: str,
    product_id: int,
    objectives: str,
    tone_guidance: str,
    target_audience: str,
    key_messages: list[str],
    markets: list[str],
    channels: list[str],
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> int:
    _authorize_workspace(principal, brand_id, "create campaign briefs")
    if not name.strip() or not product_id:
        raise ValueError("Campaign name and product are required.")
    if not key_messages or any(not item.strip() for item in key_messages):
        raise ValueError("Add at least one non-empty required key message.")
    with get_connection() as conn:
        _require_related_record(conn, principal, brand_id, "products", product_id)
        cursor = conn.execute(
            """
            INSERT INTO campaign_briefs
                (product_id, name, objectives, tone_guidance, target_audience,
                 key_messages, markets, channels, is_active, brand_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                product_id, name.strip(), objectives.strip(), tone_guidance.strip(),
                target_audience.strip(), json.dumps([item.strip() for item in key_messages]),
                json.dumps(markets), json.dumps(channels),
                brand_id if principal is not None else None,
            ),
        )
        _audit_workspace_change(
            conn, principal, action="CAMPAIGN_BRIEF_CREATED", object_type="campaign_brief",
            object_id=cursor.lastrowid, brand_id=brand_id,
        )
        conn.commit()
        return cursor.lastrowid


def update_campaign_brief(
    brief_id: int,
    name: str,
    product_id: int,
    objectives: str,
    tone_guidance: str,
    target_audience: str,
    key_messages: list[str],
    markets: list[str],
    channels: list[str],
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> None:
    if not name.strip() or not product_id:
        raise ValueError("Campaign name and product are required.")
    if not key_messages or any(not item.strip() for item in key_messages):
        raise ValueError("Add at least one non-empty required key message.")
    with get_connection() as conn:
        _authorize_workspace(principal, brand_id, "edit campaign briefs")
        if principal is not None:
            require_record_brand(
                conn, principal, brand_id, "campaign_briefs", brief_id,
                roles=_WORKSPACE_EDITORS,
            )
        _require_related_record(conn, principal, brand_id, "products", product_id)
        cursor = conn.execute(
            """
            UPDATE campaign_briefs SET product_id = ?, name = ?, objectives = ?,
                tone_guidance = ?, target_audience = ?, key_messages = ?,
                markets = ?, channels = ?
            WHERE id = ?""" + (" AND brand_id = ?" if principal is not None else ""),
            (
                product_id, name.strip(), objectives.strip(), tone_guidance.strip(),
                target_audience.strip(), json.dumps([item.strip() for item in key_messages]),
                json.dumps(markets), json.dumps(channels), brief_id,
                *([brand_id] if principal is not None else []),
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Campaign brief {brief_id} does not exist.")
        _audit_workspace_change(
            conn, principal, action="CAMPAIGN_BRIEF_UPDATED", object_type="campaign_brief",
            object_id=brief_id, brand_id=brand_id,
        )
        conn.commit()


def import_campaign_briefs_json(
    json_text: str, *, principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> dict:
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return {"imported": 0, "errors": [f"Invalid JSON: {exc.msg}."]}
    if not isinstance(payload, list) or not payload:
        return {"imported": 0, "errors": ["Import file must contain a non-empty JSON array."]}
    required = {
        "name", "product_id", "objectives", "tone_guidance", "target_audience",
        "key_messages", "markets", "channels",
    }
    errors: list[str] = []
    for index, brief in enumerate(payload, start=1):
        if not isinstance(brief, dict) or not required.issubset(brief):
            errors.append(f"Item {index}: missing required campaign fields.")
            continue
        if not isinstance(brief["product_id"], int):
            errors.append(f"Item {index}: product_id is not a known product.")
        if not isinstance(brief["key_messages"], list) or not brief["key_messages"] or any(
            not isinstance(item, str) or not item.strip() for item in brief["key_messages"]
        ):
            errors.append(f"Item {index}: key_messages must be a non-empty array of strings.")
        for field in ("markets", "channels"):
            if not isinstance(brief[field], list) or any(not isinstance(item, str) for item in brief[field]):
                errors.append(f"Item {index}: {field} must be an array of strings.")
        if not isinstance(brief["name"], str) or not brief["name"].strip():
            errors.append(f"Item {index}: name is required.")
    if errors:
        return {"imported": 0, "errors": errors}
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _authorize_workspace(principal, brand_id, "import campaign briefs")
        product_ids = {
            row["id"] for row in conn.execute(
                "SELECT id FROM products" +
                (" WHERE brand_id = ?" if principal is not None else ""),
                (brand_id,) if principal is not None else (),
            )
        }
        errors = [
            f"Item {index}: product_id is not a known product."
            for index, brief in enumerate(payload, start=1)
            if brief["product_id"] not in product_ids
        ]
        if errors:
            conn.rollback()
            return {"imported": 0, "errors": errors}
        for brief in payload:
            cursor = conn.execute(
                """
                INSERT INTO campaign_briefs
                    (product_id, name, objectives, tone_guidance, target_audience,
                     key_messages, markets, channels, is_active, brand_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    brief["product_id"], brief["name"].strip(),
                    str(brief["objectives"]), str(brief["tone_guidance"]),
                    str(brief["target_audience"]), json.dumps(brief["key_messages"]),
                    json.dumps(brief["markets"]), json.dumps(brief["channels"]),
                    brand_id if principal is not None else None,
                ),
            )
            _audit_workspace_change(
                conn, principal, action="CAMPAIGN_BRIEF_IMPORTED",
                object_type="campaign_brief", object_id=cursor.lastrowid,
                brand_id=brand_id,
            )
        conn.commit()
    return {"imported": len(payload), "errors": []}


def deactivate_brief(
    brief_id: int, *, principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    with get_connection() as conn:
        _authorize_workspace(principal, brand_id, "deactivate campaign briefs")
        if principal is not None:
            require_record_brand(
                conn, principal, brand_id, "campaign_briefs", brief_id,
                roles=_WORKSPACE_EDITORS,
            )
        cursor = conn.execute(
            "UPDATE campaign_briefs SET is_active = 0 WHERE id = ?" +
            (" AND brand_id = ?" if principal is not None else ""),
            (brief_id, *([brand_id] if principal is not None else [])),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Campaign brief {brief_id} does not exist.")
        _audit_workspace_change(
            conn, principal, action="CAMPAIGN_BRIEF_DEACTIVATED",
            object_type="campaign_brief", object_id=brief_id, brand_id=brand_id,
        )
        conn.commit()
