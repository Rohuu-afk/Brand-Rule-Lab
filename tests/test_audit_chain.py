from __future__ import annotations

import copy

import pytest

from services.audit_chain_service import (
    DOMAIN_SEPARATOR,
    GENESIS_HASH,
    AuditChainError,
    build_chain_entry,
    canonicalize_json,
    hash_event_payload,
    verify_chain,
)


def _payload(
    *,
    sequence: int = 1,
    event_id: str = "101",
    draft_id: str = "7",
    previous_hash: str = GENESIS_HASH,
    note: str | None = "A review note",
    role: str | None = None,
    provenance: str = "NO_TRUSTED_CONTEXT",
) -> dict:
    return {
        "actor": "reviewer",
        "actor_role_at_event": role,
        "actor_role_provenance": provenance,
        "created_at": "2026-11-13T00:00:00Z",
        "draft_id": draft_id,
        "event_id": event_id,
        "event_type": "COMMENT",
        "finding_id": None,
        "format": "brl.review-event.v1",
        "note": note,
        "previous_hash": previous_hash,
        "review_id": "11",
        "sequence": sequence,
    }


def _entries(count: int = 2, *, draft_id: str = "7") -> list[dict]:
    result = []
    previous = GENESIS_HASH
    for sequence in range(1, count + 1):
        entry = build_chain_entry(
            _payload(
                sequence=sequence,
                event_id=str(100 + sequence),
                draft_id=draft_id,
                previous_hash=previous,
                note=f"Note {sequence}",
            )
        )
        result.append(entry)
        previous = entry["event_hash"]
    return result


def test_rfc8785_number_vector():
    value = [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27]
    assert canonicalize_json(value) == (
        b"[333333333.3333333,1e+30,4.5,0.002,1e-27]"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, b"0"),
        (-0.0, b"0"),
        (1e30, b"1e+30"),
        (4.5, b"4.5"),
        (0.002, b"0.002"),
        (1e-27, b"1e-27"),
    ],
)
def test_rfc8785_appendix_b_number_vectors(value, expected):
    assert canonicalize_json(value) == expected


def test_rfc8785_key_order_unicode_escaping_and_null():
    value = {"z": "Café — 雪", "a": None, "quote": 'line\n"two"'}
    assert canonicalize_json(value) == (
        b'{"a":null,"quote":"line\\n\\"two\\"","z":"Caf\xc3\xa9 \xe2\x80\x94 '
        b'\xe9\x9b\xaa"}'
    )


def test_rfc8785_uses_utf16_code_unit_order_for_property_names():
    value = {"\ue000": "Private Use", "😀": "Emoji", "€": "Euro"}
    assert canonicalize_json(value) == (
        b'{"\xe2\x82\xac":"Euro","\xf0\x9f\x98\x80":"Emoji",'
        b'"\xee\x80\x80":"Private Use"}'
    )


@pytest.mark.parametrize(
    "value",
    [
        1 << 53,
        -(1 << 53),
        float("nan"),
        float("inf"),
        object(),
        (1, 2),
        {1: "not a string key"},
        "\ud800",
    ],
)
def test_jcs_rejects_unsafe_numbers_and_non_json_values(value):
    with pytest.raises(AuditChainError):
        canonicalize_json(value)


def test_nulls_are_explicit_and_deterministic_hash_is_stable():
    payload = _payload(note=None)
    assert b'"note":null' in canonicalize_json(payload)
    assert hash_event_payload(payload) == hash_event_payload(copy.deepcopy(payload))
    assert len(DOMAIN_SEPARATOR) > 0


def test_authenticated_and_simulated_role_provenance_are_explicit():
    authenticated = _payload(role="Reviewer", provenance="AUTHENTICATED")
    simulated = _payload(role="Creator", provenance="DEMO_SIMULATED")
    assert build_chain_entry(authenticated)["event_hash"]
    assert build_chain_entry(simulated)["event_hash"]
    with pytest.raises(AuditChainError, match="requires actor_role_at_event"):
        build_chain_entry(_payload(role="Reviewer", provenance="LEGACY_UNRECORDED"))
    with pytest.raises(AuditChainError, match="requires a non-empty role"):
        build_chain_entry(_payload(provenance="AUTHENTICATED"))


def test_valid_chain_checks_genesis_sequence_predecessors_and_root():
    entries = _entries()
    assert verify_chain(entries).overall_status == "PASS"
    result = verify_chain(
        entries,
        expected_draft_id="7",
        expected_event_count=2,
        expected_final_chain_hash=entries[-1]["event_hash"],
    )
    assert result.overall_status == "PASS"
    assert result.verified_entries == 2
    assert all(check.status == "PASS" for check in result.checks.values())


def test_invalid_genesis_and_broken_predecessor_are_reported():
    entries = _entries(1)
    entries[0]["payload"]["previous_hash"] = "f" * 64
    result = verify_chain(entries, expected_draft_id="7")
    assert result.checks["predecessors"].status == "FAIL"
    assert result.checks["event_hashes"].status == "FAIL"


def test_missing_sequence_duplicate_ids_and_reordering_are_reported():
    entries = _entries(3)

    missing = verify_chain([entries[0], entries[2]], expected_draft_id="7")
    assert missing.checks["sequence"].status == "FAIL"
    assert missing.checks["predecessors"].status == "FAIL"

    duplicate = copy.deepcopy(entries)
    duplicate[1]["payload"]["event_id"] = duplicate[0]["payload"]["event_id"]
    duplicate[1] = build_chain_entry(duplicate[1]["payload"])
    duplicate_result = verify_chain(duplicate, expected_draft_id="7")
    assert duplicate_result.checks["unique_ids"].status == "FAIL"

    reordered = verify_chain(entries[::-1], expected_draft_id="7")
    assert reordered.checks["sequence"].status == "FAIL"
    assert reordered.checks["predecessors"].status == "FAIL"


def test_payload_tampering_wrong_scope_count_and_root_are_reported():
    entries = _entries()
    tampered = copy.deepcopy(entries)
    tampered[0]["payload"]["note"] = "Modified"
    tampered_result = verify_chain(tampered, expected_draft_id="7")
    assert tampered_result.checks["event_hashes"].status == "FAIL"

    scope_result = verify_chain(entries, expected_draft_id="8")
    assert scope_result.checks["draft_scope"].status == "FAIL"

    count_result = verify_chain(entries, expected_event_count=3)
    assert count_result.checks["event_count"].status == "FAIL"

    root_result = verify_chain(entries, expected_final_chain_hash="f" * 64)
    assert root_result.checks["final_chain_hash"].status == "FAIL"


@pytest.mark.parametrize("expected_draft_id", [None, "7"])
def test_recomputed_mixed_draft_chain_fails_scope_verification(expected_draft_id):
    entries = _entries(1, draft_id="7")
    second_payload = _payload(
        sequence=2,
        event_id="102",
        draft_id="8",
        previous_hash=entries[0]["event_hash"],
        note="Validly recomputed but belongs to another draft",
    )
    entries.append(build_chain_entry(second_payload))

    result = verify_chain(entries, expected_draft_id=expected_draft_id)

    assert result.checks["event_hashes"].status == "PASS"
    assert result.checks["predecessors"].status == "PASS"
    assert result.checks["draft_scope"].status == "FAIL"
    assert result.overall_status == "FAIL"


def test_malformed_hash_and_extra_payload_field_are_rejected():
    malformed_hash = [{"payload": _payload(), "event_hash": "abc"}]
    assert verify_chain(malformed_hash).checks["event_hashes"].status == "FAIL"
    payload = _payload()
    payload["raw_text"] = "must not enter an event"
    with pytest.raises(AuditChainError, match="Invalid event fields"):
        build_chain_entry(payload)
    with pytest.raises(AuditChainError, match="safe integer range"):
        build_chain_entry(_payload(event_id="1" * 10_000))


def test_duplicate_sequence_and_invalid_role_provenance_are_reported():
    entries = _entries(2)
    duplicate = copy.deepcopy(entries)
    duplicate[1]["payload"]["sequence"] = 1
    duplicate[1] = build_chain_entry(duplicate[1]["payload"])
    assert verify_chain(duplicate).checks["unique_ids"].status == "FAIL"

    invalid_role = copy.deepcopy(entries)
    invalid_role[0]["payload"]["actor_role_provenance"] = "INFERRED"
    result = verify_chain(invalid_role)
    assert result.checks["role_provenance"].status == "FAIL"
    assert result.checks["entry_schema"].status == "FAIL"


def test_empty_chain_uses_genesis_root():
    result = verify_chain(
        [],
        expected_draft_id="7",
        expected_event_count=0,
        expected_final_chain_hash=GENESIS_HASH,
    )
    assert result.overall_status == "PASS"
