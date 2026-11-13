"""Self-contained, offline-verifiable audit exports."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import unicodedata
import uuid
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.audit_chain_service import (
    CANONICALIZATION,
    CHAIN_FORMAT,
    GENESIS_HASH,
    HASH_ALGORITHM,
    SAFE_INTEGER_MAX,
    ChainVerification,
    build_chain_entry,
    verify_chain,
)


EXPORT_FORMAT = "brl.audit-events-export.v1"
MANIFEST_FORMAT = "brl.audit-export-manifest.v1"
TRUST_LEVEL = "SELF_CONTAINED_UNANCHORED"
ZIP_MEMBERS = ("audit.pdf", "events.json", "export-manifest.json")
MAX_BUNDLE_SIZE = 25 * 1024 * 1024
MAX_MEMBER_SIZE = 20 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_SIZE = 40 * 1024 * 1024
MAX_JSON_DEPTH = 64
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_EXPORT_FIELDS = {
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


class AuditExportError(ValueError):
    """Raised when an export cannot be safely generated or verified."""


@dataclass(frozen=True)
class ExportVerification:
    checks: Mapping[str, str]
    details: Mapping[str, tuple[str, ...]]
    chain: ChainVerification | None = None

    @property
    def overall_status(self) -> str:
        if any(value == "FAIL" for value in self.checks.values()):
            return "FAIL"
        if any(value == "NOT_CHECKED" for value in self.checks.values()):
            return "INCOMPLETE"
        return "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "checks": {
                name: {
                    "status": status,
                    "details": list(self.details.get(name, ())),
                }
                for name, status in self.checks.items()
            },
            "chain": self.chain.to_dict() if self.chain else None,
        }


def _utc_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        raise AuditExportError("exported_at_utc must be an ISO-8601 UTC timestamp.")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AuditExportError("exported_at_utc is not a valid timestamp.") from exc
    return value


def _validate_asset_identity(value: Any) -> dict[str, str | None] | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"asset_id", "source_type", "sha256"}:
        raise AuditExportError(
            "asset_identity must be null or contain exactly asset_id, source_type, and sha256."
        )
    asset_id, source_type, digest = (
        value["asset_id"],
        value["source_type"],
        value["sha256"],
    )
    if asset_id is not None and (
        type(asset_id) is not str
        or not re.fullmatch(r"[1-9][0-9]*", asset_id)
        or len(asset_id) > len(str(SAFE_INTEGER_MAX))
        or int(asset_id) > SAFE_INTEGER_MAX
    ):
        raise AuditExportError("asset_identity.asset_id must be a safe decimal-string ID or null.")
    if source_type is not None and (
        type(source_type) is not str or not source_type.strip()
    ):
        raise AuditExportError("asset_identity.source_type must be a non-empty string or null.")
    if digest is not None and (
        type(digest) is not str or not _HASH_RE.fullmatch(digest)
    ):
        raise AuditExportError("asset_identity.sha256 must be lowercase SHA-256 hex or null.")
    return {"asset_id": asset_id, "source_type": source_type, "sha256": digest}


def _validate_export_id(value: str | None) -> str:
    if value is None:
        return str(uuid.uuid4())
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise AuditExportError("export_id must be a UUID.") from exc
    if str(parsed) != value.lower():
        raise AuditExportError("export_id must use canonical UUID syntax.")
    return value.lower()


def build_events_document(
    entries: Sequence[Mapping[str, Any]],
    *,
    draft_id: str,
    asset_identity: Mapping[str, Any] | None = None,
    export_id: str | None = None,
    exported_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the exact machine-readable export document from verified entries."""
    if type(draft_id) is not str or not re.fullmatch(r"[1-9][0-9]*", draft_id):
        raise AuditExportError("draft_id must be a positive decimal-string ID.")
    if len(draft_id) > len(str(SAFE_INTEGER_MAX)) or int(draft_id) > SAFE_INTEGER_MAX:
        raise AuditExportError("draft_id exceeds the safe integer range.")
    normalized_entries: list[dict[str, Any]] = []
    for entry in entries:
        if type(entry) is not dict or set(entry) != {"payload", "event_hash"}:
            raise AuditExportError("Each chain entry must contain payload and event_hash.")
        normalized_entries.append(
            build_chain_entry(entry["payload"])
        )
        if normalized_entries[-1]["event_hash"] != entry["event_hash"]:
            raise AuditExportError("Supplied event_hash does not match its payload.")

    root = normalized_entries[-1]["event_hash"] if normalized_entries else GENESIS_HASH
    chain_result = verify_chain(
        normalized_entries,
        expected_draft_id=draft_id,
        expected_event_count=len(normalized_entries),
        expected_final_chain_hash=root,
    )
    if chain_result.overall_status != "PASS":
        raise AuditExportError(f"Cannot export an invalid chain: {chain_result.to_dict()}")

    count = len(normalized_entries)
    if count > SAFE_INTEGER_MAX:
        raise AuditExportError("event_count exceeds the safe integer range.")
    return {
        "format": EXPORT_FORMAT,
        "export": {
            "export_id": _validate_export_id(export_id),
            "exported_at_utc": _utc_timestamp(exported_at_utc),
            "trust_level": TRUST_LEVEL,
            "draft_id": draft_id,
            "asset_identity": _validate_asset_identity(asset_identity),
            "hash_algorithm": HASH_ALGORITHM,
            "canonicalization": CANONICALIZATION,
            "chain_format": CHAIN_FORMAT,
            "event_count": count,
            "first_sequence": 1 if count else None,
            "last_sequence": count if count else None,
            "genesis_hash": GENESIS_HASH,
            "final_chain_hash": root,
        },
        "events": normalized_entries,
    }


def serialize_events_document(document: Mapping[str, Any]) -> bytes:
    """Serialize a machine-readable export document as UTF-8 JSON."""
    _validate_events_document(document)
    try:
        return json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise AuditExportError(f"Could not serialize events.json: {exc}") from exc


def _validate_events_document(document: Any) -> None:
    if type(document) is not dict or set(document) != {"format", "export", "events"}:
        raise AuditExportError("events.json must contain exactly format, export, and events.")
    if document["format"] != EXPORT_FORMAT:
        raise AuditExportError("Unsupported events.json format.")
    export = document["export"]
    if type(export) is not dict or set(export) != _EXPORT_FIELDS:
        raise AuditExportError("Export metadata fields do not match the required schema.")
    if export["trust_level"] != TRUST_LEVEL:
        raise AuditExportError("Unsupported export trust level.")
    if export["hash_algorithm"] != HASH_ALGORITHM:
        raise AuditExportError("Unsupported event hash algorithm.")
    if export["canonicalization"] != CANONICALIZATION:
        raise AuditExportError("Unsupported event canonicalization.")
    if export["chain_format"] != CHAIN_FORMAT:
        raise AuditExportError("Unsupported event chain format.")
    _validate_export_id(export["export_id"])
    _utc_timestamp(export["exported_at_utc"])
    _validate_asset_identity(export["asset_identity"])
    if type(document["events"]) is not list:
        raise AuditExportError("events must be a list.")
    count = export["event_count"]
    if type(count) is not int or count < 0 or count > SAFE_INTEGER_MAX:
        raise AuditExportError("event_count must be a non-negative safe integer.")
    if count != len(document["events"]):
        raise AuditExportError("event_count does not match events length.")
    if export["first_sequence"] != (1 if count else None):
        raise AuditExportError("first_sequence does not match event count.")
    if export["last_sequence"] != (count if count else None):
        raise AuditExportError("last_sequence does not match event count.")
    if export["genesis_hash"] != GENESIS_HASH:
        raise AuditExportError("genesis_hash is incorrect.")
    if type(export["draft_id"]) is not str or not re.fullmatch(
        r"[1-9][0-9]*", export["draft_id"]
    ):
        raise AuditExportError("draft_id must be a positive decimal-string ID.")
    if (
        len(export["draft_id"]) > len(str(SAFE_INTEGER_MAX))
        or int(export["draft_id"]) > SAFE_INTEGER_MAX
    ):
        raise AuditExportError("draft_id exceeds the safe integer range.")
    if type(export["final_chain_hash"]) is not str or not _HASH_RE.fullmatch(
        export["final_chain_hash"]
    ):
        raise AuditExportError("final_chain_hash is malformed.")
    chain = verify_chain(
        document["events"],
        expected_draft_id=export["draft_id"],
        expected_event_count=count,
        expected_final_chain_hash=export["final_chain_hash"],
    )
    if chain.overall_status != "PASS":
        raise AuditExportError(f"Event chain verification failed: {chain.to_dict()}")


def _bundled_font_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSans-VF.ttf"


def _font_candidates() -> list[Path]:
    configured = os.environ.get("BRL_AUDIT_FONT")
    if configured:
        return [Path(configured)]
    return [_bundled_font_path()]


def _find_unicode_font(text: str):
    try:
        import fitz
    except ImportError as exc:
        raise AuditExportError("PyMuPDF is required to generate audit.pdf.") from exc

    path = _font_candidates()[0]
    configured = bool(os.environ.get("BRL_AUDIT_FONT"))
    source = "BRL_AUDIT_FONT override" if configured else "bundled Noto Sans font"
    if not path.is_file():
        instruction = (
            "Set BRL_AUDIT_FONT to a valid Unicode font file."
            if configured
            else "Restore assets/fonts/NotoSans-VF.ttf from the repository."
        )
        raise AuditExportError(f"Required {source} is missing at {path}. {instruction}")

    try:
        font = fitz.Font(fontfile=str(path))
    except Exception as exc:
        raise AuditExportError(
            f"Could not load {source} at {path}: {type(exc).__name__}: {exc}"
        ) from exc

    has_glyph = getattr(font, "has_glyph", None)
    if has_glyph is None:
        raise AuditExportError(
            f"Loaded {source} at {path}, but PyMuPDF cannot verify Unicode glyph coverage."
        )
    missing = sorted(
        {
            char
            for char in text
            if not char.isspace() and has_glyph(ord(char)) == 0
        }
    )
    if missing:
        preview = "".join(missing[:12])
        raise AuditExportError(
            f"{source} at {path} is missing glyphs required by the audit text "
            f"({preview!r}); provide a Unicode font covering all required characters "
            "with BRL_AUDIT_FONT."
        )
    return fitz, path, font


def _wrap_line(text: str, *, font: Any, fontsize: float, max_width: float) -> list[str]:
    paragraphs = text.splitlines() or [""]
    output: list[str] = []
    for paragraph in paragraphs:
        words = paragraph.split()
        if not words:
            output.append("")
            continue
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if font.text_length(candidate, fontsize=fontsize) <= max_width:
                line = candidate
                continue
            if line:
                output.append(line)
                line = ""
            if font.text_length(word, fontsize=fontsize) <= max_width:
                line = word
                continue
            fragment = ""
            for char in word:
                if fragment and font.text_length(
                    fragment + char, fontsize=fontsize
                ) > max_width:
                    output.append(fragment)
                    fragment = char
                else:
                    fragment += char
            line = fragment
        if line:
            output.append(line)
    return output


def _pdf_display_text(value: str) -> str:
    escaped = []
    for char in value:
        if char == "\n":
            escaped.append("\\n")
        elif char == "\r":
            escaped.append("\\r")
        elif char == "\t":
            escaped.append("\\t")
        elif char == "\\":
            escaped.append("\\\\")
        elif char in "[]<>" or char in "\u2028\u2029":
            escaped.append(f"\\u{ord(char):04x}")
        elif unicodedata.category(char) == "Cc":
            escaped.append(f"\\u{ord(char):04x}")
        else:
            escaped.append(char)
    return "".join(escaped)


def generate_audit_pdf(document: Mapping[str, Any]) -> bytes:
    """Render a paginated, Unicode-capable PDF without persisting temporary files."""
    _validate_events_document(document)
    export = document["export"]
    lines: list[tuple[str, str]] = [
        ("Brand Rule Lab — Audit Export", "title"),
        (f"Draft ID: {export['draft_id']}", "body"),
    ]
    asset = export["asset_identity"]
    if asset is not None:
        lines.extend(
            [
                (f"Asset ID: {asset['asset_id'] or 'Unavailable'}", "body"),
                (
                    "Asset source type: "
                    + _pdf_display_text(asset["source_type"] or "Unavailable"),
                    "body",
                ),
                (f"Asset SHA-256: {asset['sha256'] or 'Unavailable'}", "body"),
            ]
        )
    lines.extend(
        [
            (f"Export timestamp (UTC): {export['exported_at_utc']}", "body"),
            (f"Trust level: {export['trust_level']}", "body"),
            (f"Event count: {export['event_count']}", "body"),
            (f"Final chain hash: {export['final_chain_hash']}", "body"),
            (
                "Trust limitation: this self-contained SHA-256 chain is not a digital "
                "signature and is not externally anchored.",
                "warning",
            ),
            ("Events", "section"),
        ]
    )
    for entry in document["events"]:
        payload = entry["payload"]
        sequence = payload["sequence"]
        event_hash = entry["event_hash"]
        lines.extend(
            [
                (f"[BRL-AUDIT-EVENT-START:{sequence}:{event_hash}]", "event"),
                (f"Sequence: {sequence}", "event"),
                (f"Event ID: {payload['event_id']}", "body"),
                (f"Actor: {_pdf_display_text(payload['actor'])}", "body"),
                (f"Actor provenance: {payload['actor_role_provenance']}", "body"),
                (
                    "Actor role at event: "
                    + _pdf_display_text(payload["actor_role_at_event"] or "Unrecorded"),
                    "body",
                ),
                (f"Action: {payload['event_type']}", "body"),
                (f"Timestamp: {payload['created_at']}", "body"),
                (f"Review ID: {payload['review_id']}", "body"),
                (f"Finding ID: {payload['finding_id'] or 'None'}", "body"),
                (
                    "Note: "
                    + _pdf_display_text(
                        payload["note"] if payload["note"] is not None else "None"
                    ),
                    "body",
                ),
                (f"Event hash: {event_hash}", "small"),
                (f"[BRL-AUDIT-EVENT-END:{sequence}:{event_hash}]", "event"),
                ("", "spacer"),
            ]
        )

    all_text = "\n".join(line for line, _style in lines)
    fitz, font_path, font = _find_unicode_font(all_text)
    doc = fitz.open()
    page_width, page_height = 595.0, 842.0
    margin_x, top, bottom = 48.0, 58.0, 48.0
    body_size, title_size = 9.5, 15.0
    leading = 13.5
    page = None
    y = top

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=page_width, height=page_height)
        y = top
        page.insert_text(
            (margin_x, 31),
            "Brand Rule Lab | Audit Export",
            fontname="AuditUnicode",
            fontfile=str(font_path),
            fontsize=8,
            color=(0.35, 0.39, 0.43),
        )

    def write_line(text: str, style: str) -> None:
        nonlocal y
        if style == "spacer":
            y += 6
            return
        size = title_size if style == "title" else body_size
        color = (0.12, 0.22, 0.33) if style in {"title", "section", "event"} else (0, 0, 0)
        if style == "warning":
            color = (0.55, 0.22, 0.12)
        if style == "small":
            size = 7.5
        weight_leading = 19 if style in {"title", "section", "event"} else leading
        max_width = page_width - 2 * margin_x
        wrapped = _wrap_line(text, font=font, fontsize=size, max_width=max_width)
        for wrapped_line in wrapped:
            if page is None or y + weight_leading > page_height - bottom:
                new_page()
            page.insert_text(
                (margin_x, y),
                wrapped_line,
                fontname="AuditUnicode",
                fontfile=str(font_path),
                fontsize=size,
                color=color,
            )
            y += weight_leading

    for line, style in lines:
        write_line(line, style)

    page_count = doc.page_count
    for page_number in range(page_count):
        footer_page = doc[page_number]
        footer_page.insert_text(
            (page_width - margin_x - 76, page_height - 22),
            f"Page {page_number + 1} of {page_count}",
            fontname="AuditUnicode",
            fontfile=str(font_path),
            fontsize=8,
            color=(0.35, 0.39, 0.43),
        )
    doc.set_metadata(
        {
            "title": "Brand Rule Lab Audit Export",
            "author": "Brand Rule Lab",
            "subject": (
                f"export_id={export['export_id']}; draft_id={export['draft_id']}; "
                f"event_count={export['event_count']}; "
                f"final_chain_hash={export['final_chain_hash']}"
            ),
            "keywords": "audit export, SHA-256, unanchored",
        }
    )
    pdf_bytes = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return pdf_bytes


def create_audit_export(
    entries: Sequence[Mapping[str, Any]],
    *,
    draft_id: str,
    asset_identity: Mapping[str, Any] | None = None,
    export_id: str | None = None,
    exported_at_utc: str | None = None,
) -> bytes:
    """Build audit.pdf, events.json, and a detached manifest in a ZIP bundle."""
    document = build_events_document(
        entries,
        draft_id=draft_id,
        asset_identity=asset_identity,
        export_id=export_id,
        exported_at_utc=exported_at_utc,
    )
    events_bytes = serialize_events_document(document)
    pdf_bytes = generate_audit_pdf(document)
    manifest = {
        "format": MANIFEST_FORMAT,
        "export_id": document["export"]["export_id"],
        "audit_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "events_json_sha256": hashlib.sha256(events_bytes).hexdigest(),
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("audit.pdf", pdf_bytes)
        archive.writestr("events.json", events_bytes)
        archive.writestr("export-manifest.json", manifest_bytes)
    return memory_file.getvalue()


def _strict_json_loads(data: bytes) -> Any:
    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AuditExportError(f"Duplicate JSON object key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                AuditExportError(f"Invalid JSON number: {value}")
            ),
        )
    except AuditExportError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise AuditExportError(f"Invalid UTF-8 JSON: {exc}") from exc

    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise AuditExportError(
                f"JSON nesting exceeds the maximum depth of {MAX_JSON_DEPTH}."
            )
        if type(current) is dict:
            stack.extend((item, depth + 1) for item in current.values())
        elif type(current) is list:
            stack.extend((item, depth + 1) for item in current)
    return value


def _normalized_pdf_text(pdf_bytes: bytes) -> str:
    try:
        import fitz

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            content_lines = []
            for page in doc:
                for line in page.get_text().splitlines():
                    normalized_line = line.translate({0x00AD: "-", 0x00A0: " "}).strip()
                    if normalized_line == "Brand Rule Lab | Audit Export":
                        continue
                    if re.fullmatch(r"Page \d+ of \d+", normalized_line):
                        continue
                    content_lines.append(line)
            return "\n".join(
                line.translate({0x00AD: "-", 0x00A0: " "}).strip()
                for line in content_lines
            )
    except Exception as exc:
        raise AuditExportError(f"Could not read audit.pdf: {type(exc).__name__}: {exc}") from exc


def _normalize_pdf_text(value: str) -> str:
    # MuPDF's ToUnicode extraction may represent embedded-font hyphens as soft hyphens.
    return " ".join(
        value.translate({0x00AD: "-", 0x00A0: " "}).split()
    )


def verify_audit_export(bundle: bytes) -> ExportVerification:
    """Verify ZIP members, detached hashes, event chain, and PDF/export agreement."""
    checks = {
        "zip_contents": "NOT_CHECKED",
        "manifest": "NOT_CHECKED",
        "file_hashes": "NOT_CHECKED",
        "events_schema": "NOT_CHECKED",
        "chain": "NOT_CHECKED",
        "pdf_scope_count": "NOT_CHECKED",
        "pdf_event_rendering": "NOT_CHECKED",
    }
    details: dict[str, tuple[str, ...]] = {}
    chain_result = None
    if type(bundle) is not bytes:
        checks["zip_contents"] = "FAIL"
        details["zip_contents"] = ("bundle must be bytes.",)
        return ExportVerification(checks, details)
    if len(bundle) > MAX_BUNDLE_SIZE:
        checks["zip_contents"] = "FAIL"
        details["zip_contents"] = (
            f"Bundle exceeds the maximum input size of {MAX_BUNDLE_SIZE} bytes.",
        )
        return ExportVerification(checks, details)

    try:
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise AuditExportError("ZIP contains duplicate member names.")
            if len(names) != len(ZIP_MEMBERS) or set(names) != set(ZIP_MEMBERS):
                raise AuditExportError(f"ZIP must contain exactly {ZIP_MEMBERS}.")
            total_declared_size = sum(info.file_size for info in infos)
            if any(info.file_size < 0 or info.file_size > MAX_MEMBER_SIZE for info in infos):
                raise AuditExportError(
                    f"ZIP member exceeds the maximum size of {MAX_MEMBER_SIZE} bytes."
                )
            if total_declared_size > MAX_TOTAL_UNCOMPRESSED_SIZE:
                raise AuditExportError(
                    "ZIP members exceed the maximum aggregate decompressed size of "
                    f"{MAX_TOTAL_UNCOMPRESSED_SIZE} bytes."
                )
            files = {}
            for info in infos:
                chunks = []
                member_size = 0
                with archive.open(info, "r") as member:
                    while True:
                        chunk = member.read(
                            min(64 * 1024, info.file_size - member_size + 1)
                        )
                        if not chunk:
                            break
                        member_size += len(chunk)
                        if member_size > info.file_size or member_size > MAX_MEMBER_SIZE:
                            raise AuditExportError(
                                f"ZIP member {info.filename} exceeds its declared or "
                                f"maximum size of {MAX_MEMBER_SIZE} bytes."
                            )
                        chunks.append(chunk)
                if member_size != info.file_size:
                    raise AuditExportError(
                        f"ZIP member {info.filename} size does not match its declaration."
                    )
                files[info.filename] = b"".join(chunks)
        checks["zip_contents"] = "PASS"
    except (
        zipfile.BadZipFile,
        OSError,
        RuntimeError,
        EOFError,
        NotImplementedError,
        zlib.error,
        AuditExportError,
    ) as exc:
        checks["zip_contents"] = "FAIL"
        details["zip_contents"] = (str(exc),)
        return ExportVerification(checks, details)

    try:
        manifest = _strict_json_loads(files["export-manifest.json"])
        expected_manifest_keys = {
            "format",
            "export_id",
            "audit_pdf_sha256",
            "events_json_sha256",
        }
        if type(manifest) is not dict or set(manifest) != expected_manifest_keys:
            raise AuditExportError("Manifest fields do not match the required schema.")
        if manifest["format"] != MANIFEST_FORMAT:
            raise AuditExportError("Unsupported manifest format.")
        _validate_export_id(manifest["export_id"])
        for field in ("audit_pdf_sha256", "events_json_sha256"):
            if type(manifest[field]) is not str or not _HASH_RE.fullmatch(manifest[field]):
                raise AuditExportError(f"Manifest {field} is malformed.")
        checks["manifest"] = "PASS"
    except AuditExportError as exc:
        checks["manifest"] = "FAIL"
        details["manifest"] = (str(exc),)
        return ExportVerification(checks, details)

    actual_pdf_hash = hashlib.sha256(files["audit.pdf"]).hexdigest()
    actual_events_hash = hashlib.sha256(files["events.json"]).hexdigest()
    if (
        actual_pdf_hash != manifest["audit_pdf_sha256"]
        or actual_events_hash != manifest["events_json_sha256"]
    ):
        checks["file_hashes"] = "FAIL"
        details["file_hashes"] = ("One or more detached file hashes do not match.",)
        return ExportVerification(checks, details)
    checks["file_hashes"] = "PASS"

    try:
        document = _strict_json_loads(files["events.json"])
        _validate_events_document(document)
        if document["export"]["export_id"] != manifest["export_id"]:
            raise AuditExportError("Manifest and events.json export_id differ.")
        checks["events_schema"] = "PASS"
        chain_result = verify_chain(
            document["events"],
            expected_draft_id=document["export"]["draft_id"],
            expected_event_count=document["export"]["event_count"],
            expected_final_chain_hash=document["export"]["final_chain_hash"],
        )
        checks["chain"] = chain_result.overall_status
        if chain_result.overall_status != "PASS":
            details["chain"] = (str(chain_result.to_dict()),)
    except AuditExportError as exc:
        checks["events_schema"] = "FAIL"
        checks["chain"] = "NOT_CHECKED"
        details["events_schema"] = (str(exc),)
        return ExportVerification(checks, details, chain_result)

    try:
        pdf_text = _normalized_pdf_text(files["audit.pdf"])
        export = document["export"]
        required_pdf_fields = (
            f"Draft ID: {export['draft_id']}",
            f"Event count: {export['event_count']}",
            f"Final chain hash: {export['final_chain_hash']}",
            f"Export timestamp (UTC): {export['exported_at_utc']}",
            f"Trust level: {export['trust_level']}",
        )
        asset = export["asset_identity"]
        if asset is not None:
            required_pdf_fields += (
                f"Asset ID: {asset['asset_id'] or 'Unavailable'}",
                "Asset source type: "
                + _pdf_display_text(asset["source_type"] or "Unavailable"),
                f"Asset SHA-256: {asset['sha256'] or 'Unavailable'}",
            )
        normalized_pdf = _normalize_pdf_text(pdf_text)
        missing = [
            field
            for field in required_pdf_fields
            if _normalize_pdf_text(field) not in normalized_pdf
        ]
        if missing:
            raise AuditExportError(f"PDF scope/count metadata missing: {missing}")
        checks["pdf_scope_count"] = "PASS"
    except AuditExportError as exc:
        checks["pdf_scope_count"] = "FAIL"
        checks["pdf_event_rendering"] = "NOT_CHECKED"
        details["pdf_scope_count"] = (str(exc),)
        return ExportVerification(checks, details, chain_result)

    try:
        pdf_lines = [
            _normalize_pdf_text(line)
            for line in pdf_text.splitlines()
            if _normalize_pdf_text(line)
        ]
        marker_pattern = re.compile(
            r"\[BRL-AUDIT-EVENT-(?:START|END):[1-9][0-9]*:[0-9a-f]{64}\]"
        )
        markers = [line for line in pdf_lines if marker_pattern.fullmatch(line)]
        expected_markers = []
        for entry in document["events"]:
            sequence = entry["payload"]["sequence"]
            event_hash = entry["event_hash"]
            expected_markers.extend(
                [
                    f"[BRL-AUDIT-EVENT-START:{sequence}:{event_hash}]",
                    f"[BRL-AUDIT-EVENT-END:{sequence}:{event_hash}]",
                ]
            )
        if markers != expected_markers:
            raise AuditExportError(
                "PDF event blocks are missing, duplicated, or out of sequence."
            )

        marker_indexes = [
            index for index, line in enumerate(pdf_lines) if marker_pattern.fullmatch(line)
        ]
        for event_index, entry in enumerate(document["events"]):
            payload = entry["payload"]
            start_index = marker_indexes[event_index * 2]
            end_index = marker_indexes[event_index * 2 + 1]
            block_text = _normalize_pdf_text(
                " ".join(pdf_lines[start_index + 1 : end_index])
            )
            visible = (
                f"Sequence: {payload['sequence']}",
                f"Event ID: {payload['event_id']}",
                f"Actor: {_pdf_display_text(payload['actor'])}",
                f"Actor provenance: {payload['actor_role_provenance']}",
                "Actor role at event: "
                + _pdf_display_text(payload["actor_role_at_event"] or "Unrecorded"),
                f"Action: {payload['event_type']}",
                f"Timestamp: {payload['created_at']}",
                f"Review ID: {payload['review_id']}",
                f"Finding ID: {payload['finding_id'] or 'None'}",
                f"Event hash: {entry['event_hash']}",
            )
            missing = [
                field
                for field in visible
                if _normalize_pdf_text(field) not in block_text
            ]
            if missing:
                raise AuditExportError(
                    f"PDF event block {payload['sequence']} has missing or mismatched "
                    f"fields: {missing}"
                )
            note = _pdf_display_text(
                payload["note"] if payload["note"] is not None else "None"
            )
            if _normalize_pdf_text(f"Note: {note}") not in block_text:
                raise AuditExportError(
                    f"PDF event block {payload['sequence']} note does not agree "
                    "with events.json."
                )
        checks["pdf_event_rendering"] = "PASS"
    except AuditExportError as exc:
        checks["pdf_event_rendering"] = "FAIL"
        details["pdf_event_rendering"] = (str(exc),)

    return ExportVerification(checks, details, chain_result)
