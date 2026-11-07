"""
services/fact_service.py
------------------------
All database operations related to facts and fact versions.
The AI is used ONLY for ranking pre-filtered candidates —
filtering is done deterministically here.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from typing import Any

from db.connection import get_connection
from models.enums import FactApprovalStatus, UserRole


def _require_fact_owner(actor_role: UserRole | str | None, action: str) -> None:
    role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
    if role_value.casefold() != UserRole.FACT_OWNER.value.casefold():
        raise PermissionError(f"Fact Owner role required to {action}.")


def get_eligible_fact_versions(
    draft_id: int | None = None,
    product_id: int | None = None,
    market: str | None = None,
    sku_aliases: list[str] | None = None,
    **kwargs: Any,
) -> list[dict]:
    """
    Return only the latest effective approved version of each fact for the given
    product and market. Older versions remain available to immutable claim
    snapshots, but cannot be selected as current evidence for a new review.

    Deterministic SQL & python filtering rules (no LLM involvement):
    1. fact_versions.approval_status = 'APPROVED'
    2. product_id and market must both resolve; missing scope returns no facts.
    3. facts.product_id and facts.markets must match the selected scope.
    4. Explicit SKU aliases are respected to resolve product if product_id is not given.
    5. Validity dates include today and exclude not-yet-valid/expired versions.
    """
    # Support legacy positional call: get_eligible_fact_versions(product_id, market, sku_aliases)
    if isinstance(product_id, str) and market is None:
        market = product_id
        product_id = draft_id
        draft_id = None
    elif draft_id is not None and product_id is None and not isinstance(draft_id, int):
        # In case draft_id was passed as product_id positionally
        product_id = draft_id
        draft_id = None

    with get_connection() as conn:
        # If draft_id is provided, resolve product_id and market from the draft
        if draft_id is not None:
            draft_row = conn.execute(
                "SELECT product_id, market FROM drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
            if draft_row:
                if product_id is None:
                    product_id = draft_row["product_id"]
                if market is None:
                    market = draft_row["market"]

        # If product_id is still None but sku_aliases are provided, resolve product_id
        if product_id is None and sku_aliases:
            all_prods = conn.execute("SELECT id, sku_aliases FROM products").fetchall()
            norm_input_aliases = {a.strip().lower() for a in sku_aliases if a}
            for p in all_prods:
                try:
                    p_aliases = json.loads(p["sku_aliases"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    p_aliases = []
                if any(pa.strip().lower() in norm_input_aliases for pa in p_aliases):
                    product_id = p["id"]
                    break

        if product_id is None or not market:
            return []

        today = date.today().isoformat()
        expire_elapsed_fact_versions(today=today, conn=conn)

        query = """
            WITH ranked_versions AS (
                SELECT
                    fv.id,
                    fv.fact_id,
                    fv.version_number,
                    fv.statement,
                    fv.evidence_ref,
                    fv.valid_from,
                    fv.valid_until,
                    fv.approval_status,
                    ROW_NUMBER() OVER (
                        PARTITION BY fv.fact_id
                        ORDER BY fv.version_number DESC
                    ) AS version_rank
                FROM fact_versions fv
                WHERE fv.approval_status IN ('APPROVED', 'EXPIRED')
                  AND (fv.valid_from IS NULL OR fv.valid_from <= ?)
            )
            SELECT
                fv.id          AS fact_version_id,
                fv.fact_id,
                fv.version_number,
                fv.statement,
                fv.evidence_ref,
                fv.valid_from,
                fv.valid_until,
                fv.approval_status,
                f.name         AS fact_name,
                f.category     AS fact_category,
                f.product_id   AS fact_product_id,
                f.markets      AS fact_markets,
                p.name         AS product_name
            FROM ranked_versions fv
            JOIN facts f ON f.id = fv.fact_id
            JOIN products p ON p.id = f.product_id
            WHERE fv.version_rank = 1
              AND fv.approval_status = 'APPROVED'
              AND (fv.valid_until IS NULL OR fv.valid_until >= ?)
              AND f.product_id = ?
        """
        params: list[Any] = [today, today, product_id]

        rows = conn.execute(query, params).fetchall()

    results: list[dict] = []
    for row in rows:
        row_dict = dict(row)

        # Market isolation check
        try:
            markets_list = json.loads(row_dict.get("fact_markets") or "[]")
        except (json.JSONDecodeError, TypeError):
            markets_list = []
        if not isinstance(markets_list, list):
            continue
        normalized_markets = {
            str(item).strip().casefold() for item in markets_list if str(item).strip()
        }
        if market.strip().casefold() not in normalized_markets and "all" not in normalized_markets:
            continue

        results.append({
            "fact_version_id": row_dict["fact_version_id"],
            "fact_id": row_dict["fact_id"],
            "version_number": row_dict["version_number"],
            "statement": row_dict["statement"],
            "evidence_ref": row_dict["evidence_ref"],
            "valid_from": row_dict.get("valid_from"),
            "valid_until": row_dict.get("valid_until"),
            "approval_status": row_dict["approval_status"],
            "fact_name": row_dict["fact_name"],
            "fact_category": row_dict["fact_category"],
            "product_id": row_dict["fact_product_id"],
            "product_name": row_dict["product_name"],
            "markets": row_dict["fact_markets"],
        })

    return results


def expire_elapsed_fact_versions(
    today: str | None = None,
    conn=None,
) -> int:
    """Expire elapsed approved evidence and propagate only its current dependent claims."""
    expiry_date = today or date.today().isoformat()
    if conn is None:
        with get_connection() as active_conn:
            return expire_elapsed_fact_versions(expiry_date, active_conn)

    expired_ids = [
        row["id"]
        for row in conn.execute(
            """
            SELECT id FROM fact_versions
            WHERE approval_status = 'APPROVED'
              AND valid_until IS NOT NULL AND valid_until < ?
            """,
            (expiry_date,),
        ).fetchall()
    ]
    for version_id in expired_ids:
        conn.execute(
            "UPDATE fact_versions SET approval_status = 'EXPIRED' WHERE id = ?",
            (version_id,),
        )
        conn.execute(
            """
            UPDATE approved_claims SET drift_status = 'EXPIRED'
            WHERE current_snapshot_id IN (
                SELECT sfl.snapshot_id
                FROM snapshot_fact_links sfl
                WHERE sfl.fact_version_id = ?
            )
            """,
            (version_id,),
        )
    return len(expired_ids)


def get_all_facts(product_id: int | None = None) -> list[dict]:
    """Return all fact parent rows, optionally filtered by product."""
    query = "SELECT * FROM facts"
    params: list = []
    if product_id is not None:
        query += " WHERE product_id = ?"
        params.append(product_id)
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_fact_versions(fact_id: int) -> list[dict]:
    """Return all versions for a fact, ordered by version number."""
    with get_connection() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM fact_versions WHERE fact_id = ? ORDER BY version_number",
                (fact_id,),
            ).fetchall()
        ]


def get_pending_fact_versions() -> list[dict]:
    """Return pending versions with product and fact context for the Fact Owner queue."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT fv.*, f.name AS fact_name, f.product_id, f.markets,
                   p.name AS product_name
            FROM fact_versions fv
            JOIN facts f ON f.id = fv.fact_id
            JOIN products p ON p.id = f.product_id
            WHERE fv.approval_status = 'PENDING_APPROVAL'
            ORDER BY fv.created_at ASC, fv.id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def create_fact_version(
    fact_id: int,
    statement: str,
    evidence_ref: str,
    valid_from: str | None = None,
    valid_until: str | None = None,
    *,
    actor_role: UserRole | str | None = None,
) -> int:
    """
    Insert a new (immutable) fact version with status PENDING_APPROVAL.
    Version numbers auto-increment per fact (MAX(version_number) + 1).
    Returns the new fact_version id.
    """
    _require_fact_owner(actor_role, "create fact versions")
    try:
        start = date.fromisoformat(valid_from) if valid_from else None
        end = date.fromisoformat(valid_until) if valid_until else None
    except ValueError as exc:
        raise ValueError("Fact validity dates must use YYYY-MM-DD.") from exc
    if (valid_from and start.isoformat() != valid_from) or (
        valid_until and end.isoformat() != valid_until
    ):
        raise ValueError("Fact validity dates must use YYYY-MM-DD.")
    if start and end and end < start:
        raise ValueError("valid_until cannot be earlier than valid_from.")

    with get_connection() as conn:
        # Determine next version number
        row = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_v FROM fact_versions WHERE fact_id = ?",
            (fact_id,),
        ).fetchone()
        next_v = row["next_v"]

        cur = conn.execute(
            """
            INSERT INTO fact_versions
                (fact_id, version_number, statement, evidence_ref, valid_from, valid_until, approval_status)
            VALUES (?, ?, ?, ?, ?, ?, 'PENDING_APPROVAL')
            """,
            (fact_id, next_v, statement, evidence_ref, valid_from, valid_until),
        )
        conn.commit()
        return cur.lastrowid


def approve_fact_version(
    fact_version_id: int,
    approved_by: str = "demo_fact_owner",
    *,
    actor_role: UserRole | str | None = None,
) -> int:
    """
    Approve a pending fact version and flag claims linked to earlier approved
    versions of that fact in the same transaction.

    Returns the number of dependent claims moved from CURRENT to REVIEW_REQUIRED.
    """
    _require_fact_owner(actor_role, "approve fact versions")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        pending = conn.execute(
            """
            SELECT fact_id, valid_from, valid_until
            FROM fact_versions WHERE id = ? AND approval_status = 'PENDING_APPROVAL'
            """,
            (fact_version_id,),
        ).fetchone()
        if pending is None:
            return 0
        today = date.today().isoformat()
        if pending["valid_from"] and pending["valid_from"] > today:
            raise ValueError(f"Evidence is not valid until {pending['valid_from']}.")
        if pending["valid_until"] and pending["valid_until"] < today:
            raise ValueError(f"Evidence expired on {pending['valid_until']}; it cannot be approved.")

        updated = conn.execute(
            """
            UPDATE fact_versions
            SET approval_status = 'APPROVED',
                approved_by = ?,
                approved_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ? AND approval_status = 'PENDING_APPROVAL'
            """,
            (approved_by, fact_version_id),
        )
        if updated.rowcount != 1:
            return 0

        conn.execute(
            """
            UPDATE approved_claims
            SET drift_status = 'REVIEW_REQUIRED'
            WHERE drift_status = 'CURRENT'
              AND id IN (
                  SELECT s.approved_claim_id
                  FROM approved_claim_snapshots s
                  JOIN snapshot_fact_links sfl ON sfl.snapshot_id = s.id
                  JOIN fact_versions old_fv ON old_fv.id = sfl.fact_version_id
                  JOIN approved_claims ac ON ac.id = s.approved_claim_id
                  WHERE ac.current_snapshot_id = s.id
                    AND old_fv.fact_id = ? AND old_fv.id != ?
              )
            """,
            (pending["fact_id"], fact_version_id),
        )
        affected = conn.execute("SELECT changes() AS n").fetchone()["n"]
        conn.commit()
        return affected


def reject_fact_version(
    fact_version_id: int, *, actor_role: UserRole | str | None = None
) -> bool:
    """Reject a pending fact version without changing any prior version."""
    _require_fact_owner(actor_role, "reject fact versions")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE fact_versions
            SET approval_status = 'REJECTED'
            WHERE id = ? AND approval_status = 'PENDING_APPROVAL'
            """,
            (fact_version_id,),
        )
        conn.commit()
        return cursor.rowcount == 1


def expire_fact_version(
    fact_version_id: int, *, actor_role: UserRole | str | None = None
) -> int:
    """
    Expire a fact version.
    Flag ONLY claims linked to that exact fact_version_id as EXPIRED.
    SQL-driven propagation, no Gemini calls.
    Preserve unrelated claims as CURRENT.
    Returns the number of approved claims flagged as EXPIRED.
    """
    _require_fact_owner(actor_role, "expire fact versions")
    with get_connection() as conn:
        # Expire the version record
        updated = conn.execute(
            """
            UPDATE fact_versions SET approval_status = 'EXPIRED'
            WHERE id = ? AND approval_status = 'APPROVED'
            """,
            (fact_version_id,),
        )
        if updated.rowcount != 1:
            return 0

        # Flag ONLY dependent approved claims linked to that exact version
        conn.execute(
            """
            UPDATE approved_claims
            SET drift_status = 'EXPIRED'
            WHERE current_snapshot_id IN (
                SELECT snapshot_id FROM snapshot_fact_links
                WHERE fact_version_id = ?
            )
            """,
            (fact_version_id,),
        )

        affected = conn.execute(
            """
            SELECT COUNT(DISTINCT approved_claim_id) AS n
            FROM approved_claim_snapshots s
            JOIN snapshot_fact_links sfl ON sfl.snapshot_id = s.id
            JOIN approved_claims ac ON ac.id = s.approved_claim_id
            WHERE ac.current_snapshot_id = s.id
              AND sfl.fact_version_id = ?
            """,
            (fact_version_id,),
        ).fetchone()["n"]

        conn.commit()
        return affected


def flag_claims_for_new_version(old_fact_version_id: int) -> int:
    """
    When a new fact version is published, flag claims linked to the old version
    whose drift_status is CURRENT as REVIEW_REQUIRED (drift detection).
    Keep EXPIRED and REVIEW_REQUIRED clearly separate.
    Returns count of claims flagged.
    """
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE approved_claims
            SET drift_status = 'REVIEW_REQUIRED'
            WHERE drift_status = 'CURRENT'
              AND current_snapshot_id IN (
                SELECT snapshot_id FROM snapshot_fact_links
                WHERE fact_version_id = ?
              )
            """,
            (old_fact_version_id,),
        )
        affected = conn.execute(
            "SELECT changes() AS n",
        ).fetchone()["n"]
        conn.commit()
        return affected


def create_fact(
    product_id: int,
    name: str,
    category: str,
    markets: list[str],
    *,
    actor_role: UserRole | str | None = None,
) -> int:
    """Create a new fact parent record. Returns new fact id."""
    _require_fact_owner(actor_role, "create facts")
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO facts (product_id, name, category, markets) VALUES (?,?,?,?)",
            (product_id, name, category, json.dumps(markets)),
        )
        conn.commit()
        return cur.lastrowid


def import_facts_csv(
    csv_text: str, *, actor_role: UserRole | str | None = None
) -> dict:
    """Validate a whole facts CSV before inserting any pending evidence versions."""
    _require_fact_owner(actor_role, "import facts")
    if len(csv_text.encode("utf-8")) > 10 * 1024 * 1024:
        return {"imported": 0, "errors": ["CSV exceeds the 10 MB upload limit."]}
    required = {
        "product_or_sku", "fact_name", "category", "markets", "statement",
        "evidence_reference", "valid_from", "valid_until",
    }
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
    except csv.Error as exc:
        return {"imported": 0, "errors": [f"CSV could not be read: {exc}"]}
    headers = {header.strip() for header in (reader.fieldnames or []) if header}
    if not required.issubset(headers):
        return {
            "imported": 0,
            "errors": [
                "Required CSV headers: product_or_sku, fact_name, category, markets, "
                "statement, evidence_reference, valid_from, valid_until."
            ],
        }

    with get_connection() as conn:
        products = conn.execute("SELECT id, name, sku_aliases FROM products").fetchall()
    product_lookup: dict[str, int] = {}
    for product in products:
        product_lookup[product["name"].strip().casefold()] = product["id"]
        try:
            aliases = json.loads(product["sku_aliases"] or "[]")
        except (json.JSONDecodeError, TypeError):
            aliases = []
        for alias in aliases:
            if isinstance(alias, str):
                product_lookup[alias.strip().casefold()] = product["id"]

    valid_rows: list[dict] = []
    errors: list[str] = []
    for row_number, raw in enumerate(reader, start=2):
        row = {str(key).strip(): (value or "").strip() for key, value in raw.items() if key}
        product_id = product_lookup.get(row.get("product_or_sku", "").casefold())
        if product_id is None:
            errors.append(f"Row {row_number}: unknown product_or_sku.")
            continue
        if not all(row.get(field) for field in ("fact_name", "category", "statement", "evidence_reference")):
            errors.append(f"Row {row_number}: fact_name, category, statement, and evidence_reference are required.")
            continue
        markets = [value.strip().upper() for value in row.get("markets", "").split("|") if value.strip()]
        if not markets:
            errors.append(f"Row {row_number}: markets must contain at least one market code.")
            continue
        try:
            valid_from = date.fromisoformat(row["valid_from"]).isoformat() if row["valid_from"] else None
            valid_until = date.fromisoformat(row["valid_until"]).isoformat() if row["valid_until"] else None
        except ValueError:
            errors.append(f"Row {row_number}: valid_from/valid_until must be ISO dates (YYYY-MM-DD) or blank.")
            continue
        if valid_from and valid_until and valid_until < valid_from:
            errors.append(f"Row {row_number}: valid_until is earlier than valid_from.")
            continue
        valid_rows.append({
            "product_id": product_id,
            "name": row["fact_name"],
            "category": row["category"],
            "markets": markets,
            "statement": row["statement"],
            "evidence_ref": row["evidence_reference"],
            "valid_from": valid_from,
            "valid_until": valid_until,
        })

    if errors:
        return {"imported": 0, "errors": errors}
    if not valid_rows:
        return {"imported": 0, "errors": ["CSV contains no fact rows."]}

    with get_connection() as conn:
        for row in valid_rows:
            fact_cursor = conn.execute(
                "INSERT INTO facts (product_id, name, category, markets) VALUES (?, ?, ?, ?)",
                (row["product_id"], row["name"], row["category"], json.dumps(row["markets"])),
            )
            conn.execute(
                """
                INSERT INTO fact_versions
                    (fact_id, version_number, statement, evidence_ref, valid_from, valid_until, approval_status)
                VALUES (?, 1, ?, ?, ?, ?, 'PENDING_APPROVAL')
                """,
                (
                    fact_cursor.lastrowid, row["statement"], row["evidence_ref"],
                    row["valid_from"], row["valid_until"],
                ),
            )
        conn.commit()
    return {"imported": len(valid_rows), "errors": []}
