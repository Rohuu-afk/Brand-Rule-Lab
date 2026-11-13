"""
app.py
------
Brand Rule Lab — Streamlit entrypoint.

Run locally:
    streamlit run app.py

The app:
  - applies the database schema and seeds demo data on first launch
  - renders the sidebar navigation
  - routes to the selected page module
"""

import sys
import logging
from pathlib import Path

logger = logging.getLogger("brand_rule_lab.app")

# Ensure project root is on sys.path (needed when run from subdirs)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

st.set_page_config(
    page_title="Brand Rule Lab",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)

from services.demo_access import (
    configured_demo_passwords,
    demo_access_enabled,
    demo_database_is_isolated,
    load_streamlit_demo_settings,
)

try:
    load_streamlit_demo_settings(st.secrets)
except Exception as exc:
    logger.warning(
        "Streamlit demo configuration could not be loaded (%s)",
        type(exc).__name__,
    )

from services.auth_service import AuthenticationError, login_oidc_identity, logout, resolve_session
from services.brand_service import accept_invitation, list_brands

# Apply schema + seed on initial server startup (seed applies migrations once).
from db.connection import get_db_path
from db.seed import seed


@st.cache_resource
def _initialize_database(database_path: str) -> bool:
    if database_path != str(get_db_path().resolve()):
        raise RuntimeError("Database path changed while initialization was starting.")
    seed(force=False, verbose=False)
    if demo_access_enabled():
        if not demo_database_is_isolated(database_path):
            logger.error(
                "Public demo access is enabled without an isolated judge-demo database."
            )
        else:
            passwords = configured_demo_passwords()
            if passwords is None:
                logger.warning(
                    "Public demo access needs all four synthetic demo passwords."
                )
            else:
                from db.provision_demo_accounts import provision_demo_accounts

                provision_demo_accounts(passwords=passwords)
    return True


_initialize_database(str(get_db_path().resolve()))


def _get_authenticated_principal():
    token = st.session_state.get("brl_session_token")
    if not token:
        return None
    try:
        return resolve_session(token)
    except AuthenticationError:
        st.session_state.pop("brl_session_token", None)
        return None


principal = _get_authenticated_principal()
force_public_auth = bool(st.query_params.get("verify") or st.query_params.get("reset"))
if force_public_auth:
    existing_token = st.session_state.pop("brl_session_token", None)
    if existing_token:
        logout(existing_token)
    principal = None
elif principal is None:
    try:
        identity = st.user
        if getattr(identity, "is_logged_in", False):
            identity_data = identity.to_dict()
            oidc_token = login_oidc_identity(
                identity_data.get("sub"),
                identity_data.get("email"),
                identity_data.get("email_verified"),
            )
            st.session_state["brl_session_token"] = oidc_token
            st.session_state["brl_oidc_authenticated"] = True
            principal = resolve_session(oidc_token)
    except AuthenticationError as exc:
        st.session_state["brl_oidc_error"] = str(exc)

invite_token = st.query_params.get("invite")
if principal is not None and invite_token:
    try:
        joined_brand = accept_invitation(principal, invite_token)
    except (ValueError, PermissionError):
        st.session_state["brl_invitation_error"] = (
            "You are signed in, but this invitation could not be accepted. "
            "Confirm the account email matches the verified invited address."
        )
    else:
        st.session_state["active_brand_id"] = joined_brand["id"]
        st.query_params.clear()
        st.rerun()
invitation_error = st.session_state.pop("brl_invitation_error", None)
if invitation_error:
    st.error(invitation_error)
if principal is None:
    from ui.public_home import render

    render()
    st.stop()
elif not list_brands(principal):
    from ui.pages.brand_workspace import render

    render(principal)
    st.stop()

if principal is not None:
    # Navigation
    from ui.navigation import render_sidebar, PAGE_MODULE
    from ui.styles import inject_styles

    principal, page_label = render_sidebar(principal)

    # Dynamic page routing
    import importlib

    module_path = PAGE_MODULE.get(page_label)
    if module_path:
        try:
            module = importlib.import_module(module_path)
            # Every authenticated route receives the session-backed identity. Pages
            # resolve permissions from the selected brand membership themselves.
            module.render(principal=principal)
        except Exception as exc:
            logger.exception("Page rendering failed for %s", module_path)
            st.error("This page could not be loaded. Please try again or contact the workspace administrator.")
    else:
        st.error(f"Page not found: {page_label!r}")
