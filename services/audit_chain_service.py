"""Canonical review-event hash-chain primitives and offline verification."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

import rfc8785


DOMAIN_SEPARATOR = b"BRL-REVIEW-EVENT-CHAIN\x00v1\x00"
GENESIS_HASH = "0" * 64
CHAIN_FORMAT = "brl.review-event.v1"
HASH_ALGORITHM = "SHA-256"
CANONICALIZATION = "RFC8785-JCS"

ROLE_PROVENANCE = frozenset(
    {
        "AUTHENTICATED",
        "DEMO_SIMULATED",
        "NO_TRUSTED_CONTEXT",
        "LEGACY_UNRECORDED",
    }
)
EVENT_TYPES = frozenset(
    {
        "SUBMITTED",
        "APPROVED",
        "CHANGES_REQUESTED",
        "ESCALATED",
        "OVERRIDE",
        "COMMENT",
    }
)
PAYLOAD_FIELDS = frozenset(
    {
        "actor",
        "actor_role_at_event",
        "actor_role_provenance",
        "created_at",
        "draft_id",
        "event_id",
        "event_type",
        "finding_id",
        "format",
        "note",
        "previous_hash",
        "review_id",
        "sequence",
    }
)
SAFE_INTEGER_MAX = (1 << 53) - 1
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_ID_RE = re.compile(r"[1-9][0-9]*\Z")
_UTC_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)


class AuditChainError(ValueError):
    """Raised when an event cannot be represented in the audit-chain format."""


@dataclass(frozen=True)
class VerificationCheck:
    status: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChainVerification:
    checks: Mapping[str, VerificationCheck]
    verified_entries: int

    @property
    def overall_status(self) -> str:
        statuses = {check.status for check in self.checks.values()}
        if "FAIL" in statuses:
            return "FAIL"
        if "NOT_CHECKED" in statuses:
            return "INCOMPLETE"
        return "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "verified_entries": self.verified_entries,
            "checks": {
                name: {"status": check.status, "details": list(check.details)}
                for name, check in self.checks.items()
            },
        }


def _validate_json_value(value: Any, path: str = "$") -> None:
    if value is None or type(value) in (str, bool):
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise AuditChainError(f"{path} contains invalid Unicode.") from exc
        return
    if type(value) is int:
        if not -SAFE_INTEGER_MAX <= value <= SAFE_INTEGER_MAX:
            raise AuditChainError(f"{path} is outside the safe integer range.")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise AuditChainError(f"{path} must be a finite number.")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise AuditChainError(f"{path} contains a non-string object key.")
            _validate_json_value(key, f"{path}.<key>")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise AuditChainError(f"{path} has unsupported type {type(value).__name__}.")


def canonicalize_json(value: Any) -> bytes:
    """Serialize strict JSON-compatible values with RFC 8785 JCS."""
    _validate_json_value(value)
    try:
        return rfc8785.dumps(value)
    except rfc8785.CanonicalizationError as exc:
        raise AuditChainError(f"JCS canonicalization failed: {exc}") from exc


def _validate_safe_id(value: Any, field: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise AuditChainError(f"{field} must be a positive decimal-string ID.")
    if len(value) > len(str(SAFE_INTEGER_MAX)) or int(value) > SAFE_INTEGER_MAX:
        raise AuditChainError(f"{field} exceeds the safe integer range.")


def _validate_timestamp(value: Any, field: str) -> None:
    if type(value) is not str or not _UTC_TIMESTAMP_RE.fullmatch(value):
        raise AuditChainError(f"{field} must be an ISO-8601 UTC timestamp.")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AuditChainError(f"{field} is not a valid timestamp.") from exc


def validate_event_payload(payload: Any) -> dict[str, Any]:
    """Validate the exact version-1 event payload and return it unchanged."""
    if type(payload) is not dict:
        raise AuditChainError("Event payload must be a JSON object.")
    if set(payload) != PAYLOAD_FIELDS:
        missing = sorted(PAYLOAD_FIELDS - set(payload))
        extra = sorted(set(payload) - PAYLOAD_FIELDS)
        raise AuditChainError(f"Invalid event fields; missing={missing}, extra={extra}.")

    if payload["format"] != CHAIN_FORMAT:
        raise AuditChainError("Unsupported event chain format.")
    _validate_safe_id(payload["draft_id"], "draft_id")
    _validate_safe_id(payload["event_id"], "event_id")
    _validate_safe_id(payload["review_id"], "review_id")
    _validate_safe_id(payload["finding_id"], "finding_id", nullable=True)
    sequence = payload["sequence"]
    if type(sequence) is not int or not 1 <= sequence <= SAFE_INTEGER_MAX:
        raise AuditChainError("sequence must be a positive safe integer.")
    _validate_timestamp(payload["created_at"], "created_at")
    if type(payload["actor"]) is not str or not payload["actor"].strip():
        raise AuditChainError("actor must be a non-empty string.")
    if type(payload["event_type"]) is not str or payload["event_type"] not in EVENT_TYPES:
        raise AuditChainError("event_type is not supported.")
    if payload["note"] is not None and type(payload["note"]) is not str:
        raise AuditChainError("note must be a string or null.")
    if (
        type(payload["actor_role_provenance"]) is not str
        or payload["actor_role_provenance"] not in ROLE_PROVENANCE
    ):
        raise AuditChainError("actor_role_provenance is not supported.")

    role = payload["actor_role_at_event"]
    provenance = payload["actor_role_provenance"]
    if provenance in {"AUTHENTICATED", "DEMO_SIMULATED"}:
        if type(role) is not str or not role.strip():
            raise AuditChainError(f"{provenance} requires a non-empty role.")
    elif role is not None:
        raise AuditChainError(f"{provenance} requires actor_role_at_event to be null.")

    if type(payload["previous_hash"]) is not str or not _HASH_RE.fullmatch(
        payload["previous_hash"]
    ):
        raise AuditChainError("previous_hash must be 64 lowercase hexadecimal characters.")
    canonicalize_json(payload)
    return payload


def hash_event_payload(payload: Mapping[str, Any]) -> str:
    """Return the SHA-256 hash for a validated canonical event payload."""
    valid_payload = validate_event_payload(dict(payload))
    digest = hashlib.sha256(DOMAIN_SEPARATOR + canonicalize_json(valid_payload))
    return digest.hexdigest()


def build_chain_entry(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a payload and return its canonical event-hash entry."""
    valid_payload = validate_event_payload(dict(payload))
    return {"payload": valid_payload, "event_hash": hash_event_payload(valid_payload)}


def _check(status: str, *details: str) -> VerificationCheck:
    return VerificationCheck(status, tuple(details))


def verify_chain(
    entries: Sequence[Any],
    *,
    expected_draft_id: str | None = None,
    expected_event_count: int | None = None,
    expected_final_chain_hash: str | None = None,
) -> ChainVerification:
    """Verify supplied entries without opening or modifying any database."""
    issues: dict[str, list[str]] = {
        "entry_schema": [],
        "event_hashes": [],
        "sequence": [],
        "predecessors": [],
        "unique_ids": [],
        "draft_scope": [],
        "role_provenance": [],
        "event_count": [],
        "final_chain_hash": [],
    }
    if not isinstance(entries, (list, tuple)):
        return ChainVerification(
            {"entry_schema": _check("FAIL", "entries must be a list or tuple.")}, 0
        )

    seen_sequences: set[int] = set()
    seen_event_ids: set[str] = set()
    verified = 0
    scope_draft_id = expected_draft_id
    previous_claimed_hash = GENESIS_HASH
    last_claimed_hash = GENESIS_HASH
    last_computed_hash = GENESIS_HASH

    if expected_draft_id is not None:
        try:
            _validate_safe_id(expected_draft_id, "expected_draft_id")
        except AuditChainError as exc:
            issues["draft_scope"].append(str(exc))

    for index, entry in enumerate(entries, start=1):
        if type(entry) is not dict or set(entry) != {"payload", "event_hash"}:
            issues["entry_schema"].append(f"Entry {index} must contain payload and event_hash.")
            continue
        payload = entry["payload"]
        if (
            type(payload) is dict
            and (
                type(payload.get("actor_role_provenance")) is not str
                or payload.get("actor_role_provenance") not in ROLE_PROVENANCE
            )
        ):
            issues["role_provenance"].append(
                f"Entry {index} has invalid actor_role_provenance."
            )
        claimed_hash = entry["event_hash"]
        try:
            validate_event_payload(payload)
        except AuditChainError as exc:
            issues["entry_schema"].append(f"Entry {index}: {exc}")
            continue

        sequence = payload["sequence"]
        event_id = payload["event_id"]
        draft_id = payload["draft_id"]
        if scope_draft_id is None:
            scope_draft_id = draft_id
        elif draft_id != scope_draft_id:
            issues["draft_scope"].append(
                f"Entry {index} belongs to draft {draft_id}, expected {scope_draft_id}."
            )
        if sequence in seen_sequences or event_id in seen_event_ids:
            issues["unique_ids"].append(f"Entry {index} duplicates a sequence or event_id.")
        seen_sequences.add(sequence)
        seen_event_ids.add(event_id)
        if sequence != index:
            issues["sequence"].append(
                f"Entry at position {index} has sequence {sequence}; expected {index}."
            )
        if payload["previous_hash"] != previous_claimed_hash:
            issues["predecessors"].append(f"Entry {index} has a broken predecessor link.")
        if index == 1 and payload["previous_hash"] != GENESIS_HASH:
            issues["predecessors"].append("First entry does not use the per-draft genesis.")
        try:
            computed_hash = hash_event_payload(payload)
        except AuditChainError as exc:
            issues["event_hashes"].append(f"Entry {index}: {exc}")
            computed_hash = ""
        if type(claimed_hash) is not str or not _HASH_RE.fullmatch(claimed_hash):
            issues["event_hashes"].append(f"Entry {index} has a malformed event_hash.")
        elif computed_hash and claimed_hash != computed_hash:
            issues["event_hashes"].append(f"Entry {index} event_hash does not match its payload.")
        else:
            verified += 1

        previous_claimed_hash = claimed_hash if type(claimed_hash) is str else ""
        last_claimed_hash = previous_claimed_hash
        last_computed_hash = computed_hash

    if len(seen_sequences) != len(entries):
        issues["sequence"].append("One or more entries have no valid unique sequence.")
    if expected_event_count is not None:
        if type(expected_event_count) is not int or expected_event_count < 0:
            issues["event_count"].append("expected_event_count must be a non-negative integer.")
        elif len(entries) != expected_event_count:
            issues["event_count"].append(
                f"Received {len(entries)} entries; expected {expected_event_count}."
            )
    expected_root = expected_final_chain_hash
    if expected_root is not None and (
        type(expected_root) is not str or not _HASH_RE.fullmatch(expected_root)
    ):
        issues["final_chain_hash"].append("Expected final chain hash is malformed.")
    elif expected_root is not None and expected_root != last_claimed_hash:
        issues["final_chain_hash"].append("Final chain hash does not match the expected root.")
    if last_claimed_hash != last_computed_hash:
        issues["final_chain_hash"].append(
            "Final entry hash is not equal to its recomputed payload hash."
        )
    if not entries and expected_root not in (None, GENESIS_HASH):
        issues["final_chain_hash"].append("Empty chain root must be the genesis hash.")

    checks = {
        name: _check("FAIL" if errors else "PASS", *errors)
        for name, errors in issues.items()
    }
    return ChainVerification(checks, verified)
