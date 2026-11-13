"""
ui/pages/ledger.py
------------------
📂 Review History & Audit Trail:
Chronological audit ledger recording review events (SUBMITTED, APPROVED,
CHANGES_REQUESTED, ESCALATED, OVERRIDE, COMMENT) with actor signatures,
timestamps, and detailed notes.
"""

from __future__ import annotations

import html

import streamlit as st
from ui.styles import demo_banner, inject_styles
from ui.presentation import humanize
from ui.components import page_header
from services.auth_service import AuthenticatedPrincipal
from services.ledger_service import (
    get_all_drafts, get_review_audit_trail, get_draft_with_versions,
    get_review_events,
)


EVENT_LABELS = {
    "SUBMITTED": "Submitted",
    "APPROVED": "Approved",
    "CHANGES_REQUESTED": "Changes requested",
    "ESCALATED": "Escalated",
    "OVERRIDE": "Reviewer override",
    "COMMENT": "Comment",
}

EVENT_COLORS = {
    "SUBMITTED": "var(--brl-info, #637B95)",
    "APPROVED": "var(--brl-pass, #547A62)",
    "CHANGES_REQUESTED": "var(--brl-warning, #997638)",
    "ESCALATED": "var(--brl-fail, #9C4B4B)",
    "OVERRIDE": "var(--brl-text-muted, #606068)",
    "COMMENT": "var(--brl-text-muted, #77777D)",
}


def _render_event_item(ev: dict, show_draft_title: bool = False):
    etype = ev.get("event_type", "UNKNOWN")
    event_label = EVENT_LABELS.get(etype, humanize(etype))
    color = EVENT_COLORS.get(etype, "#333333")
    actor = ev.get("actor", "system")
    created_at = ev.get("created_at", "—")
    note = ev.get("note")
    actor_html = html.escape(str(actor))
    created_at_html = html.escape(str(created_at))

    draft_info = ""
    if show_draft_title and ev.get("draft_title"):
        draft_info = (
            f" · Draft: <strong>{html.escape(str(ev['draft_title']))}</strong> "
            f"(id={html.escape(str(ev.get('draft_id', '?')))})"
        )
    draft_info_html = (
        f'<div style="color:var(--brl-text-secondary,#555);font-size:11px;margin-top:2px;">{draft_info}</div>'
        if draft_info else ""
    )

    st.markdown(
        f"<div class='brl-event-card' style='border-left-color:{color};'>"
        f"<strong style='color:{color};'>{html.escape(event_label)}</strong> "
        f"<span>by <code>{actor_html}</code></span>"
        f"<time>{created_at_html}</time>"
        f"{draft_info_html}</div>",
        unsafe_allow_html=True,
    )
    if note:
        st.markdown(
            f'<div class="brl-event-note">{html.escape(str(note))}</div>',
            unsafe_allow_html=True,
        )


def render(principal: AuthenticatedPrincipal) -> None:
    inject_styles()
    page_header("Content Review", "Review History & Audit Trail")
    demo_banner()

    st.markdown(
        "Append-only audit trail logging all review submissions, human sign-offs, "
        "change requests, escalations, and compliance comments across drafts."
    )

    brand_id = st.session_state.get("active_brand_id")
    drafts = get_all_drafts(principal=principal, brand_id=brand_id)
    if not drafts:
        st.info("No drafts recorded yet. Create and check drafts in **New Review**.")
        return

    # Option to inspect a specific draft or the combined timeline
    all_drafts_value = None
    labels_by_id = {
        d["id"]: (
            f"{d['title']} · {d.get('product_name') or 'Unassigned'} · "
            f"{humanize(d['status'])} · {(d.get('updated_at') or '')[:10]}"
        )
        for d in drafts
    }
    selected_option = st.selectbox(
        "Select audit view",
        [all_drafts_value, *labels_by_id],
        format_func=lambda draft_id: (
            "All drafts · Combined audit feed"
            if draft_id is None else labels_by_id[draft_id]
        ),
    )

    st.markdown("---")

    if selected_option is None:
        # Global timeline across all drafts
        all_events = get_review_events(principal, brand_id)

        event_count = len(all_events)
        event_word = "event" if event_count == 1 else "events"
        st.subheader(f"Global event timeline · {event_count} {event_word}")
        if not all_events:
            st.info("No review events have been logged yet.")
            return

        for ev in all_events:
            _render_event_item(ev, show_draft_title=True)

    else:
        selected_id = selected_option

        draft_data = get_draft_with_versions(
            selected_id, principal=principal, brand_id=brand_id
        )
        if not draft_data:
            st.info("This draft is no longer available in the selected workspace.")
            return
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Status", humanize(draft_data["status"]))
        c2.metric("Product", draft_data.get("product_name") or "Unassigned")
        c3.metric("Draft versions", len(draft_data.get("versions", [])))
        c4.metric(
            "Market / channel",
            f"{draft_data.get('market') or '—'} / {draft_data.get('channel') or '—'}",
        )

        with st.expander("Draft revision history", expanded=False):
            for version in draft_data.get("versions", []):
                st.markdown(
                    f"**Version {version['version_number']}** — "
                    f"Source: `{version['source_type']}` · Created: `{version['created_at']}`"
                )
                st.code(version["raw_text"], language=None)

        events = get_review_audit_trail(
            selected_id, principal=principal, brand_id=brand_id
        )
        st.subheader(f"Review events for {draft_data['title']} ({len(events)} events)")
        if not events:
            st.info("No review events recorded yet. Submit the draft for review first.")
            return

        for ev in events:
            _render_event_item(ev, show_draft_title=False)
