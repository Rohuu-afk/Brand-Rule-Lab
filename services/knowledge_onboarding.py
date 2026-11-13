"""Source-preserving storage and human review for brand knowledge onboarding."""

from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from ai.base import AIProviderError
from ai.prompts import (
    build_knowledge_extraction_prompt,
    build_knowledge_extraction_repair_prompt,
)
from db.connection import get_connection
from models.enums import BrandRole, UserRole
from models.schemas import (
    AIResponseValidationError,
    validate_knowledge_extraction_response,
)
from services.document_parser import MAX_BYTES, ParseError, parse_for_onboarding
from services.auth_service import (
    AuthenticatedPrincipal,
    require_legacy_test_actor,
    require_principal,
)
from services.audit_service import append_audit_event
from services.brand_service import require_brand_membership, require_record_brand


_CATEGORIES = {
    "BRAND_RULE", "TONE", "TERMINOLOGY", "PRODUCT_FACT", "EVIDENCE",
    "RESTRICTION", "ASSET",
}
_RULE_CATEGORIES = {"BRAND_RULE", "TONE", "TERMINOLOGY", "RESTRICTION"}
_DEFAULT_RULE_METHOD = "Reviewer classification required; inactive until source and method are verified."
_KNOWLEDGE_CHUNK_CHARS = 2600
_KNOWLEDGE_OUTPUT_TOKENS = 2048
_TRUNCATION_FINISH_REASONS = {"length", "max_tokens", "max_output_tokens"}
_CONFLICT_STOP_WORDS = {
    "a", "an", "and", "are", "as", "be", "by", "contains", "contain",
    "for", "formula", "from", "has", "have", "in", "is", "it", "of",
    "on", "or", "product", "the", "their", "this", "to", "with",
    "g", "kg", "l", "mg", "ml",
}


class KnowledgeOnboardingError(ValueError):
    """An uploaded source or AI proposal could not be safely processed."""


def _audit_knowledge_change(
    conn: sqlite3.Connection, principal: AuthenticatedPrincipal | None, *,
    action: str, object_type: str, object_id: int | None, brand_id: int | None,
    details: dict[str, Any] | None = None,
) -> None:
    if principal is not None:
        append_audit_event(
            conn, principal, action=action, object_type=object_type,
            object_id=object_id, brand_id=brand_id, details=details,
        )


def _require_knowledge_access(
    principal: AuthenticatedPrincipal | None,
    brand_id: int | None,
    action: str,
) -> None:
    """Fail closed for normal callers; keep unscoped access only for legacy tests."""
    if principal is None:
        require_legacy_test_actor(action)
        return
    if brand_id is None:
        raise ValueError(f"A brand is required to {action}.")
    require_brand_membership(principal, brand_id)


def save_brand_profile(
    brand: str,
    industry: str = "",
    audience: str = "",
    description: str = "",
    personality: str = "",
    visual_identity: str = "",
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    _require_knowledge_access(principal, brand_id, "save brand identity")
    actor = require_principal(principal, action="save brand identity") if principal else None
    if actor is not None:
        if brand_id is None:
            raise ValueError("A brand is required to save brand identity.")
        brand = require_brand_membership(actor, brand_id, (BrandRole.OWNER,))["name"]
    brand = brand.strip()
    if not brand:
        raise ValueError("Brand name is required.")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO brand_profiles
                (brand, industry, audience, description, personality, visual_identity)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(brand) DO UPDATE SET
                industry = excluded.industry, audience = excluded.audience,
                description = excluded.description, personality = excluded.personality,
                visual_identity = excluded.visual_identity,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            (
                brand, industry.strip(), audience.strip(), description.strip(),
                personality.strip(), visual_identity.strip(),
            ),
        )
        _audit_knowledge_change(
            conn, actor, action="BRAND_PROFILE_UPDATED", object_type="brand_profile",
            object_id=brand_id, brand_id=brand_id,
        )
        conn.commit()


def get_brand_profiles(
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    _require_knowledge_access(principal, brand_id, "read brand identity")
    with get_connection() as conn:
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for authenticated profile lookup.")
            membership = require_brand_membership(principal, brand_id)
            return [dict(row) for row in conn.execute(
                "SELECT * FROM brand_profiles WHERE brand = ? ORDER BY brand",
                (membership["name"],),
            ).fetchall()]
        return [dict(row) for row in conn.execute(
            "SELECT * FROM brand_profiles ORDER BY brand"
        ).fetchall()]


def create_onboarding_product(
    name: str,
    brand: str,
    description: str = "",
    market: str = "",
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> int:
    _require_knowledge_access(principal, brand_id, "create a product")
    actor = require_principal(principal, action="create a product") if principal else None
    if actor is not None:
        if brand_id is None:
            raise ValueError("A brand is required to create a product.")
        brand = require_brand_membership(
            actor, brand_id, (BrandRole.OWNER, BrandRole.FACT_OWNER)
        )["name"]
    name, brand = name.strip(), brand.strip()
    if not name or not brand:
        raise ValueError("Product and brand names are required.")
    markets = [market.strip()] if market.strip() else []
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM products WHERE name = ? AND brand = ?"
            + (" AND brand_id = ?" if actor is not None else ""),
            (name, brand, brand_id) if actor is not None else (name, brand),
        ).fetchone()
        if existing:
            return int(existing["id"])
        cursor = conn.execute(
            "INSERT INTO products (name, brand, description, markets, brand_id) VALUES (?, ?, ?, ?, ?)",
            (name, brand, description.strip(), json.dumps(markets), brand_id if actor else None),
        )
        _audit_knowledge_change(
            conn, actor, action="PRODUCT_CREATED", object_type="product",
            object_id=int(cursor.lastrowid), brand_id=brand_id,
        )
        conn.commit()
    return int(cursor.lastrowid)


def save_source_document(
    filename: str,
    data: bytes,
    document_type: str,
    purpose: str,
    uploaded_by: str,
    product_id: int | None = None,
    market: str | None = None,
    channel: str | None = None,
    content_type: str | None = None,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> int:
    _require_knowledge_access(principal, brand_id, "upload brand knowledge")
    actor = require_principal(principal, action="upload brand knowledge") if principal else None
    if actor is not None:
        if brand_id is None:
            raise ValueError("A brand is required to upload a source document.")
        require_brand_membership(actor, brand_id)
    if not data or len(data) > MAX_BYTES:
        raise KnowledgeOnboardingError("The document is empty or exceeds the upload size limit.")
    if not document_type.strip() or not purpose.strip():
        raise KnowledgeOnboardingError("Choose a document type and describe its purpose.")
    try:
        parsed = parse_for_onboarding(
            filename, data, visual_asset=document_type == "Visual assets"
        )
    except ParseError as exc:
        raise KnowledgeOnboardingError(str(exc)) from exc
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with get_connection() as conn:
        if actor is not None and product_id is not None:
            require_record_brand(conn, actor, brand_id, "products", product_id)
        cursor = conn.execute(
            """
            INSERT INTO knowledge_documents
                (filename, document_type, purpose, product_id, market, mime_type,
                 channel, content_type, original_bytes, extracted_text, page_records,
                 extraction_status, uploaded_by, brand_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                Path(filename).name, document_type.strip(), purpose.strip(), product_id,
                market.strip() if market and market.strip() else None, mime_type,
                channel.strip() if channel and channel.strip() else None,
                content_type.strip() if content_type and content_type.strip() else None, data,
                parsed["text"], json.dumps(parsed["page_records"], ensure_ascii=False),
                parsed["extraction_status"], uploaded_by.strip() or "demo_user",
                brand_id if actor else None,
            ),
        )
        _audit_knowledge_change(
            conn, actor, action="SOURCE_DOCUMENT_UPLOADED",
            object_type="knowledge_document", object_id=int(cursor.lastrowid),
            brand_id=brand_id,
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_source_documents(
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> list[dict]:
    _require_knowledge_access(principal, brand_id, "read source documents")
    with get_connection() as conn:
        params: tuple = ()
        scope = ""
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for authenticated source lookup.")
            require_brand_membership(principal, brand_id)
            scope = " WHERE d.brand_id = ?"
            params = (brand_id,)
        rows = conn.execute(
            """
            SELECT d.id, d.filename, d.document_type, d.purpose, d.product_id, d.market,
                   d.channel, d.content_type, d.extraction_status, d.page_records,
                   d.ai_extraction_status, d.ai_extraction_error, d.ai_extraction_at,
                   d.created_at, p.name AS product_name
            FROM knowledge_documents d LEFT JOIN products p ON p.id = d.product_id
            """ + scope + " ORDER BY d.created_at DESC, d.id DESC", params
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["page_records"] = json.loads(item["page_records"])
        results.append(item)
    return results


def get_source_document(
    document_id: int,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> dict:
    _require_knowledge_access(principal, brand_id, "read a source document")
    with get_connection() as conn:
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for authenticated source lookup.")
            require_record_brand(conn, principal, brand_id, "knowledge_documents", document_id)
        row = conn.execute(
            "SELECT * FROM knowledge_documents WHERE id = ?", (document_id,)
        ).fetchone()
    if row is None:
        raise ValueError(f"Source document {document_id} does not exist.")
    document = dict(row)
    document["page_records"] = json.loads(document["page_records"])
    return document


def correct_extracted_page(
    document_id: int, page_number: int, corrected_text: str,
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> None:
    _require_knowledge_access(principal, brand_id, "correct extracted document text")
    if not corrected_text.strip():
        raise ValueError("Corrected page text cannot be empty.")
    with get_connection() as conn:
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required to correct extracted text.")
            require_record_brand(conn, principal, brand_id, "knowledge_documents", document_id)
        row = conn.execute(
            "SELECT page_records FROM knowledge_documents WHERE id = ?", (document_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Source document {document_id} does not exist.")
        pages = json.loads(row["page_records"])
        page = next((p for p in pages if p["page_number"] == page_number), None)
        if page is None:
            raise ValueError(f"Page {page_number} does not exist in this document.")
        page["text"] = corrected_text
        page["method"] = "user_corrected"
        page["quality"] = 1.0
        page["warnings"] = ["User-corrected text; compare with the preserved original page."]
        full_text = "\n".join(item["text"] for item in pages if item["text"].strip())
        status = "NEEDS_ATTENTION" if any(item["warnings"] for item in pages) else "PROCESSED"
        conn.execute(
            """
            UPDATE knowledge_documents
            SET page_records = ?, extracted_text = ?, extraction_status = ?
            WHERE id = ?
            """,
            (json.dumps(pages, ensure_ascii=False), full_text, status, document_id),
        )
        affected = conn.execute(
            "SELECT * FROM knowledge_entries WHERE document_id = ? AND source_page = ? "
            "AND status != 'APPROVED'",
            (document_id, str(page_number)),
        ).fetchall()
        for entry in affected:
            conn.execute(
                """
                UPDATE knowledge_entries
                SET source_quote_verified = 0, source_support_confirmed = 0,
                    source_support_confirmed_by = NULL, source_support_confirmed_at = NULL
                WHERE id = ?
                """,
                (entry["id"],),
            )
            _set_latest_version_provenance(
                conn, entry["id"], source_quote_verified=0,
                source_support_confirmed=0, source_support_confirmed_by=None,
                source_support_confirmed_at=None,
            )
            conn.execute(
                "INSERT INTO knowledge_decisions (entry_id, decision, actor, note) "
                "VALUES (?, 'SOURCE_INVALIDATED', 'Source correction', ?)",
                (entry["id"], f"Page {page_number} text was corrected; quote verification and support confirmation were reset."),
            )
            refreshed = conn.execute(
                "SELECT * FROM knowledge_entries WHERE id = ?", (entry["id"],)
            ).fetchone()
            _append_rule_provenance_history(
                conn, refreshed, "SOURCE_INVALIDATED", "Source correction",
                f"Page {page_number} text was corrected; provenance must be reviewed again.",
            )
        _audit_knowledge_change(
            conn, principal, action="SOURCE_PAGE_CORRECTED",
            object_type="knowledge_document", object_id=document_id,
            brand_id=brand_id,
            details={"page_number": page_number, "invalidated_entries": len(affected)},
        )
        conn.commit()


def get_original_page_preview(
    document_id: int, page_number: int,
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> bytes | None:
    document = get_source_document(document_id, principal, brand_id)
    if Path(document["filename"]).suffix.lower() != ".pdf":
        return None
    try:
        import fitz
        pdf = fitz.open(stream=document["original_bytes"], filetype="pdf")
        try:
            if page_number < 1 or page_number > pdf.page_count:
                raise ValueError(f"Page {page_number} is outside this PDF.")
            image = pdf[page_number - 1].get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False)
            return image.tobytes("png")
        finally:
            pdf.close()
    except ImportError as exc:
        raise KnowledgeOnboardingError("PyMuPDF is required to preview the original PDF.") from exc


def _build_knowledge_chunks(
    document_id: int, pages: list[dict]
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for page in pages:
        text = page["text"]
        start = 0
        page_chunks: list[str] = []
        while start < len(text):
            end = min(start + _KNOWLEDGE_CHUNK_CHARS, len(text))
            if end < len(text):
                split = max(
                    text.rfind("\n", start + _KNOWLEDGE_CHUNK_CHARS // 2, end),
                    text.rfind(" ", start + _KNOWLEDGE_CHUNK_CHARS // 2, end),
                )
                if split > start:
                    end = split + 1
            page_chunks.append(text[start:end])
            start = end
        for page_chunk_number, chunk_text in enumerate(page_chunks, start=1):
            chunks.append({
                "source_document_id": document_id,
                "page_number": page["page_number"],
                "section_name": page.get("section_name") or page.get("section"),
                "page_chunk_number": page_chunk_number,
                "page_chunk_count": len(page_chunks),
                "text": chunk_text,
                "warnings": page.get("warnings", []),
                "method": page.get("method"),
            })

    for chunk_number, chunk in enumerate(chunks, start=1):
        chunk["chunk_number"] = chunk_number
        chunk["chunk_count"] = len(chunks)
    return chunks


def _response_metadata(provider: Any) -> dict[str, Any]:
    metadata = getattr(provider, "last_response_metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _response_finish_reason(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("finish_reason")
    return value.casefold() if isinstance(value, str) else None


def _validation_summary(error: Exception) -> str:
    return str(error).splitlines()[0][:240] or type(error).__name__


def _validated_chunk_quote(
    entry: dict[str, Any], chunk: dict[str, Any]
) -> tuple[int, list[str], str, str] | None:
    quote = entry["source_quote"]
    requested_page = entry["page_number"]
    if requested_page is not None and requested_page != chunk["page_number"]:
        return None
    quote_span = _find_source_span(chunk["text"], quote)
    if quote_span is None:
        return None
    source_quote = chunk["text"][quote_span[0]:quote_span[1]]
    content_span = _find_source_span(source_quote, entry["content"])
    if content_span is None:
        return None
    content_start = quote_span[0] + content_span[0]
    numbered_prefix_start = max(0, quote_span[0] - 400)
    numbered_prefix = chunk["text"][numbered_prefix_start:quote_span[0]]
    numbered_id = re.search(r"CLM-\d{2}\s*$", numbered_prefix)
    if numbered_id:
        block_start = numbered_prefix_start + numbered_id.start()
        next_id = re.search(r"\bCLM-\d{2}\b", chunk["text"][quote_span[1]:])
        block_end = (
            quote_span[1] + next_id.start()
            if next_id else len(chunk["text"])
        )
        source_quote = chunk["text"][block_start:block_end].strip()
        source_content = source_quote
    else:
        source_content = chunk["text"][
            content_start:quote_span[0] + content_span[1]
        ]
    warnings = list(entry["warnings"])
    warnings.extend(chunk["warnings"])
    if chunk["method"] == "user_corrected":
        warnings.append(
            "Quotation is from user-corrected text, not verified against the original."
        )
    return (
        chunk["page_number"],
        list(dict.fromkeys(warnings)),
        source_quote,
        source_content,
    )


def _find_source_span(source: str, candidate: str) -> tuple[int, int] | None:
    """Resolve whitespace-normalized model text to its exact source character span."""
    candidate_text = " ".join(candidate.split())
    if not candidate_text:
        return None

    normalized_chars: list[str] = []
    source_offsets: list[int] = []
    previous_end: int | None = None
    for token in re.finditer(r"\S+", source):
        if previous_end is not None:
            normalized_chars.append(" ")
            source_offsets.append(previous_end)
        normalized_chars.extend(token.group())
        source_offsets.extend(range(token.start(), token.end()))
        previous_end = token.end()

    normalized_source = "".join(normalized_chars)
    match_start = normalized_source.find(candidate_text)
    if match_start < 0:
        return None
    match_end = match_start + len(candidate_text)
    return source_offsets[match_start], source_offsets[match_end - 1] + 1


def _knowledge_entry_fingerprint(entry: dict[str, Any]) -> tuple[str, str, str]:
    normalize = lambda value: re.sub(r"\s+", " ", value).strip().casefold()
    return (
        entry["category"],
        normalize(entry["content"]),
        normalize(entry["source_quote"]),
    )


def extract_document_proposals(
    document_id: int, provider: Any | None = None,
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> dict:
    _require_knowledge_access(principal, brand_id, "extract knowledge proposals")
    document = get_source_document(document_id, principal, brand_id)
    _record_ai_extraction(document_id, "RUNNING", principal=principal, brand_id=brand_id)
    pages = [page for page in document["page_records"] if page["text"].strip()]
    if not pages:
        _record_ai_extraction(
            document_id, "FAILED",
            "No readable extracted text is available. Correct the text or upload a clearer file.",
            principal=principal, brand_id=brand_id,
        )
        raise KnowledgeOnboardingError(
            "No readable text is available for AI extraction. Correct the text manually or upload a clearer file."
        )
    if sum(len(page["text"]) for page in pages) > 30_000:
        _record_ai_extraction(
            document_id, "FAILED",
            "The 30,000-character extraction limit was exceeded; split the source document.",
            principal=principal, brand_id=brand_id,
        )
        raise KnowledgeOnboardingError(
            "This document contains more than 30,000 characters of extracted text. "
            "Split it into smaller source files so each AI request remains focused."
        )
    chunks = _build_knowledge_chunks(document_id, pages)
    if provider is None:
        try:
            from ai.provider_factory import get_provider
            provider = get_provider()
        except AIProviderError as exc:
            failure = (
                f"AI provider request failed ({type(exc).__name__}). "
                "Retry or enter knowledge manually."
            )
            _record_ai_extraction(
                document_id, "FAILED", failure, principal=principal, brand_id=brand_id
            )
            raise KnowledgeOnboardingError(
                "The configured AI provider is unavailable. The uploaded document is saved; "
                "you can correct its text and add knowledge manually."
            ) from exc

    accepted: list[dict[str, Any]] = []
    rejected_invalid_schema = 0
    rejected_untraceable = 0
    rejected_duplicate = 0
    completed_chunks = 0
    failed_chunks: list[str] = []
    chunk_diagnostics: list[dict[str, Any]] = []
    seen_entries: set[tuple[str, str, str]] = set()

    for chunk in chunks:
        prompt = build_knowledge_extraction_prompt(document_id, chunk)
        try:
            response = provider.generate_text(
                prompt, temperature=0.1, max_output_tokens=_KNOWLEDGE_OUTPUT_TOKENS,
                json_mode=True,
            )
        except AIProviderError as exc:
            failed_chunks.append(
                f"page {chunk['page_number']} chunk {chunk['chunk_number']}: "
                f"provider request failed ({type(exc).__name__})"
            )
            chunk_diagnostics.append({
                "chunk_number": chunk["chunk_number"],
                "page_number": chunk["page_number"],
                "status": "failed",
                "error": f"provider request failed ({type(exc).__name__})",
            })
            continue

        response_text = response if isinstance(response, str) else ""
        metadata = _response_metadata(provider)
        finish_reason = _response_finish_reason(metadata)
        diagnostic = {
            "chunk_number": chunk["chunk_number"],
            "page_number": chunk["page_number"],
            "initial_finish_reason": finish_reason,
            "initial_response_chars": len(response_text),
            "repair_attempted": False,
        }
        if isinstance(metadata.get("usage"), dict):
            diagnostic["initial_usage"] = metadata["usage"]
        retry_reason: str | None = None
        valid: list[tuple[int, dict[str, Any]]] = []
        invalid: list[tuple[int, str]] = []
        if finish_reason in _TRUNCATION_FINISH_REASONS:
            retry_reason = (
                f"provider reported finish_reason={finish_reason}"
            )
        else:
            try:
                valid, invalid = validate_knowledge_extraction_response(response_text)
            except AIResponseValidationError as exc:
                retry_reason = _validation_summary(exc)
            if not response_text.strip() and retry_reason is None:
                retry_reason = "provider returned an empty or non-text response"

        if retry_reason is not None:
            diagnostic["repair_attempted"] = True
            repair_prompt = build_knowledge_extraction_repair_prompt(
                document_id, chunk, response_text, retry_reason
            )
            try:
                repaired = provider.generate_text(
                    repair_prompt,
                    temperature=0.0,
                    max_output_tokens=_KNOWLEDGE_OUTPUT_TOKENS,
                    json_mode=True,
                )
            except AIProviderError as exc:
                failed_chunks.append(
                    f"page {chunk['page_number']} chunk {chunk['chunk_number']}: "
                    f"repair request failed ({type(exc).__name__})"
                )
                diagnostic.update({
                    "status": "failed",
                    "repair_error": f"provider request failed ({type(exc).__name__})",
                })
                chunk_diagnostics.append(diagnostic)
                continue
            response_text = repaired if isinstance(repaired, str) else ""
            metadata = _response_metadata(provider)
            finish_reason = _response_finish_reason(metadata)
            diagnostic["repair_finish_reason"] = finish_reason
            diagnostic["repair_response_chars"] = len(response_text)
            if isinstance(metadata.get("usage"), dict):
                diagnostic["repair_usage"] = metadata["usage"]
            if finish_reason in _TRUNCATION_FINISH_REASONS:
                failed_chunks.append(
                    f"page {chunk['page_number']} chunk {chunk['chunk_number']}: "
                    f"repair was truncated (finish_reason={finish_reason}; "
                    f"initial finish_reason={diagnostic['initial_finish_reason']})"
                )
                diagnostic.update({
                    "status": "failed",
                    "error": f"repair truncated (finish_reason={finish_reason})",
                })
                chunk_diagnostics.append(diagnostic)
                continue
            try:
                valid, invalid = validate_knowledge_extraction_response(response_text)
            except AIResponseValidationError as exc:
                failed_chunks.append(
                    f"page {chunk['page_number']} chunk {chunk['chunk_number']}: "
                    f"repair response was invalid ({_validation_summary(exc)}; "
                    f"initial finish_reason={diagnostic['initial_finish_reason']})"
                )
                diagnostic.update({
                    "status": "failed",
                    "error": f"repair response was invalid ({_validation_summary(exc)})",
                })
                chunk_diagnostics.append(diagnostic)
                continue

        completed_chunks += 1
        diagnostic["status"] = "processed"
        diagnostic["valid_items"] = len(valid)
        diagnostic["invalid_items"] = len(invalid)
        chunk_diagnostics.append(diagnostic)
        rejected_invalid_schema += len(invalid)
        for _, item in valid:
            provenance = _validated_chunk_quote(item, chunk)
            if provenance is None:
                rejected_untraceable += 1
                continue
            page_number, warnings, source_quote, source_content = provenance
            entry = {
                "category": item["category"],
                "content": source_content,
                "explanation": "",
                "product_id": document["product_id"],
                "market": document["market"],
                "channel": document["channel"],
                "content_type": document["content_type"],
                "source_page": str(page_number),
                "source_quote": source_quote,
                "confidence": item["confidence"],
                "warnings": warnings,
            }
            fingerprint = _knowledge_entry_fingerprint(entry)
            if fingerprint in seen_entries:
                rejected_duplicate += 1
                continue
            seen_entries.add(fingerprint)
            accepted.append(entry)

    if not accepted:
        details = "; ".join(failed_chunks)
        only_provider_failures = failed_chunks and all(
            "provider request failed" in item or "repair request failed" in item
            for item in failed_chunks
        )
        failure = (
            "The configured AI provider is unavailable for all extraction chunks."
            if only_provider_failures
            else f"No source-verifiable proposals were returned after {len(chunks)} chunk(s)."
        )
        if details:
            failure += f" Failed chunks: {details}."
        _record_ai_extraction(
            document_id, "FAILED", failure, principal=principal, brand_id=brand_id
        )
        raise KnowledgeOnboardingError(
            f"{failure} The document remains saved and extraction is marked FAILED."
        )
    try:
        created = _insert_entries(
            accepted, document_id=document_id,
            actor=(principal.username if principal else "AI proposal"),
            principal=principal, brand_id=brand_id,
        )
    except Exception:
        _record_ai_extraction(
            document_id, "FAILED",
            "AI proposals could not be saved to the local database.",
            principal=principal, brand_id=brand_id,
        )
        raise
    rejected = (
        rejected_invalid_schema + rejected_untraceable + rejected_duplicate
    )
    status = "PARTIAL" if rejected or failed_chunks else "SUCCEEDED"
    messages = []
    if rejected:
        messages.append(
            f"{rejected} response item(s) were invalid, untraceable, or duplicate."
        )
    if failed_chunks:
        messages.append("Failed chunks: " + "; ".join(failed_chunks) + ".")
    message = " ".join(messages) or None
    _record_ai_extraction(
        document_id, status, message, principal=principal, brand_id=brand_id
    )
    return {
        "created": len(created),
        "rejected": rejected,
        "rejected_invalid_schema": rejected_invalid_schema,
        "rejected_untraceable": rejected_untraceable,
        "rejected_duplicate": rejected_duplicate,
        "chunks_processed": completed_chunks,
        "failed_chunks": len(failed_chunks),
        "provider": (
            getattr(provider, "last_provider_used", None)
            or getattr(provider, "provider_name", None)
        ),
        "model": getattr(provider, "last_model_used", None),
        "used_fallback": bool(getattr(provider, "used_fallback", False)),
        "chunk_diagnostics": chunk_diagnostics,
    }


def _record_ai_extraction(
    document_id: int, status: str, error: str | None = None,
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> None:
    with get_connection() as conn:
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required to update source extraction status.")
            require_record_brand(conn, principal, brand_id, "knowledge_documents", document_id)
        cursor = conn.execute(
            """
            UPDATE knowledge_documents
            SET ai_extraction_status = ?,
                ai_extraction_error = ?,
                ai_extraction_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?""" + (" AND brand_id = ?" if principal is not None else ""),
            (status, error, document_id, brand_id) if principal is not None else (status, error, document_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Source document {document_id} does not exist.")
        _audit_knowledge_change(
            conn, principal, action="AI_EXTRACTION_STATUS_CHANGED",
            object_type="knowledge_document", object_id=document_id,
            brand_id=brand_id, details={"status": status},
        )
        conn.commit()


def add_manual_entry(
    category: str,
    content: str,
    explanation: str = "",
    product_id: int | None = None,
    market: str | None = None,
    channel: str | None = None,
    content_type: str | None = None,
    document_id: int | None = None,
    source_page: int | None = None,
    source_quote: str | None = None,
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> int:
    actor = require_principal(principal, action="add brand knowledge") if principal else None
    if actor is not None:
        if brand_id is None:
            raise ValueError("A brand is required to add knowledge.")
        require_brand_membership(actor, brand_id)
    if category not in _CATEGORIES or not content.strip():
        raise ValueError("Choose a valid category and enter the proposed knowledge.")
    if category == "ASSET" and not document_id:
        raise ValueError("Select an uploaded image for an asset proposal.")
    source_quote = source_quote.strip() if source_quote and source_quote.strip() else None
    source_page_text = str(source_page) if source_page else None
    if source_quote and not document_id:
        raise ValueError("Attach a source document before entering a source quotation.")
    if source_quote and not source_page_text:
        raise ValueError("Select the source page for the quotation.")
    source_quote_verified = False
    if document_id and source_page_text and source_quote:
        document = get_source_document(document_id, actor, brand_id)
        page = next(
            (item for item in document["page_records"] if str(item["page_number"]) == source_page_text),
            None,
        )
        if page is None or source_quote not in page["text"]:
            raise ValueError("The quotation must match the selected source page exactly.")
        source_quote_verified = True
    warnings = []
    if category != "ASSET" and not document_id:
        warnings.append("Manually supplied; not verified against a source document.")
    elif category != "ASSET" and not source_quote_verified:
        warnings.append("A document is attached, but no exact quotation has been verified.")
    return _insert_entries([{
        "category": category,
        "content": content.strip(),
        "explanation": explanation.strip(),
        "product_id": product_id,
        "market": market.strip() if market and market.strip() else None,
        "channel": channel.strip() if channel and channel.strip() else None,
        "content_type": content_type.strip() if content_type and content_type.strip() else None,
        "source_page": source_page_text,
        "source_quote": source_quote,
        "confidence": None,
        "warnings": warnings,
    }], document_id=document_id, actor=(actor.username if actor else "Manual proposal"),
        principal=actor, brand_id=brand_id)[0]


def _insert_entries(
    entries: list[dict], document_id: int | None, actor: str,
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> list[int]:
    created: list[int] = []
    with get_connection() as conn:
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required to save knowledge proposals.")
            require_brand_membership(principal, brand_id)
            for item in entries:
                if item.get("product_id") is not None:
                    require_record_brand(
                        conn, principal, brand_id, "products", item["product_id"]
                    )
            if document_id is not None:
                require_record_brand(conn, principal, brand_id, "knowledge_documents", document_id)
        scoped_brand_id = brand_id if principal is not None else None
        for item in entries:
            quote_verified = bool(
                document_id
                and item.get("source_page")
                and item.get("source_quote")
                and _source_quote_matches_page(
                    conn, document_id, item["source_page"], item["source_quote"]
                )
            )
            if item.get("source_quote") and not quote_verified:
                raise ValueError("The quotation must match the selected source page exactly.")
            warnings = list(item.get("warnings") or [])
            if item["category"] != "ASSET" and document_id and not quote_verified:
                warning = "A document is attached, but no exact quotation has been verified."
                if warning not in warnings:
                    warnings.append(warning)
            cursor = conn.execute(
                """
                INSERT INTO knowledge_entries
                    (document_id, category, content, explanation, product_id, market,
                     channel, content_type, source_page, source_quote, confidence, warnings,
                     rule_readiness_classification, rule_checking_method,
                     source_quote_verified, source_support_confirmed, brand_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    document_id, item["category"], item["content"], item["explanation"],
                    item["product_id"], item["market"], item.get("channel"),
                    item.get("content_type"), item["source_page"],
                    item["source_quote"], item["confidence"],
                    json.dumps(warnings, ensure_ascii=False),
                    "UNCLEAR" if item["category"] in _RULE_CATEGORIES else None,
                    _DEFAULT_RULE_METHOD if item["category"] in _RULE_CATEGORIES else None,
                    int(quote_verified), scoped_brand_id,
                ),
            )
            entry_id = int(cursor.lastrowid)
            created.append(entry_id)
            _audit_knowledge_change(
                conn, principal, action="KNOWLEDGE_PROPOSAL_CREATED",
                object_type="knowledge_entry", object_id=entry_id,
                brand_id=brand_id, details={"category": item["category"]},
            )
            conn.execute(
                """
                INSERT INTO knowledge_entry_versions
                    (entry_id, version_number, content, explanation, actor, source_quote,
                     source_page, source_document_id, product_id, market, channel, content_type,
                     source_quote_verified, source_support_confirmed)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    entry_id, item["content"], item["explanation"], actor,
                    item["source_quote"], item["source_page"], document_id,
                    item["product_id"], item["market"], item.get("channel"),
                    item.get("content_type"), int(quote_verified),
                ),
            )
            conn.execute(
                "INSERT INTO knowledge_decisions (entry_id, decision, actor) VALUES (?, 'PROPOSED', ?)",
                (entry_id, actor),
            )
            if item["category"] in _RULE_CATEGORIES:
                conn.execute(
                    """
                    INSERT INTO rule_readiness_history
                        (entry_id, version_number, event_type, actor,
                         readiness_classification, checking_method,
                         source_document_id, source_page, source_quote,
                         source_quote_verified, source_support_confirmed, note)
                    VALUES (?, 1, 'PROPOSED', ?, 'UNCLEAR', ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        entry_id, actor, _DEFAULT_RULE_METHOD, document_id,
                        item["source_page"], item["source_quote"], int(quote_verified),
                        "New rule proposal is not classified or active.",
                    ),
                )
            _record_related_entries(conn, entry_id)
        conn.commit()
    return created


def _source_quote_matches_page(
    conn: sqlite3.Connection,
    document_id: int | None,
    source_page: str | int | None,
    source_quote: str | None,
) -> bool:
    if not document_id or not source_page or not source_quote:
        return False
    row = conn.execute(
        "SELECT page_records FROM knowledge_documents WHERE id = ?", (document_id,)
    ).fetchone()
    if row is None:
        return False
    try:
        pages = json.loads(row["page_records"] or "[]")
    except (TypeError, json.JSONDecodeError):
        return False
    page = next(
        (item for item in pages if str(item.get("page_number")) == str(source_page)),
        None,
    )
    return bool(page and source_quote.strip() in page.get("text", ""))


def _require_source_reviewer(
    category: str, actor_role: UserRole | str | None
) -> None:
    require_legacy_test_actor("make a source-provenance decision")
    role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
    role = role_value.strip().casefold()
    allowed = (
        {"fact owner"}
        if category in {"PRODUCT_FACT", "EVIDENCE", "ASSET"}
        else {"reviewer", "brand manager"}
    )
    if role not in allowed:
        required = "Fact Owner" if "fact owner" in allowed else "Reviewer"
        raise PermissionError(f"Source provenance decisions require the {required} demo role.")


def _require_knowledge_role(
    category: str, actor_role: UserRole | str | None, action: str
) -> None:
    require_legacy_test_actor(f"{action} knowledge entries")
    role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
    role = role_value.strip().casefold()
    allowed = (
        {"fact owner"}
        if category in {"PRODUCT_FACT", "EVIDENCE", "ASSET"}
        else {"reviewer", "brand manager"}
    )
    if role not in allowed:
        required = "Fact Owner" if "fact owner" in allowed else "Reviewer"
        raise PermissionError(f"{required} demo role required to {action} {category.replace('_', ' ').lower()}.")


def _require_entry_brand(
    conn: sqlite3.Connection,
    principal: AuthenticatedPrincipal,
    brand_id: int | None,
    entry_id: int,
    category: str,
    action: str,
) -> AuthenticatedPrincipal:
    actor = require_principal(principal, action=action)
    if brand_id is None:
        raise ValueError(f"A brand is required to {action}.")
    roles = (
        (BrandRole.OWNER, BrandRole.FACT_OWNER)
        if category in {"PRODUCT_FACT", "EVIDENCE", "ASSET"}
        else (BrandRole.OWNER, BrandRole.REVIEWER)
    )
    require_record_brand(
        conn, actor, brand_id, "knowledge_entries", entry_id, roles=roles
    )
    return actor


def _set_latest_version_provenance(
    conn: sqlite3.Connection, entry_id: int, **values: Any
) -> None:
    row = conn.execute(
        "SELECT MAX(version_number) AS version FROM knowledge_entry_versions WHERE entry_id = ?",
        (entry_id,),
    ).fetchone()
    if row is None or row["version"] is None:
        return
    assignments = ", ".join(f"{field} = ?" for field in values)
    conn.execute(
        f"UPDATE knowledge_entry_versions SET {assignments} "
        "WHERE entry_id = ? AND version_number = ?",
        (*values.values(), entry_id, row["version"]),
    )


def _append_rule_provenance_history(
    conn: sqlite3.Connection, entry: sqlite3.Row, event_type: str,
    actor: str, note: str,
) -> None:
    if entry["category"] not in _RULE_CATEGORIES:
        return
    version = conn.execute(
        "SELECT COALESCE(MAX(version_number), 0) + 1 AS next "
        "FROM rule_readiness_history WHERE entry_id = ?",
        (entry["id"],),
    ).fetchone()["next"]
    conn.execute(
        """
        INSERT INTO rule_readiness_history
            (entry_id, version_number, event_type, actor,
             readiness_classification, checking_method, rule_type,
             rule_parameters, rule_scope, rule_severity, positive_tests,
             negative_tests, source_document_id, source_page, source_quote,
             source_quote_verified, source_support_confirmed,
             source_support_confirmed_by, source_support_confirmed_at, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry["id"], version, event_type, actor,
            entry["rule_readiness_classification"] or "UNCLEAR",
            entry["rule_checking_method"] or _DEFAULT_RULE_METHOD,
            entry["rule_type"], entry["rule_parameters"], entry["rule_scope"],
            entry["rule_severity"], entry["rule_positive_tests"],
            entry["rule_negative_tests"], entry["document_id"],
            entry["source_page"], entry["source_quote"],
            entry["source_quote_verified"], entry["source_support_confirmed"],
            entry["source_support_confirmed_by"], entry["source_support_confirmed_at"],
            note,
        ),
    )


def verify_source_quote_location(
    entry_id: int,
    actor: str,
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    with get_connection() as conn:
        entry = conn.execute(
            "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if entry is None or entry["status"] not in {
            "PENDING_APPROVAL", "NEEDS_CLARIFICATION", "UNRESOLVED"
        }:
            raise ValueError("Only pending proposals can have source quotations verified.")
        if principal is not None:
            actor = _require_entry_brand(
                conn, principal, brand_id, entry_id, entry["category"],
                "verify a knowledge source quotation",
            )
            actor = actor.username
        else:
            _require_source_reviewer(entry["category"], actor_role)
        if not _source_quote_matches_page(
            conn, entry["document_id"], entry["source_page"], entry["source_quote"]
        ):
            raise ValueError("The exact quotation was not found on the selected source page.")
        conn.execute(
            "UPDATE knowledge_entries SET source_quote_verified = 1 WHERE id = ?",
            (entry_id,),
        )
        _set_latest_version_provenance(conn, entry_id, source_quote_verified=1)
        conn.execute(
            "INSERT INTO knowledge_decisions (entry_id, decision, actor, note) "
            "VALUES (?, 'SOURCE_QUOTE_VERIFIED', ?, ?)",
            (entry_id, actor, "Exact quotation located on the selected document page."),
        )
        updated = conn.execute(
            "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        _append_rule_provenance_history(
            conn, updated, "SOURCE_QUOTE_VERIFIED", actor,
            "Exact quotation located on the selected document page.",
        )
        _audit_knowledge_change(
            conn, principal, action="SOURCE_QUOTE_VERIFIED",
            object_type="knowledge_entry", object_id=entry_id, brand_id=brand_id,
        )
        conn.commit()


def confirm_source_support(
    entry_id: int,
    actor: str,
    note: str = "",
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    with get_connection() as conn:
        entry = conn.execute(
            "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if entry is None or entry["status"] not in {
            "PENDING_APPROVAL", "NEEDS_CLARIFICATION", "UNRESOLVED"
        }:
            raise ValueError("Only pending proposals can receive source-support confirmation.")
        if principal is not None:
            authenticated_actor = _require_entry_brand(
                conn, principal, brand_id, entry_id, entry["category"],
                "confirm knowledge source support",
            )
            actor = authenticated_actor.username
        else:
            _require_source_reviewer(entry["category"], actor_role)
        if not entry["source_quote_verified"]:
            raise ValueError("Verify the quotation on its source page before confirming support.")
        if not _source_quote_matches_page(
            conn, entry["document_id"], entry["source_page"], entry["source_quote"]
        ):
            raise ValueError("The verified quotation no longer matches the selected source page.")
        timestamp = conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now') AS confirmed_at"
        ).fetchone()["confirmed_at"]
        conn.execute(
            """
            UPDATE knowledge_entries
            SET source_support_confirmed = 1, source_support_confirmed_by = ?,
                source_support_confirmed_at = ?
            WHERE id = ?
            """,
            (actor, timestamp, entry_id),
        )
        _set_latest_version_provenance(
            conn, entry_id, source_support_confirmed=1,
            source_support_confirmed_by=actor, source_support_confirmed_at=timestamp,
        )
        conn.execute(
            "INSERT INTO knowledge_decisions (entry_id, decision, actor, note) "
            "VALUES (?, 'SOURCE_SUPPORT_CONFIRMED', ?, ?)",
            (
                entry_id, actor,
                note.strip() or "Reviewer confirmed the quotation supports the proposed knowledge.",
            ),
        )
        updated = conn.execute(
            "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        _append_rule_provenance_history(
            conn, updated, "SOURCE_SUPPORT_CONFIRMED", actor,
            note.strip() or "Reviewer confirmed the quotation supports the proposed knowledge.",
        )
        _audit_knowledge_change(
            conn, principal, action="SOURCE_SUPPORT_CONFIRMED",
            object_type="knowledge_entry", object_id=entry_id, brand_id=brand_id,
        )
        conn.commit()


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\W+", " ", folded).strip()


def _record_related_entries(conn: sqlite3.Connection, entry_id: int) -> None:
    current = conn.execute(
        "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    existing = conn.execute(
        """
        SELECT * FROM knowledge_entries WHERE id != ? AND category = ?
          AND COALESCE(product_id, -1) = COALESCE(?, -1)
          AND COALESCE(lower(market), '') = COALESCE(lower(?), '')
          AND COALESCE(lower(channel), '') = COALESCE(lower(?), '')
          AND COALESCE(lower(content_type), '') = COALESCE(lower(?), '')
        """,
        (
            entry_id, current["category"], current["product_id"], current["market"],
            current["channel"], current["content_type"],
        ),
    ).fetchall()
    for old in existing:
        old_text, new_text = _normalized(old["content"]), _normalized(current["content"])
        if not old_text or not new_text:
            continue
        if old_text == new_text:
            kind = "DUPLICATE"
        else:
            old_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", old_text))
            new_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", new_text))
            old_subject = {
                token for token in old_text.split()
                if any(character.isalpha() for character in token)
                and token not in _CONFLICT_STOP_WORDS
            }
            new_subject = {
                token for token in new_text.split()
                if any(character.isalpha() for character in token)
                and token not in _CONFLICT_STOP_WORDS
            }
            opposites = (
                ("must", "must not"), ("always", "never"),
                ("use", "avoid"), ("allowed", "prohibited"),
            )
            contradicts = any(a in old_text and b in new_text or b in old_text and a in new_text
                              for a, b in opposites)
            same_subject = bool(old_subject & new_subject)
            if not (
                old_numbers and new_numbers and old_numbers != new_numbers and same_subject
            ) and not contradicts:
                continue
            kind = "CONFLICT"
        first_id, second_id = sorted((int(old["id"]), entry_id))
        conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_conflicts
                (first_entry_id, second_entry_id, conflict_type)
            VALUES (?, ?, ?)
            """,
            (first_id, second_id, kind),
        )
        field = "potential_duplicates" if kind == "DUPLICATE" else "potential_conflicts"
        for target, related_id in ((int(old["id"]), entry_id), (entry_id, int(old["id"]))):
            row = conn.execute(
                f"SELECT {field} FROM knowledge_entries WHERE id = ?", (target,)
            ).fetchone()
            related = json.loads(row[field] or "[]")
            if related_id not in related:
                related.append(related_id)
            conn.execute(
                f"UPDATE knowledge_entries SET {field} = ? WHERE id = ?",
                (json.dumps(related), target),
            )


def get_knowledge_entries(
    status: str | None = None,
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> list[dict]:
    _require_knowledge_access(principal, brand_id, "read knowledge entries")
    query = """
        SELECT e.*, d.filename AS source_filename, p.name AS product_name
        FROM knowledge_entries e
        LEFT JOIN knowledge_documents d ON d.id = e.document_id
        LEFT JOIN products p ON p.id = e.product_id
    """
    params: list[Any] = []
    filters = []
    if principal is not None:
        if brand_id is None:
            raise ValueError("A brand is required for authenticated knowledge lookup.")
        require_brand_membership(principal, brand_id)
        filters.append("e.brand_id = ?")
        params.append(brand_id)
    if status:
        filters.append("e.status = ?")
        params.append(status)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY e.created_at DESC, e.id DESC"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        for key in ("warnings", "potential_duplicates", "potential_conflicts"):
            item[key] = json.loads(item[key] or "[]")
        results.append(item)
    return results


def get_knowledge_conflicts(
    unresolved_only: bool = False,
    *, principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> list[dict]:
    _require_knowledge_access(principal, brand_id, "read knowledge conflicts")
    query = """
        SELECT c.*, a.content AS first_content, a.category AS first_category,
               a.source_quote AS first_quote, da.filename AS first_source,
               b.content AS second_content, b.category AS second_category,
               b.source_quote AS second_quote, db.filename AS second_source
        FROM knowledge_conflicts c
        JOIN knowledge_entries a ON a.id = c.first_entry_id
        JOIN knowledge_entries b ON b.id = c.second_entry_id
        LEFT JOIN knowledge_documents da ON da.id = a.document_id
        LEFT JOIN knowledge_documents db ON db.id = b.document_id
    """
    filters = []
    params: tuple = ()
    if principal is not None:
        if brand_id is None:
            raise ValueError("A brand is required for authenticated conflict lookup.")
        require_brand_membership(principal, brand_id)
        filters.append("a.brand_id = ? AND b.brand_id = ?")
        params = (brand_id, brand_id)
    if unresolved_only:
        filters.append("c.status = 'UNRESOLVED'")
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY c.id DESC"
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def _archive_conflicts_for_edit(
    conn: sqlite3.Connection, entry_id: int, actor: str
) -> None:
    conflicts = conn.execute(
        "SELECT * FROM knowledge_conflicts "
        "WHERE first_entry_id = ? OR second_entry_id = ?",
        (entry_id, entry_id),
    ).fetchall()
    for conflict in conflicts:
        other_entry_id = (
            conflict["second_entry_id"]
            if conflict["first_entry_id"] == entry_id
            else conflict["first_entry_id"]
        )
        versions = {}
        for related_entry_id in (entry_id, other_entry_id):
            versions[related_entry_id] = conn.execute(
                "SELECT COALESCE(MAX(version_number), 1) AS version "
                "FROM knowledge_entry_versions WHERE entry_id = ?",
                (related_entry_id,),
            ).fetchone()["version"]
        conn.execute(
            """
            INSERT INTO knowledge_conflict_history
                (edited_entry_id, other_entry_id, first_entry_id, second_entry_id,
                 conflict_type, previous_status, resolution_note, resolved_by,
                 resolved_at, edited_entry_version, other_entry_version, actor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id, other_entry_id, conflict["first_entry_id"],
                conflict["second_entry_id"], conflict["conflict_type"],
                conflict["status"], conflict["resolution_note"],
                conflict["resolved_by"], conflict["resolved_at"],
                versions[entry_id], versions[other_entry_id], actor,
            ),
        )
    conn.execute(
        "DELETE FROM knowledge_conflicts WHERE first_entry_id = ? OR second_entry_id = ?",
        (entry_id, entry_id),
    )
    for related in conn.execute(
        "SELECT id, potential_duplicates, potential_conflicts FROM knowledge_entries"
    ).fetchall():
        duplicate_ids = json.loads(related["potential_duplicates"] or "[]")
        conflict_ids = json.loads(related["potential_conflicts"] or "[]")
        if related["id"] == entry_id:
            duplicate_ids = []
            conflict_ids = []
        else:
            duplicate_ids = [value for value in duplicate_ids if value != entry_id]
            conflict_ids = [value for value in conflict_ids if value != entry_id]
        conn.execute(
            "UPDATE knowledge_entries SET potential_duplicates = ?, potential_conflicts = ? "
            "WHERE id = ?",
            (json.dumps(duplicate_ids), json.dumps(conflict_ids), related["id"]),
        )


def edit_pending_entry(
    entry_id: int,
    content: str,
    explanation: str,
    product_id: int | None,
    market: str | None,
    actor: str,
    channel: str | None = None,
    content_type: str | None = None,
    source_document_id: int | None = None,
    source_page: int | None = None,
    source_quote: str | None = None,
    detach_source: bool = False,
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    if not content.strip():
        raise ValueError("Knowledge content cannot be empty.")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None or row["status"] not in {"PENDING_APPROVAL", "NEEDS_CLARIFICATION", "UNRESOLVED"}:
            raise ValueError("Only pending proposals can be edited. Create a new proposal to change approved knowledge.")
        if principal is not None:
            authenticated_actor = _require_entry_brand(
                conn, principal, brand_id, entry_id, row["category"],
                "edit a knowledge proposal",
            )
            actor = authenticated_actor.username
            if product_id is not None:
                require_record_brand(conn, authenticated_actor, brand_id, "products", product_id)
            if source_document_id is not None:
                require_record_brand(
                    conn, authenticated_actor, brand_id, "knowledge_documents", source_document_id
                )
        else:
            _require_knowledge_role(row["category"], actor_role, "edit")
        if detach_source and source_document_id is not None:
            raise ValueError("Choose either a source document or manual entry for the new version.")
        next_document_id = (
            None if detach_source else
            source_document_id if source_document_id is not None else row["document_id"]
        )
        next_source_page = str(source_page) if source_page else None
        next_source_quote = source_quote.strip() if source_quote and source_quote.strip() else None
        if next_source_quote and not next_source_page:
            raise ValueError("Select the source page for the quotation.")
        if next_source_page and not next_document_id:
            raise ValueError("Attach a source document before selecting a source page.")
        if next_source_quote and not _source_quote_matches_page(
            conn, next_document_id, next_source_page, next_source_quote
        ):
            raise ValueError("The quotation must match the selected source page exactly.")
        quote_verified = bool(next_source_quote and next_source_page and next_document_id)
        version = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 AS next FROM knowledge_entry_versions WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()["next"]
        _archive_conflicts_for_edit(conn, entry_id, actor)
        try:
            warnings = json.loads(row["warnings"] or "[]")
        except (TypeError, json.JSONDecodeError):
            warnings = []
        if not isinstance(warnings, list):
            warnings = []
        warnings = [
            warning for warning in warnings
            if warning not in {
                "A document is attached, but no exact quotation has been verified.",
                "Manually supplied; not verified against a source document.",
                "Source support must be confirmed again for this version.",
            }
        ]
        if row["category"] != "ASSET":
            warnings.append(
                "Manually supplied; not verified against a source document."
                if next_document_id is None
                else "A document is attached, but no exact quotation has been verified."
                if not quote_verified
                else ""
            )
            warnings = [warning for warning in warnings if warning]
        conn.execute(
            """
            UPDATE knowledge_entries SET content = ?, explanation = ?, product_id = ?,
                market = ?, channel = ?, content_type = ?, document_id = ?,
                source_page = ?, source_quote = ?, source_quote_verified = ?,
                source_support_confirmed = 0, source_support_confirmed_by = NULL,
                source_support_confirmed_at = NULL, warnings = ?, status = 'PENDING_APPROVAL',
                rule_readiness_classification = CASE WHEN category IN
                    ('BRAND_RULE','TONE','TERMINOLOGY','RESTRICTION') THEN 'UNCLEAR'
                    ELSE rule_readiness_classification END,
                rule_checking_method = CASE WHEN category IN
                    ('BRAND_RULE','TONE','TERMINOLOGY','RESTRICTION') THEN ?
                    ELSE rule_checking_method END,
                rule_type = NULL, rule_parameters = '{}', rule_scope = '{}',
                rule_severity = 'MEDIUM', rule_positive_tests = '[]',
                rule_negative_tests = '[]', clarification_note = NULL
            WHERE id = ?
            """,
            (
                content.strip(), explanation.strip(), product_id, market,
                channel.strip() if channel and channel.strip() else None,
                content_type.strip() if content_type and content_type.strip() else None,
                next_document_id, next_source_page, next_source_quote,
                int(quote_verified), json.dumps(warnings, ensure_ascii=False),
                _DEFAULT_RULE_METHOD,
                entry_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO knowledge_entry_versions
                (entry_id, version_number, content, explanation, actor, source_quote,
                 source_page, source_document_id, product_id, market, channel, content_type,
                 source_quote_verified, source_support_confirmed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                entry_id, version, content.strip(), explanation.strip(), actor,
                next_source_quote, next_source_page, next_document_id, product_id,
                market, channel.strip() if channel and channel.strip() else None,
                content_type.strip() if content_type and content_type.strip() else None,
                int(quote_verified),
            ),
        )
        conn.execute(
            "INSERT INTO knowledge_decisions (entry_id, decision, actor, note) VALUES (?, 'EDITED', ?, ?)",
            (entry_id, actor, f"Saved version {version}"),
        )
        _audit_knowledge_change(
            conn, principal, action="KNOWLEDGE_PROPOSAL_EDITED",
            object_type="knowledge_entry", object_id=entry_id, brand_id=brand_id,
            details={"version": version},
        )
        if row["category"] in _RULE_CATEGORIES:
            readiness_version = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 AS next "
                "FROM rule_readiness_history WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()["next"]
            conn.execute(
                """
                INSERT INTO rule_readiness_history
                    (entry_id, version_number, event_type, actor,
                     readiness_classification, checking_method,
                     source_document_id, source_page, source_quote,
                     source_quote_verified, source_support_confirmed, note)
                VALUES (?, ?, 'EDITED', ?, 'UNCLEAR', ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    entry_id, readiness_version, actor, _DEFAULT_RULE_METHOD,
                    next_document_id, next_source_page, next_source_quote,
                    int(quote_verified),
                    "Proposal text changed; readiness must be reviewed again.",
                ),
            )
        updated = conn.execute(
            "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        _record_related_entries(conn, entry_id)
        conn.commit()


def resolve_knowledge_conflict(
    conflict_id: int,
    resolution: str,
    actor: str,
    note: str = "",
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    if resolution not in {"KEEP_BOTH", "FIRST", "SECOND", "DISMISSED"}:
        raise ValueError("Choose a valid conflict resolution.")
    with get_connection() as conn:
        conflict = conn.execute(
            """
            SELECT a.id AS first_id, a.category AS first_category,
                   b.id AS second_id, b.category AS second_category
            FROM knowledge_conflicts c
            JOIN knowledge_entries a ON a.id = c.first_entry_id
            JOIN knowledge_entries b ON b.id = c.second_entry_id
            WHERE c.id = ? AND c.status = 'UNRESOLVED'
            """,
            (conflict_id,),
        ).fetchone()
        if conflict is None:
            raise ValueError("Conflict does not exist or has already been resolved.")
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required to resolve a knowledge conflict.")
            authenticated_actor = require_principal(
                principal, action="resolve knowledge conflicts"
            )
            require_record_brand(
                conn, authenticated_actor, brand_id, "knowledge_entries",
                conflict["first_id"],
            )
            require_record_brand(
                conn, authenticated_actor, brand_id, "knowledge_entries",
                conflict["second_id"],
            )
            actor = authenticated_actor.username
        categories = {conflict["first_category"], conflict["second_category"]}
        needed_role = (
            "fact owner"
            if categories & {"PRODUCT_FACT", "EVIDENCE", "ASSET"}
            else "reviewer"
        )
        allowed_roles = {needed_role}
        if needed_role == "reviewer":
            allowed_roles.add("brand manager")
        if principal is not None:
            needed = (BrandRole.OWNER, BrandRole.FACT_OWNER) if needed_role == "fact owner" else (
                BrandRole.OWNER, BrandRole.REVIEWER,
            )
            require_brand_membership(principal, brand_id, needed)
        else:
            role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
            if role_value.strip().casefold() not in allowed_roles:
                raise PermissionError(f"This conflict requires the {needed_role.title()} demo role.")
        cursor = conn.execute(
            """
            UPDATE knowledge_conflicts SET status = ?, resolution_note = ?,
                resolved_by = ?, resolved_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ? AND status = 'UNRESOLVED'
            """,
            (resolution, note.strip(), actor, conflict_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Conflict does not exist or has already been resolved.")
        _audit_knowledge_change(
            conn, principal, action="KNOWLEDGE_CONFLICT_RESOLVED",
            object_type="knowledge_conflict", object_id=conflict_id,
            brand_id=brand_id, details={"resolution": resolution},
        )
        conn.commit()


def decide_knowledge_entry(
    entry_id: int,
    decision: str,
    actor: str,
    note: str = "",
    *,
    actor_role: UserRole | str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> dict:
    if decision not in {"APPROVE", "REJECT", "CLARIFY", "UNRESOLVED"}:
        raise ValueError("Choose approve, reject, request clarification, or mark unresolved.")
    staged_files: list[Path] = []
    linked = None
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        entry = conn.execute(
            """
            SELECT e.*, d.filename AS source_filename
            FROM knowledge_entries e
            LEFT JOIN knowledge_documents d ON d.id = e.document_id
            WHERE e.id = ?
            """,
            (entry_id,),
        ).fetchone()
        if entry is None:
            raise ValueError(f"Knowledge proposal {entry_id} does not exist.")
        if principal is not None:
            authenticated_actor = _require_entry_brand(
                conn, principal, brand_id, entry_id, entry["category"],
                f"{decision.lower()} a knowledge proposal",
            )
            actor = authenticated_actor.username
        else:
            _require_knowledge_role(entry["category"], actor_role, decision.lower())
        if entry["status"] not in {"PENDING_APPROVAL", "NEEDS_CLARIFICATION", "UNRESOLVED"}:
            raise ValueError("Only pending proposals can receive a new decision.")
        if decision == "APPROVE":
            if entry["category"] in _RULE_CATEGORIES:
                raise ValueError(
                    "Rule proposals require a Reviewer decision through the Rule Readiness page."
                )
            if entry["category"] == "PRODUCT_FACT" and (
                not entry["product_id"] or not entry["market"]
            ):
                raise ValueError("Assign a product and market to a fact before approving it.")
            if entry["category"] == "ASSET" and (
                not entry["product_id"] or not entry["document_id"]
            ):
                raise ValueError("An uploaded image and product are required for an asset proposal.")
            if entry["document_id"] and entry["category"] != "ASSET":
                if not entry["source_quote_verified"] or not _source_quote_matches_page(
                    conn, entry["document_id"], entry["source_page"], entry["source_quote"]
                ):
                    raise ValueError(
                        "Verify an exact quotation on the selected source page before approval."
                    )
                if not entry["source_support_confirmed"]:
                    raise ValueError(
                        "A reviewer must confirm that the quotation supports this proposal before approval."
                    )
            conflicts = conn.execute(
                """
                SELECT * FROM knowledge_conflicts
                WHERE status = 'UNRESOLVED'
                  AND (first_entry_id = ? OR second_entry_id = ?)
                """,
                (entry_id, entry_id),
            ).fetchall()
            if conflicts:
                raise ValueError("Resolve the displayed duplicate or conflict before approving this proposal.")
            decisions = conn.execute(
                """
                SELECT * FROM knowledge_conflicts
                WHERE first_entry_id = ? OR second_entry_id = ?
                """,
                (entry_id, entry_id),
            ).fetchall()
            for conflict in decisions:
                selected = (
                    conflict["first_entry_id"] if conflict["status"] == "FIRST"
                    else conflict["second_entry_id"] if conflict["status"] == "SECOND"
                    else entry_id
                )
                if conflict["status"] in {"FIRST", "SECOND"} and selected != entry_id:
                    raise ValueError("The conflict resolution selected the other proposal.")
            approved_version = conn.execute(
                "SELECT COALESCE(MAX(version_number), 1) AS n FROM knowledge_entry_versions WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()["n"]
            conn.execute(
                """
                UPDATE knowledge_entries SET status = 'APPROVED', approved_by = ?,
                    approved_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                    approved_version = ? WHERE id = ?
                """,
                (actor, approved_version, entry_id),
            )
        else:
            status = {
                "REJECT": "REJECTED",
                "CLARIFY": "NEEDS_CLARIFICATION",
                "UNRESOLVED": "UNRESOLVED",
            }[decision]
            conn.execute("UPDATE knowledge_entries SET status = ? WHERE id = ?", (status, entry_id))
        conn.execute(
            "INSERT INTO knowledge_decisions (entry_id, decision, actor, note) VALUES (?, ?, ?, ?)",
            (entry_id, decision, actor, note.strip()),
        )
        if entry["category"] in _RULE_CATEGORIES:
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
                     source_document_id, source_page, source_quote, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id, history_version, decision, actor,
                    entry["rule_readiness_classification"] or "UNCLEAR",
                    entry["rule_checking_method"] or _DEFAULT_RULE_METHOD,
                    entry["rule_type"], entry["rule_parameters"],
                    entry["rule_scope"], entry["rule_severity"],
                    entry["rule_positive_tests"], entry["rule_negative_tests"],
                    entry["document_id"], entry["source_page"],
                    entry["source_quote"], note.strip(),
                ),
            )
        snapshot = dict(entry)
        if decision == "APPROVE":
            try:
                linked = _activate_approved_entry(conn, snapshot, actor, staged_files)
            except Exception:
                conn.rollback()
                for path in staged_files:
                    path.unlink(missing_ok=True)
                with get_connection() as audit_conn:
                    audit_conn.execute(
                        "INSERT INTO knowledge_decisions (entry_id, decision, actor, note) "
                        "VALUES (?, 'ACTIVATION_FAILED', ?, 'Downstream record creation failed; approval rolled back')",
                        (entry_id, actor),
                    )
                    audit_conn.commit()
                raise
        try:
            if linked:
                conn.execute(
                    "UPDATE knowledge_entries SET linked_record_type = ?, linked_record_id = ? WHERE id = ?",
                    (linked["type"], linked["id"], entry_id),
                )
            _audit_knowledge_change(
                conn, principal, action=f"KNOWLEDGE_{decision}",
                object_type="knowledge_entry", object_id=entry_id,
                brand_id=brand_id, details={
                    "category": entry["category"],
                    "linked_record_type": linked["type"] if linked else None,
                    "linked_record_id": linked["id"] if linked else None,
                },
            )
            conn.commit()
        except Exception:
            conn.rollback()
            for path in staged_files:
                path.unlink(missing_ok=True)
            if decision == "APPROVE":
                with get_connection() as audit_conn:
                    audit_conn.execute(
                        "INSERT INTO knowledge_decisions (entry_id, decision, actor, note) "
                        "VALUES (?, 'ACTIVATION_FAILED', ?, 'Activation commit failed; approval rolled back')",
                        (entry_id, actor),
                    )
                    audit_conn.commit()
            raise
    return {"decision": decision, "linked_record": linked}


def _activate_approved_entry(
    conn: sqlite3.Connection,
    entry: dict,
    actor: str,
    staged_files: list[Path],
) -> dict | None:
    category = entry["category"]
    source_ref = (
        f"{entry['source_filename']} page {entry['source_page']}"
        if (
            entry.get("source_filename") and entry.get("source_page")
            and entry.get("source_quote_verified")
            and entry.get("source_support_confirmed")
        )
        else "Manual knowledge (not source-verified)"
    )
    if category in {"BRAND_RULE", "TONE", "TERMINOLOGY", "RESTRICTION"}:
        cursor = conn.execute(
            """
            INSERT INTO guidelines
                (product_id, title, content, source_ref, version, is_active,
                 source_type, section_ref, market_scope, channel_scope,
                 content_type, source_document_id, source_page, source_quote)
            VALUES (?, ?, ?, ?, '1.0', 1, 'onboarding', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["product_id"], category.replace("_", " ").title(),
                entry["content"], source_ref, entry.get("source_page") or "",
                entry.get("market"), entry.get("channel"), entry.get("content_type"),
                entry.get("document_id"), entry.get("source_page"), entry.get("source_quote"),
            ),
        )
        return {"type": "guideline", "id": int(cursor.lastrowid)}
    if category == "PRODUCT_FACT":
        if not entry["product_id"] or not entry["market"]:
            raise ValueError("Assign a product and market to a fact before approving it.")
        fact_cursor = conn.execute(
            "INSERT INTO facts (product_id, name, category, markets) VALUES (?, ?, 'onboarding_fact', ?)",
            (entry["product_id"], entry["content"][:120], json.dumps([entry["market"]])),
        )
        version_cursor = conn.execute(
            """
            INSERT INTO fact_versions
                (fact_id, version_number, statement, evidence_ref, approval_status,
                 approved_by, approved_at)
            VALUES (?, 1, ?, ?, 'APPROVED', ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            """,
            (fact_cursor.lastrowid, entry["content"], source_ref, actor),
        )
        return {"type": "fact_version", "id": int(version_cursor.lastrowid)}
    if category == "ASSET":
        if not entry["product_id"] or not entry.get("document_id"):
            raise ValueError("Select a product and source document for an asset proposal.")
        document = conn.execute(
            "SELECT filename, original_bytes FROM knowledge_documents WHERE id = ?",
            (entry["document_id"],),
        ).fetchone()
        if document is None:
            raise ValueError("The source image document no longer exists.")
        if Path(document["filename"]).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            return None
        from services.asset_service import register_image_asset
        asset_id = register_image_asset(
            name=entry["content"][:120],
            asset_type="onboarding_asset",
            filename=document["filename"],
            data=document["original_bytes"],
            product_id=entry["product_id"],
            connection=conn,
            staged_files=staged_files,
        )
        return {"type": "asset", "id": asset_id}
    # Evidence is retained as a verified source record, never made into a fact.
    return None


def get_setup_readiness(
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> dict:
    _require_knowledge_access(principal, brand_id, "read workspace setup readiness")
    with get_connection() as conn:
        scope_brand = None
        profile_brand = None
        if principal is not None:
            if brand_id is None:
                raise ValueError("A brand is required for authenticated setup readiness.")
            membership = require_brand_membership(principal, brand_id)
            scope_brand, profile_brand = brand_id, membership["name"]
        profiles = conn.execute(
            "SELECT COUNT(*) AS n FROM brand_profiles" +
            (" WHERE brand = ?" if profile_brand is not None else ""),
            (profile_brand,) if profile_brand is not None else (),
        ).fetchone()["n"]
        approved_entries = conn.execute(
            "SELECT category, COUNT(*) AS n FROM knowledge_entries WHERE status = 'APPROVED'" +
            (" AND brand_id = ?" if scope_brand is not None else "") + " GROUP BY category",
            (scope_brand,) if scope_brand is not None else (),
        ).fetchall()
        category_counts = {row["category"]: row["n"] for row in approved_entries}
        rules = conn.execute(
            "SELECT COUNT(*) AS n FROM guidelines WHERE is_active = 1" +
            (" AND brand_id = ?" if scope_brand is not None else ""),
            (scope_brand,) if scope_brand is not None else (),
        ).fetchone()["n"]
        facts = conn.execute(
            "SELECT COUNT(*) AS n FROM fact_versions fv JOIN facts f ON f.id = fv.fact_id "
            "JOIN products p ON p.id = f.product_id WHERE fv.approval_status = 'APPROVED'" +
            (" AND p.brand_id = ?" if scope_brand is not None else ""),
            (scope_brand,) if scope_brand is not None else (),
        ).fetchone()["n"]
        assets = conn.execute(
            "SELECT COUNT(*) AS n FROM brand_assets WHERE owner_approved = 1 AND is_active = 1" +
            (" AND brand_id = ?" if scope_brand is not None else ""),
            (scope_brand,) if scope_brand is not None else (),
        ).fetchone()["n"]
        conflicts = conn.execute(
            "SELECT COUNT(*) AS n FROM knowledge_conflicts c "
            "JOIN knowledge_entries a ON a.id = c.first_entry_id "
            "JOIN knowledge_entries b ON b.id = c.second_entry_id "
            "WHERE c.status = 'UNRESOLVED'" +
            (" AND a.brand_id = ? AND b.brand_id = ?" if scope_brand is not None else ""),
            (scope_brand, scope_brand) if scope_brand is not None else (),
        ).fetchone()["n"]
        pending_entries = conn.execute(
            """
            SELECT COUNT(*) AS n FROM knowledge_entries
            WHERE status IN ('PENDING_APPROVAL','NEEDS_CLARIFICATION','UNRESOLVED')""" +
            (" AND brand_id = ?" if scope_brand is not None else ""),
            (scope_brand,) if scope_brand is not None else (),
        ).fetchone()["n"]
        attention = conn.execute(
            """
            SELECT COUNT(*) AS n FROM knowledge_documents
            WHERE (extraction_status IN ('NEEDS_ATTENTION','FAILED')
               OR ai_extraction_status IN ('FAILED','PARTIAL'))""" +
            (" AND brand_id = ?" if scope_brand is not None else ""),
            (scope_brand,) if scope_brand is not None else (),
        ).fetchone()["n"]
        review_count = conn.execute(
            "SELECT COUNT(*) AS n FROM drafts" +
            (" WHERE brand_id = ?" if scope_brand is not None else ""),
            (scope_brand,) if scope_brand is not None else (),
        ).fetchone()["n"]
        approval_count = conn.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE status IN ('READY_FOR_REVIEW','APPROVED')" +
            (" AND brand_id = ?" if scope_brand is not None else ""),
            (scope_brand,) if scope_brand is not None else (),
        ).fetchone()["n"]
        products = conn.execute(
            "SELECT COUNT(*) AS n FROM products" +
            (" WHERE brand_id = ?" if scope_brand is not None else ""),
            (scope_brand,) if scope_brand is not None else (),
        ).fetchone()["n"]
    return {
        "brand_identity": profiles > 0,
        "products": products,
        "rules": rules,
        "facts": facts,
        "terminology": category_counts.get("TERMINOLOGY", 0),
        "assets": assets,
        "conflicts": conflicts,
        "pending_entries": pending_entries,
        "documents_attention": attention,
        "reviews": review_count,
        "approvals": approval_count,
        "checklist": [
            {"label": "Set up brand information", "done": profiles > 0},
            {"label": "Add or upload brand guidelines", "done": rules > 0},
            {"label": "Verify proposed brand knowledge", "done": category_counts.get("BRAND_RULE", 0) + category_counts.get("TONE", 0) > 0},
            {"label": "Add approved product facts", "done": facts > 0},
            {"label": "Register an approved brand asset", "done": assets > 0},
            {"label": "Run your first content review", "done": review_count > 0},
            {"label": "Submit a draft for human approval", "done": approval_count > 0},
        ],
    }


def get_document_status(
    document_id: int,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> str:
    return get_source_document(document_id, principal, brand_id)["extraction_status"]
