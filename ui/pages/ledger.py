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
from services.ledger_service import get_all_drafts, get_review_audit_trail, get_draft_with_versions
from db.connection import get_connection


EVENT_ICONS = {
    "SUBMITTED": "📬",
    "APPROVED": "✅",
    "CHANGES_REQUESTED": "🔄",
    "ESCALATED": "🚨",
    "OVERRIDE": "⚡",
    "COMMENT": "💬",
}

EVENT_COLORS = {
    "SUBMITTED": "#637B95",
    "APPROVED": "#547A62",
    "CHANGES_REQUESTED": "#997638",
    "ESCALATED": "#9C4B4B",
    "OVERRIDE": "#606068",
    "COMMENT": "#77777D",
}


def _render_event_item(ev: dict, show_draft_title: bool = False):
    etype = ev.get("event_type", "UNKNOWN")
    icon = EVENT_ICONS.get(etype, "📌")
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
        f'<div style="color:#555;font-size:12px;margin-top:2px;">{draft_info}</div>'
        if draft_info else ""
    )

    st.markdown(
        f"<div style='border-left: 4px solid {color}; padding: 8px 12px; margin-bottom: 8px; background-color: #FAFAFA; border-radius: 0 4px 4px 0;'>"
        f"<span style='font-size: 16px; margin-right: 6px;'>{icon}</span>"
        f"<strong style='color:{color}; font-size: 14px;'>{html.escape(humanize(etype))}</strong> "
        f"<span style='color: #666; font-size: 13px;'>by <code>{actor_html}</code></span>"
        f"<span style='color: #999; font-size: 12px; float: right;'>🕒 {created_at_html}</span>"
        f"{draft_info_html}"
        f"</div>",
        unsafe_allow_html=True,
    )
    if note:
        st.markdown(f"> 💬 *\"{note}\"*")


def render():
    inject_styles()
    page_header("Content Review", "Review History & Audit Trail")
    demo_banner()

    st.markdown(
        "Append-only audit trail logging all review submissions, human sign-offs, "
        "change requests, escalations, and compliance comments across drafts."
    )

    drafts = get_all_drafts()
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
        with get_connection() as conn:
            all_events = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT re.*, r.reviewer, d.id AS draft_id, d.title AS draft_title, p.name AS product_name
                    FROM review_events re
                    JOIN reviews r ON r.id = re.review_id
                    JOIN drafts d ON d.id = r.draft_id
                    LEFT JOIN products p ON p.id = d.product_id
                    ORDER BY re.created_at DESC
                    """
                ).fetchall()
            ]

        st.subheader(f"🕒 Global Event Timeline ({len(all_events)} events)")
        if not all_events:
            st.info("No review events have been logged yet.")
            return

        for ev in all_events:
            _render_event_item(ev, show_draft_title=True)

    else:
        selected_id = selected_option

        draft_data = get_draft_with_versions(selected_id)
        if draft_data:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Status", humanize(draft_data["status"]))
            c2.metric("Product", draft_data.get("product_name") or "Unassigned")
            c3.metric("Draft Versions", len(draft_data.get("versions", [])))
            c4.metric("Market / Channel", f"{draft_data.get('market','—')} / {draft_data.get('channel','—')}")

            with st.expander("📄 Draft Revision History", expanded=False):
                for v in draft_data.get("versions", []):
                    st.markdown(
                        f"**Version {v['version_number']}** — Source: `{v['source_type']}` | Created: `{v['created_at']}`"
                    )
                    st.code(v["raw_text"], language=None)

        events = get_review_audit_trail(selected_id)
        st.subheader(f"🕒 Review Events for {draft_data['title']} ({len(events)} events)")
        if not events:
            st.info("No review events recorded yet. Submit the draft for review first.")
            return

        for ev in events:
            _render_event_item(ev, show_draft_title=False)
