"""Tamper-evident audit history and export controls."""

from __future__ import annotations

import streamlit as st

from services.audit_service import (
    AuditIntegrityError,
    export_audit_json,
    export_audit_pdf,
    verify_audit_export,
)
from services.auth_service import AuthenticatedPrincipal
from services.brand_service import BrandAccessError, require_brand_membership
from ui.components import page_header
from ui.styles import demo_banner, inject_styles


def render(principal: AuthenticatedPrincipal | None = None) -> None:
    inject_styles()
    page_header("Governance", "Audit & Exports")
    demo_banner()
    if principal is None:
        st.error("An authenticated reviewer or Fact Owner is required.")
        return
    brand_id = st.session_state.get("active_brand_id")
    if brand_id is None:
        st.error("Choose a brand workspace first.")
        return

    try:
        membership = require_brand_membership(principal, brand_id)
        brand_name = membership.get("name", f"Brand #{brand_id}")
    except BrandAccessError as exc:
        st.error(str(exc))
        return
    st.caption(f"Exporting audit log for: **{brand_name}**")
    st.markdown(
        "Export the append-only application event chain. JSON exports can be verified "
        "offline; any changed, removed, or reordered record is detected."
    )
    try:
        json_bytes = export_audit_json(principal, brand_id)
        pdf_bytes = export_audit_pdf(principal, brand_id)
    except (PermissionError, AuditIntegrityError) as exc:
        st.error(str(exc))
        return
    st.download_button(
        "Download verifiable JSON",
        json_bytes,
        file_name="brand_rule_lab_audit.json",
        mime="application/json",
    )
    st.download_button(
        "Download PDF report",
        pdf_bytes,
        file_name="brand_rule_lab_audit.pdf",
        mime="application/pdf",
    )
    verification = verify_audit_export(json_bytes)
    if verification["valid"]:
        st.success(
            f"Current export verified: {verification['record_count']} record(s); "
            f"head hash {verification.get('head_hash') or 'EMPTY'}."
        )
    else:
        st.error(f"Current export failed verification: {verification['reason']}")

    st.divider()
    st.subheader("Verify an exported audit JSON")
    st.caption("Verification is local to this app process; the file is not uploaded to an external service.")
    uploaded_export = st.file_uploader(
        "Choose a Brand Rule Lab audit JSON file",
        type=["json"],
        key="audit_export_to_verify",
    )
    if uploaded_export is not None and st.button(
        "Verify audit file", key="verify_audit_file"
    ):
        result = verify_audit_export(uploaded_export.getvalue())
        if result["valid"]:
            st.success(
                f"Valid hash chain: {result['record_count']} record(s); "
                f"head hash {result.get('head_hash') or 'EMPTY'}."
            )
        else:
            st.error(f"Audit file verification failed: {result['reason']}")
