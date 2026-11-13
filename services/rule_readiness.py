"""Human-reviewed, source-grounded rule classification and activation."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any

from db.connection import get_connection
from engines.rule_engine import run_self_tests, validate_regex_pattern
from models.enums import BrandRole, RuleType, Severity, UserRole
from services.auth_service import (
    AuthenticatedPrincipal,
    require_legacy_test_actor,
    require_principal,
)
from services.brand_service import require_brand_membership, require_record_brand


RULE_CATEGORIES = {"BRAND_RULE", "TONE", "TERMINOLOGY", "RESTRICTION"}
READINESS_CLASSES = {
    "CODE_CHECKABLE",
    "AI_ASSISTED",
    "HUMAN_JUDGEMENT",
    "UNCLEAR",
}

_METHODS = {
    "AI_ASSISTED": "AI assessment only; findings remain advisory and require human review.",
    "HUMAN_JUDGEMENT": "Human decision only; no automated enforcement.",
    "UNCLEAR": "Clarification required; not available to the review engine.",
}
_PREDICATE_FIELDS = {
    RuleType.TERM_LIST.value: {
        "mode", "terms", "case_sensitive", "suggested_correction", "explanation",
    },
    RuleType.REGEX.value: {"pattern", "require_match", "suggested_correction"},
    RuleType.NUMERIC_RANGE.value: {
        "extract_pattern", "min", "max", "suggested_correction",
    },
    RuleType.DATE_WINDOW.value: {
        "extract_pattern", "date_format", "earliest", "latest",
        "suggested_correction",
    },
    RuleType.REQUIRED_TEXT.value: {
        "pattern", "trigger_on", "require_match", "suggested_correction",
    },
    RuleType.WCAG_CONTRAST.value: {"foreground", "background", "min_ratio"},
    RuleType.ASSET_HASH.value: {"approved_hash", "hash_type", "max_hamming"},
}
_SCOPE_FIELDS = {"market", "markets", "channel", "channels", "content_type"}


def _reviewer(actor_role: UserRole | str | None) -> None:
    require_legacy_test_actor("review rule readiness proposals")
    role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
    if role_value.strip().casefold() not in {"reviewer", "brand manager"}:
        raise PermissionError("Rule readiness decisions require the Reviewer demo role.")


def _source_quote_is_valid(entry: Any) -> bool:
    if not entry["document_id"] or not entry["source_page"] or not entry["source_quote"]:
        return False
    document = get_connection()
    try:
        row = document.execute(
            "SELECT page_records FROM knowledge_documents WHERE id = ?",
            (entry["document_id"],),
        ).fetchone()
    finally:
        document.close()
    if row is None:
        return False
    try:
        pages = json.loads(row["page_records"] or "[]")
    except (TypeError, json.JSONDecodeError):
        return False
    page = next(
        (p for p in pages if str(p.get("page_number")) == str(entry["source_page"])),
        None,
    )
    return bool(page and entry["source_quote"] in page.get("text", ""))


def validate_scope(scope: Any) -> dict[str, Any]:
    if not isinstance(scope, dict) or set(scope) - _SCOPE_FIELDS:
        raise ValueError("Scope must be an object containing only market, channel, or content_type.")
    normalized: dict[str, Any] = {}
    for key, value in scope.items():
        if key in {"market", "markets", "channel", "channels"}:
            values = [value] if isinstance(value, str) else value
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(item, str) or not item.strip() for item in values)
            ):
                raise ValueError(f"{key} scope must be a non-empty string or string list.")
            normalized[key] = [item.strip() for item in values]
        else:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("content_type scope must be a non-empty string.")
            normalized[key] = value.strip()
    return normalized


def validate_predicate(rule_type: str, parameters: Any) -> dict[str, Any]:
    """Validate a narrow, explicit predicate schema; reject unknown keys and types."""
    try:
        kind = RuleType(rule_type).value
    except ValueError as exc:
        raise ValueError(f"Unsupported deterministic predicate: {rule_type!r}.") from exc
    if kind not in _PREDICATE_FIELDS:
        raise ValueError(f"Predicate {kind!r} is not available for rule readiness.")
    if not isinstance(parameters, dict):
        raise ValueError("Predicate parameters must be a JSON object.")
    allowed = _PREDICATE_FIELDS[kind]
    unknown = set(parameters) - allowed
    if unknown:
        raise ValueError(f"Unsupported parameter(s) for {kind}: {', '.join(sorted(unknown))}.")

    def require_string(key: str) -> str:
        value = parameters.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Predicate parameter {key!r} must be a non-empty string.")
        return value

    def validate_regex(key: str, required: bool = True):
        pattern = parameters.get(key)
        if pattern is None and not required:
            return None
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"Predicate parameter {key!r} must be a non-empty regex.")
        try:
            return validate_regex_pattern(pattern)
        except ValueError as exc:
            raise ValueError(f"Invalid regex in {key!r}: {exc}") from exc

    if kind == RuleType.TERM_LIST.value:
        mode = parameters.get("mode")
        terms = parameters.get("terms")
        if mode not in {"banned", "required"}:
            raise ValueError("term_list mode must be 'banned' or 'required'.")
        if not isinstance(terms, list) or not terms or any(
            not isinstance(term, str) or not term.strip() for term in terms
        ):
            raise ValueError("term_list requires a non-empty list of terms.")
        if "case_sensitive" in parameters and not isinstance(parameters["case_sensitive"], bool):
            raise ValueError("case_sensitive must be a boolean.")
    elif kind == RuleType.REGEX.value:
        validate_regex("pattern")
        if "require_match" in parameters and not isinstance(parameters["require_match"], bool):
            raise ValueError("require_match must be a boolean.")
    elif kind == RuleType.NUMERIC_RANGE.value:
        compiled = validate_regex("extract_pattern")
        if compiled.groups != 1:
            raise ValueError("numeric_range extract_pattern must contain exactly one capture group.")
        minimum, maximum = parameters.get("min"), parameters.get("max")
        if minimum is None and maximum is None:
            raise ValueError("numeric_range needs a minimum, maximum, or both.")
        for key, value in (("min", minimum), ("max", maximum)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"numeric_range {key} must be a finite number.")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("numeric_range minimum cannot exceed maximum.")
    elif kind == RuleType.DATE_WINDOW.value:
        compiled = validate_regex("extract_pattern")
        if compiled.groups != 1:
            raise ValueError("date_window extract_pattern must contain exactly one capture group.")
        date_format = parameters.get("date_format", "%Y-%m-%d")
        if not isinstance(date_format, str) or not date_format:
            raise ValueError("date_format must be a non-empty string.")
        if not parameters.get("earliest") and not parameters.get("latest"):
            raise ValueError("date_window needs an earliest date, latest date, or both.")
        for key in ("earliest", "latest"):
            value = parameters.get(key)
            if value is not None:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be an ISO date (YYYY-MM-DD).") from exc
        if parameters.get("earliest") and parameters.get("latest"):
            if parameters["earliest"] > parameters["latest"]:
                raise ValueError("date_window earliest date cannot be after latest date.")
    elif kind == RuleType.REQUIRED_TEXT.value:
        validate_regex("pattern")
        validate_regex("trigger_on", required=False)
        if "require_match" in parameters and not isinstance(parameters["require_match"], bool):
            raise ValueError("require_match must be a boolean.")
    elif kind == RuleType.WCAG_CONTRAST.value:
        foreground = require_string("foreground")
        background = require_string("background")
        for key, color in (("foreground", foreground), ("background", background)):
            if not re.fullmatch(r"#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", color):
                raise ValueError(f"{key} must be a 3- or 6-digit hex colour.")
        ratio = parameters.get("min_ratio", 4.5)
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 1 <= ratio <= 21:
            raise ValueError("min_ratio must be a number from 1 to 21.")
    elif kind == RuleType.ASSET_HASH.value:
        digest = require_string("approved_hash")
        hash_type = parameters.get("hash_type", "sha256")
        if hash_type not in {"sha256", "perceptual"}:
            raise ValueError("asset_hash hash_type must be 'sha256' or 'perceptual'.")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest) if hash_type == "sha256" else not re.fullmatch(r"[0-9a-fA-F]{1,64}", digest):
            raise ValueError("approved_hash must be a hexadecimal hash of the selected type.")
        max_hamming = parameters.get("max_hamming", 10)
        if isinstance(max_hamming, bool) or not isinstance(max_hamming, int) or not 0 <= max_hamming <= 64:
            raise ValueError("max_hamming must be an integer from 0 to 64.")
    return dict(parameters)


def get_rule_proposals(
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict[str, Any]]:
    if principal is None:
        require_legacy_test_actor("read unscoped rule proposals")
    with get_connection() as conn:
        params: tuple = ()
        scope = ""
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for authenticated rule proposals.")
            require_brand_membership(principal, brand_id, (BrandRole.OWNER, BrandRole.REVIEWER))
            scope = " AND e.brand_id = ?"
            params = (brand_id,)
        rows = conn.execute(
            """
            SELECT e.*, d.filename AS source_filename, p.name AS product_name
            FROM knowledge_entries e
            LEFT JOIN knowledge_documents d ON d.id = e.document_id
            LEFT JOIN products p ON p.id = e.product_id
            WHERE e.category IN ('BRAND_RULE','TONE','TERMINOLOGY','RESTRICTION')""" + scope + """
            ORDER BY e.created_at DESC, e.id DESC
            """, params
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        for key in ("rule_parameters", "rule_scope"):
            try:
                item[key] = json.loads(item[key] or "{}")
            except json.JSONDecodeError:
                item[key] = {}
        raw_warnings = item.get("warnings")
        if isinstance(raw_warnings, str):
            try:
                parsed_warnings = json.loads(raw_warnings or "[]")
            except json.JSONDecodeError:
                parsed_warnings = [raw_warnings] if raw_warnings.strip() else []
        else:
            parsed_warnings = raw_warnings
        if not isinstance(parsed_warnings, list):
            parsed_warnings = [parsed_warnings] if parsed_warnings is not None else []
        item["warnings"] = [str(warning) for warning in parsed_warnings]
        results.append(item)
    return results


def save_rule_classification(
    entry_id: int,
    readiness_classification: str,
    actor: str,
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
    rule_type: str | None = None,
    parameters: dict[str, Any] | None = None,
    scope: dict[str, Any] | None = None,
    severity: str = "MEDIUM",
    positive_tests: list[str] | None = None,
    negative_tests: list[str] | None = None,
    clarification_note: str = "",
) -> None:
    actor_name = actor
    authenticated_actor = (
        require_principal(principal, action="classify rule proposals")
        if principal else None
    )
    if authenticated_actor is None:
        _reviewer(actor_role)
    else:
        actor_name = authenticated_actor.username
    classification = readiness_classification.strip().upper()
    if classification not in READINESS_CLASSES:
        raise ValueError("Select one of the four supported rule readiness classifications.")
    method = (
        f"Deterministic Python predicate: {rule_type}"
        if classification == "CODE_CHECKABLE" and rule_type
        else _METHODS.get(classification)
    )
    if not method:
        raise ValueError("Choose a supported predicate for a CODE-CHECKABLE rule.")
    if classification == "CODE_CHECKABLE":
        parameters = validate_predicate(rule_type or "", parameters or {})
        scope = validate_scope(scope or {})
        try:
            severity = Severity(severity).value
        except ValueError as exc:
            raise ValueError("Choose a valid finding severity.") from exc
        positive_tests = [value.strip() for value in (positive_tests or [])]
        negative_tests = [value.strip() for value in (negative_tests or [])]
        if not positive_tests or not negative_tests or any(
            not value for value in positive_tests + negative_tests
        ):
            raise ValueError("A code-checkable rule needs non-empty passing and failing examples.")
    else:
        rule_type, parameters, scope = None, {}, {}
        severity = "MEDIUM"
        positive_tests, negative_tests = [], []
    if classification == "UNCLEAR" and not clarification_note.strip():
        clarification_note = ""

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            entry = conn.execute(
                "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            if authenticated_actor is not None:
                if brand_id is None:
                    raise ValueError("A brand is required for authenticated rule classification.")
                require_record_brand(
                    conn, authenticated_actor, brand_id, "knowledge_entries", entry_id,
                    roles=(BrandRole.OWNER, BrandRole.REVIEWER),
                )
            if entry is None or entry["category"] not in RULE_CATEGORIES:
                raise ValueError("The selected entry is not a rule-readiness proposal.")
            if entry["status"] not in {
                "PENDING_APPROVAL", "NEEDS_CLARIFICATION", "UNRESOLVED"
            }:
                raise ValueError("Only pending rule proposals can be classified.")
            if entry["status"] == "NEEDS_CLARIFICATION" and classification != "UNCLEAR":
                if not clarification_note.strip():
                    raise ValueError("Record how the requested clarification was resolved.")
            if classification == "UNCLEAR" and not clarification_note.strip():
                raise ValueError("Record the question that needs clarification.")
            version = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 AS next "
                "FROM rule_readiness_history WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()["next"]
            status = "NEEDS_CLARIFICATION" if classification == "UNCLEAR" else "PENDING_APPROVAL"
            conn.execute(
                """
                UPDATE knowledge_entries
                SET rule_readiness_classification = ?, rule_checking_method = ?,
                    rule_type = ?, rule_parameters = ?, rule_scope = ?,
                    rule_severity = ?, rule_positive_tests = ?, rule_negative_tests = ?,
                    clarification_note = ?, status = ?
                WHERE id = ?
                """,
                (
                    classification, method, rule_type,
                    json.dumps(parameters or {}, ensure_ascii=False),
                    json.dumps(scope or {}, ensure_ascii=False), severity,
                    json.dumps(positive_tests or [], ensure_ascii=False),
                    json.dumps(negative_tests or [], ensure_ascii=False),
                    clarification_note.strip() or None, status, entry_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO rule_readiness_history
                    (entry_id, version_number, event_type, actor,
                     readiness_classification, checking_method, rule_type,
                     rule_parameters, rule_scope, rule_severity,
                     positive_tests, negative_tests,
                     source_document_id, source_page, source_quote,
                     source_quote_verified, source_support_confirmed,
                     source_support_confirmed_by, source_support_confirmed_at, note)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id, version, "CLASSIFIED" if classification != "UNCLEAR" else "CLARIFICATION_REQUIRED",
                    actor_name, classification, method, rule_type,
                    json.dumps(parameters or {}, ensure_ascii=False),
                    json.dumps(scope or {}, ensure_ascii=False), severity,
                    json.dumps(positive_tests or [], ensure_ascii=False),
                    json.dumps(negative_tests or [], ensure_ascii=False),
                    entry["document_id"], entry["source_page"], entry["source_quote"],
                    entry["source_quote_verified"], entry["source_support_confirmed"],
                    entry["source_support_confirmed_by"], entry["source_support_confirmed_at"],
                    clarification_note.strip(),
                ),
            )
            conn.execute(
                "INSERT INTO knowledge_decisions (entry_id, decision, actor, note) "
                "VALUES (?, ?, ?, ?)",
                (
                    entry_id, "CLASSIFIED" if classification != "UNCLEAR" else "CLARIFY",
                    actor_name, clarification_note.strip() or method,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def approve_rule_proposal(
    entry_id: int,
    actor: str,
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> dict[str, Any]:
    authenticated_actor = (
        require_principal(principal, action="approve rule proposals")
        if principal is not None else None
    )
    if authenticated_actor is None:
        _reviewer(actor_role)
    else:
        actor = authenticated_actor.username
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            entry = conn.execute(
                """
                SELECT e.*, d.filename, d.extraction_status, d.page_records
                FROM knowledge_entries e
                LEFT JOIN knowledge_documents d ON d.id = e.document_id
                WHERE e.id = ?
                """,
                (entry_id,),
            ).fetchone()
            if authenticated_actor is not None:
                if brand_id is None:
                    raise ValueError("A brand is required for authenticated rule approval.")
                require_record_brand(
                    conn, authenticated_actor, brand_id,
                    "knowledge_entries", entry_id,
                    roles=(BrandRole.OWNER, BrandRole.REVIEWER),
                )
            if entry is None or entry["category"] not in RULE_CATEGORIES:
                raise ValueError("The selected entry is not a rule-readiness proposal.")
            if entry["rule_readiness_classification"] == "UNCLEAR":
                raise ValueError("Clarify and reclassify this proposal before approval.")
            if entry["status"] != "PENDING_APPROVAL":
                raise ValueError("Only classified pending proposals can be approved.")
            classification = entry["rule_readiness_classification"]
            if classification not in READINESS_CLASSES or classification == "UNCLEAR":
                raise ValueError("Clarify and reclassify this proposal before approval.")
            if (
                not entry["document_id"] or not entry["source_page"]
                or not entry["source_quote"]
            ):
                raise ValueError("A source document, page, and exact quotation are required.")
            try:
                pages = json.loads(entry["page_records"] or "[]")
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("The linked source document has invalid page data.") from exc
            page = next(
                (item for item in pages if str(item.get("page_number")) == str(entry["source_page"])),
                None,
            )
            if page is None or entry["source_quote"] not in page.get("text", ""):
                raise ValueError("The quoted source text no longer matches the selected page.")
            if not entry["source_quote_verified"]:
                raise ValueError("Verify the exact quotation on its source page before approval.")
            if not entry["source_support_confirmed"]:
                raise ValueError(
                    "A reviewer must confirm that the quotation supports this rule before approval."
                )
            conflict = conn.execute(
                """
                SELECT 1 FROM knowledge_conflicts
                WHERE status = 'UNRESOLVED'
                  AND (first_entry_id = ? OR second_entry_id = ?)
                """,
                (entry_id, entry_id),
            ).fetchone()
            if conflict:
                raise ValueError("Resolve the displayed proposal conflict before approval.")
            content = entry["content"].strip()
            product_id = entry["product_id"]
            market = entry["market"]
            channel = entry["channel"]
            content_type = entry["content_type"]
            linked_type: str
            linked_id: int | None = None

            if classification == "CODE_CHECKABLE":
                kind = entry["rule_type"]
                parameters = validate_predicate(
                    kind, json.loads(entry["rule_parameters"] or "{}")
                )
                scope = validate_scope(json.loads(entry["rule_scope"] or "{}"))
                severity = Severity(entry["rule_severity"]).value
                rule = {
                    "name": content[:120],
                    "description": content,
                    "rule_type": kind,
                    "parameters": json.dumps(parameters),
                    "scope": json.dumps(scope),
                    "severity": severity,
                    "positive_tests": entry["rule_positive_tests"],
                    "negative_tests": entry["rule_negative_tests"],
                    "is_active": 1,
                    "owner_approved": 1,
                    "readiness_class": "CODE_CHECKABLE",
                    "source_verified": True,
                }
                outcomes = run_self_tests(rule)
                if not outcomes or not all(item["passed"] for item in outcomes):
                    raise ValueError("The approved predicate did not pass its positive and negative checks.")
                cursor = conn.execute(
                    """
                    INSERT INTO brand_rules
                        (guideline_id, product_id, name, description, rule_type,
                         scope, parameters, severity, positive_tests, negative_tests,
                         is_active, owner_approved, review_status, readiness_class,
                         checking_method, source_document_id, source_page, source_quote)
                    VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 'APPROVED',
                            'CODE_CHECKABLE', ?, ?, ?, ?)
                    """,
                    (
                        product_id, content[:120], content, kind,
                        json.dumps(scope, ensure_ascii=False),
                        json.dumps(parameters, ensure_ascii=False), severity,
                        rule["positive_tests"], rule["negative_tests"],
                        f"Deterministic Python predicate: {kind}",
                        entry["document_id"], entry["source_page"], entry["source_quote"],
                    ),
                )
                linked_type, linked_id = "brand_rule", int(cursor.lastrowid)
                _record_rule_history(conn, linked_id, actor)
            elif classification == "AI_ASSISTED":
                source_ref = f"{entry['filename']} · page {entry['source_page']}"
                cursor = conn.execute(
                    """
                    INSERT INTO guidelines
                        (product_id, title, content, source_ref, version, is_active,
                         source_type, section_ref, market_scope, channel_scope,
                         content_type, readiness_class, source_document_id, source_page,
                         source_quote)
                    VALUES (?, ?, ?, ?, '1.0', 1, 'knowledge_onboarding', ?, ?, ?, ?,
                            'AI_ASSISTED', ?, ?, ?)
                    """,
                    (
                        product_id, f"Approved AI-assisted rule #{entry_id}: {content[:100]}",
                        content, source_ref, f"Page {entry['source_page']}",
                        market, channel, content_type, entry["document_id"],
                        entry["source_page"], entry["source_quote"],
                    ),
                )
                linked_type, linked_id = "guideline", int(cursor.lastrowid)
            else:
                # Human-judgement guidance is retained as an approved record only;
                # it is deliberately not copied into deterministic or AI guidance.
                linked_type, linked_id = "human_judgement", entry_id

            approved_version = conn.execute(
                "SELECT COALESCE(MAX(version_number), 1) AS n "
                "FROM knowledge_entry_versions WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()["n"]
            conn.execute(
                """
                UPDATE knowledge_entries
                SET status = 'APPROVED', approved_by = ?,
                    approved_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                    approved_version = ?, linked_record_type = ?, linked_record_id = ?
                WHERE id = ?
                """,
                (actor, approved_version, linked_type, linked_id, entry_id),
            )
            conn.execute(
                "INSERT INTO knowledge_decisions (entry_id, decision, actor, note) "
                "VALUES (?, 'APPROVE', ?, ?)",
                (entry_id, actor, f"{classification}: {entry['rule_checking_method']}"),
            )
            history_version = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 AS next "
                "FROM rule_readiness_history WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()["next"]
            conn.execute(
                """
                INSERT INTO rule_readiness_history
                    (entry_id, version_number, event_type, actor,
                     readiness_classification, checking_method, rule_type,
                     rule_parameters, rule_scope, rule_severity,
                     positive_tests, negative_tests,
                     source_document_id, source_page, source_quote,
                     source_quote_verified, source_support_confirmed,
                     source_support_confirmed_by, source_support_confirmed_at)
                 VALUES (?, ?, 'APPROVED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id, history_version, actor, classification,
                    entry["rule_checking_method"], entry["rule_type"],
                    entry["rule_parameters"], entry["rule_scope"],
                    entry["rule_severity"], entry["rule_positive_tests"],
                    entry["rule_negative_tests"], entry["document_id"],
                    entry["source_page"], entry["source_quote"],
                    entry["source_quote_verified"], entry["source_support_confirmed"],
                    entry["source_support_confirmed_by"], entry["source_support_confirmed_at"],
                ),
            )
            conn.commit()
            return {"classification": classification, "linked_record_type": linked_type, "linked_record_id": linked_id}
        except Exception:
            conn.rollback()
            raise


def _record_rule_history(conn, rule_id: int, actor: str) -> None:
    row = conn.execute("SELECT * FROM brand_rules WHERE id = ?", (rule_id,)).fetchone()
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
        VALUES (?, ?, 'APPROVED', ?, ?, ?, ?, ?)
        """,
        (
            rule_id, version, actor, json.dumps(snapshot, ensure_ascii=False),
            row["source_document_id"], row["source_page"], row["source_quote"],
        ),
    )
