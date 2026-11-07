"""
ui/pages/new_review.py
-----------------------
Creator page: upload or paste draft content and execute brand compliance checks.
Supports multi-format ingestion (PDF, DOCX, TXT, PNG, JPG, Pasted Text).
"""

from __future__ import annotations

import json
import hashlib
import logging

import streamlit as st

from ai.base import safe_error_summary
from services.document_parser import MAX_BYTES, parse_file, parse_text, ParseError
from services.guideline_service import get_all_assets, get_all_products, get_all_briefs
from services.review_service import run_review
from services.ledger_service import get_all_drafts
from ui.styles import demo_banner, ai_disclosure, inject_styles
from ui.components import page_header
from db.connection import get_connection

logger = logging.getLogger("brand_rule_lab.ui.new_review")


def _create_draft_record(
    title: str,
    product_id: int | None,
    brief_id: int | None,
    channel: str | None,
    market: str | None,
    content_type: str | None = None,
) -> int:
    """Insert a draft record and return its id."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO drafts (title, product_id, brief_id, channel, market, content_type) "
            "VALUES (?,?,?,?,?,?)",
            (title, product_id, brief_id, channel, market, content_type),
        )
        conn.commit()
        return cur.lastrowid


def _brief_matches_scope(brief: dict, market: str, channel: str) -> bool:
    try:
        markets = json.loads(brief.get("markets") or "[]")
        channels = json.loads(brief.get("channels") or "[]")
    except (json.JSONDecodeError, TypeError):
        return False
    return (
        (not markets or "all" in markets or market in markets)
        and (not channels or "all" in channels or channel in channels)
    )


def _extraction_attention_messages(parsed_result: dict) -> list[str]:
    messages = [
        f"Page {page['page_number']}: {warning}"
        for page in parsed_result.get("page_records", [])
        for warning in page.get("warnings", [])
    ]
    if parsed_result.get("extraction_status") == "NEEDS_ATTENTION" and not messages:
        messages.append(
            "Document extraction needs attention; verify the recovered text against the original."
        )
    return messages


def render():
    inject_styles()
    page_header("Content Review", "Start a new review")
    demo_banner()
    ai_disclosure()

    products = get_all_products()
    product_options = {"(No product selected)": None} | {p["name"]: p["id"] for p in products}

    # ---- Step 1: Context ----
    st.subheader("1. Choose your product and campaign")
    st.caption("Draft title is required. Product, market, and channel are recommended to scope the checks; a campaign brief is optional.")
    col1, col2 = st.columns(2)

    with col1:
        draft_title = st.text_input("Draft title (required)", placeholder="e.g. Monsoon Clear Skin Launch Post")
        selected_product_name = st.selectbox(
            "Product (recommended)",
            list(product_options.keys()),
            help="Select the specific product this draft is marketing. Product-scoped rules apply only to matching products.",
        )
        product_id = product_options[selected_product_name]

    with col2:
        market = st.selectbox(
            "Market (recommended)",
            ["", "IN", "US", "GB", "AU", "SG"],
            help="Geographic market (e.g. IN for India, US for United States). Scoped rules execute only in designated markets.",
        )
        channel = st.selectbox(
            "Publishing channel (recommended)",
            ["", "instagram", "email", "website", "print_ad", "tv", "radio"],
            help="Publishing channel. Channel-specific constraints apply accordingly.",
        )
        content_type = st.text_input(
            "Content type (optional)",
            placeholder="For example: paid social, product page, packaging",
            help="Used to apply guidance scoped to a specific content type.",
        )

    # Campaign brief (optional)
    briefs = get_all_briefs(product_id=product_id) if product_id is not None else []
    briefs = [
        brief for brief in briefs if _brief_matches_scope(brief, market, channel)
    ]
    brief_options = {"(No brief attached)": None} | {b["name"]: b["id"] for b in briefs}
    selected_brief_name = st.selectbox("Campaign brief (optional)", list(brief_options.keys()), help="Attach a campaign brief to check messaging alignment and required talking points.")
    brief_id = brief_options[selected_brief_name]

    # ---- Step 2: Content ----
    st.subheader("2. Add your marketing content (required)")
    st.caption("Paste your copy or upload a PDF, Word, text, or image file. Files are parsed locally.")
    input_method = st.radio(
        "Content input method:",
        ["📋 Paste text copy", "📎 Upload document or asset file"],
        horizontal=True,
    )

    parsed_result: dict | None = None
    parse_error: str | None = None
    uploaded_hash: str | None = None
    uploaded_is_image = False

    if "Paste text" in input_method:
        pasted = st.text_area(
            "Draft copy",
            height=160,
            placeholder="e.g. Say goodbye to acne forever! Our 2.5% BPO wash clears breakouts in 3 days. It's the best solution for everyone.",
        )
        if pasted.strip():
            parsed_result = parse_text(pasted.strip())

    else:
        uploaded = st.file_uploader(
            "Upload file (PDF, DOCX, TXT, PNG, JPG — max 10 MB)",
            type=["pdf", "docx", "txt", "png", "jpg", "jpeg"],
            help="PDFs are extracted via PyMuPDF with page tracking. DOCX files preserve paragraphs. Images are processed via Tesseract OCR.",
        )
        if uploaded:
            if uploaded.size > MAX_BYTES:
                parse_error = f"File exceeds the {MAX_BYTES // (1024 * 1024)} MB upload limit."
            else:
                data = uploaded.getvalue()
                try:
                    parsed_result = parse_file(uploaded.name, data)
                    uploaded_is_image = parsed_result["source_type"] == "image"
                    if uploaded_is_image:
                        uploaded_hash = hashlib.sha256(data).hexdigest()
                    st.success(
                        f"✅ Extracted {parsed_result['word_count']} words from {parsed_result['source_type'].upper()} file."
                    )
                except ParseError as exc:
                    parse_error = str(exc)

    if parse_error:
        st.error(f"⚠️ {parse_error}")

    if parsed_result:
        extraction_warnings = _extraction_attention_messages(parsed_result)
        if extraction_warnings:
            st.warning(
                "Only recovered text is available for this review. Missing or uncertain page content "
                "is not checked; verify the original document before relying on the results."
            )
            with st.expander("Page extraction warnings", expanded=True):
                for warning in extraction_warnings:
                    st.write(warning)

    # ---- Step 3: Review Extracted Text ----
    st.subheader("3. Preview your content")
    st.caption("Check that the extracted text is accurate before running the review.")

    if parsed_result:
        preview_snippet = parsed_result["text"][:3000]
        if len(parsed_result["text"]) > 3000:
            preview_snippet += "…"
        st.text_area("Extracted Copy Preview", value=preview_snippet, height=130, disabled=True)
    else:
        st.info("ℹ️ Enter or upload copy in Step 2 to preview extracted text.")

    # Visual & Accessibility metadata (optional)
    visual_meta: dict = {}
    with st.expander("🎨 Visual & Asset Branding (Optional)", expanded=False):
        st.caption("Optional: add colors to check text contrast. For image uploads, add alt text and optionally compare against an approved asset.")
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            fg_col = st.text_input("Foreground Color (Hex)", placeholder="e.g. #000000", key="meta_fg")
            bg_col = st.text_input("Background Color (Hex)", placeholder="e.g. #FFFFFF", key="meta_bg")
            if fg_col.strip() and bg_col.strip():
                visual_meta["foreground"] = fg_col.strip()
                visual_meta["background"] = bg_col.strip()
        with col_v2:
            alt_txt = st.text_input("Image Alt Text (Optional)", placeholder="e.g. Monsoon Clear Skin bottle with pump", key="meta_alt")
            if uploaded_is_image:
                visual_meta["alt_text"] = alt_txt.strip()
            approved_assets = get_all_assets(product_id=product_id) if product_id else []
            asset_by_id = {asset["id"]: asset for asset in approved_assets}
            selected_asset_id = st.selectbox(
                "Approved image to compare (optional)",
                [None, *asset_by_id],
                format_func=lambda asset_id: (
                    "(No approved image selected)"
                    if asset_id is None
                    else f"{asset_by_id[asset_id]['name']} · {asset_by_id[asset_id]['asset_type']}"
                ),
            )
            if selected_asset_id is not None:
                visual_meta["asset_id"] = selected_asset_id
            if uploaded_is_image and uploaded_hash:
                visual_meta["submitted_hash"] = uploaded_hash
        if parsed_result and parsed_result.get("unobserved_pages"):
            visual_meta["unobserved_pages"] = parsed_result["unobserved_pages"]
            st.warning(
                "Could not check PDF page(s): "
                + ", ".join(str(page) for page in parsed_result["unobserved_pages"])
                + ". Those pages will be recorded as NOT_OBSERVED."
            )

    # ---- Step 4: Run Brand Review ----
    st.subheader("4. Run the brand check")
    st.caption("The review uses deterministic brand rules and, when available, advisory AI checks.")
    review_text = parsed_result["text"] if parsed_result else ""
    review_consent_nonce = st.session_state.get("_review_consent_nonce", 0)
    review_scope = hashlib.sha256(
        f"{draft_title}\0{review_text}".encode("utf-8")
    ).hexdigest()[:12]
    consent_key = (
        f"new_review_external_ai_consent_{review_consent_nonce}_{review_scope}"
    )
    ai_consent = st.checkbox(
        "I consent to send this review's extracted draft text to NVIDIA NIM (or the configured Google Gemini fallback, if invoked) for claim extraction and tone analysis. Fact ranking sends each extracted claim and eligible approved fact candidates.",
        value=False,
        key=consent_key,
    )
    if not ai_consent:
        st.info(
            "Without consent, no external AI request is made. Deterministic Python checks still run; AI-only assessments are marked NOT_OBSERVED."
        )
    if len(review_text) > 8000:
        st.warning(
            "The tone model evaluates only the first 8,000 characters. The complete draft still receives deterministic checks."
        )
    if len(review_text) > 30000:
        st.warning(
            "AI claim extraction is skipped above 30,000 characters to keep requests bounded. Deterministic checks still cover the complete draft."
        )

    if not draft_title.strip():
        st.info("Enter a draft title above to continue.")
        return

    if not parsed_result:
        st.info("Provide draft content above to continue.")
        return

    if st.button("🚀 Run Brand Check", type="primary"):
        st.session_state["_review_consent_nonce"] = review_consent_nonce + 1
        try:
            ai = None
            if ai_consent:
                from ai.provider_factory import get_provider
                from ai.base import AIProviderError

                try:
                    ai = get_provider()
                except AIProviderError:
                    st.warning(
                        "AI-assisted checks are unavailable right now. Brand rules will still run; "
                        "AI-only checks will be marked NOT_OBSERVED."
                    )

                    class _OfflineAI:
                        def generate_text(self, prompt, **kw):
                            raise AIProviderError("AI Provider unavailable or offline.")

                    ai = _OfflineAI()

            draft_id = _create_draft_record(
                title=draft_title.strip(),
                product_id=product_id,
                brief_id=brief_id,
                channel=channel or None,
                market=market or None,
                content_type=content_type.strip() or None,
            )

            with st.spinner("Executing brand compliance pipeline (deterministic rules + AI assistance)…"):
                result = run_review(
                    draft_id=draft_id,
                    raw_text=parsed_result["text"],
                    source_type=parsed_result["source_type"],
                    page_map=parsed_result["page_map"],
                    product_id=product_id,
                    market=market or None,
                    channel=channel or None,
                    content_type=content_type.strip() or None,
                    brief_id=brief_id,
                    ai_provider=ai,
                    visual_metadata=visual_meta or None,
                    ai_consent=ai_consent,
                )

            st.session_state["last_draft_version_id"] = result["draft_version_id"]
            st.session_state["last_draft_id"] = draft_id

            ai_status = result.get("ai_status", "unavailable")
            for warning in result.get("ai_warnings", []):
                st.warning(warning)
            if ai_status == "not_consented":
                st.info(result["ai_summary"])
            elif ai_status in ("unavailable", "partial"):
                st.warning(
                    "AI-assisted review could not be completed fully. Your rule-based checks are still available."
                )
            elif ai_status in ("success", "fallback_success"):
                st.caption("AI-assisted checks completed. Suggestions are advisory.")

            if result.get("ai_errors") or result.get("ai_operations"):
                with st.expander("Technical details", expanded=False):
                    for error in result.get("ai_errors", []):
                        st.code(error)
                    if result.get("ai_operations"):
                        st.write("Models used by completed operations:")
                        st.json(result["ai_operations"])
                    if result.get("ai_fallback_reason"):
                        st.write("Provider fallback:")
                        st.code(result["ai_fallback_reason"])
                    if result.get("ai_operation_status"):
                        st.write("Operation status:")
                        st.json(result["ai_operation_status"])

            n_findings = len(result["findings"])
            n_fail = sum(1 for f in result["findings"] if f["result"] == "FAIL")
            n_claims = result["claim_count"]

            st.success(
                f"Checks complete for {n_claims} claim(s). "
                f"{n_findings} item(s) need attention, including {n_fail} guideline issue(s)."
            )
            st.info("Open **Review Results** to see what was found and decide what to do next.")

        except Exception as exc:
            clean_err = safe_error_summary(exc)
            logger.error("Review could not be completed from the New Review page (%s).", clean_err)
            st.error(
                "The review could not be completed. Please try again; your original draft has not been replaced."
            )
            with st.expander("Technical details", expanded=False):
                st.code(clean_err)
