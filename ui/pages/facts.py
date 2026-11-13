"""
ui/pages/facts.py
-----------------
Fact catalog and version governance.
Fact Owner: browse facts, create facts, create new versions, approve/expire versions.
Creator & Reviewer: view-only mode with clear permission notice.
"""

from __future__ import annotations

import streamlit as st
from ui.styles import demo_banner, inject_styles
from ui.presentation import humanize
from ui.components import page_header
from models.enums import BrandRole, UserRole
from services.guideline_service import create_product, get_all_products
from services.fact_service import (
    get_all_facts,
    get_fact_versions,
    create_fact,
    create_fact_version,
    approve_fact_version,
    expire_fact_version,
    import_facts_csv,
)
from services.auth_service import AuthenticatedPrincipal
from services.brand_service import require_brand_membership


def render(
    role: UserRole | None = None,
    principal: AuthenticatedPrincipal | None = None,
):
    inject_styles()
    page_header("Workspace", "Products & Facts")
    demo_banner()

    brand_id = st.session_state.get("active_brand_id")
    if principal is not None:
        membership = require_brand_membership(principal, brand_id)
        role = membership["role"]
    is_fact_owner = role in {BrandRole.OWNER.value, BrandRole.FACT_OWNER.value}
    if not is_fact_owner:
        st.info("You are viewing facts in read-only mode. A workspace Fact Owner manages evidence versions.")
    else:
        st.caption(
            "Fact versions are immutable. Create a new version to update evidence; expiring a version flags linked claims."
        )
    products = get_all_products(principal=principal, brand_id=brand_id)
    if role == BrandRole.OWNER.value:
        with st.expander("Add a product", expanded=not products):
            with st.form("create_product_form"):
                product_name = st.text_input("Product name")
                product_aliases = st.text_input(
                    "SKU or aliases (comma separated)",
                    help="Optional names or SKUs that identify this product.",
                )
                product_markets = st.multiselect(
                    "Markets served", ["IN", "US", "GB", "AU", "SG"]
                )
                product_description = st.text_area("Product description (optional)")
                create_product_submitted = st.form_submit_button(
                    "Create product", type="primary"
                )
            if create_product_submitted:
                try:
                    product_id = create_product(
                        product_name,
                        sku_aliases=product_aliases.split(","),
                        markets=product_markets,
                        description=product_description,
                        principal=principal,
                        brand_id=brand_id,
                    )
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.success(f"Product created (id={product_id}). You can now add facts and evidence.")
                    st.rerun()
    if is_fact_owner:
        with st.expander("Import facts from CSV", expanded=False):
            st.caption(
                "Headers: product_or_sku,fact_name,category,markets,statement,evidence_reference,"
                "valid_from,valid_until. Use | between market codes and YYYY-MM-DD dates. "
                "Every row is validated before any records are written; imports await Fact Owner approval."
            )
            csv_upload = st.file_uploader("CSV facts file", type=["csv"], key="facts_csv_upload")
            if st.button("Validate and import CSV", key="import_facts_csv"):
                if csv_upload is None:
                    st.error("Choose a CSV file first.")
                else:
                    outcome = import_facts_csv(
                        csv_upload.getvalue().decode("utf-8-sig"), actor_role=role,
                        principal=principal, brand_id=brand_id,
                    )
                    if outcome["errors"]:
                        for error in outcome["errors"]:
                            st.error(error)
                    else:
                        st.success(
                            f"Imported {outcome['imported']} pending fact version(s); no version was pre-approved."
                        )
                        st.rerun()

    product_options = {"All Products": None} | {p["name"]: p["id"] for p in products}
    selected_product_name = st.selectbox("Filter by product", list(product_options.keys()))
    product_id = product_options[selected_product_name]

    facts = get_all_facts(product_id=product_id, principal=principal, brand_id=brand_id)

    st.markdown(f"**{len(facts)} fact(s) found**")
    if not facts:
        st.info("No facts found for the selected filter.")

    for fact in facts:
        versions = get_fact_versions(fact["id"], principal=principal, brand_id=brand_id)
        with st.expander(
            f"{fact['name']} [{fact['category']}] — {len(versions)} version(s)",
            expanded=False,
        ):
            for v in versions:
                badge_cls = {
                    "APPROVED": "badge-pass",
                    "PENDING_APPROVAL": "badge-warning",
                    "REJECTED": "badge-fail",
                    "EXPIRED": "badge-fail",
                }.get(v["approval_status"], "badge-review")
                st.markdown(
                    f"<span class='badge {badge_cls}'>Version {v['version_number']} · {humanize(v['approval_status'])}</span>",
                    unsafe_allow_html=True,
                )
                st.write(v["statement"])

                meta_parts = []
                if v.get("evidence_ref"):
                    meta_parts.append(f"Evidence: {v['evidence_ref']}")
                if v.get("valid_from") or v.get("valid_until"):
                    vf = v.get("valid_from") or "Start"
                    vu = v.get("valid_until") or "Indefinite"
                    meta_parts.append(f"Validity: {vf} → {vu}")
                if v.get("approved_by"):
                    meta_parts.append(f"Approved by: {v['approved_by']}")
                if meta_parts:
                    st.caption(" | ".join(meta_parts))

                # Fact governance controls: ONLY visible and executable by FACT_OWNER
                if is_fact_owner:
                    col1, col2 = st.columns(2)
                    if v["approval_status"] == "PENDING_APPROVAL":
                        if col1.button(
                            "Approve version", key=f"approve_fv_{v['id']}", type="primary"
                        ):
                            affected = approve_fact_version(
                                v["id"], principal=principal, brand_id=brand_id
                            )
                            if affected:
                                st.warning(
                                    f"Fact version approved. {affected} dependent claim(s) need re-verification."
                                )
                            else:
                                st.success("Fact version approved.")
                            st.rerun()

                    if v["approval_status"] == "APPROVED":
                        if col2.button(
                            "Expire version", key=f"expire_fv_{v['id']}"
                        ):
                            n = expire_fact_version(
                                v["id"], principal=principal, brand_id=brand_id
                            )
                            msg = f"Fact version expired. {n} dependent claim(s) flagged." if n else "Fact version expired."
                            st.warning(msg)
                            st.rerun()

                st.markdown("---")

            # Create new version form: ONLY visible to FACT_OWNER
            if is_fact_owner:
                with st.form(key=f"new_version_form_{fact['id']}"):
                    st.markdown("**Create new version**")
                    new_statement = st.text_area("New fact statement", key=f"nv_stmt_{fact['id']}")
                    new_evidence = st.text_input("Evidence reference", key=f"nv_ev_{fact['id']}")
                    valid_from = st.date_input("Valid from (optional)", value=None, key=f"nv_vf_{fact['id']}")
                    valid_until = st.date_input("Valid until (optional)", value=None, key=f"nv_vu_{fact['id']}")
                    submitted = st.form_submit_button("Create Version (Pending Approval)")
                    if submitted:
                        if not new_statement.strip():
                            st.error("Statement cannot be empty.")
                        else:
                            create_fact_version(
                                fact["id"],
                                new_statement.strip(),
                                new_evidence.strip(),
                                str(valid_from) if valid_from else None,
                                str(valid_until) if valid_until else None,
                                principal=principal, brand_id=brand_id,
                            )
                            st.success("New fact version created. It is waiting for approval.")
                            st.rerun()

    # Create new fact form: ONLY visible to FACT_OWNER
    if is_fact_owner:
        st.markdown("---")
        st.subheader("Create a new fact")
        pid_opts = {p["name"]: p["id"] for p in products}
        if not pid_opts:
            st.info("Create a product before adding facts.")
        else:
            if st.session_state.get("nf_prod") not in pid_opts:
                st.session_state["nf_prod"] = next(iter(pid_opts))
            with st.form("new_fact_form"):
                p_name = st.selectbox("Product", list(pid_opts.keys()), key="nf_prod")
                f_name = st.text_input("Fact name")
                f_cat = st.selectbox(
                    "Category",
                    ["ingredient", "efficacy_claim", "product_specification", "statistic", "other"],
                )
                f_markets = st.multiselect("Markets", ["IN", "US", "GB", "AU", "SG"])
                if st.form_submit_button("Create Fact"):
                    if not f_name.strip():
                        st.error("Fact name required.")
                    else:
                        new_fid = create_fact(
                            pid_opts[p_name], f_name.strip(), f_cat, f_markets,
                            principal=principal, brand_id=brand_id,
                        )
                        st.success(f"Fact created (id={new_fid}). Add a version above.")
                        st.rerun()
