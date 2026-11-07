"""Fact Owner queue for approving or rejecting proposed evidence versions."""

from __future__ import annotations

import streamlit as st

from models.enums import UserRole
from services.fact_service import (
    approve_fact_version,
    get_pending_fact_versions,
    reject_fact_version,
)
from ui.styles import demo_banner, inject_styles
from ui.components import page_header


def render(role: UserRole | None = None) -> None:
    inject_styles()
    page_header("Governance", "Fact Approvals")
    demo_banner()

    if role != UserRole.FACT_OWNER:
        st.info("Pending fact decisions are available in the simulated Fact Owner role.")
        return

    st.caption(
        "Verify each evidence source before approving. Approved versions become available to reviews; "
        "rejected versions remain in the audit history."
    )
    pending = get_pending_fact_versions()
    if not pending:
        st.info("No fact versions are waiting for approval.")
        return

    for fact_version in pending:
        with st.container(border=True):
            st.subheader(
                f"{fact_version['fact_name']} · Version {fact_version['version_number']}"
            )
            st.caption(
                f"{fact_version['product_name']} · Evidence: "
                f"{fact_version.get('evidence_ref') or 'No evidence reference provided'}"
            )
            st.write(fact_version["statement"])
            if fact_version.get("valid_from") or fact_version.get("valid_until"):
                st.caption(
                    f"Validity: {fact_version.get('valid_from') or 'Not specified'} to "
                    f"{fact_version.get('valid_until') or 'Not specified'}"
                )

            approve_col, reject_col = st.columns(2)
            if approve_col.button(
                "Approve evidence",
                key=f"approve_pending_fact_{fact_version['id']}",
                type="primary",
            ):
                affected = approve_fact_version(fact_version["id"], actor_role=role)
                if affected:
                    st.success(
                        f"Approved. {affected} claim(s) linked to earlier evidence now need re-verification."
                    )
                else:
                    st.success("Fact version approved.")
                st.rerun()

            if reject_col.button(
                "Reject proposal",
                key=f"reject_pending_fact_{fact_version['id']}",
            ):
                if reject_fact_version(fact_version["id"], actor_role=role):
                    st.success("Fact version rejected and retained in the audit history.")
                else:
                    st.warning("This fact version is no longer waiting for approval.")
                st.rerun()
