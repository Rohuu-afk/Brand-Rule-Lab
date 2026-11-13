"""Reusable asset registration, approval, and local identity checks."""

from __future__ import annotations

import streamlit as st

from models.enums import BrandRole
from services.auth_service import AuthenticatedPrincipal
from services.brand_service import require_brand_membership
from services.document_parser import MAX_BYTES
from services.asset_service import decide_asset, register_image_asset
from services.guideline_service import get_all_assets, get_all_products
from ui.styles import demo_banner, inject_styles
from ui.components import page_header


def render(principal: AuthenticatedPrincipal | None = None):
    inject_styles()
    page_header("Workspace", "Brand Assets")
    demo_banner()
    brand_id = st.session_state.get("active_brand_id")
    membership = require_brand_membership(principal, brand_id)
    role = membership["role"]
    can_manage = role in {
        BrandRole.OWNER.value, BrandRole.REVIEWER.value, BrandRole.FACT_OWNER.value
    }
    if not can_manage:
        st.info("Asset registration and approval are available to workspace Reviewers, Fact Owners and Owners.")
    products = get_all_products(principal=principal, brand_id=brand_id)
    product_options = {p["name"]: p["id"] for p in products}
    if not products:
        st.info("Add a product before registering assets.")
        return

    selected = st.selectbox("Filter by product", ["All Products"] + list(product_options))
    product_id = product_options.get(selected)
    assets = get_all_assets(
        product_id=product_id, include_inactive=True,
        principal=principal, brand_id=brand_id,
    )

    for asset in assets:
        state = "Approved and active" if asset["is_active"] and asset["owner_approved"] else (
            "Pending approval" if not asset["owner_approved"] else "Disabled"
        )
        with st.expander(f"{asset['name']} [{asset['asset_type']}] — {state}"):
            st.write(f"Product: {asset.get('product_name') or 'All'}")
            st.caption(f"SHA-256 exact identity: {asset.get('sha256_hash') or 'Unavailable'}")
            st.caption(f"Perceptual similarity hash: {asset.get('perceptual_hash') or 'Unavailable'}")
            st.caption("Perceptual hashes indicate similarity only; they do not prove visual correctness.")
            if can_manage and not asset["owner_approved"]:
                if st.button("Approve asset", key=f"asset_approve_{asset['id']}"):
                    decide_asset(asset["id"], approved=True, principal=principal, brand_id=brand_id)
                    st.success("Asset approved.")
                    st.rerun()
            elif can_manage and asset["is_active"]:
                if st.button("Disable asset", key=f"asset_disable_{asset['id']}"):
                    decide_asset(asset["id"], approved=False, principal=principal, brand_id=brand_id)
                    st.success("Asset disabled.")
                    st.rerun()

    if can_manage:
        st.subheader("Register an approved reusable image")
        with st.form("register_asset_form"):
            name = st.text_input("Asset name")
            asset_type = st.selectbox("Asset type", ["logo", "packaging", "product_image", "other"])
            product_name = st.selectbox("Product", list(product_options))
            uploaded = st.file_uploader("Image file (PNG/JPG/JPEG, max 10 MB)", type=["png", "jpg", "jpeg"])
            submitted = st.form_submit_button("Register pending asset")
        if submitted:
            if uploaded is None:
                st.error("Choose an image to register.")
            elif uploaded.size > MAX_BYTES:
                st.error(f"Image exceeds the {MAX_BYTES // (1024 * 1024)} MB upload limit.")
            else:
                try:
                    asset_id = register_image_asset(
                        name, asset_type, uploaded.name, uploaded.getvalue(),
                        product_options[product_name],
                        principal=principal,
                        brand_id=brand_id,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success(f"Asset #{asset_id} registered locally and is awaiting approval.")
                    st.rerun()
