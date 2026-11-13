from __future__ import annotations

import hashlib
import io
import json
import zipfile

import fitz
import pytest

from services.audit_chain_service import GENESIS_HASH, build_chain_entry
from services.audit_export_service import (
    AuditExportError,
    MAX_BUNDLE_SIZE,
    MAX_JSON_DEPTH,
    MAX_MEMBER_SIZE,
    MAX_TOTAL_UNCOMPRESSED_SIZE,
    _bundled_font_path,
    _find_unicode_font,
    _font_candidates,
    build_events_document,
    create_audit_export,
    generate_audit_pdf,
    serialize_events_document,
    verify_audit_export,
)


def _entries(count: int = 2) -> list[dict]:
    entries = []
    previous = GENESIS_HASH
    for sequence in range(1, count + 1):
        payload = {
            "actor": "reviewer",
            "actor_role_at_event": None,
            "actor_role_provenance": "NO_TRUSTED_CONTEXT",
            "created_at": f"2026-11-13T00:00:{sequence:02d}Z",
            "draft_id": "7",
            "event_id": str(100 + sequence),
            "event_type": "COMMENT",
            "finding_id": None,
            "format": "brl.review-event.v1",
            "note": f"Review note {sequence}",
            "previous_hash": previous,
            "review_id": "11",
            "sequence": sequence,
        }
        entry = build_chain_entry(payload)
        entries.append(entry)
        previous = entry["event_hash"]
    return entries


def _bundle_files(bundle: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _repack(files: dict[str, bytes]) -> bytes:
    return _repack_items(list(files.items()))


def _repack_items(items: list[tuple[str, bytes]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in items:
            archive.writestr(name, value)
    return stream.getvalue()


def _update_manifest_hashes(files: dict[str, bytes]) -> None:
    manifest = json.loads(files["export-manifest.json"])
    manifest["audit_pdf_sha256"] = hashlib.sha256(files["audit.pdf"]).hexdigest()
    manifest["events_json_sha256"] = hashlib.sha256(files["events.json"]).hexdigest()
    files["export-manifest.json"] = json.dumps(manifest).encode()


def _presentation_pdf(document: dict, *, order=None, wrong_actor_sequence=None) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    export = document["export"]
    lines = [
        f"Draft ID: {export['draft_id']}",
        f"Event count: {export['event_count']}",
        f"Final chain hash: {export['final_chain_hash']}",
        f"Export timestamp (UTC): {export['exported_at_utc']}",
        f"Trust level: {export['trust_level']}",
    ]
    events = document["events"]
    for index in order if order is not None else range(len(events)):
        entry = events[index]
        payload = entry["payload"]
        sequence = payload["sequence"]
        event_hash = entry["event_hash"]
        actor = payload["actor"]
        if wrong_actor_sequence == sequence:
            actor = events[(index + 1) % len(events)]["payload"]["actor"]
        lines.extend(
            [
                f"[BRL-AUDIT-EVENT-START:{sequence}:{event_hash}]",
                f"Sequence: {sequence}",
                f"Event ID: {payload['event_id']}",
                f"Actor: {actor}",
                f"Actor provenance: {payload['actor_role_provenance']}",
                "Actor role at event: Unrecorded",
                f"Action: {payload['event_type']}",
                f"Timestamp: {payload['created_at']}",
                f"Review ID: {payload['review_id']}",
                f"Finding ID: {payload['finding_id'] or 'None'}",
                f"Note: {payload['note'] or 'None'}",
                f"Event hash: {event_hash}",
                f"[BRL-AUDIT-EVENT-END:{sequence}:{event_hash}]",
            ]
        )
    for index, line in enumerate(lines):
        page.insert_text((36, 36 + index * 14), line, fontname="helv", fontsize=8)
    result = pdf.tobytes()
    pdf.close()
    return result


def test_export_bundle_has_exact_members_and_non_circular_manifest():
    bundle = create_audit_export(
        _entries(),
        draft_id="7",
        export_id="4dc6b4d2-8ce7-4af4-923e-848a36f11f45",
        exported_at_utc="2026-11-13T00:10:00Z",
    )
    files = _bundle_files(bundle)
    assert set(files) == {"audit.pdf", "events.json", "export-manifest.json"}

    events = json.loads(files["events.json"])
    manifest = json.loads(files["export-manifest.json"])
    assert set(events) == {"format", "export", "events"}
    assert set(events["export"]) == {
        "export_id",
        "exported_at_utc",
        "trust_level",
        "draft_id",
        "asset_identity",
        "hash_algorithm",
        "canonicalization",
        "chain_format",
        "event_count",
        "first_sequence",
        "last_sequence",
        "genesis_hash",
        "final_chain_hash",
    }
    assert "events_json_sha256" not in events
    assert manifest == {
        "format": "brl.audit-export-manifest.v1",
        "export_id": events["export"]["export_id"],
        "audit_pdf_sha256": hashlib.sha256(files["audit.pdf"]).hexdigest(),
        "events_json_sha256": hashlib.sha256(files["events.json"]).hexdigest(),
    }
    assert verify_audit_export(bundle).overall_status == "PASS"


def test_asset_identity_available_and_unavailable():
    asset = {"asset_id": "9", "source_type": "video", "sha256": "a" * 64}
    with_asset = build_events_document(
        _entries(1), draft_id="7", asset_identity=asset
    )
    assert with_asset["export"]["asset_identity"] == asset
    without_asset = build_events_document(_entries(1), draft_id="7")
    assert without_asset["export"]["asset_identity"] is None

    bundle = create_audit_export(_entries(1), draft_id="7", asset_identity=asset)
    result = verify_audit_export(bundle)
    assert result.checks["pdf_scope_count"] == "PASS"


def test_export_rejects_asset_fields_that_could_leak_file_paths():
    with pytest.raises(AuditExportError, match="exactly"):
        build_events_document(
            _entries(1),
            draft_id="7",
            asset_identity={
                "asset_id": "9",
                "source_type": "video",
                "sha256": "a" * 64,
                "file_path": "private/raw.mp4",
            },
        )


def test_manifest_file_tampering_fails_detached_hash_validation():
    files = _bundle_files(create_audit_export(_entries(1), draft_id="7"))
    events = json.loads(files["events.json"])
    manifest = json.loads(files["export-manifest.json"])
    manifest["events_json_sha256"] = "0" * 64
    files["export-manifest.json"] = json.dumps(manifest).encode()
    result = verify_audit_export(_repack(files))
    assert result.checks["file_hashes"] == "FAIL"

    files = _bundle_files(create_audit_export(_entries(1), draft_id="7"))
    files["events.json"] += b" "
    result = verify_audit_export(_repack(files))
    assert result.checks["file_hashes"] == "FAIL"


def test_event_and_manifest_tampering_are_reported():
    files = _bundle_files(create_audit_export(_entries(1), draft_id="7"))
    events = json.loads(files["events.json"])
    events["export"]["event_count"] = 2
    files["events.json"] = json.dumps(events).encode()
    manifest = json.loads(files["export-manifest.json"])
    manifest["events_json_sha256"] = hashlib.sha256(files["events.json"]).hexdigest()
    files["export-manifest.json"] = json.dumps(manifest).encode()
    result = verify_audit_export(_repack(files))
    assert result.checks["events_schema"] == "FAIL"


def test_pdf_and_json_scope_count_and_event_content_agree():
    document = build_events_document(
        _entries(1),
        draft_id="7",
        export_id="4dc6b4d2-8ce7-4af4-923e-848a36f11f45",
        exported_at_utc="2026-11-13T00:10:00Z",
        asset_identity={"asset_id": "9", "source_type": "video", "sha256": "a" * 64},
    )
    pdf_bytes = generate_audit_pdf(document)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        text = " ".join(page.get_text() for page in pdf)
    text = " ".join(text.replace("\xa0", " ").replace("\xad", "-").split())
    assert "Draft ID: 7" in text
    assert "Asset ID: 9" in text
    assert "Event count: 1" in text
    assert f"Final chain hash: {document['export']['final_chain_hash']}" in text
    assert "Sequence: 1" in text
    assert "Actor provenance: NO_TRUSTED_CONTEXT" in text
    assert "Action: COMMENT" in text
    assert "Note: Review note 1" in text


def test_unicode_pdf_rendering_and_multi_page_pagination():
    entries = _entries(24)
    for index, entry in enumerate(entries, start=1):
        entry["payload"]["note"] = f"Résumé — crème brûlée, event {index}. " + (
            "Long evidence note " * 24
        )
        entries[index - 1] = build_chain_entry(entry["payload"])
        if index < len(entries):
            entries[index]["payload"]["previous_hash"] = entries[index - 1]["event_hash"]
            entries[index] = build_chain_entry(entries[index]["payload"])

    bundle = create_audit_export(entries, draft_id="7")
    files = _bundle_files(bundle)
    with fitz.open(stream=files["audit.pdf"], filetype="pdf") as pdf:
        assert pdf.page_count > 1
        rendered_text = " ".join(page.get_text() for page in pdf)
    rendered_text = rendered_text.replace("\xa0", " ").replace("\xad", "-")
    assert "Résumé" in rendered_text
    assert "crème brûlée" in rendered_text
    result = verify_audit_export(bundle)
    assert result.checks["pdf_scope_count"] == "PASS"
    assert result.checks["pdf_event_rendering"] == "PASS"


def test_bundled_font_is_discovered_and_loaded(monkeypatch):
    monkeypatch.delenv("BRL_AUDIT_FONT", raising=False)

    assert _font_candidates() == [_bundled_font_path()]
    fitz_module, font_path, font = _find_unicode_font("Zoë — Αθήνα — Москва")

    assert fitz_module is fitz
    assert font_path.is_file()
    assert font.has_glyph(ord("ë")) != 0
    assert font.has_glyph(ord("Α")) != 0
    assert font.has_glyph(ord("М")) != 0


def test_unicode_actor_role_and_note_render_with_bundled_font(monkeypatch):
    monkeypatch.delenv("BRL_AUDIT_FONT", raising=False)
    payload = _entries(1)[0]["payload"]
    payload["actor"] = "Zoë — Αθήνα — Москва"
    payload["actor_role_at_event"] = "Revisión"
    payload["actor_role_provenance"] = "AUTHENTICATED"
    payload["note"] = "Résumé — έλεγχος — Проверка"
    entry = build_chain_entry(payload)

    bundle = create_audit_export([entry], draft_id="7")
    files = _bundle_files(bundle)
    with fitz.open(stream=files["audit.pdf"], filetype="pdf") as pdf:
        rendered_text = "\n".join(page.get_text() for page in pdf)
    events_document = json.loads(files["events.json"])

    assert "Zoë" in rendered_text and "Москва" in rendered_text
    assert "Revisión" in rendered_text
    assert "Résumé" in rendered_text and "Проверка" in rendered_text
    assert events_document["events"][0]["payload"]["actor"] == payload["actor"]
    assert events_document["events"][0]["payload"]["note"] == payload["note"]
    assert events_document["events"][0]["event_hash"] == entry["event_hash"]
    assert verify_audit_export(bundle).overall_status == "PASS"


def test_unsupported_bundled_glyph_fails_with_actionable_font_error(monkeypatch):
    monkeypatch.delenv("BRL_AUDIT_FONT", raising=False)
    payload = _entries(1)[0]["payload"]
    payload["actor"] = "\u6f22"
    entry = build_chain_entry(payload)
    document = build_events_document([entry], draft_id="7")

    with pytest.raises(
        AuditExportError,
        match=r"missing glyphs required by the audit text.*BRL_AUDIT_FONT",
    ):
        generate_audit_pdf(document)


def test_valid_font_override_is_selected_instead_of_bundled_font(
    monkeypatch, tmp_path
):
    override = tmp_path / "audit-font.ttf"
    override.write_bytes(_bundled_font_path().read_bytes())
    monkeypatch.setenv("BRL_AUDIT_FONT", str(override))

    assert _font_candidates() == [override]
    _fitz_module, selected_path, _font = _find_unicode_font("Zoë — Αθήνα")

    assert selected_path == override
    assert selected_path != _bundled_font_path()


def test_valid_font_override_renders_and_extracts_pdf_text(monkeypatch, tmp_path):
    override = tmp_path / "audit-font.ttf"
    override.write_bytes(_bundled_font_path().read_bytes())
    monkeypatch.setenv("BRL_AUDIT_FONT", str(override))
    payload = _entries(1)[0]["payload"]
    payload["actor"] = "Zoë — Αθήνα"
    payload["note"] = "Résumé — Проверка"
    entry = build_chain_entry(payload)
    document = build_events_document([entry], draft_id="7")

    pdf_bytes = generate_audit_pdf(document)

    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        extracted = "\n".join(page.get_text() for page in pdf)
        embedded_font_names = {
            font[3] for page in pdf for font in page.get_fonts(full=True)
        }
    assert "Zoë" in extracted
    assert "Αθήνα" in extracted
    assert "Résumé" in extracted
    assert "Проверка" in extracted
    assert "Noto Sans Regular" in embedded_font_names


def test_missing_bundled_font_fails_with_restore_instruction(monkeypatch, tmp_path):
    monkeypatch.delenv("BRL_AUDIT_FONT", raising=False)
    monkeypatch.setattr(
        "services.audit_export_service._bundled_font_path",
        lambda: tmp_path / "NotoSans-VF.ttf",
    )

    with pytest.raises(AuditExportError, match="Restore assets/fonts/NotoSans-VF.ttf"):
        _find_unicode_font("Audit")


@pytest.mark.parametrize("font_bytes", [b"not a font", b""])
def test_invalid_font_override_fails_clearly(monkeypatch, tmp_path, font_bytes):
    invalid_font = tmp_path / "invalid.ttf"
    invalid_font.write_bytes(font_bytes)
    monkeypatch.setenv("BRL_AUDIT_FONT", str(invalid_font))
    monkeypatch.setattr(
        "services.audit_export_service._bundled_font_path",
        lambda: pytest.fail("invalid override must not fall back to bundled font"),
    )

    assert _font_candidates() == [invalid_font]
    with pytest.raises(AuditExportError, match="Could not load BRL_AUDIT_FONT override"):
        _find_unicode_font("Audit")


def test_sensitive_fields_and_raw_media_are_not_exported():
    bundle = create_audit_export(_entries(1), draft_id="7")
    combined = b"\n".join(_bundle_files(bundle).values())
    assert b"raw_text" not in combined
    assert b"uploaded_file" not in combined
    assert b"raw video bytes" not in combined
    assert b"secret-token" not in combined


def test_export_metadata_is_exact_and_asset_absence_is_null():
    document = build_events_document(_entries(1), draft_id="7")
    serialized = serialize_events_document(document)
    decoded = json.loads(serialized)
    assert decoded["export"]["asset_identity"] is None
    assert decoded["export"]["trust_level"] == "SELF_CONTAINED_UNANCHORED"
    assert decoded["export"]["event_count"] == 1
    assert decoded["export"]["first_sequence"] == 1
    assert decoded["export"]["last_sequence"] == 1
    assert decoded["export"]["genesis_hash"] == GENESIS_HASH


def test_multiline_actor_and_note_are_rendered_as_escaped_single_line_values():
    payload = _entries(1)[0]["payload"]
    payload["actor"] = "reviewer\nAction: APPROVED\r\tFinding ID: 99"
    payload["note"] = "Needs review\nAction: APPROVED\r\tSequence: 99"
    entry = build_chain_entry(payload)
    bundle = create_audit_export([entry], draft_id="7")
    files = _bundle_files(bundle)
    with fitz.open(stream=files["audit.pdf"], filetype="pdf") as pdf:
        rendered = "\n".join(page.get_text() for page in pdf).replace("\xa0", " ")

    assert "reviewer\\nAction: APPROVED\\r\\tFinding ID: 99" in rendered
    assert "Needs review\\nAction: APPROVED\\r\\tSequence: 99" in rendered
    assert "Action: APPROVED" not in {
        line.strip() for line in rendered.splitlines()
    }
    assert verify_audit_export(bundle).overall_status == "PASS"


def test_malformed_zip_is_reported_without_raising():
    result = verify_audit_export(b"not a zip archive")
    assert result.checks["zip_contents"] == "FAIL"


def test_duplicate_zip_member_names_are_rejected():
    files = _bundle_files(create_audit_export(_entries(1), draft_id="7"))
    items = list(files.items()) + [("audit.pdf", files["audit.pdf"])]
    result = verify_audit_export(_repack_items(items))
    assert result.checks["zip_contents"] == "FAIL"
    assert "duplicate" in result.details["zip_contents"][0].lower()


def test_manifest_substitution_between_exports_fails_file_hash_validation():
    first = _bundle_files(create_audit_export(_entries(1), draft_id="7"))
    second = _bundle_files(create_audit_export(_entries(1), draft_id="7"))
    first["export-manifest.json"] = second["export-manifest.json"]

    result = verify_audit_export(_repack(first))

    assert result.checks["file_hashes"] == "FAIL"


def test_oversized_input_bundle_is_rejected_before_zip_processing():
    result = verify_audit_export(b"x" * (MAX_BUNDLE_SIZE + 1))
    assert result.checks["zip_contents"] == "FAIL"
    assert "maximum input size" in result.details["zip_contents"][0]


def test_oversized_decompressed_member_is_rejected():
    files = _bundle_files(create_audit_export(_entries(1), draft_id="7"))
    files["audit.pdf"] = b"a" * (MAX_MEMBER_SIZE + 1)

    result = verify_audit_export(_repack(files))

    assert result.checks["zip_contents"] == "FAIL"
    assert "maximum size" in result.details["zip_contents"][0]


def test_oversized_aggregate_decompressed_members_are_rejected():
    member = b"a" * (MAX_TOTAL_UNCOMPRESSED_SIZE // 3 + 1)
    bundle = _repack(
        {
            "audit.pdf": member,
            "events.json": member,
            "export-manifest.json": member,
        }
    )

    result = verify_audit_export(bundle)

    assert result.checks["zip_contents"] == "FAIL"
    assert "aggregate decompressed size" in result.details["zip_contents"][0]


@pytest.mark.parametrize(
    "events_json",
    [
        b"{",
        None,
    ],
)
def test_malformed_and_deeply_nested_json_are_reported(events_json):
    files = _bundle_files(create_audit_export(_entries(1), draft_id="7"))
    if events_json is None:
        document = json.loads(files["events.json"])
        nested = 0
        for _ in range(MAX_JSON_DEPTH):
            nested = [nested]
        document["unexpected_nested_value"] = nested
        files["events.json"] = json.dumps(document).encode()
    else:
        files["events.json"] = events_json
    _update_manifest_hashes(files)

    result = verify_audit_export(_repack(files))

    assert result.checks["events_schema"] == "FAIL"
    if events_json is None:
        assert "nesting" in result.details["events_schema"][0]


def test_very_long_note_exports_and_verifies_without_truncation():
    payload = _entries(1)[0]["payload"]
    payload["note"] = "Long evidence " * 1200
    entry = build_chain_entry(payload)

    bundle = create_audit_export([entry], draft_id="7")
    files = _bundle_files(bundle)
    result = verify_audit_export(bundle)

    assert len(files["events.json"]) > 10_000
    assert result.overall_status == "PASS"
    with fitz.open(stream=files["audit.pdf"], filetype="pdf") as pdf:
        assert pdf.page_count > 1


@pytest.mark.parametrize(
    "asset",
    [
        {"asset_id": "0", "source_type": "video", "sha256": "a" * 64},
        {"asset_id": "9", "source_type": " ", "sha256": "a" * 64},
        {"asset_id": "9", "source_type": "video", "sha256": "A" * 64},
        {"asset_id": 9, "source_type": "video", "sha256": "a" * 64},
        {"asset_id": "1" * 10_000, "source_type": "video", "sha256": "a" * 64},
    ],
)
def test_invalid_asset_identity_is_rejected(asset):
    with pytest.raises(AuditExportError):
        build_events_document(_entries(1), draft_id="7", asset_identity=asset)


def test_empty_chain_export_verifies_with_genesis_root():
    bundle = create_audit_export([], draft_id="7")

    result = verify_audit_export(bundle)

    assert result.overall_status == "PASS"
    assert result.chain is not None
    assert result.chain.verified_entries == 0


def test_pdf_verifier_rejects_reordered_event_blocks():
    entries = _entries(2)
    document = build_events_document(
        entries,
        draft_id="7",
        export_id="4dc6b4d2-8ce7-4af4-923e-848a36f11f45",
        exported_at_utc="2026-11-13T00:10:00Z",
    )
    files = _bundle_files(
        create_audit_export(
            entries,
            draft_id="7",
            export_id=document["export"]["export_id"],
            exported_at_utc=document["export"]["exported_at_utc"],
        )
    )
    files["audit.pdf"] = _presentation_pdf(document, order=[1, 0])
    _update_manifest_hashes(files)

    result = verify_audit_export(_repack(files))

    assert result.checks["pdf_event_rendering"] == "FAIL"
    assert "out of sequence" in result.details["pdf_event_rendering"][0]


def test_pdf_verifier_keeps_event_fields_associated_with_their_block():
    entries = _entries(2)
    previous_hash = GENESIS_HASH
    distinct_entries = []
    for sequence, actor in enumerate(("alice", "bob"), start=1):
        payload = dict(entries[sequence - 1]["payload"])
        payload["actor"] = actor
        payload["previous_hash"] = previous_hash
        entry = build_chain_entry(payload)
        distinct_entries.append(entry)
        previous_hash = entry["event_hash"]
    document = build_events_document(distinct_entries, draft_id="7")
    files = _bundle_files(create_audit_export(distinct_entries, draft_id="7"))
    files["audit.pdf"] = _presentation_pdf(document, wrong_actor_sequence=1)
    _update_manifest_hashes(files)

    result = verify_audit_export(_repack(files))

    assert result.checks["pdf_event_rendering"] == "FAIL"
    assert "event block 1" in result.details["pdf_event_rendering"][0]
