"""
ui/pages/approvals.py
----------------------
📜 Claim-to-Fact Ledger:
Complete register of all approved marketing claims, cryptographic hashes,
stable identifiers, linked fact versions, and real-time drift statuses.
"""

from __future__ import annotations

import streamlit as st
from ui.styles import demo_banner, inject_styles
from ui.presentation import humanize
from ui.components import page_header
from services.approval_service import (
    get_approved_claims,
    get_claim_approval_snapshots,
    get_claim_fact_links,
    get_snapshot_fact_links,
)


def render():
    inject_styles()
    page_header("Governance", "Claim-to-Fact Ledger")
    demo_banner()

    st.markdown(
        "Immutable ledger linking every approved promotional claim to its underlying "
        "substantiating fact versions. If a fact changes or expires, drift is automatically flagged here."
    )

    all_claims = get_approved_claims()
    if not all_claims:
        st.info("No approved claims in ledger yet. Review and approve a draft in **Review Results**.")
        return

    # Top summary metrics
    n_total = len(all_claims)
    n_current = sum(1 for c in all_claims if c["drift_status"] == "CURRENT")
    n_review = sum(1 for c in all_claims if c["drift_status"] == "REVIEW_REQUIRED")
    n_expired = sum(1 for c in all_claims if c["drift_status"] == "EXPIRED")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Approved Claims", n_total)
    m2.metric("🟢 Current", n_current)
    m3.metric("🟡 Needs re-verification", n_review)
    m4.metric("🔴 Expired", n_expired)

    st.markdown("---")

    # Filter controls
    f_col1, f_col2 = st.columns([1, 2])
    with f_col1:
        drift_options = {
            "All statuses": "All",
            "Active and verified": "CURRENT",
            "Needs re-verification": "REVIEW_REQUIRED",
            "Evidence expired": "EXPIRED",
        }
        selected_drift = st.selectbox(
            "Filter by status",
            list(drift_options),
            key="ledger_drift_filter",
        )
        drift_filter = drift_options[selected_drift]
    with f_col2:
        products_in_ledger = sorted(
            list({c.get("product_name") or "Unspecified" for c in all_claims})
        )
        prod_filter = st.selectbox(
            "Filter by product",
            ["All Products"] + products_in_ledger,
            key="ledger_prod_filter",
        )

    # Apply filters
    filtered_claims = all_claims
    if drift_filter != "All":
        filtered_claims = [c for c in filtered_claims if c["drift_status"] == drift_filter]
    if prod_filter != "All Products":
        filtered_claims = [
            c for c in filtered_claims
            if (c.get("product_name") or "Unspecified") == prod_filter
        ]

    st.markdown(f"**Showing {len(filtered_claims)} of {n_total} claim(s)**")

    if not filtered_claims:
        st.info("No claims match the selected filter criteria.")
        return

    for claim in filtered_claims:
        drift = claim["drift_status"]
        drift_icon = {
            "CURRENT": "🟢",
            "REVIEW_REQUIRED": "🟡",
            "EXPIRED": "🔴",
        }.get(drift, "⚪")

        status_badge_html = {
            "CURRENT": "<span style='background-color:#E8F8F5;color:#117A65;padding:4px 10px;border-radius:12px;font-weight:bold;font-size:12px;'>🟢 Active & Verified</span>",
            "REVIEW_REQUIRED": "<span style='background-color:#FEF9E7;color:#B7950B;padding:4px 10px;border-radius:12px;font-weight:bold;font-size:12px;'>🟡 Needs Re-verification</span>",
            "EXPIRED": "<span style='background-color:#FDEDEC;color:#922B21;padding:4px 10px;border-radius:12px;font-weight:bold;font-size:12px;'>🔴 Evidence Expired</span>",
        }.get(drift, f"<span>{drift}</span>")

        human_drift = humanize(drift)

        with st.expander(
            f"{drift_icon} {claim['stable_claim_id']} — {claim.get('draft_title','Draft')} [{human_drift}]",
            expanded=(drift != "CURRENT"),
        ):
            c_header, c_status = st.columns([3, 2])
            with c_header:
                st.markdown(f"### `{claim['stable_claim_id']}`")
            with c_status:
                st.markdown(status_badge_html, unsafe_allow_html=True)

            st.markdown(f"**Approved Marketing Claim:**\n> {claim['approved_text']}")

            # Cryptographic hash and provenance
            h_col1, h_col2 = st.columns(2)
            with h_col1:
                st.caption(f"**Normalized Text Hash (SHA-256):**")
                st.code(claim.get("approved_text_hash", "N/A"), language=None)
            with h_col2:
                st.caption(f"**Stable Traceable Identifier:**")
                st.code(claim["stable_claim_id"], language=None)

            # Metadata provenance bar
            st.caption(
                f"👤 **Approved by:** `{claim.get('approved_by', 'demo_reviewer')}` &nbsp;|&nbsp; "
                f"🕒 **Approved at:** `{claim.get('approved_at', 'N/A')}` &nbsp;|&nbsp; "
                f"📦 **Product:** `{claim.get('product_name', 'N/A')}` &nbsp;|&nbsp; "
                f"📄 **Origin Draft ID:** `#{claim.get('draft_id', 'N/A')}`"
            )

            # Linked fact versions
            fact_links = get_claim_fact_links(claim["id"])
            st.markdown("#### 🔗 Linked Fact Versions (Exact Audit Provenance)")
            if fact_links:
                for fl in fact_links:
                    fv_status = fl.get("approval_status", "?")
                    fv_icon = {
                        "APPROVED": "🟢",
                        "EXPIRED": "🔴",
                        "PENDING_APPROVAL": "🟡",
                        "REJECTED": "⚫",
                    }.get(fv_status, "⚪")

                    human_fv = {
                        "APPROVED": "Active & Approved",
                        "EXPIRED": "Evidence expired",
                        "PENDING_APPROVAL": "Waiting for approval",
                        "REJECTED": "Rejected",
                    }.get(fv_status, fv_status)

                    st.markdown(
                        f"- {fv_icon} **{fl['fact_name']}** — "
                        f"**Version {fl['version_number']}** `[{human_fv}]`\n"
                        f"  > *\"{fl['statement']}\"*\n"
                        f"  *Evidence Reference: {fl.get('evidence_ref', 'N/A')}*"
                    )
            else:
                st.info("ℹ️ No fact versions linked to this claim.")
                st.caption("No individual fact versions explicitly linked.")

            snapshots = get_claim_approval_snapshots(claim["id"])
            if snapshots:
                with st.expander(f"Approval history ({len(snapshots)} snapshot(s))"):
                    for snapshot in snapshots:
                        st.markdown(
                            f"**Snapshot #{snapshot['id']}** · {snapshot['approved_at']} · "
                            f"Reviewer: `{snapshot['reviewer']}` · Market: `{snapshot.get('market') or 'Unspecified'}`"
                        )
                        st.write(snapshot["approved_text"])
                        historical_links = get_snapshot_fact_links(snapshot["id"])
                        if historical_links:
                            for link in historical_links:
                                st.caption(
                                    f"{link['fact_name']} v{link['version_number']} "
                                    f"(Fact Version #{link['fact_version_id']}) · {link.get('evidence_ref') or 'No evidence ref'}"
                                )
                        else:
                            st.caption("No fact versions were linked in this approval snapshot.")

            # Drift diagnosis & recommendation
            if drift == "REVIEW_REQUIRED":
                st.warning(
                    "**Evidence changed:** One or more linked fact versions have been superseded. "
                    "Re-check this claim against the current approved facts."
                )
            elif drift == "EXPIRED":
                st.error(
                    "**Evidence expired:** One or more linked fact versions are no longer active. "
                    "Hold this claim for re-verification."
                )
            else:
                st.success("✅ **Active**: All linked fact versions are currently active and approved.")
