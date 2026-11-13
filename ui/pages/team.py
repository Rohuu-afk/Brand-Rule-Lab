"""Brand Owner membership administration."""

import streamlit as st

from models.enums import BrandRole
from services.auth_service import AuthenticatedPrincipal
from services.brand_service import (
    BrandAccessError,
    change_member_role,
    invite_member,
    list_members,
    remove_member,
    transfer_brand_ownership,
)
from ui.components import page_header
from ui.styles import inject_styles


def render(principal: AuthenticatedPrincipal) -> None:
    inject_styles()
    brand_id = st.session_state.get("active_brand_id")
    if not brand_id:
        st.info("Choose a brand workspace first.")
        return
    page_header("Administration", "Team & Permissions")
    st.caption("Invite people to this brand and assign only the role they need.")
    try:
        members = list_members(principal, brand_id)
    except BrandAccessError as exc:
        st.error(str(exc))
        return
    if members:
        with st.container(border=True):
            for member in members:
                col_u, col_r = st.columns([3, 2])
                col_u.write(f"**{member['username']}**")
                col_r.caption(member['role'])
    st.divider()
    st.subheader("Invite a team member")
    with st.form("invite_member_form"):
        email = st.text_input("Invitee email")
        role = st.selectbox("Brand role", [BrandRole.CREATOR.value, BrandRole.REVIEWER.value, BrandRole.FACT_OWNER.value])
        submitted = st.form_submit_button("Create invitation", type="primary")
    if submitted:
        try:
            invitation = invite_member(principal, brand_id, email, role)
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.success(invitation["delivery_status"])
            if "unavailable" in invitation["delivery_status"].casefold():
                st.code(invitation["token"], language="text")
                st.caption("Share this one-time token with the invitee through a secure channel.")

    st.divider()
    st.subheader("Manage member roles")
    st.caption("Role changes cannot create Brand Owner access. Ownership transfers require a separate, explicit handoff.")
    for member in members:
        if member["user_id"] == principal.user_id or member["role"] == BrandRole.OWNER.value:
            continue
        new_role = st.selectbox(
            f"Role for {member['username']}",
            [BrandRole.CREATOR.value, BrandRole.REVIEWER.value, BrandRole.FACT_OWNER.value],
            index=[BrandRole.CREATOR.value, BrandRole.REVIEWER.value, BrandRole.FACT_OWNER.value].index(member["role"]),
            key=f"role_{member['user_id']}",
        )
        if st.button("Save role", key=f"save_role_{member['user_id']}"):
            try:
                change_member_role(principal, brand_id, member["user_id"], new_role)
                st.success("Role updated.")
                st.rerun()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))

    eligible = [
        member for member in members
        if member["role"] != BrandRole.OWNER.value
        and member["user_id"] != principal.user_id
    ]
    if eligible:
        with st.expander("Transfer brand ownership"):
            st.warning(
                "The selected member will become the Brand Owner. Your account will remain "
                "in this workspace with the role you choose below."
            )
            target = st.selectbox(
                "New Brand Owner",
                eligible,
                format_func=lambda member: f"{member['username']} · {member['role']}",
                key="ownership_transfer_target",
            )
            outgoing_role = st.selectbox(
                "Your role after transfer",
                [BrandRole.REVIEWER.value, BrandRole.CREATOR.value, BrandRole.FACT_OWNER.value],
                key="ownership_transfer_outgoing_role",
            )
            confirmation = st.text_input(
                'Type "TRANSFER OWNERSHIP" to confirm', key="ownership_transfer_confirmation"
            )
            if st.button(
                "Transfer ownership",
                key="transfer_brand_ownership",
                type="primary",
                disabled=confirmation != "TRANSFER OWNERSHIP",
            ):
                try:
                    transfer_brand_ownership(
                        principal, brand_id, target["user_id"],
                        outgoing_owner_role=outgoing_role,
                    )
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.success(f"Ownership transferred to {target['username']}.")
                    st.rerun()
