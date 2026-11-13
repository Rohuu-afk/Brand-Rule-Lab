"""Guided, draft-only content review workflow."""

from __future__ import annotations

import hashlib
import json
import logging

import streamlit as st

from ai.base import safe_error_summary
from db.connection import get_connection
from models.enums import BrandRole
from services.audit_service import append_audit_event
from services.auth_service import AuthenticatedPrincipal, require_principal
from services.brand_service import require_brand_membership, require_record_brand
from services.document_parser import (
    MAX_BYTES,
    MAX_VIDEO_BYTES,
    ParseError,
    parse_file,
    parse_text,
)
from services.guideline_service import get_all_assets, get_all_briefs, get_all_products
from services.review_service import run_review
from ui.components import page_header
from ui.styles import ai_disclosure, demo_banner, inject_styles

logger = logging.getLogger("brand_rule_lab.ui.new_review")

_STEPS = ("Content type", "Context", "Add content", "Review settings")
_CONTENT_TYPES = {
    "Social media post": "paid social",
    "Advertisement": "advertisement",
    "Product description": "product description",
    "Website content": "website",
    "Packaging artwork": "packaging",
    "Video": "video",
    "Other": "other",
}
_CONTENT_DESCRIPTIONS = {
    "Social media post": "Posts, captions, and short-form copy",
    "Advertisement": "Paid campaigns and promotional ads",
    "Product description": "Product pages and catalog copy",
    "Website content": "Landing pages and site copy",
    "Packaging artwork": "Pack copy and visual artwork",
    "Video": "Scripts or sampled video frames",
    "Other": "Another marketing content format",
}
_WIDGET_KEYS = (
    "new_review_step",
    "new_review_content_kind",
    "new_review_content_kind_value",
    "new_review_title",
    "new_review_title_value",
    "new_review_product",
    "new_review_product_name_value",
    "new_review_market",
    "new_review_market_value",
    "new_review_channel",
    "new_review_channel_value",
    "new_review_product_id",
    "new_review_content_type",
    "new_review_content_type_value",
    "new_review_brief",
    "new_review_brief_name_value",
    "new_review_brief_id",
    "new_review_input_method",
    "new_review_pasted_copy",
    "new_review_pasted_copy_value",
    "new_review_upload",
    "new_review_saved_input_method",
    "new_review_parsed_result",
    "new_review_upload_bytes",
    "new_review_upload_name",
    "new_review_upload_hash",
    "new_review_visual_checks",
    "meta_fg",
    "meta_bg",
    "meta_alt",
    "new_review_approved_asset",
    "new_review_packaging_asset",
    "new_review_completed",
)


def _create_draft_record(
    title: str,
    product_id: int | None,
    brief_id: int | None,
    channel: str | None,
    market: str | None,
    content_type: str | None = None,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> int:
    """Insert a draft record and return its id."""
    actor = require_principal(principal, action="create drafts")
    with get_connection() as conn:
        if brand_id is None:
            raise ValueError("A brand is required to create a draft.")
        if product_id is not None:
            require_record_brand(
                conn, actor, brand_id, "products", product_id,
                roles=(BrandRole.OWNER, BrandRole.CREATOR),
            )
        else:
            require_brand_membership(
                actor, brand_id, (BrandRole.OWNER, BrandRole.CREATOR)
            )
        if brief_id is not None:
            require_record_brand(conn, actor, brand_id, "campaign_briefs", brief_id)
        cursor = conn.execute(
            "INSERT INTO drafts "
            "(title, product_id, brief_id, channel, market, content_type, "
            "submitted_by, submitted_by_user_id, brand_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title, product_id, brief_id, channel, market, content_type,
                actor.username, actor.user_id, brand_id,
            ),
        )
        append_audit_event(
            conn,
            actor,
            action="DRAFT_CREATED",
            object_type="draft",
            object_id=cursor.lastrowid,
            brand_id=brand_id,
            details={"title": title.strip(), "product_id": product_id},
        )
        conn.commit()
        return int(cursor.lastrowid)


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
    is_video = parsed_result.get("source_type") == "video"
    messages = []
    for record in parsed_result.get("page_records", []):
        source = (
            f"Frame {record.get('frame_number', record['page_number'])}"
            + (f" at {record['timestamp']}" if record.get("timestamp") else "")
            if is_video else f"Page {record['page_number']}"
        )
        messages.extend(f"{source}: {warning}" for warning in record.get("warnings", []))
    if parsed_result.get("extraction_status") == "NEEDS_ATTENTION" and not messages:
        messages.append(
            "Document extraction needs attention; verify the recovered text against the original."
        )
    return messages


def _reset_review() -> None:
    for key in _WIDGET_KEYS:
        st.session_state.pop(key, None)
    for key in list(st.session_state):
        if key.startswith((
            "new_review_approved_asset_",
            "new_review_packaging_asset_",
        )):
            st.session_state.pop(key, None)
    st.session_state["new_review_step"] = 1


def _select_content_kind(content_kind: str) -> None:
    st.session_state["new_review_content_kind"] = content_kind
    st.session_state["new_review_content_kind_value"] = content_kind


def _render_stepper(step: int) -> None:
    steps = "".join(
        (
            '<div class="brl-review-step is-complete">'
            if index < step else
            '<div class="brl-review-step is-active">'
            if index == step else
            '<div class="brl-review-step">'
        )
        + f'<span class="brl-review-step__number">{index + 1}</span>'
        + f'<span class="brl-review-step__label">{label}</span></div>'
        for index, label in enumerate(_STEPS)
    )
    st.markdown(f'<div class="brl-review-stepper">{steps}</div>', unsafe_allow_html=True)


def _show_file_summary(parsed_result: dict, name: str) -> None:
    file_type = str(parsed_result.get("source_type", "file")).upper()
    st.success(
        f"{name} · {file_type} · {parsed_result.get('word_count', 0)} words extracted"
    )
    if parsed_result.get("video_metadata"):
        meta = parsed_result["video_metadata"]
        st.info(
            f"Sampled {meta['sampled_keyframes']} frames across "
            f"{meta['duration_seconds']:.1f} seconds. Unsampled moments are not checked."
        )


def _render_content_step() -> bool:
    """Render input and preview, retaining parsed uploads between wizard steps."""
    st.subheader("Add the content to review")
    st.caption("Paste copy or upload a supported document, image, artwork, or short video.")
    if "new_review_input_method" not in st.session_state:
        st.session_state["new_review_input_method"] = st.session_state.get(
            "new_review_saved_input_method", "Paste text"
        )
    input_method = st.radio(
        "How would you like to add content?",
        ("Paste text", "Upload a file"),
        horizontal=True,
        key="new_review_input_method",
    )
    previous_method = st.session_state.get("new_review_saved_input_method")
    if previous_method and previous_method != input_method:
        if previous_method == "Paste text":
            st.session_state["new_review_pasted_copy_value"] = st.session_state.get(
                "new_review_pasted_copy",
                st.session_state.get("new_review_pasted_copy_value", ""),
            )
        st.session_state.pop("new_review_parsed_result", None)
        st.session_state.pop("new_review_upload_bytes", None)
        st.session_state.pop("new_review_upload_name", None)
        st.session_state.pop("new_review_upload_hash", None)
    st.session_state["new_review_saved_input_method"] = input_method

    parse_error = None
    parsed_result = None
    if input_method == "Paste text":
        if (
            "new_review_pasted_copy" not in st.session_state
            and st.session_state.get("new_review_pasted_copy_value") is not None
        ):
            st.session_state["new_review_pasted_copy"] = st.session_state[
                "new_review_pasted_copy_value"
            ]
        pasted = st.text_area(
            "Marketing copy",
            height=180,
            placeholder="Paste a caption, product claim, ad, or other copy…",
            key="new_review_pasted_copy",
        )
        if pasted.strip():
            parsed_result = parse_text(pasted.strip())
            st.session_state["new_review_parsed_result"] = parsed_result
            st.session_state.pop("new_review_upload_bytes", None)
            st.session_state.pop("new_review_upload_name", None)
            st.session_state.pop("new_review_upload_hash", None)
        else:
            st.session_state.pop("new_review_parsed_result", None)
    else:
        uploaded = st.file_uploader(
            "Choose a file",
            type=["pdf", "docx", "txt", "png", "jpg", "jpeg", "mp4", "mov", "webm"],
            max_upload_size=max(MAX_BYTES, MAX_VIDEO_BYTES) // (1024 * 1024),
            help=(
                f"Documents and images: up to {MAX_BYTES // (1024 * 1024)} MB. "
                f"Video: up to {MAX_VIDEO_BYTES // (1024 * 1024)} MB and 120 seconds; "
                "at most 12 keyframes are sampled."
            ),
            key="new_review_upload",
        )
        if uploaded:
            data = uploaded.getvalue()
            content_hash = hashlib.sha256(data).hexdigest()
            if content_hash != st.session_state.get("new_review_upload_hash"):
                for key in (
                    "new_review_parsed_result", "new_review_upload_bytes",
                    "new_review_upload_name", "new_review_upload_hash",
                ):
                    st.session_state.pop(key, None)
                extension = uploaded.name.rsplit(".", 1)[-1].casefold() if "." in uploaded.name else ""
                file_limit = MAX_VIDEO_BYTES if extension in {"mp4", "mov", "webm"} else MAX_BYTES
                if uploaded.size > file_limit:
                    parse_error = (
                        f"This file is larger than the {file_limit // (1024 * 1024)} MB limit."
                    )
                else:
                    try:
                        parsed_result = parse_file(uploaded.name, data)
                    except ParseError as exc:
                        parse_error = str(exc)
                    else:
                        st.session_state["new_review_parsed_result"] = parsed_result
                        st.session_state["new_review_upload_bytes"] = data
                        st.session_state["new_review_upload_name"] = uploaded.name
                        st.session_state["new_review_upload_hash"] = content_hash
            else:
                parsed_result = st.session_state.get("new_review_parsed_result")
        else:
            parsed_result = st.session_state.get("new_review_parsed_result")
            saved_name = st.session_state.get("new_review_upload_name")
            if parsed_result and saved_name:
                st.caption(f"Using parsed file: {saved_name}")
                if st.button("Remove file", key="new_review_remove_upload"):
                    for key in (
                        "new_review_parsed_result", "new_review_upload_bytes",
                        "new_review_upload_name", "new_review_upload_hash",
                    ):
                        st.session_state.pop(key, None)
                    st.rerun()

    if parse_error:
        st.error(parse_error)
    if parsed_result:
        if input_method == "Upload a file":
            _show_file_summary(
                parsed_result, st.session_state.get("new_review_upload_name", "Uploaded file")
            )
        warnings = _extraction_attention_messages(parsed_result)
        if warnings:
            st.warning(
                "Some source content could not be read reliably. Review the original before "
                "relying on results for the missing content."
            )
            with st.expander("Extraction details", expanded=False):
                for warning in warnings:
                    st.write(warning)
        text = parsed_result["text"]
        if text:
            preview = text[:3000] + ("…" if len(text) > 3000 else "")
            st.text_area("Content preview", value=preview, height=150, disabled=True)
        elif parsed_result.get("source_type") == "image":
            st.info(
                "No readable copy was extracted. Image metadata and supported visual checks "
                "can still be reviewed."
            )
        else:
            st.warning("No text was extracted from this file. Try another supported file.")

        upload_bytes = st.session_state.get("new_review_upload_bytes")
        if upload_bytes and parsed_result.get("source_type") == "image":
            st.image(upload_bytes, caption="Uploaded artwork preview", width="stretch")
        elif upload_bytes and parsed_result.get("source_type") == "video":
            st.video(upload_bytes)
            st.caption(
                "Only the sampled frames shown in the resulting review are checked; "
                "this is not frame-by-frame video analysis."
            )
    else:
        st.info("Your content preview will appear here before checks run.")

    back, _, continue_col = st.columns([1, 2, 1.2])
    with back:
        if st.button("Back", key="new_review_content_back"):
            if st.session_state.get("new_review_input_method") == "Paste text":
                st.session_state["new_review_pasted_copy_value"] = st.session_state.get(
                    "new_review_pasted_copy", ""
                )
            st.session_state["new_review_step"] = 2
            st.rerun()
    with continue_col:
        if st.button(
            "Continue to review settings",
            type="primary",
            key="new_review_content_continue",
            disabled=not bool(parsed_result),
        ):
            st.session_state["new_review_pasted_copy_value"] = st.session_state.get(
                "new_review_pasted_copy", ""
            )
            st.session_state["new_review_step"] = 4
            st.rerun()
    return bool(parsed_result)


def _visual_metadata(
    principal: AuthenticatedPrincipal,
    brand_id: int,
    product_id: int | None,
    parsed_result: dict,
) -> dict:
    visual_meta: dict = {}
    source_type = parsed_result.get("source_type")
    upload_bytes = st.session_state.get("new_review_upload_bytes")
    has_image = source_type == "image" and upload_bytes is not None
    if parsed_result.get("video_metadata"):
        visual_meta["video_analysis"] = parsed_result["video_metadata"]
        visual_meta["video_frames"] = [
            {
                "frame_number": record["frame_number"],
                "timestamp": record["timestamp"],
                "confidence": record.get("confidence"),
                "warnings": record.get("warnings", []),
            }
            for record in parsed_result.get("page_records", [])
        ]
    if parsed_result.get("unobserved_pages"):
        visual_meta["unobserved_pages"] = parsed_result["unobserved_pages"]
        visual_meta["unobserved_source_type"] = source_type
        noun = "frame(s)" if source_type == "video" else "PDF page(s)"
        st.warning(
            f"Could not check {noun}: "
            + ", ".join(str(page) for page in parsed_result["unobserved_pages"])
            + ". They will be recorded as checks that could not be completed."
        )

    assets = []
    packaging_assets = {}
    supports_packaging_reference = source_type in {"image", "pdf", "docx", "text"}
    if product_id is not None and supports_packaging_reference:
        assets = get_all_assets(
            product_id=product_id, principal=principal, brand_id=brand_id
        )
        packaging_assets = {
            asset["id"]: asset for asset in assets
            if str(asset.get("asset_type", "")).casefold() == "packaging"
        }
    has_optional_checks = (
        source_type in {"image", "video", "pdf"} or bool(packaging_assets)
    )
    if not has_optional_checks:
        return visual_meta

    if not st.checkbox(
        "Include optional visual and accessibility details",
        value=True,
        key="new_review_visual_checks",
        help=(
            "This controls optional contrast, image-comparison, and accessibility details. "
            "Text checks and extracted file content still run. It does not verify all visual layout."
        ),
    ):
        return visual_meta

    with st.container(border=True):
        st.caption("Only controls supported for this content are shown.")
        if source_type in {"image", "pdf"}:
            columns = st.columns(2) if has_image else [st.container()]
            with columns[0]:
                foreground = st.text_input(
                    "Text color (hex)", placeholder="#000000", key="meta_fg"
                )
                background = st.text_input(
                    "Background color (hex)", placeholder="#FFFFFF", key="meta_bg"
                )
                if foreground.strip() and background.strip():
                    visual_meta["foreground"] = foreground.strip()
                    visual_meta["background"] = background.strip()
            if has_image:
                with columns[1]:
                    alt_text = st.text_input(
                        "Image description (optional)",
                        placeholder="Describe the important visual content",
                        key="meta_alt",
                    )
                    visual_meta["alt_text"] = alt_text.strip()
        elif source_type == "video":
            st.caption(
                "Text extracted from sampled keyframes is still checked. Unsampled frames "
                "and motion are not checked."
            )

        if has_image and product_id is not None:
            asset_by_id = {asset["id"]: asset for asset in assets}
            approved_id = st.selectbox(
                "Approved image to compare (optional)",
                [None, *asset_by_id],
                format_func=lambda asset_id: (
                    "(No approved image selected)"
                    if asset_id is None
                    else f"{asset_by_id[asset_id]['name']} · {asset_by_id[asset_id]['asset_type']}"
                ),
                key=f"new_review_approved_asset_{brand_id}_{product_id}",
            )
            if approved_id is not None:
                visual_meta["asset_id"] = approved_id
            visual_meta["submitted_hash"] = hashlib.sha256(upload_bytes).hexdigest()
            visual_meta["submitted_image_bytes"] = upload_bytes

        if packaging_assets:
            packaging_id = st.selectbox(
                "Approved packaging artwork to compare (optional)",
                [None, *packaging_assets],
                format_func=lambda asset_id: (
                    "(No packaging reference selected)"
                    if asset_id is None else packaging_assets[asset_id]["name"]
                ),
                key=f"new_review_packaging_asset_{brand_id}_{product_id}",
                help=(
                    "Compares extracted text with an approved packaging reference. "
                    "It does not verify physical text size, layout, color, or logo placement."
                ),
            )
            if packaging_id is not None:
                visual_meta["packaging_reference_asset_id"] = packaging_id
    return visual_meta


def _run_review(
    principal: AuthenticatedPrincipal,
    brand_id: int,
    title: str,
    product_id: int | None,
    brief_id: int | None,
    channel: str,
    market: str,
    content_type: str,
    parsed_result: dict,
    visual_meta: dict,
    ai_consent: bool,
) -> None:
    ai = None
    if ai_consent:
        from ai.base import AIProviderError
        from ai.provider_factory import get_provider

        try:
            ai = get_provider()
        except AIProviderError:
            st.warning(
                "AI assistance is unavailable. Deterministic brand checks will still run; "
                "AI-only checks will be marked as not completed."
            )

            class _OfflineAI:
                def generate_text(self, prompt, **kwargs):
                    raise AIProviderError("AI provider unavailable or offline.")

            ai = _OfflineAI()

    draft_id = _create_draft_record(
        title=title.strip(),
        product_id=product_id,
        brief_id=brief_id,
        channel=channel or None,
        market=market or None,
        content_type=content_type,
        principal=principal,
        brand_id=brand_id,
    )
    with st.spinner("Checking your content against the selected brand guidance…"):
        result = run_review(
            draft_id=draft_id,
            raw_text=parsed_result["text"],
            source_type=parsed_result["source_type"],
            page_map=parsed_result["page_map"],
            product_id=product_id,
            market=market or None,
            channel=channel or None,
            content_type=content_type,
            brief_id=brief_id,
            ai_provider=ai,
            visual_metadata=visual_meta or None,
            ai_consent=ai_consent,
            principal=principal,
            brand_id=brand_id,
        )
    st.session_state["last_draft_version_id"] = result["draft_version_id"]
    st.session_state["last_draft_id"] = draft_id
    st.session_state["new_review_completed"] = {
        "draft_id": draft_id,
        "title": title.strip(),
        "finding_count": len(result["findings"]),
        "claim_count": result["claim_count"],
        "ai_status": result.get("ai_status", "unavailable"),
        "ai_summary": result.get("ai_summary"),
        "ai_warnings": result.get("ai_warnings", []),
        "ai_errors": result.get("ai_errors", []),
        "ai_operations": result.get("ai_operations", []),
        "ai_fallback_reason": result.get("ai_fallback_reason"),
        "ai_operation_status": result.get("ai_operation_status"),
    }


def render(principal: AuthenticatedPrincipal) -> None:
    inject_styles()
    page_header("Reviews", "Create a content review")
    brand_id = st.session_state.get("active_brand_id")
    membership = require_brand_membership(
        principal, brand_id, (BrandRole.OWNER, BrandRole.CREATOR)
    )
    if st.session_state.get("_new_review_brand_id") != brand_id:
        _reset_review()
        st.session_state["_new_review_brand_id"] = brand_id
    if membership["slug"] == "demo-brand":
        demo_banner()

    completed = st.session_state.get("new_review_completed")
    if completed:
        st.success(f"Checks are complete for **{completed['title']}**.")
        st.caption(
            f"{completed['claim_count']} claim(s) checked · "
            f"{completed['finding_count']} finding(s) recorded"
        )
        if completed.get("ai_summary"):
            st.info(completed["ai_summary"])
        if (
            completed.get("ai_warnings")
            or completed.get("ai_errors")
            or completed.get("ai_operation_status")
            or completed.get("ai_operations")
            or completed.get("ai_fallback_reason")
        ):
            with st.expander("AI checks and limitations", expanded=False):
                for warning in completed.get("ai_warnings", []):
                    st.warning(warning)
                for error in completed.get("ai_errors", []):
                    st.caption(f"AI check detail · {error}")
                for operation, status in (completed.get("ai_operation_status") or {}).items():
                    st.caption(f"{operation.replace('_', ' ').title()} · {status}")
                for operation, provider in (completed.get("ai_operations") or {}).items():
                    st.caption(f"{operation.replace('_', ' ').title()} provider · {provider}")
                if completed.get("ai_fallback_reason"):
                    st.caption(f"Provider fallback · {completed['ai_fallback_reason']}")
        if st.button("View review results", type="primary", key="new_review_view_results"):
            st.session_state["last_draft_id"] = completed["draft_id"]
            st.session_state["sidebar_page"] = "Review Results"
            st.rerun()
        st.button(
            "Start another review",
            key="new_review_start_another",
            on_click=_reset_review,
        )
        return

    if st.session_state.pop("load_sample_review", False):
        sample = (
            "Monsoon Clear Skin Face Wash guarantees clear skin in 3 days. "
            "It's the best choice for everyone."
        )
        st.session_state["new_review_content_kind"] = "Social media post"
        st.session_state["new_review_content_kind_value"] = "Social media post"
        st.session_state["new_review_title"] = "Sample: Monsoon launch post"
        st.session_state["new_review_title_value"] = "Sample: Monsoon launch post"
        st.session_state["new_review_product"] = "Monsoon Clear Skin Face Wash"
        st.session_state["new_review_product_name_value"] = "Monsoon Clear Skin Face Wash"
        st.session_state["new_review_market"] = "IN"
        st.session_state["new_review_market_value"] = "IN"
        st.session_state["new_review_channel"] = "instagram"
        st.session_state["new_review_channel_value"] = "instagram"
        st.session_state["new_review_content_type"] = "paid social"
        st.session_state["new_review_content_type_value"] = "paid social"
        st.session_state["new_review_input_method"] = "Paste text"
        st.session_state["new_review_saved_input_method"] = "Paste text"
        st.session_state["new_review_pasted_copy"] = sample
        st.session_state["new_review_pasted_copy_value"] = sample
        st.session_state["new_review_parsed_result"] = parse_text(sample)
        st.session_state["new_review_brief"] = "Monsoon Clear Skin Launch Campaign"
        st.session_state["new_review_brief_name_value"] = "Monsoon Clear Skin Launch Campaign"
        st.session_state["new_review_step"] = 4

    products = get_all_products(principal=principal, brand_id=brand_id)
    product_options = {"(No product selected)": None} | {
        product["name"]: product["id"] for product in products
    }
    step = st.session_state.setdefault("new_review_step", 1)
    _render_stepper(step - 1)

    if step == 1:
        st.subheader("What type of content are you reviewing?")
        st.caption("Choose the closest match. You can refine the context in the next step.")
        if (
            "new_review_content_kind" not in st.session_state
            and st.session_state.get("new_review_content_kind_value")
        ):
            st.session_state["new_review_content_kind"] = st.session_state[
                "new_review_content_kind_value"
            ]
        if "new_review_content_kind" not in st.session_state:
            st.session_state["new_review_content_kind"] = "Social media post"
        content_kinds = list(_CONTENT_TYPES)
        for row_start in range(0, len(content_kinds), 3):
            columns = st.columns(3)
            for column, content_kind in zip(
                columns, content_kinds[row_start:row_start + 3]
            ):
                with column:
                    with st.container(border=True):
                        st.markdown(f"**{content_kind}**")
                        st.caption(_CONTENT_DESCRIPTIONS[content_kind])
                        selected = (
                            st.session_state["new_review_content_kind"] == content_kind
                        )
                        st.button(
                            "Selected" if selected else "Select",
                            key=f"new_review_kind_{content_kind.casefold().replace(' ', '_')}",
                            type="primary" if selected else "secondary",
                            width="stretch",
                            on_click=_select_content_kind,
                            args=(content_kind,),
                        )
        if st.button("Continue", type="primary", key="new_review_type_continue"):
            st.session_state["new_review_content_kind_value"] = st.session_state.get(
                "new_review_content_kind", "Other"
            )
            st.session_state["new_review_step"] = 2
            st.rerun()
        if membership["slug"] == "demo-brand" and st.button(
            "Load a sample review", key="new_review_load_sample"
        ):
            st.session_state["load_sample_review"] = True
            st.rerun()
        return

    if step == 2:
        st.subheader("Add the review context")
        st.caption(f"Brand workspace · **{membership['name']}**")
        if (
            "new_review_title" not in st.session_state
            and st.session_state.get("new_review_title_value") is not None
        ):
            st.session_state["new_review_title"] = st.session_state["new_review_title_value"]
        st.text_input(
            "Review name",
            placeholder="For example, Summer launch social post",
            key="new_review_title",
        )
        if (
            "new_review_product" not in st.session_state
            and st.session_state.get("new_review_product_name_value") in product_options
        ):
            st.session_state["new_review_product"] = st.session_state[
                "new_review_product_name_value"
            ]
        selected_product_name = st.selectbox(
            "Product",
            list(product_options),
            help="Product selection helps apply relevant facts and brand rules.",
            key="new_review_product",
        )
        product_id = product_options[selected_product_name]
        if (
            "new_review_market" not in st.session_state
            and st.session_state.get("new_review_market_value") in ("", "IN", "US", "GB", "AU", "SG")
        ):
            st.session_state["new_review_market"] = st.session_state["new_review_market_value"]
        market_column, channel_column = st.columns(2)
        with market_column:
            market = st.selectbox(
                "Target market",
                ["", "IN", "US", "GB", "AU", "SG"],
                key="new_review_market",
            )
        if (
            "new_review_channel" not in st.session_state
            and st.session_state.get("new_review_channel_value")
            in ("", "instagram", "email", "website", "print_ad", "tv", "radio")
        ):
            st.session_state["new_review_channel"] = st.session_state[
                "new_review_channel_value"
            ]
        with channel_column:
            channel = st.selectbox(
                "Publishing channel",
                ["", "instagram", "email", "website", "print_ad", "tv", "radio"],
                key="new_review_channel",
            )
        selected_content_kind = st.session_state.get(
            "new_review_content_kind",
            st.session_state.get("new_review_content_kind_value", "Other"),
        )
        selected_content_type = _CONTENT_TYPES[selected_content_kind]
        if selected_content_type == "other":
            if (
                "new_review_content_type" not in st.session_state
                and st.session_state.get("new_review_content_type_value") is not None
            ):
                st.session_state["new_review_content_type"] = st.session_state[
                    "new_review_content_type_value"
                ]
            content_type = st.text_input(
                "Describe the content format",
                placeholder="For example, influencer video or event banner",
                key="new_review_content_type",
            )
        else:
            content_type = selected_content_type
            st.caption(f"Review type · {selected_content_kind}")

        brief_options = {"(No campaign brief)": None}
        if product_id is not None:
            briefs = get_all_briefs(
                product_id=product_id, principal=principal, brand_id=brand_id
            )
            briefs = [
                brief for brief in briefs
                if _brief_matches_scope(brief, market, channel)
            ]
            brief_options.update({brief["name"]: brief["id"] for brief in briefs})
        if (
            "new_review_brief" not in st.session_state
            and st.session_state.get("new_review_brief_name_value") in brief_options
        ):
            st.session_state["new_review_brief"] = st.session_state[
                "new_review_brief_name_value"
            ]
        if st.session_state.get("new_review_brief") not in brief_options:
            st.session_state["new_review_brief"] = "(No campaign brief)"
        selected_brief = st.selectbox(
            "Campaign brief (optional)",
            list(brief_options),
            key="new_review_brief",
        )
        back, _, continue_col = st.columns([1, 2, 1])
        with back:
            if st.button("Back", key="new_review_context_back"):
                st.session_state["new_review_step"] = 1
                st.rerun()
        with continue_col:
            if st.button(
                "Continue",
                type="primary",
                key="new_review_context_continue",
                disabled=not bool(st.session_state.get("new_review_title", "").strip()),
            ):
                st.session_state["new_review_title_value"] = st.session_state.get(
                    "new_review_title", ""
                )
                st.session_state["new_review_content_kind_value"] = selected_content_kind
                st.session_state["new_review_product_id"] = product_id
                st.session_state["new_review_product_name_value"] = selected_product_name
                st.session_state["new_review_market_value"] = market
                st.session_state["new_review_channel_value"] = channel
                st.session_state["new_review_content_type_value"] = content_type
                st.session_state["new_review_brief_id"] = brief_options[selected_brief]
                st.session_state["new_review_brief_name_value"] = selected_brief
                st.session_state["new_review_step"] = 3
                st.rerun()
        return

    if step == 3:
        _render_content_step()
        return

    if step != 4:
        st.session_state["new_review_step"] = 1
        st.rerun()
        return

    title = st.session_state.get("new_review_title_value", "").strip()
    product_id = st.session_state.get("new_review_product_id")
    market = st.session_state.get("new_review_market_value", "")
    channel = st.session_state.get("new_review_channel_value", "")
    content_type = st.session_state.get("new_review_content_type_value") or _CONTENT_TYPES.get(
        st.session_state.get("new_review_content_kind", "Other"), "other"
    )
    brief_id = st.session_state.get("new_review_brief_id")
    parsed_result = st.session_state.get("new_review_parsed_result")
    if not title or not parsed_result:
        st.warning("A review name and content are required before checks can run.")
        if st.button("Return to content", key="new_review_missing_content_back"):
            st.session_state["new_review_step"] = 3 if title else 2
            st.rerun()
        return

    st.subheader("Confirm checks and run your review")
    st.caption(
        "Deterministic brand rules always run. Supported visual checks are optional. "
        "AI analysis is off unless you explicitly consent."
    )
    st.caption(
        "This creates an internal review draft only. A human reviewer must approve "
        "anything before it can move toward publishing."
    )
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(
            f"{st.session_state.get('new_review_content_kind_value', 'Other')} · "
            f"{st.session_state.get('new_review_product', '(No product selected)')} · "
            f"{market or 'Market not selected'} · {channel or 'Channel not selected'}"
        )
        st.caption(
            f"{parsed_result.get('word_count', 0)} words · "
            f"{parsed_result.get('source_type', 'content').upper()} input"
        )
    visual_meta = _visual_metadata(principal, brand_id, product_id, parsed_result)
    review_text = parsed_result["text"]
    if len(review_text) > 8000:
        st.warning(
            "If AI is enabled, tone analysis evaluates only the first 8,000 characters. "
            "Deterministic checks cover the full extracted text."
        )
    if len(review_text) > 30000:
        st.warning(
            "AI claim extraction is skipped above 30,000 characters. Deterministic checks "
            "still cover the full extracted text."
        )
    with st.expander("What AI receives", expanded=False):
        ai_disclosure()
    review_scope = hashlib.sha256(
        f"{title}\0{review_text}".encode("utf-8")
    ).hexdigest()[:12]
    consent_key = f"new_review_external_ai_consent_{brand_id}_{review_scope}"
    ai_consent = st.checkbox(
        "I agree to send the extracted text to the configured AI provider for claim "
        "and tone analysis. Fact ranking also sends extracted claims and eligible "
        "approved fact candidates.",
        value=False,
        key=consent_key,
    )
    if not ai_consent:
        st.info(
            "Without consent, no external AI request is made. Deterministic checks still "
            "run; AI-only checks are marked as not completed."
        )

    back, _, run_col = st.columns([1, 2, 1.2])
    with back:
        if st.button("Back", key="new_review_settings_back"):
            st.session_state["new_review_step"] = 3
            st.rerun()
    with run_col:
        run_check = st.button(
            "Run brand check",
            type="primary",
            key="new_review_run_check",
        )
    if run_check:
        st.session_state["_review_consent_nonce"] = (
            st.session_state.get("_review_consent_nonce", 0) + 1
        )
        try:
            _run_review(
                principal,
                brand_id,
                title,
                product_id,
                brief_id,
                channel,
                market,
                content_type,
                parsed_result,
                visual_meta,
                ai_consent,
            )
        except Exception as exc:
            clean_error = safe_error_summary(exc)
            logger.error("Review could not be completed from New Review (%s).", clean_error)
            st.error(
                "The review could not be completed. Your source content is still here; "
                "check the settings and try again."
            )
            with st.expander("Troubleshooting details", expanded=False):
                st.code(clean_error)
        else:
            st.rerun()
