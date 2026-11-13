"""Public landing and sign-in view for Brand Rule Lab."""

from __future__ import annotations

import sqlite3
import os
from pathlib import Path

import streamlit as st

from db.connection import get_connection, get_db_path
from services.auth_service import (
    AuthenticationError,
    authenticate_user,
    create_email_verification_token_for_email,
    create_password_reset_token,
    login,
    register_invited_user,
    login_oidc_identity,
    reset_password,
    resolve_session,
    sso_required,
    get_user,
    verify_email,
)
from services.demo_access import (
    configured_demo_passwords,
    demo_access_enabled,
    demo_database_is_isolated,
)
from services.brand_service import (
    accept_invitation,
    accept_invitation_for_verified_email,
    can_register_initial_owner,
    get_invitation_details,
    get_pending_invitations_for_email,
    list_brands,
    register_initial_owner,
    register_invited_user_with_oidc,
)
from services.email_service import application_url, send_email
from ui.styles import inject_styles


_LOGO = Path(__file__).parent / "assets" / "brand-rule-lab-logo.png"
_DEMO_ROLES = {
    "Creator": ("demo_creator", "DEMO_CREATOR_PASSWORD"),
    "Reviewer": ("demo_reviewer", "DEMO_REVIEWER_PASSWORD"),
    "Fact Owner": ("demo_fact_owner", "DEMO_FACT_OWNER_PASSWORD"),
    "Brand Owner": ("demo_owner", "DEMO_OWNER_PASSWORD"),
}


def _render_demo_access() -> None:
    st.subheader("Choose a demo role")
    st.caption(
        "Explore preloaded synthetic content. Demo access is enabled only on a "
        "separate judge-demo database."
    )
    if not demo_access_enabled():
        st.info("Demo access is not enabled for this deployment.")
        return
    if not demo_database_is_isolated(get_db_path()):
        st.error("Demo access is blocked because this app is not using an isolated judge-demo database.")
        return
    if sso_required():
        st.info("Password-based demo access is unavailable because this deployment requires SSO.")
        return

    passwords = configured_demo_passwords()
    available = {}
    if passwords:
        for label, (username, _) in _DEMO_ROLES.items():
            user = get_user(username)
            password = passwords[username]
            if (
                user is not None
                and user["is_active"]
                and _demo_account_is_scoped(username)
            ):
                try:
                    authenticate_user(username, password)
                except AuthenticationError:
                    continue
                available[label] = (username, password)
    if not available:
        st.warning(
            "Demo accounts are not ready. Configure all four synthetic demo "
            "passwords in this app's secrets and let the isolated demo database initialize."
        )
        return

    selected_role = st.selectbox("Demo role", list(available), key="public_demo_role")
    username, password = available[selected_role]
    with st.container(border=True):
        st.markdown(f"**{selected_role} · Brand Rule Lab Demo**")
        st.caption("Synthetic account credentials")
        st.code(f"Username  {username}\nPassword  {password}")
        if st.button(
            f"Enter as {selected_role}",
            type="primary",
            width="stretch",
            key="public_enter_demo",
        ):
            try:
                token = login(username, password)
                principal = resolve_session(token)
                brands = list_brands(principal)
            except (AuthenticationError, PermissionError):
                st.error("This demo account could not sign in. Ask the administrator to re-provision demo access.")
                return
            if not brands:
                st.error("This demo account is not assigned to the demo workspace.")
                return
            st.session_state["brl_session_token"] = token
            st.session_state["active_brand_id"] = brands[0]["id"]
            st.rerun()


def _demo_account_is_scoped(username: str) -> bool:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT b.slug, b.is_active, bm.role
            FROM users u
            JOIN brand_memberships bm ON bm.user_id = u.id
            JOIN brands b ON b.id = bm.brand_id
            WHERE u.username = ?
            """,
            (username,),
        ).fetchall()
    expected_role = {
        "demo_creator": "Creator",
        "demo_reviewer": "Reviewer",
        "demo_fact_owner": "Fact Owner",
        "demo_owner": "Brand Owner",
    }[username]
    return (
        len(rows) == 1
        and rows[0]["slug"] == "demo-brand"
        and rows[0]["is_active"] == 1
        and rows[0]["role"] == expected_role
    )


def _show_sign_in() -> None:
    st.subheader("Sign in")
    st.caption("Use your work email or username to open your brand workspace.")
    st.info(
        "New here? Ask your Brand Owner to invite your work email. Open the "
        "invitation link to create an account or join an existing account. "
        "Google sign-in is available when enabled for this app."
    )
    if _oidc_configured():
        st.button("Continue with SSO", on_click=st.login, type="secondary")
    if sso_required():
        if not _oidc_configured():
            st.error("SSO is required, but the identity provider is not configured.")
        return
    with st.form("login_form"):
        username = st.text_input("Username or email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
    if submitted:
        try:
            token = login(username, password)
        except AuthenticationError:
            st.error("We couldn't sign you in. Check your details and try again.")
        else:
            st.session_state["brl_session_token"] = token
            invite_token = st.query_params.get("invite")
            if invite_token:
                try:
                    brand = accept_invitation(resolve_session(token), invite_token)
                except (ValueError, PermissionError):
                    st.error("You signed in, but this invitation could not be accepted.")
                else:
                    st.session_state["active_brand_id"] = brand["id"]
                    st.query_params.clear()
            st.rerun()


def _oidc_configured() -> bool:
    try:
        auth = st.secrets.get("auth", {})
        required = ("redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url")
        return all(str(auth.get(key, "")).strip() for key in required)
    except Exception:
        return False


def _render_invitation(token: str) -> None:
    invitation = get_invitation_details(token)
    if invitation is None:
        st.error("This invitation is invalid, expired, or has already been used.")
        return
    st.subheader("Join your invited workspace")
    st.write(f"{invitation['brand_name']} · {invitation['role']}")
    st.caption(f"Invitation for {invitation['email']}")
    if sso_required():
        if _oidc_configured():
            st.button("Continue with SSO", on_click=st.login, type="primary")
        else:
            st.error("SSO is required, but the identity provider is not configured.")
        return
    if _oidc_configured():
        st.button(
            "Continue with SSO",
            on_click=st.login,
            type="secondary",
            key="invitation_sso_sign_in",
        )
    if st.button("Already have an account? Sign in", key="invitation_sign_in"):
        st.session_state["brl_show_login"] = True
        st.rerun()
    with st.form("invitation_registration_form"):
        username = st.text_input("Choose a username")
        password = st.text_input("Password (at least 12 characters)", type="password")
        submitted = st.form_submit_button("Create account and join", type="primary")
    if submitted:
        try:
            register_invited_user(token, username, password)
            session_token = login(invitation["email"], password)
            brand = accept_invitation(resolve_session(session_token), token)
        except (AuthenticationError, ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.session_state["brl_session_token"] = session_token
            st.session_state["active_brand_id"] = brand["id"]
            st.query_params.clear()
            st.rerun()


def _render_initial_owner_registration() -> None:
    if sso_required() or not can_register_initial_owner():
        return
    with st.expander("Create the first Brand Owner account"):
        st.caption("Public registration closes as soon as the first account or workspace exists.")
        with st.form("initial_owner_registration_form"):
            username = st.text_input("Username", key="first_owner_username")
            email = st.text_input("Work email", key="first_owner_email")
            brand_name = st.text_input("Brand workspace name", key="first_owner_brand")
            password = st.text_input("Password (at least 12 characters)", type="password", key="first_owner_password")
            submitted = st.form_submit_button("Create owner account", type="primary")
        if not submitted:
            return
        try:
            account = register_initial_owner(username, email, password, brand_name)
        except ValueError as exc:
            st.error(str(exc))
            return
        verification_url = application_url(f"?verify={account['token']}")
        delivered = send_email(
            email.strip().casefold(),
            "Verify your Brand Rule Lab account",
            f"Verify your email and activate your workspace owner account: {verification_url}\n\nThis link expires in 24 hours and can be used once.",
        )
        if delivered:
            st.success("Account created. Check your email for the verification link before signing in.")
        elif os.getenv("ALLOW_DEV_AUTH_LINKS", "0").casefold() in {"1", "true", "yes"}:
            st.warning("Email is not configured. Local development verification link:")
            st.code(verification_url)
        else:
            st.warning(
                "Your account is waiting for email verification, but we couldn't "
                "send the link. Ask the workspace owner to configure email delivery, "
                "then use “Resend email verification” before signing in."
            )


def _oidc_identity_data() -> dict | None:
    try:
        identity = st.user
        if not getattr(identity, "is_logged_in", False):
            return None
        return identity.to_dict()
    except Exception:
        return None


def _render_oidc_account_setup() -> None:
    identity = _oidc_identity_data()
    if not identity:
        return
    subject = str(identity.get("sub") or "").strip()
    email = str(identity.get("email") or "").strip().casefold()
    email_verified = identity.get("email_verified") is True or str(
        identity.get("email_verified", "")
    ).casefold() == "true"
    if not subject or not email or not email_verified:
        st.error("The identity provider did not provide a verified email address.")
        return

    invitations = get_pending_invitations_for_email(email)
    if invitations:
        st.subheader("Join an invited workspace with SSO")
        with st.form("google_invitation_registration_form"):
            invitation_id = st.selectbox(
                "Workspace invitation",
                [item["id"] for item in invitations],
                format_func=lambda selected_id: next(
                    f"{item['brand_name']} · {item['role']}"
                    for item in invitations if item["id"] == selected_id
                ),
            )
            username = st.text_input("Choose a username")
            submitted = st.form_submit_button("Create account and join", type="primary")
        if submitted:
            try:
                register_invited_user_with_oidc(
                    invitation_id, username, subject, email,
                    email_verified=email_verified,
                )
                session_token = login_oidc_identity(subject, email, email_verified)
                brand = accept_invitation_for_verified_email(
                    resolve_session(session_token), invitation_id
                )
            except sqlite3.IntegrityError:
                st.error("That SSO account, username, or email is already registered.")
            except (AuthenticationError, ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.session_state["brl_session_token"] = session_token
                st.session_state["brl_oidc_authenticated"] = True
                st.session_state["active_brand_id"] = brand["id"]
                st.query_params.clear()
                st.rerun()
        return

    if can_register_initial_owner():
        st.subheader("Create the first workspace with SSO")
        with st.form("google_initial_owner_form"):
            username = st.text_input("Choose a username")
            brand_name = st.text_input("Brand workspace name")
            submitted = st.form_submit_button("Create Brand Owner account", type="primary")
        if submitted:
            try:
                register_initial_owner(
                    username, email, None, brand_name,
                    oidc_subject=subject, oidc_email_verified=email_verified,
                )
                session_token = login_oidc_identity(subject, email, email_verified)
                principal = resolve_session(session_token)
                brand = next(
                    item for item in list_brands(principal)
                    if item["role"] == "Brand Owner"
                )
            except sqlite3.IntegrityError:
                st.error("That SSO account, username, or email is already registered.")
            except (AuthenticationError, ValueError, PermissionError, StopIteration) as exc:
                st.error(str(exc))
            else:
                st.session_state["brl_session_token"] = session_token
                st.session_state["brl_oidc_authenticated"] = True
                st.session_state["active_brand_id"] = brand["id"]
                st.query_params.clear()
                st.rerun()
        return

    st.info("This SSO account needs an invitation or an existing verified workspace account.")


def _render_password_reset() -> bool:
    if sso_required() and st.query_params.get("reset"):
        st.error("Password resets are disabled because this workspace requires SSO.")
        return True
    reset_token = st.query_params.get("reset")
    if reset_token:
        st.subheader("Set a new password")
        with st.form("password_reset_form"):
            password = st.text_input("New password (at least 12 characters)", type="password")
            submitted = st.form_submit_button("Reset password", type="primary")
        if submitted:
            if reset_password(reset_token, password):
                st.query_params.clear()
                st.success("Password updated. Sign in with your new password.")
            else:
                st.error("This reset link is invalid, expired, or has already been used.")
        return True

    return False


def _render_account_recovery() -> None:
    if sso_required():
        return
    with st.expander("Forgot your password?"):
        with st.form("password_reset_request_form"):
            email = st.text_input("Account email")
            submitted = st.form_submit_button("Send reset link")
        if submitted:
            token = create_password_reset_token(email)
            if token:
                reset_url = application_url(f"?reset={token}")
                send_email(
                    email.strip().casefold(), "Reset your Brand Rule Lab password",
                    f"Use this one-time link within one hour to reset your password: {reset_url}",
                )
            st.success("If an active verified account matches that email, a reset link will be sent.")
    with st.expander("Resend email verification"):
        with st.form("email_verification_request_form"):
            email = st.text_input("Account email", key="verification_email")
            submitted = st.form_submit_button("Resend verification link")
        if submitted:
            token = create_email_verification_token_for_email(email)
            verification_url = application_url(f"?verify={token}") if token else None
            delivered = bool(
                token and send_email(
                    email.strip().casefold(),
                    "Verify your Brand Rule Lab account",
                    f"Verify your email using this one-time link: {verification_url}",
                )
            )
            if (
                token and not delivered
                and __import__("os").getenv("ALLOW_DEV_AUTH_LINKS", "0").casefold()
                in {"1", "true", "yes"}
            ):
                st.code(verification_url)
            st.success("If that address has an unverified account, a verification link has been sent.")


def render() -> None:
    """Render a guided public landing page, sign-in view, or isolated demo entry."""
    inject_styles()
    oidc_error = st.session_state.pop("brl_oidc_error", None)
    if oidc_error:
        st.error(oidc_error)
    verify_token = st.query_params.get("verify")
    if verify_token:
        verified = verify_email(verify_token)
        st.query_params.clear()
        if verified:
            st.success("Email verified. You can now sign in.")
        else:
            st.error("This verification link is invalid, expired, or has already been used.")
    if _render_password_reset():
        return
    show_login = bool(st.session_state.get("brl_show_login"))
    show_demo = bool(st.session_state.get("brl_show_demo"))
    invite_token = st.query_params.get("invite")
    brand, nav, entry = st.columns([2.2, 3.5, 2.7], vertical_alignment="center")
    with brand:
        logo, wordmark = st.columns([1, 5], vertical_alignment="center")
        with logo:
            st.image(str(_LOGO), width=44)
        with wordmark:
            st.markdown("**Brand Rule Lab**")
    with nav:
        st.markdown(
            '<div class="brl-public-nav"><a href="#product">Product</a>'
            '<a href="#how-it-works">How it works</a>'
            '<a href="#features">Features</a></div>',
            unsafe_allow_html=True,
        )
    with entry:
        sign_in, demo = st.columns(2)
        with sign_in:
            if st.button("Sign in", key="public_auth_toggle", width="stretch"):
                st.session_state["brl_show_login"] = not show_login
                st.session_state["brl_show_demo"] = False
                st.rerun()
        with demo:
            if st.button("Open demo", key="public_demo_toggle", type="primary", width="stretch"):
                st.session_state["brl_show_demo"] = not show_demo
                st.session_state["brl_show_login"] = False
                st.rerun()

    if invite_token:
        st.divider()
        if show_login:
            invitation = get_invitation_details(invite_token)
            if invitation is not None:
                st.caption(
                    f"Sign in with the invited account ({invitation['email']}) "
                    "to join this workspace."
                )
                _show_sign_in()
                _render_account_recovery()
                if st.button("Create a new invited account", key="invitation_register_instead"):
                    st.session_state["brl_show_login"] = False
                    st.rerun()
            else:
                _render_invitation(invite_token)
        else:
            _render_invitation(invite_token)
            _render_oidc_account_setup()
    elif show_login:
        st.divider()
        intro, form = st.columns([1.1, 0.9], gap="large", vertical_alignment="center")
        with intro:
            st.caption("YOUR BRAND WORKSPACE")
            st.title("Review with confidence.")
            st.write(
                "Check content against approved guidance and evidence, resolve findings, "
                "then send it to a human reviewer."
            )
            st.markdown("**Submit → Check → Fix → Approve**")
        with form:
            with st.container(border=True):
                _show_sign_in()
                _render_account_recovery()
    elif show_demo:
        st.divider()
        demo_intro, demo_entry = st.columns([1.1, 0.9], gap="large", vertical_alignment="center")
        with demo_intro:
            st.caption("A PRELOADED SYNTHETIC WORKSPACE")
            st.title("See the review workflow in action.")
            st.write(
                "Choose a role to explore a prepared review, supporting evidence, "
                "human approval and the claim record."
            )
            st.markdown("**Creator · Reviewer · Fact Owner · Brand Owner**")
        with demo_entry:
            with st.container(border=True):
                _render_demo_access()
    else:
        hero, preview = st.columns([1.15, 0.85], gap="large", vertical_alignment="center")
        with hero:
            st.caption("AI-ASSISTED BRAND GOVERNANCE")
            st.title("Every claim checked. Every brand protected.")
            st.markdown(
                "Review marketing content against your brand guidelines, approved facts "
                "and assets — before it gets published."
            )
            actions = st.columns([1, 1.1])
            with actions[0]:
                if st.button("Enter demo", type="primary", key="public_open_demo", width="stretch"):
                    st.session_state["brl_show_demo"] = True
                    st.rerun()
            with actions[1]:
                if st.button("Sign in to your workspace", key="public_open_workspace", width="stretch"):
                    st.session_state["brl_show_login"] = True
                    st.rerun()
            st.caption("AI can help explain issues. People make the decisions.")
        with preview:
            with st.container(border=True):
                st.caption("ONE CLEAR WORKFLOW")
                st.markdown("### Submit · Check · Fix · Approve")
                st.write("Find the issue, see the evidence, and decide what to do next.")
                st.divider()
                st.markdown("**Human approval stays in control**")
                st.caption("Nothing is published automatically.")

        st.markdown('<div id="product"></div>', unsafe_allow_html=True)
        st.subheader("One review, from draft to decision")
        cards = st.columns(3, gap="medium")
        for column, title, description in zip(
            cards,
            ("What it checks", "How it works", "Why it matters"),
            (
                "Brand tone, terminology, factual claims, visual consistency and accessibility.",
                "Submit content, check it, fix findings, get human approval, and keep a record.",
                "Catch unsupported claims and inconsistent messaging before content goes live.",
            ),
        ):
            with column:
                with st.container(border=True):
                    st.markdown(f"**{title}**")
                    st.write(description)

        st.markdown('<div id="how-it-works"></div>', unsafe_allow_html=True)
        st.subheader("A guided path from content to confidence")
        steps = st.columns(5)
        for column, number, title in zip(
            steps, ("01", "02", "03", "04", "05"),
            ("Submit", "Check", "Fix", "Approve", "Track"),
        ):
            with column:
                st.markdown(f"**{number} · {title}**")
        st.markdown('<div id="features"></div>', unsafe_allow_html=True)

    if not invite_token:
        _render_oidc_account_setup()
    _render_initial_owner_registration()
