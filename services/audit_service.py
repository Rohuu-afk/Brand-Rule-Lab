"""Tamper-evident audit events and verifiable export helpers."""

from __future__ import annotations

import hashlib
import json
import textwrap
from datetime import datetime, timezone
from typing import Any

from db.connection import get_connection
from models.enums import UserRole
from services.auth_service import AuthenticatedPrincipal, require_principal


_EXPORT_VERSION = 1
_EXPORT_ROLES = (UserRole.REVIEWER, UserRole.FACT_OWNER)


class AuditIntegrityError(RuntimeError):
    """Raised when an audit chain cannot be safely exported."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _event_payload(
    *,
    actor_user_id: int | None,
    actor_username: str,
    action: str,
    object_type: str,
    object_id: str | None,
    brand_id: int | None,
    details: dict[str, Any],
    previous_hash: str | None,
    created_at: str,
) -> dict[str, Any]:
    payload = {
        "actor_user_id": actor_user_id,
        "actor_username": actor_username,
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "details": details,
        "previous_hash": previous_hash,
        "created_at": created_at,
    }
    if brand_id is not None:
        payload["brand_id"] = brand_id
    return payload


def _hash_event(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def append_audit_event(
    conn,
    principal: AuthenticatedPrincipal,
    *,
    action: str,
    object_type: str,
    object_id: str | int | None = None,
    brand_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    """Append an event to an existing transaction using a validated principal."""
    actor = require_principal(principal, action="record an audit event")
    event_details = details or {}
    if not action.strip() or not object_type.strip():
        raise ValueError("Audit action and object type are required.")
    _canonical(event_details)
    if brand_id is not None and conn.execute(
        """
        SELECT 1 FROM brands b JOIN brand_memberships bm ON bm.brand_id = b.id
        WHERE b.id = ? AND b.is_active = 1 AND bm.user_id = ?
        """,
        (brand_id, actor.user_id),
    ).fetchone() is None:
        raise PermissionError("Active brand membership is required to record this audit event.")
    created_at = datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    previous = conn.execute(
        "SELECT event_hash FROM audit_events "
        + ("WHERE brand_id = ? " if brand_id is not None else "WHERE brand_id IS NULL ")
        + "ORDER BY id DESC LIMIT 1",
        (brand_id,) if brand_id is not None else (),
    ).fetchone()
    previous_hash = previous["event_hash"] if previous else None
    payload = _event_payload(
        actor_user_id=actor.user_id,
        actor_username=actor.username,
        action=action.strip(),
        object_type=object_type.strip(),
        object_id=None if object_id is None else str(object_id),
        brand_id=brand_id,
        details=event_details,
        previous_hash=previous_hash,
        created_at=created_at,
    )
    cursor = conn.execute(
        """
        INSERT INTO audit_events
            (actor_user_id, actor_username, action, object_type, object_id,
             brand_id, details_json, previous_hash, event_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor.user_id,
            actor.username,
            payload["action"],
            payload["object_type"],
            payload["object_id"],
            brand_id,
            _canonical(event_details),
            previous_hash,
            _hash_event(payload),
            created_at,
        ),
    )
    return int(cursor.lastrowid)


def _append_system_audit_event(
    conn,
    *,
    action: str,
    object_type: str,
    actor_user_id: int | None = None,
    actor_username: str = "system:authentication",
    object_id: str | int | None = None,
    brand_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    """Record state changes proven by one-time auth tokens without a session."""
    event_details = details or {}
    _canonical(event_details)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    previous = conn.execute(
        "SELECT event_hash FROM audit_events "
        + ("WHERE brand_id = ? " if brand_id is not None else "WHERE brand_id IS NULL ")
        + "ORDER BY id DESC LIMIT 1",
        (brand_id,) if brand_id is not None else (),
    ).fetchone()
    previous_hash = previous["event_hash"] if previous else None
    payload = _event_payload(
        actor_user_id=actor_user_id,
        actor_username=actor_username,
        action=action.strip(),
        object_type=object_type.strip(),
        object_id=None if object_id is None else str(object_id),
        brand_id=brand_id,
        details=event_details,
        previous_hash=previous_hash,
        created_at=created_at,
    )
    cursor = conn.execute(
        """
        INSERT INTO audit_events
            (actor_user_id, actor_username, action, object_type, object_id,
             brand_id, details_json, previous_hash, event_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_user_id, actor_username, payload["action"], payload["object_type"],
            payload["object_id"], brand_id, _canonical(event_details), previous_hash,
            _hash_event(payload), created_at,
        ),
    )
    return int(cursor.lastrowid)


def record_audit_event(
    principal: AuthenticatedPrincipal,
    *,
    action: str,
    object_type: str,
    object_id: str | int | None = None,
    brand_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    """Append one event using the currently authenticated actor identity."""
    actor = require_principal(principal, action="record an audit event")
    if not action.strip() or not object_type.strip():
        raise ValueError("Audit action and object type are required.")
    event_details = details or {}
    if not isinstance(event_details, dict):
        raise ValueError("Audit details must be a JSON object.")
    try:
        _canonical(event_details)
    except (TypeError, ValueError) as exc:
        raise ValueError("Audit details must be JSON serializable.") from exc

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event_id = append_audit_event(
            conn,
            actor,
            action=action,
            object_type=object_type,
            object_id=object_id,
            brand_id=brand_id,
            details=event_details,
        )
        conn.commit()
        return event_id


def _rows(brand_id: int | None = None) -> list[dict[str, Any]]:
    with get_connection() as conn:
        query = (
            "SELECT id, actor_user_id, actor_username, action, object_type, object_id, brand_id, "
            "details_json, previous_hash, event_hash, created_at "
            "FROM audit_events "
            + ("WHERE brand_id = ? " if brand_id is not None else "")
            + "ORDER BY id"
        )
        rows = conn.execute(query, (brand_id,) if brand_id is not None else ()).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        event = dict(row)
        try:
            event["details"] = json.loads(event.pop("details_json"))
        except (TypeError, json.JSONDecodeError):
            event["details"] = None
        events.append(event)
    return events


def verify_audit_chain(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify links and canonical hashes in an exported or queried event list."""
    previous_hash = None
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            return {"valid": False, "reason": f"Invalid record at index {index}."}
        details = record.get("details")
        if not isinstance(details, dict):
            return {"valid": False, "reason": f"Invalid details at record {index}."}
        payload = _event_payload(
            actor_user_id=record.get("actor_user_id"),
            actor_username=record.get("actor_username", ""),
            action=record.get("action", ""),
            object_type=record.get("object_type", ""),
            object_id=record.get("object_id"),
            brand_id=record.get("brand_id"),
            details=details,
            previous_hash=record.get("previous_hash"),
            created_at=record.get("created_at", ""),
        )
        expected = _hash_event(payload)
        if record.get("previous_hash") != previous_hash:
            return {"valid": False, "reason": f"Broken link at record {index}."}
        if record.get("event_hash") != expected:
            return {"valid": False, "reason": f"Payload changed at record {index}."}
        previous_hash = record.get("event_hash")
    return {
        "valid": True,
        "record_count": len(records),
        "head_hash": previous_hash,
    }


def export_audit_json(
    principal: AuthenticatedPrincipal, brand_id: int | None = None
) -> bytes:
    """Return a structured, independently verifiable JSON audit package."""
    if brand_id is not None:
        require_principal(principal, action="export audit records")
        with get_connection() as conn:
            membership = conn.execute(
                "SELECT bm.role FROM brand_memberships bm "
                "JOIN brands b ON b.id = bm.brand_id "
                "WHERE bm.brand_id = ? AND bm.user_id = ? AND b.is_active = 1",
                (brand_id, principal.user_id),
            ).fetchone()
        if membership is None or membership["role"] not in {
            "Brand Owner", "Reviewer", "Fact Owner"
        }:
            raise PermissionError("Your brand role cannot export audit records.")
    else:
        require_principal(principal, roles=_EXPORT_ROLES, action="export audit records")
    records = _rows(brand_id)
    verification = verify_audit_chain(records)
    if not verification["valid"]:
        raise AuditIntegrityError(
            "Audit chain integrity check failed; the export was not generated."
        )
    package = {
        "format": "brand-rule-lab-audit",
        "version": _EXPORT_VERSION,
        "records": records,
        "verification": verification,
    }
    return (_canonical(package) + "\n").encode("utf-8")


def verify_audit_export(data: bytes | str) -> dict[str, Any]:
    """Verify a previously exported JSON package without database access."""
    try:
        package = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        records = package["records"]
    except (TypeError, ValueError, UnicodeDecodeError, KeyError, json.JSONDecodeError):
        return {"valid": False, "reason": "Invalid audit export package."}
    if (
        not isinstance(package, dict)
        or package.get("format") != "brand-rule-lab-audit"
        or package.get("version") != _EXPORT_VERSION
        or not isinstance(records, list)
    ):
        return {"valid": False, "reason": "Unsupported audit export package."}
    verification = verify_audit_chain(records)
    if not verification["valid"]:
        return verification
    if package.get("verification") != verification:
        return {"valid": False, "reason": "Export verification metadata does not match its records."}
    return verification


def export_audit_pdf(
    principal: AuthenticatedPrincipal, brand_id: int | None = None
) -> bytes:
    """Create a readable PDF report from the same verified JSON export."""
    package = json.loads(export_audit_json(principal, brand_id).decode("utf-8"))
    verification = package["verification"]
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF export requires the installed PyMuPDF dependency.") from exc

    document = fitz.open()
    margin_x = 48
    margin_top = 48
    margin_bottom = 48
    font_size = 9
    line_height = 13
    page = document.new_page()

    def add_header(target_page, *, first_page: bool) -> float:
        title = "Brand Rule Lab — Audit Export" if first_page else "Brand Rule Lab — Audit Export (continued)"
        target_page.insert_text(
            (margin_x, margin_top), title, fontsize=12, fontname="helv"
        )
        y = margin_top + 20
        if first_page:
            summary = [
                f"Exported: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
                f"Records: {verification['record_count']}",
                f"Verification: VALID",
                f"Head hash: {verification.get('head_hash') or 'EMPTY'}",
                "",
            ]
            for line in summary:
                target_page.insert_text((margin_x, y), line, fontsize=font_size)
                y += line_height
        return y

    y = add_header(page, first_page=True)
    width_chars = 100
    for record in package["records"]:
        lines = [
            f"{record['created_at']} | {record['actor_username']} | "
            f"{record['action']} | {record['object_type']}:{record.get('object_id') or '-'}",
            f"Previous hash: {record.get('previous_hash') or 'EMPTY'}",
            f"Event hash: {record['event_hash']}",
            "",
        ]
        wrapped = [
            fragment
            for line in lines
            for fragment in (textwrap.wrap(line, width=width_chars) or [""])
        ]
        block_height = len(wrapped) * line_height
        if y + block_height > page.rect.height - margin_bottom:
            page = document.new_page()
            y = add_header(page, first_page=False)
        for line in wrapped:
            page.insert_text((margin_x, y), line, fontsize=font_size)
            y += line_height
    return document.tobytes()
