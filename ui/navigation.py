"""
ui/navigation.py
----------------
Sidebar navigation and role selector.
No authentication — this is a demo app.
"""

from __future__ import annotations

import streamlit as st

from models.enums import UserRole


PAGE_GROUPS = {
    UserRole.CREATOR: {
        "Workspace": ["Overview", "Brand Knowledge", "Brand Guidelines", "Products & Facts", "Brand Assets"],
        "Content Review": ["New Review", "Review Results", "Review History"],
        "Governance": ["Claim Ledger", "Rule Tests", "Insights"],
    },
    UserRole.REVIEWER: {
        "Workspace": ["Overview", "Brand Knowledge", "Brand Guidelines", "Products & Facts", "Brand Assets"],
        "Content Review": ["Review Results", "Review History"],
        "Governance": ["Claim Ledger", "Draft Approvals", "Rule Readiness", "Rule Tests", "Insights"],
    },
    UserRole.FACT_OWNER: {
        "Workspace": ["Overview", "Brand Knowledge", "Brand Guidelines", "Products & Facts", "Brand Assets"],
        "Content Review": ["Review History"],
        "Governance": ["Claim Ledger", "Fact Approvals", "Rule Tests", "Insights"],
    },
}

PAGES = {
    role: [page for group_pages in groups.values() for page in group_pages]
    for role, groups in PAGE_GROUPS.items()
}


# Map page labels to module paths
PAGE_MODULE = {
    "Overview": "ui.pages.overview",
    "Brand Knowledge": "ui.pages.brand_knowledge",
    "Brand Guidelines": "ui.pages.guidelines",
    "Products & Facts": "ui.pages.facts",
    "Brand Assets": "ui.pages.assets",
    "New Review": "ui.pages.new_review",
    "Review Results": "ui.pages.review_results",
    "Claim Ledger": "ui.pages.approvals",
    "Review History": "ui.pages.ledger",
    "Draft Approvals": "ui.pages.approval_queue",
    "Rule Readiness": "ui.pages.rule_readiness",
    "Rule Tests": "ui.pages.rule_tests",
    "Fact Approvals": "ui.pages.fact_approvals",
    "Insights": "ui.pages.insights",
    # Backward compatibility mappings
    "📜 Claim Ledger":             "ui.pages.approvals",
    "📜 Claim Ledger (Approvals)": "ui.pages.approvals",
    "📂 Review History":           "ui.pages.ledger",
    "✅ Approvals":                "ui.pages.approvals",
    "📂 Ledger":                   "ui.pages.ledger",
}


def _select_page(page_label: str) -> None:
    st.session_state["sidebar_page"] = page_label


def render_sidebar() -> tuple[UserRole, str]:
    """
    Render sidebar navigation.
    Returns (selected_role, selected_page_label).
    """
    with st.sidebar:
        st.markdown(
            '<div class="brl-wordmark">'
            '<div class="brl-wordmark__mark">BR</div>'
            '<div><div class="brl-wordmark__name">Brand Rule Lab</div>'
            '<div class="brl-wordmark__tag">Governance workspace</div></div>'
            '</div>',
            unsafe_allow_html=True,
        )

        st.markdown("#### Workspace role")
        st.caption("Simulated role control; this is not sign-in or authentication.")
        role_options = [r.value for r in UserRole]
        selected_role_str = st.selectbox(
            "Select your role",
            role_options,
            index=0,
            key="sidebar_role",
            help="No sign-in required — this is a demo. Each role has specific permissions.",
        )
        selected_role = UserRole(selected_role_str)

        role_desc = {
            UserRole.CREATOR: "Creator · Draft checks and claim submission",
            UserRole.REVIEWER: "Reviewer · Compliance decisions and approvals",
            UserRole.FACT_OWNER: "Fact Owner · Evidence and version governance",
        }.get(selected_role, "")
        if role_desc:
            st.caption(role_desc)

        groups = PAGE_GROUPS[selected_role]
        available_pages = [page for options in groups.values() for page in options]
        selected_page = st.session_state.get("sidebar_page", "Overview")
        if selected_page not in available_pages:
            selected_page = "Overview"
            st.session_state["sidebar_page"] = selected_page

        for group_name, options in groups.items():
            st.markdown(f"<div class='brl-nav-group'>{group_name}</div>", unsafe_allow_html=True)
            for page_label in options:
                st.button(
                    page_label,
                    key=f"sidebar_page_{page_label.casefold().replace(' ', '_').replace('&', 'and')}",
                    type="primary" if page_label == selected_page else "secondary",
                    width="stretch",
                    on_click=_select_page,
                    args=(page_label,),
                )

        st.markdown(
            "<div class='brl-sidebar-footer'>BRAND RULE LAB · DEMO<br>AI-assisted · human governed</div>",
            unsafe_allow_html=True,
        )

    return selected_role, selected_page
