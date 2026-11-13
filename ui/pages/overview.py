"""Role-aware workspace dashboard with a clear next action."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from db.connection import get_connection
from models.enums import BrandRole, UserRole
from services.auth_service import AuthenticatedPrincipal
from services.brand_service import require_brand_membership
from services.knowledge_onboarding import get_setup_readiness
from ui.styles import ai_disclosure, demo_banner, inject_styles


_PRIMARY_ACTIONS = {
    BrandRole.OWNER.value: ("Start a content review", "New Review"),
    UserRole.CREATOR.value: ("Start a content review", "New Review"),
    UserRole.REVIEWER.value: ("Open the approval queue", "Draft Approvals"),
    UserRole.FACT_OWNER.value: ("Review evidence updates", "Fact Approvals"),
}


def _dashboard_counts(brand_id: int) -> tuple[int, int, int, int]:
    with get_connection() as conn:
        counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status IN
                    ('DRAFT','CHECKED','READY_FOR_REVIEW','CHANGES_REQUESTED')
                    THEN 1 ELSE 0 END) AS active_reviews,
                SUM(CASE WHEN status = 'READY_FOR_REVIEW'
                    THEN 1 ELSE 0 END) AS awaiting_approval
            FROM drafts WHERE brand_id = ?
            """,
            (brand_id,),
        ).fetchone()
        approved_this_month = conn.execute(
            """
            SELECT COUNT(DISTINCT r.draft_id) FROM review_events e
            JOIN reviews r ON r.id = e.review_id
            JOIN drafts d ON d.id = r.draft_id
            WHERE d.brand_id = ? AND e.event_type = 'APPROVED'
              AND strftime('%Y-%m', e.created_at) = strftime('%Y-%m', 'now')
            """,
            (brand_id,),
        ).fetchone()[0]
        evidence_attention = conn.execute(
            """
            SELECT COUNT(*) FROM approved_claims ac
            JOIN drafts d ON d.id = ac.draft_id
            WHERE d.brand_id = ? AND ac.drift_status IN ('REVIEW_REQUIRED','EXPIRED')
            """,
            (brand_id,),
        ).fetchone()[0]
    return (
        counts["active_reviews"] or 0,
        counts["awaiting_approval"] or 0,
        evidence_attention,
        approved_this_month,
    )


def _recent_activity(brand_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT actor_username, action, object_type, created_at
            FROM audit_events WHERE brand_id = ?
            ORDER BY id DESC LIMIT 5
            """,
            (brand_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _render_ai_diagnostics() -> None:
    from ai.base import AIProviderError, safe_error_summary
    from ai.provider_factory import get_provider_diagnostics

    diagnostics = get_provider_diagnostics()
    st.caption("Tests send a fixed, non-draft prompt. Credential values are never displayed.")
    st.write(f"Selected provider: **{diagnostics['selected_provider']}**")
    columns = st.columns(2)
    for column, provider_name, provider_key in zip(
        columns, ("NVIDIA NIM", "Google Gemini"), ("nvidia", "gemini")
    ):
        config = diagnostics["providers"][provider_name]
        with column:
            st.markdown(f"**{provider_name}**")
            st.caption(
                f"Credential: {'configured' if config['credential_configured'] else 'not configured'} · "
                f"Model: {config['model']}"
            )
            if st.button(
                f"Test {provider_name}",
                key=f"test_{provider_key}_provider_connection",
                disabled=not config["credential_configured"],
            ):
                try:
                    if provider_key == "nvidia":
                        from ai.nvidia_provider import NvidiaNimProvider

                        provider = NvidiaNimProvider()
                    else:
                        from ai.gemini_provider import GeminiProvider

                        provider = GeminiProvider()
                    provider.test_connection()
                    st.session_state[f"{provider_key}_connection_result"] = {
                        "success": True,
                        "model": provider.last_model_used or config["model"],
                    }
                except AIProviderError as exc:
                    st.session_state[f"{provider_key}_connection_result"] = {
                        "success": False,
                        "category": safe_error_summary(exc),
                    }
            result = st.session_state.get(f"{provider_key}_connection_result")
            if result:
                if result["success"]:
                    st.success(f"Connected · {result['model']}")
                else:
                    st.error(result["category"])


def _navigate(page: str) -> None:
    st.session_state["sidebar_page"] = page


def render(principal: AuthenticatedPrincipal) -> None:
    inject_styles()
    brand_id = st.session_state.get("active_brand_id")
    membership = require_brand_membership(principal, brand_id)
    role = membership["role"]
    readiness = get_setup_readiness(principal, brand_id)
    active, awaiting, attention, approved = _dashboard_counts(brand_id)

    hour = datetime.now().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    st.caption("WORKSPACE / OVERVIEW")
    st.title(f"{greeting}, {principal.username}.")
    st.write(f"{membership['name']} is ready. What would you like to work on?")
    if membership["slug"] == "demo-brand":
        demo_banner()

    action_label, action_page = _PRIMARY_ACTIONS.get(
        role, ("Open your workspace", "Brand Knowledge")
    )
    button_label = "＋  " + action_label
    if st.button(button_label, type="primary", key="overview_primary_action"):
        _navigate(action_page)
        st.rerun()

    metrics = st.columns(4, gap="medium")
    for column, label, value in zip(
        metrics,
        ("Active reviews", "Awaiting approval", "Evidence needs review", "Approved this month"),
        (active, awaiting, attention, approved),
    ):
        column.metric(label, value)

    activity_column, next_column = st.columns([1.2, 0.8], gap="large")
    with activity_column:
        st.subheader("Recent activity")
        activity = _recent_activity(brand_id)
        if not activity:
            st.caption("Workspace activity will appear here as reviews and decisions are recorded.")
        for item in activity:
            st.markdown(
                f"**{item['action'].replace('_', ' ').title()}** · "
                f"{item['object_type']} · {item['actor_username']}"
            )
            st.caption(item["created_at"])

    with next_column:
        st.subheader("Your next step")
        if role in {BrandRole.OWNER.value, UserRole.CREATOR.value}:
            next_items = [
                ("Create a review", "New Review"),
                ("Continue a review", "Review Results"),
            ]
        elif role == UserRole.REVIEWER.value:
            next_items = [
                ("Decide on submitted content", "Draft Approvals"),
                ("Review proposed rules", "Rule Readiness"),
            ]
        else:
            next_items = [
                ("Review pending evidence", "Fact Approvals"),
                ("Update brand knowledge", "Brand Knowledge"),
            ]
        for label, page in next_items:
            if st.button(label, key=f"overview_next_{page.casefold().replace(' ', '_')}"):
                _navigate(page)
                st.rerun()

    with st.expander("Workspace setup", expanded=False):
        st.caption("Optional setup improves review coverage. You can start a review now.")
        for item in readiness["checklist"]:
            status = "Ready" if item["done"] else "Not set up"
            st.markdown(f"**{item['label']}** · {status}")
        if readiness["documents_attention"]:
            st.warning(
                f"{readiness['documents_attention']} source document(s) need attention. "
                "Check their extraction warnings before relying on their content."
            )

    if role in {BrandRole.OWNER.value, UserRole.REVIEWER.value}:
        with st.expander("AI and data use"):
            ai_disclosure()
        if role == BrandRole.OWNER.value:
            with st.expander("Advanced · provider diagnostics"):
                _render_ai_diagnostics()
