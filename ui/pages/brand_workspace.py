"""First-use brand workspace onboarding."""

import streamlit as st

from services.auth_service import AuthenticatedPrincipal
from services.brand_service import accept_invitation, create_brand
from ui.components import page_header
from ui.styles import inject_styles


def render(principal: AuthenticatedPrincipal) -> None:
    inject_styles()
    page_header("Workspace", "Create your brand workspace")
    st.write(
        "A brand workspace keeps guidelines, drafts, reviews, assets, claims, and audit "
        "records separate. The person who creates it becomes its Brand Owner."
    )
    with st.form("create_brand_form"):
        name = st.text_input("Brand name", help="Use the name your team will recognize.")
        submitted = st.form_submit_button("Create brand", type="primary")
    if submitted:
        try:
            brand = create_brand(principal, name)
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.session_state["active_brand_id"] = brand["id"]
            st.success(f"{brand['name']} is ready. You are its Brand Owner.")
            st.rerun()
    st.divider()
    st.subheader("Or accept a team invitation")
    with st.form("accept_brand_invitation_form"):
        token = st.text_input("Invitation token", type="password")
        accepted = st.form_submit_button("Accept invitation")
    if accepted:
        try:
            brand = accept_invitation(principal, token)
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.session_state["active_brand_id"] = brand["id"]
            st.success(f"You now have access to {brand['name']}.")
            st.rerun()
