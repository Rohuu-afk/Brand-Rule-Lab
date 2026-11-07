"""Reviewer queue for drafts submitted for human approval."""

from __future__ import annotations

import streamlit as st

from models.enums import UserRole
from services.ledger_service import get_review_queue
from ui.styles import demo_banner, inject_styles
from ui.presentation import humanize
from ui.components import page_header


def render(role: UserRole | None = None) -> None:
    inject_styles()
    page_header("Governance", "Draft Approvals")
    demo_banner()

    if role != UserRole.REVIEWER:
        st.info("This queue is available in the simulated Reviewer role.")
        return

    st.caption(
        "Review submitted drafts, inspect their findings and evidence, then approve or request changes "
        "from Review Results."
    )
    queue = get_review_queue()
    if not queue:
        st.info("No drafts are waiting for review.")
        return

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
                "Open findings and decision",
                key=f"open_review_{draft['id']}",
                type="primary",
            ):
                st.session_state["last_draft_id"] = draft["id"]
                st.session_state["sidebar_page"] = "Review Results"
                st.rerun()
