"""Safe draft-only CMS integration demonstration."""

from __future__ import annotations

import streamlit as st

from services.audit_service import record_audit_event
from services.cms_adapters import CMSConfigurationError, get_cms_adapter
from services.auth_service import AuthenticatedPrincipal
from services.brand_service import require_brand_membership
from models.enums import BrandRole
from ui.components import page_header
from ui.styles import demo_banner, inject_styles


def render(principal: AuthenticatedPrincipal | None = None) -> None:
    inject_styles()
    page_header("Governance", "CMS Draft Integrations")
    demo_banner()
    if principal is None:
        st.error("An authenticated Brand Owner is required.")
        return
    brand_id = st.session_state.get("active_brand_id")
    require_brand_membership(principal, brand_id, (BrandRole.OWNER,))
    st.info(
        "This integration can create unpublished CMS drafts only. It cannot publish; "
        "a human must review and publish content in the CMS."
    )
    provider = st.selectbox("Provider", ["WordPress", "Shopify", "Contentful"])
    mode = st.radio(
        "Connection",
        ["Local demo", f"Live {provider}"],
        horizontal=True,
    )
    with st.form("cms_draft_form"):
        title = st.text_input("Draft title")
        content = st.text_area("Draft content")
        submitted = st.form_submit_button("Create unpublished draft", type="primary")
    if submitted:
        adapter_mode = "live" if mode.startswith("Live ") else "mock"
        try:
            result = get_cms_adapter(provider, mode=adapter_mode).create_draft(
                {"title": title, "content": content}
            )
        except (ValueError, CMSConfigurationError) as exc:
            st.error(str(exc))
        else:
            if principal is not None:
                record_audit_event(
                    principal,
                    action="CMS_DRAFT_CREATED",
                    object_type="cms_draft",
                    object_id=result["draft_id"],
                    brand_id=brand_id,
                    details={"provider": result["provider"], "mode": result["mode"]},
                )
            st.success(f"Created {result['provider']} draft {result['draft_id']}.")
            if result.get("edit_url"):
                st.link_button("Open draft in WordPress", result["edit_url"])
            st.caption(
                "Status: draft. Published: no."
                if result["mode"] == "live"
                else "Local demo only. No external network request was made."
            )
