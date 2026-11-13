"""
ui/navigation.py
----------------
Authenticated sidebar navigation.
"""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

from models.enums import BrandRole, UserRole
from services.auth_service import AuthenticatedPrincipal
from services.brand_service import list_brands, require_brand_membership


_WORDMARK_LOGO_PATH = Path(__file__).parent / "assets" / "brand-rule-lab-logo.png"


PAGE_GROUPS = {
    UserRole.CREATOR: {
        "Workspace": ["Overview"],
        "Reviews": ["New Review", "Review Results", "Review History"],
        "Records": ["Claim Ledger"],
    },
    UserRole.REVIEWER: {
        "Workspace": ["Overview"],
        "Reviews": ["Review Results", "Review History", "Draft Approvals"],
        "Governance": [
            "Brand Knowledge", "Brand Guidelines", "Rule Readiness",
            "Claim Ledger", "Audit & Exports",
        ],
    },
    UserRole.FACT_OWNER: {
        "Workspace": ["Overview"],
        "Evidence": ["Brand Knowledge", "Products & Facts", "Brand Assets", "Fact Approvals"],
        "Records": ["Claim Ledger", "Audit & Exports"],
    },
    BrandRole.OWNER: {
        "Workspace": ["Overview"],
        "Reviews": ["New Review", "Review Results", "Review History", "Draft Approvals"],
        "Brand governance": [
            "Brand Knowledge", "Brand Guidelines", "Products & Facts", "Brand Assets",
            "Fact Approvals", "Claim Ledger", "Rule Readiness", "Insights",
        ],
        "Administration": ["Team & Permissions", "CMS Draft Integrations", "Audit & Exports"],
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
    "Audit & Exports": "ui.pages.audit",
    "CMS Draft Integrations": "ui.pages.integrations",
    "Team & Permissions": "ui.pages.team",
    # Backward compatibility mappings
    "📜 Claim Ledger":             "ui.pages.approvals",
    "📜 Claim Ledger (Approvals)": "ui.pages.approvals",
    "📂 Review History":           "ui.pages.ledger",
    "✅ Approvals":                "ui.pages.approvals",
    "📂 Ledger":                   "ui.pages.ledger",
}


def _select_page(page_label: str) -> None:
    st.session_state["sidebar_page"] = page_label


def render_sidebar(principal: AuthenticatedPrincipal) -> tuple[AuthenticatedPrincipal, str]:
    """
    Render sidebar navigation.
    Returns (authenticated principal, selected page label).
    """
    with st.sidebar:
        logo_data = base64.b64encode(_WORDMARK_LOGO_PATH.read_bytes()).decode("ascii")
        st.markdown(
            '<div class="brl-wordmark">'
            f'<img class="brl-wordmark__logo" src="data:image/png;base64,{logo_data}" '
            'alt="Brand Rule Lab logo">'
            '<div><div class="brl-wordmark__name">Brand Rule Lab</div>'
            '<div class="brl-wordmark__tag">Governance workspace</div></div>'
            '</div>',
            unsafe_allow_html=True,
        )

        st.toggle("Dark mode", key="brl_dark_mode", value=True)
        brands = list_brands(principal)
        if not brands:
            return principal, "Overview"
        active_id = st.session_state.get("active_brand_id")
        if active_id not in {brand["id"] for brand in brands}:
            active_id = brands[0]["id"]
            st.session_state["active_brand_id"] = active_id
        brand_labels = {f'{brand["name"]} · {brand["role"]}': brand["id"] for brand in brands}
        selected_label = next(label for label, value in brand_labels.items() if value == active_id)
        chosen_label = st.selectbox("Brand workspace", list(brand_labels), index=list(brand_labels).index(selected_label))
        active_id = brand_labels[chosen_label]
        st.session_state["active_brand_id"] = active_id
        membership = require_brand_membership(principal, active_id)
        selected_role = membership["role"]
        st.markdown(f"**Signed in:** `{principal.username}`")
        st.caption(f"Role: {selected_role}")
        if st.button("Log out", key="logout"):
            from services.auth_service import logout

            logout(st.session_state.get("brl_session_token", ""))
            st.session_state.pop("brl_session_token", None)
            if st.session_state.pop("brl_oidc_authenticated", False):
                st.logout()
            st.rerun()

        role_desc = {
            UserRole.CREATOR.value: "Draft reviews and claim submission",
            UserRole.REVIEWER.value: "Compliance decisions and approvals",
            UserRole.FACT_OWNER.value: "Evidence and version governance",
            BrandRole.OWNER.value: "Workspace and team administration",
        }.get(selected_role, "")
        if role_desc:
            st.caption(role_desc)

        groups = PAGE_GROUPS[BrandRole.OWNER if selected_role == BrandRole.OWNER.value else UserRole(selected_role)]
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

    return principal, selected_page
