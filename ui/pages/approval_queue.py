"""Reviewer queue for drafts submitted for human approval."""

from __future__ import annotations

import streamlit as st

from models.enums import BrandRole
from services.auth_service import AuthenticatedPrincipal
from services.brand_service import require_brand_membership
from services.ledger_service import get_review_queue
from ui.styles import demo_banner, inject_styles
from ui.presentation import humanize
from ui.components import page_header


def render(principal: AuthenticatedPrincipal) -> None:
    inject_styles()
    page_header("Governance", "Draft Approvals")
    demo_banner()

    brand_id = st.session_state.get("active_brand_id")
    try:
        require_brand_membership(
            principal, brand_id, (BrandRole.OWNER, BrandRole.REVIEWER)
        )
    except PermissionError:
        st.info("This queue is available only to authenticated Reviewers.")
        return

    st.caption(
        "Check each submitted draft, its findings, and supporting evidence. "
        "Opening a draft does not publish it; the reviewer makes the decision in Review Results."
    )
    queue = get_review_queue(principal=principal, brand_id=brand_id)
    if not queue:
        st.info("No drafts are waiting for reviewer approval. When a Creator submits a draft, it will appear here.")
        return

    st.subheader(f"Waiting for your review · {len(queue)}")
    for draft in queue:
        with st.container(border=True):
            st.subheader(draft["title"])
            st.caption(
                f"{draft.get('product_name') or 'Product not selected'} · "
                f"{draft.get('market') or 'Market not selected'} · "
                f"{draft.get('channel') or 'Channel not selected'}"
            )
            st.write(
                f"Version {draft.get('latest_version') or '—'} · "
                f"{humanize(draft['status'])} · Submitted {draft.get('updated_at') or '—'}"
            )
            if st.button(
                "Review findings and decide",
                key=f"open_review_{draft['id']}",
                type="primary",
            ):
                st.session_state["last_draft_id"] = draft["id"]
                st.session_state["sidebar_page"] = "Review Results"
                st.rerun()
