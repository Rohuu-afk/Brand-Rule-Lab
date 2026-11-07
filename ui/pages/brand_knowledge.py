"""Guided brand setup, source document recovery, and proposal review."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from models.enums import UserRole
from ui.components import page_header
from ui.styles import inject_styles
from services.document_parser import MAX_BYTES
from services.guideline_service import get_all_products
from services.knowledge_onboarding import (
    KnowledgeOnboardingError,
    add_manual_entry,
    correct_extracted_page,
    create_onboarding_product,
    confirm_source_support,
    decide_knowledge_entry,
    edit_pending_entry,
    extract_document_proposals,
    get_brand_profiles,
    get_knowledge_conflicts,
    get_knowledge_entries,
    get_original_page_preview,
    get_source_documents,
    resolve_knowledge_conflict,
    save_brand_profile,
    save_source_document,
    verify_source_quote_location,
)


_DOCUMENT_TYPES = [
    "Brand guidelines",
    "Product information",
    "Scientific or research evidence",
    "Approved terminology",
    "Marketing and campaign information",
    "Visual assets",
    "Other supporting document",
]
_PERSONALITY_PRESETS = [
    "Formal", "Professional", "Friendly", "Playful", "Premium", "Minimal", "Other",
]
_CATEGORY_LABELS = {
    "BRAND_RULE": "Brand rule",
    "TONE": "Tone and writing",
    "TERMINOLOGY": "Approved terminology",
    "PRODUCT_FACT": "Product fact",
    "EVIDENCE": "Supporting evidence",
    "RESTRICTION": "Restriction",
    "ASSET": "Visual asset",
}


def _product_options() -> tuple[list[str], dict[str, int | None]]:
    products = get_all_products()
    options = ["No specific product"]
    mapping: dict[str, int | None] = {options[0]: None}
    for product in products:
        label = f"{product['name']} — {product['brand']} (#{product['id']})"
        options.append(label)
        mapping[label] = product["id"]
    return options, mapping


def _render_provenance(entry: dict, role: str) -> None:
    if not entry.get("document_id"):
        st.caption("Provenance: manually supplied; not verified against a source document.")
        return
    if not entry.get("source_quote") or not entry.get("source_page"):
        st.warning("Document attached only. No exact quotation and page have been verified.")
        return
    if not entry.get("source_quote_verified"):
        st.warning("Quotation is recorded but its exact location on this page is not verified.")
        if st.button("Verify quotation location", key=f"verify_quote_{entry['id']}"):
            try:
                verify_source_quote_location(entry["id"], role, actor_role=role)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.success("Exact quotation located on the selected page.")
                st.rerun()
        return
    st.info(f"Exact quotation located on page {entry['source_page']}.")
    if entry.get("source_support_confirmed"):
        st.success(
            "Reviewer confirmed support"
            + (f" ({entry['source_support_confirmed_by']})." if entry.get("source_support_confirmed_by") else ".")
        )
        return
    st.warning("Quotation location is verified; whether it supports this proposal is not yet confirmed.")
    with st.form(f"confirm_source_support_{entry['id']}"):
        confirmed = st.checkbox(
            "I reviewed this quotation and confirm that it supports the proposed knowledge."
        )
        note = st.text_input("Reviewer note (optional)")
        if st.form_submit_button("Confirm source support"):
            if not confirmed:
                st.error("Confirm the statement above before recording source support.")
            else:
                try:
                    confirm_source_support(entry["id"], role, note, actor_role=role)
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.success("Source support confirmation recorded.")
                    st.rerun()


def _source_preview(document_id: int, filename: str, page_number: int) -> None:
    preview = get_original_page_preview(document_id, page_number)
    if preview:
        st.image(preview, caption=f"Original page {page_number}", width="stretch")
    elif Path(filename).suffix.lower() in {".png", ".jpg", ".jpeg"}:
        from services.knowledge_onboarding import get_source_document
        document = get_source_document(document_id)
        st.image(document["original_bytes"], caption="Preserved original image", width="stretch")
    else:
        st.caption("Original file is preserved. Preview is available for PDF and image files.")


def render() -> None:
    inject_styles()
    page_header("Workspace", "Brand Knowledge")
    st.write(
        "Bring together your brand rules, writing guidance, product facts, evidence, "
        "restrictions and visual assets. Nothing suggested by AI becomes active without review."
    )
    role = st.session_state.get("sidebar_role", UserRole.CREATOR.value)
    tabs = st.tabs(["Set up your brand", "Upload and recover documents", "Review proposals"])

    with tabs[0]:
        _render_manual_setup()
    with tabs[1]:
        _render_documents(role)
    with tabs[2]:
        _render_proposals(role)


def _render_manual_setup() -> None:
    st.subheader("Create your brand knowledge")
    st.caption("Skip anything that is not relevant. You can return and continue later.")
    profiles = get_brand_profiles()
    profile_default = profiles[0] if profiles else {}

    with st.expander("A. Brand identity and tone", expanded=not profiles):
        with st.form("brand_identity_form"):
            brand = st.text_input("Brand name", value=profile_default.get("brand", ""))
            industry = st.text_input("Industry", value=profile_default.get("industry", ""))
            audience = st.text_input("Target audience", value=profile_default.get("audience", ""))
            description = st.text_area(
                "Brand description and products or services",
                value=profile_default.get("description", ""),
            )
            preset_personality = st.multiselect(
                "Brand personality (optional)",
                _PERSONALITY_PRESETS,
                help="Choose any descriptors that fit, then add your own wording below.",
            )
            personality = st.text_area(
                "Preferred tone and custom personality",
                value=profile_default.get("personality", ""),
                placeholder="Describe the voice in normal language; e.g., warm, clear and never pushy.",
            )
            visual_identity = st.text_area(
                "Visual identity (colours, fonts, logo and packaging guidance)",
                value=profile_default.get("visual_identity", ""),
                placeholder="Record brand colours, typography and visual-use rules. Upload actual approved assets below.",
                key="brand_visual_identity_draft",
            )
            if st.form_submit_button("Save brand identity"):
                try:
                    descriptions = preset_personality + ([personality.strip()] if personality.strip() else [])
                    visual_identity = st.session_state.get("brand_visual_identity_draft", "")
                    save_brand_profile(
                        brand, industry, audience, description,
                        "; ".join(dict.fromkeys(descriptions)), visual_identity,
                    )
                    st.success("Brand identity saved.")
                except ValueError as exc:
                    st.error(str(exc))

    with st.expander("Add a product or service"):
        with st.form("onboarding_product_form"):
            product_name = st.text_input("Product or service name")
            brand_name = st.text_input("Brand name")
            product_description = st.text_area("Description or specifications")
            market = st.text_input("Applicable market (for example IN)")
            if st.form_submit_button("Save product"):
                try:
                    product_id = create_onboarding_product(
                        product_name, brand_name, product_description, market
                    )
                    st.success(f"Saved product #{product_id}.")
                except ValueError as exc:
                    st.error(str(exc))

    st.markdown("#### Add proposed rules, facts, terminology or restrictions")
    options, mapping = _product_options()
    category = st.selectbox(
        "Knowledge category", list(_CATEGORY_LABELS),
        format_func=lambda key: _CATEGORY_LABELS[key],
    )
    content = st.text_area("Knowledge to review", key="manual_knowledge_content")
    explanation = st.text_area("Context or explanation", key="manual_knowledge_explanation")
    product_label = st.selectbox("Product scope", options, key="manual_knowledge_product")
    market = st.text_input("Market scope (leave blank if not specified)", key="manual_knowledge_market")
    channel = st.text_input("Channel scope (optional)", key="manual_knowledge_channel")
    content_type = st.text_input(
        "Content type scope (optional)",
        key="manual_knowledge_content_type",
        placeholder="For example: paid social, product page",
    )
    documents = get_source_documents()
    document_labels = ["No source document"] + [
        f"{item['filename']} (#{item['id']})" for item in documents
    ]
    document_ids = {label: item["id"] for label, item in zip(document_labels[1:], documents)}
    selected_document = st.selectbox("Optional source document", document_labels)
    selected_page = None
    source_quote = None
    selected_id = document_ids.get(selected_document)
    if selected_id:
        doc = next(item for item in documents if item["id"] == selected_id)
        page_options = [item["page_number"] for item in doc["page_records"]]
        if page_options:
            selected_page = st.selectbox("Source page", page_options, key="manual_source_page")
            source_quote = st.text_area(
                "Exact quotation from that page",
                help=(
                    "A document attachment is not verified evidence. The quotation must match "
                    "the selected page, then a reviewer must confirm that it supports the proposal."
                ),
            )
    if st.button("Save as pending proposal"):
        try:
            entry_id = add_manual_entry(
                category=category,
                content=content,
                explanation=explanation,
                product_id=mapping[product_label],
                market=market,
                channel=channel,
                content_type=content_type,
                document_id=selected_id,
                source_page=selected_page,
                source_quote=source_quote,
            )
            st.success(f"Proposal #{entry_id} saved for human review.")
        except (ValueError, KnowledgeOnboardingError) as exc:
            st.error(str(exc))
    st.info("Manual entries are proposals too. They do not become active until an authorised demo role approves them.")


def _render_documents(role: str) -> None:
    st.subheader("Upload an existing document")
    st.caption(
        "Supported here: PDF, DOCX, TXT, CSV, XLSX, PNG and JPEG. Legacy XLS workbooks are not supported. "
        "Raw files stay in the local database; extracted text is sent to NVIDIA NIM or its configured "
        "Gemini fallback only when you explicitly request AI extraction below."
    )
    options, mapping = _product_options()
    with st.form("knowledge_document_upload", clear_on_submit=True):
        upload = st.file_uploader(
            "Choose a brand document or image",
            type=["pdf", "docx", "txt", "csv", "xlsx", "png", "jpg", "jpeg"],
            max_upload_size=max(1, MAX_BYTES // (1024 * 1024)),
        )
        document_type = st.selectbox("Document type", _DOCUMENT_TYPES)
        purpose = st.text_input(
            "Intended purpose",
            placeholder="For example: source of current ingredient percentages",
        )
        product_label = st.selectbox("Related product (optional)", options, key="upload_product")
        market = st.text_input("Market scope (optional)", key="upload_market")
        channel = st.text_input("Channel scope (optional)", key="upload_channel")
        content_type = st.text_input(
            "Content type scope (optional)",
            key="upload_content_type",
            placeholder="For example: paid social, product page",
        )
        save_upload = st.form_submit_button("Save and inspect document")
    if save_upload:
        if upload is None:
            st.error("Choose a supported file first.")
        else:
            try:
                document_id = save_source_document(
                    upload.name, upload.getvalue(), document_type, purpose, role,
                    mapping[product_label], market, channel, content_type,
                )
                st.session_state["onboarding_last_document_id"] = document_id
                st.success(f"Document saved locally as source #{document_id}. Original file is preserved.")
            except KnowledgeOnboardingError as exc:
                st.error(str(exc))

    documents = get_source_documents()
    if not documents:
        st.info("No source documents uploaded yet. You can also create proposals manually.")
        return
    labels = [f"#{item['id']} — {item['filename']}" for item in documents]
    id_by_label = {label: item["id"] for label, item in zip(labels, documents)}
    default_id = st.session_state.get("onboarding_last_document_id")
    default_index = next(
        (i for i, item in enumerate(documents) if item["id"] == default_id), 0
    )
    selected_label = st.selectbox("Saved source document", labels, index=default_index)
    document_id = id_by_label[selected_label]
    selected = next(item for item in documents if item["id"] == document_id)
    status_placeholder = st.empty()
    status_placeholder.caption(
        f"Type: {selected['document_type']} · Text extraction: {selected['extraction_status']} · "
        f"AI proposals: {selected['ai_extraction_status']}"
    )
    extraction_error_placeholder = st.empty()
    if selected["ai_extraction_error"]:
        extraction_error_placeholder.warning(selected["ai_extraction_error"])

    for page in selected["page_records"]:
        page_number = page["page_number"]
        warnings = page.get("warnings", [])
        method = page.get("method", "text")
        quality_label = (
            "OCR confidence (not factual accuracy)"
            if method in {"ocr", "text+ocr"} else "text quality"
        )
        title = (
            f"Page / section {page_number} · {method} · "
            f"{quality_label} {page.get('quality', 0):.0%}"
        )
        with st.expander(title, expanded=bool(warnings)):
            original_col, text_col = st.columns(2)
            with original_col:
                _source_preview(document_id, selected["filename"], page_number)
            with text_col:
                if method in {"ocr", "text+ocr"}:
                    st.caption(
                        "OCR is an estimate of recognition quality, not proof that the "
                        "text is factually correct. Compare every important number and claim "
                        "with the original page before approving a proposal."
                    )
                if warnings:
                    for warning in warnings:
                        st.warning(warning)
                corrected = st.text_area(
                    "Extracted text (edit to correct)",
                    value=page.get("text", ""),
                    height=200,
                    key=f"page_text_{document_id}_{page_number}",
                )
                if st.button("Save corrected text", key=f"correct_{document_id}_{page_number}"):
                    try:
                        correct_extracted_page(document_id, page_number, corrected)
                        st.success("Correction saved. The original source remains unchanged.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

    st.markdown("#### Propose structured knowledge with AI")
    st.caption(
        "AI may propose rules, tone, terminology, facts, evidence, restrictions or assets. "
        "It cannot approve them. Verify quotations and uncertain numbers against the original."
    )
    consent = st.checkbox(
        "I consent to sending this document's extracted text to the configured external AI provider "
        "(NVIDIA NIM, with Gemini fallback if configured) to prepare unapproved proposals."
    )
    if st.button("Extract proposals with AI", disabled=not consent, key=f"extract_{document_id}"):
        try:
            result = extract_document_proposals(document_id)
            summary = (
                f"Processed {result['chunks_processed']} chunk(s); created "
                f"{result['created']} source-validated pending proposal(s); rejected "
                f"{result['rejected']} invalid, untraceable or duplicate item(s); "
                f"{result['failed_chunks']} chunk(s) failed."
            )
            if result["provider"] and result["model"]:
                summary += f" Provider: {result['provider']} ({result['model']})."
            if result["used_fallback"]:
                summary += " A configured fallback provider was used."
            if result["failed_chunks"] or result["rejected"]:
                st.warning(summary)
            else:
                st.success(summary)
            with st.expander("Extraction chunk diagnostics"):
                for chunk in result["chunk_diagnostics"]:
                    usage = chunk.get("initial_usage", {})
                    tokens = (
                        f"{usage['completion_tokens']} completion / "
                        f"{usage['total_tokens']} total tokens"
                        if "completion_tokens" in usage and "total_tokens" in usage
                        else "usage unavailable"
                    )
                    details = (
                        f"Chunk {chunk['chunk_number']} · page {chunk['page_number']} · "
                        f"{chunk['status']} · finish={chunk.get('initial_finish_reason') or 'not supplied'} · "
                        f"{tokens}"
                    )
                    if chunk.get("repair_attempted"):
                        details += (
                            f" · repair finish="
                            f"{chunk.get('repair_finish_reason') or 'not supplied'}"
                        )
                    st.caption(details)
        except KnowledgeOnboardingError as exc:
            st.error(str(exc))
        finally:
            selected = next(
                item for item in get_source_documents() if item["id"] == document_id
            )
            status_placeholder.caption(
                f"Type: {selected['document_type']} · Text extraction: {selected['extraction_status']} · "
                f"AI proposals: {selected['ai_extraction_status']}"
            )
            extraction_error_placeholder.empty()
            if selected["ai_extraction_error"]:
                extraction_error_placeholder.warning(selected["ai_extraction_error"])


def _render_proposals(role: str) -> None:
    st.subheader("Human review queue")
    st.caption(
        "The sidebar role is simulated for this demo. Brand rules need Reviewer; product facts, "
        "evidence and assets need Fact Owner. There is no sign-in or real role-based access control."
    )

    conflicts = get_knowledge_conflicts(unresolved_only=True)
    if conflicts:
        st.markdown("#### Potential duplicates and conflicts")
        st.warning("These are not resolved automatically. Compare both entries and source documents.")
        for conflict in conflicts:
            with st.container(border=True):
                st.write(f"**{conflict['conflict_type'].title()} #{conflict['id']}**")
                left, right = st.columns(2)
                with left:
                    st.write(conflict["first_content"])
                    st.caption(f"Source: {conflict['first_source'] or 'Manual'}")
                    if conflict["first_quote"]:
                        st.caption(f"Quote: “{conflict['first_quote']}”")
                with right:
                    st.write(conflict["second_content"])
                    st.caption(f"Source: {conflict['second_source'] or 'Manual'}")
                    if conflict["second_quote"]:
                        st.caption(f"Quote: “{conflict['second_quote']}”")
                if role in {UserRole.REVIEWER.value, UserRole.FACT_OWNER.value}:
                    resolution = st.selectbox(
                        "Human decision",
                        ["FIRST", "SECOND", "KEEP_BOTH", "DISMISSED"],
                        format_func=lambda value: {
                            "FIRST": "Keep the first proposal",
                            "SECOND": "Keep the second proposal",
                            "KEEP_BOTH": "Both can be correct in their scopes",
                            "DISMISSED": "Not a real duplicate or conflict",
                        }[value],
                        key=f"conflict_resolution_{conflict['id']}",
                    )
                    note = st.text_input("Resolution note", key=f"conflict_note_{conflict['id']}")
                    if st.button("Save conflict decision", key=f"resolve_{conflict['id']}"):
                        try:
                            resolve_knowledge_conflict(
                                conflict["id"], resolution, role, note, actor_role=role
                            )
                            st.success("Resolution recorded. Proposals still require individual approval.")
                            st.rerun()
                        except (ValueError, PermissionError) as exc:
                            st.error(str(exc))

    entries = get_knowledge_entries()
    pending = [item for item in entries if item["status"] in {
        "PENDING_APPROVAL", "NEEDS_CLARIFICATION", "UNRESOLVED"
    }]
    if not pending:
        st.info("No proposals are waiting for review. Upload documents or add knowledge manually to get started.")
    for entry in pending:
        label = f"#{entry['id']} · {_CATEGORY_LABELS[entry['category']]} · {entry['status'].replace('_', ' ').title()}"
        with st.expander(label, expanded=False):
            st.caption(
                f"Source: {entry['source_filename'] or 'Manual'}"
                + (f" · page {entry['source_page']}" if entry["source_page"] else "")
                + (f" · product {entry['product_name']}" if entry["product_name"] else "")
            )
            if entry["source_quote"]:
                st.info(f"Exact source quotation: “{entry['source_quote']}”")
            _render_provenance(entry, role)
            for warning in entry["warnings"]:
                st.warning(warning)
            if entry["confidence"] is not None:
                st.caption(f"AI extraction confidence estimate: {entry['confidence']:.0%}; not a truth or approval score.")
            if entry["document_id"]:
                source = next(
                    (item for item in get_source_documents() if item["id"] == entry["document_id"]),
                    None,
                )
                if source and entry["source_page"]:
                    _source_preview(entry["document_id"], source["filename"], int(entry["source_page"]))

            options, mapping = _product_options()
            selected_product = next(
                (label for label, product_id in mapping.items() if product_id == entry["product_id"]),
                options[0],
            )
            documents = get_source_documents()
            source_options = ["Manual entry (no document)"] + [
                f"{item['filename']} (#{item['id']})" for item in documents
            ]
            source_mapping = {
                label: item["id"]
                for label, item in zip(source_options[1:], documents)
            }
            selected_source = next(
                (
                    label for label, source_id in source_mapping.items()
                    if source_id == entry.get("document_id")
                ),
                source_options[0],
            )
            with st.form(f"edit_entry_{entry['id']}"):
                content = st.text_area("Proposed content", value=entry["content"])
                explanation = st.text_area("Explanation", value=entry["explanation"])
                product_label = st.selectbox(
                    "Product scope", options,
                    index=options.index(selected_product),
                    key=f"edit_product_{entry['id']}",
                )
                market = st.text_input("Market scope", value=entry["market"] or "")
                channel = st.text_input("Channel scope", value=entry["channel"] or "")
                content_type = st.text_input(
                    "Content type scope", value=entry["content_type"] or ""
                )
                source_label = st.selectbox(
                    "Source document for this version",
                    source_options,
                    index=source_options.index(selected_source),
                    key=f"edit_source_document_{entry['id']}",
                )
                source_document_id = source_mapping.get(source_label)
                source_page = None
                source_quote = None
                if source_document_id:
                    selected_doc = next(
                        item for item in documents if item["id"] == source_document_id
                    )
                    page_labels = ["Select page"] + [
                        str(page["page_number"]) for page in selected_doc["page_records"]
                    ]
                    page_label = st.selectbox(
                        "Re-enter source page",
                        page_labels,
                        key=f"edit_source_page_{entry['id']}",
                    )
                    source_page = int(page_label) if page_label != "Select page" else None
                    source_quote = st.text_area(
                        "Re-enter exact quotation for this version",
                        key=f"edit_source_quote_{entry['id']}",
                        help="The old quotation is not carried into the new version.",
                    )
                if st.form_submit_button("Save edits as a new proposal version"):
                    try:
                        edit_pending_entry(
                            entry["id"], content, explanation,
                            mapping[product_label], market, role,
                            channel=channel, content_type=content_type,
                            source_document_id=source_document_id,
                            source_page=source_page,
                            source_quote=source_quote,
                            detach_source=(entry.get("document_id") is not None and source_document_id is None),
                            actor_role=role,
                        )
                        st.success("Proposal edits saved and versioned; review it again before approval.")
                        st.rerun()
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))

            required_role = (
                UserRole.FACT_OWNER.value
                if entry["category"] in {"PRODUCT_FACT", "EVIDENCE", "ASSET"}
                else UserRole.REVIEWER.value
            )
            rule_proposal = entry["category"] in {
                "BRAND_RULE", "TONE", "TERMINOLOGY", "RESTRICTION"
            }
            if rule_proposal:
                st.caption(
                    "Rule proposals must be classified and approved in the Rule Readiness page; "
                    "this general proposal button cannot activate them."
                )
            st.caption(f"Approval responsibility: {required_role}.")
            approve_col, reject_col, clarify_col, unresolved_col = st.columns(4)
            if approve_col.button(
                "Approve", key=f"approve_entry_{entry['id']}",
                disabled=role != required_role or rule_proposal,
            ):
                try:
                    result = decide_knowledge_entry(
                        entry["id"], "APPROVE", role, actor_role=role
                    )
                    linked = result["linked_record"]
                    if linked:
                        st.success(f"Approved and linked to existing {linked['type']} record #{linked['id']}.")
                    else:
                        st.success("Evidence proposal approved for traceability; it was not made an active product fact.")
                    st.rerun()
                except (ValueError, KnowledgeOnboardingError) as exc:
                    st.error(str(exc))
            if reject_col.button("Reject", key=f"reject_entry_{entry['id']}"):
                try:
                    decide_knowledge_entry(entry["id"], "REJECT", role, actor_role=role)
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
            if clarify_col.button("Request clarification", key=f"clarify_entry_{entry['id']}"):
                try:
                    decide_knowledge_entry(
                        entry["id"], "CLARIFY", role, "More information is needed.",
                        actor_role=role,
                    )
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
            if unresolved_col.button("Mark unresolved", key=f"unresolved_entry_{entry['id']}"):
                try:
                    decide_knowledge_entry(
                        entry["id"], "UNRESOLVED", role, actor_role=role
                    )
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()

    with st.expander("Decision and version history"):
        with st.container():
            from db.connection import get_connection
            with get_connection() as conn:
                history = conn.execute(
                    """
                    SELECT v.entry_id, v.version_number, v.actor, v.created_at, v.content
                    FROM knowledge_entry_versions v ORDER BY v.entry_id, v.version_number
                    """
                ).fetchall()
                decisions = conn.execute(
                    """
                    SELECT entry_id, decision, actor, note, created_at
                    FROM knowledge_decisions ORDER BY id DESC LIMIT 100
                    """
                ).fetchall()
            st.markdown("**Versions**")
            for row in history:
                st.caption(
                    f"Proposal #{row['entry_id']} · v{row['version_number']} · "
                    f"{row['actor']} · {row['created_at']}"
                )
                st.write(row["content"])
            st.markdown("**Recent decisions**")
            for row in decisions:
                st.caption(
                    f"Proposal #{row['entry_id']} · {row['decision']} · "
                    f"{row['actor']} · {row['created_at']}"
                )
                if row["note"]:
                    st.write(row["note"])
