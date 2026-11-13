"""
ui/pages/approvals.py
----------------------
📜 Claim-to-Fact Ledger:
Complete register of all approved marketing claims, cryptographic hashes,
stable identifiers, linked fact versions, and real-time drift statuses.
"""

from __future__ import annotations

import html

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
from services.auth_service import AuthenticatedPrincipal


def render(principal: AuthenticatedPrincipal) -> None:
    inject_styles()
    page_header("Governance", "Claim-to-Fact Ledger")
    demo_banner()

    st.markdown(
        "Immutable ledger linking every approved promotional claim to its underlying "
        "substantiating fact versions. If a fact changes or expires, drift is automatically flagged here."
    )

    brand_id = st.session_state.get("active_brand_id")
    all_claims = get_approved_claims(
        principal=principal, brand_id=brand_id
    )
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
    m2.metric("Active", n_current)
    m3.metric("Needs re-verification", n_review)
    m4.metric("Expired", n_expired)

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
        status_badge_html = {
            "CURRENT": "<span class='badge badge-pass'>Active & Verified</span>",
            "REVIEW_REQUIRED": "<span class='badge badge-warning'>Needs Re-verification</span>",
            "EXPIRED": "<span class='badge badge-fail'>Evidence Expired</span>",
        }.get(drift, f"<span class='badge badge-unlinked'>{drift}</span>")

        human_drift = humanize(drift)

        with st.expander(
            f"{claim['stable_claim_id']} — {claim.get('draft_title','Draft')} [{human_drift}]",
            expanded=(drift != "CURRENT"),
        ):
            c_header, c_status = st.columns([3, 2])
            with c_header:
                st.markdown(f"### `{claim['stable_claim_id']}`")
            with c_status:
                st.markdown(status_badge_html, unsafe_allow_html=True)

            st.markdown("**Approved wording**")
            st.markdown(
                f'<div class="quote-box">{html.escape(str(claim["approved_text"]))}</div>',
                unsafe_allow_html=True,
            )
            meta_product, meta_reviewer, meta_date = st.columns(3)
            meta_product.caption(f"Product · {claim.get('product_name') or 'Unassigned'}")
            meta_reviewer.caption(f"Approved by · {claim.get('approved_by') or 'Not recorded'}")
            meta_date.caption(f"Approved at · {claim.get('approved_at') or 'Not recorded'}")

            # Linked fact versions
            fact_links = get_claim_fact_links(
                claim["id"], principal=principal, brand_id=brand_id
            )
            st.markdown("#### Evidence linked to this claim")
            if fact_links:
                for fl in fact_links:
                    fv_status = fl.get("approval_status", "?")
                    human_fv = {
                        "APPROVED": "Active & Approved",
                        "EXPIRED": "Evidence expired",
                        "PENDING_APPROVAL": "Waiting for approval",
                        "REJECTED": "Rejected",
                    }.get(fv_status, fv_status)

                    with st.container(border=True):
                        st.markdown(
                            f"**{fl['fact_name']} · Version {fl['version_number']}** "
                            f"— {human_fv}"
                        )
                        st.markdown(
                            f'<div class="quote-box">{html.escape(str(fl["statement"]))}</div>',
                            unsafe_allow_html=True,
                        )
                        st.caption(
                            f"Evidence reference · {fl.get('evidence_ref') or 'Not recorded'}"
                        )
            else:
                st.info("No approved fact versions are linked to this claim.")
                st.caption("No individual fact versions explicitly linked.")

            with st.expander("Traceability details"):
                st.caption("Stable claim ID")
                st.code(claim["stable_claim_id"], language=None)
                st.caption("Approved wording hash · SHA-256")
                st.code(claim.get("approved_text_hash") or "Not recorded", language=None)
                st.caption(f"Originating draft · #{claim.get('draft_id', 'Not recorded')}")

            snapshots = get_claim_approval_snapshots(
                claim["id"], principal=principal, brand_id=brand_id
            )
            if snapshots:
                with st.expander(f"Approval history ({len(snapshots)} snapshot(s))"):
                    for snapshot in snapshots:
                        st.markdown(
                            f"**Snapshot #{snapshot['id']}** · {snapshot['approved_at']} · "
                            f"Reviewer: `{snapshot['reviewer']}` · Market: `{snapshot.get('market') or 'Unspecified'}`"
                        )
                        st.write(snapshot["approved_text"])
                        historical_links = get_snapshot_fact_links(
                            snapshot["id"], principal=principal, brand_id=brand_id
                        )
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
