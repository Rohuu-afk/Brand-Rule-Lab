from __future__ import annotations

import json
from datetime import timedelta

import pytest

from db.connection import apply_schema, get_connection
from models.enums import BrandRole, UserRole
from services.auth_service import (
    AuthenticationError,
    create_user,
    login,
    login_oidc_identity,
    register_invited_user,
    resolve_session,
    verify_email,
)
from services.brand_service import (
    BrandAccessError,
    accept_invitation,
    accept_invitation_for_verified_email,
    can_register_initial_owner,
    change_member_role,
    create_brand,
    invite_member,
    get_pending_invitations_for_email,
    list_brands,
    list_members,
    remove_member,
    register_initial_owner,
    register_oidc_brand_owner,
    register_invited_user_with_oidc,
    transfer_brand_ownership,
)
from services.audit_service import (
    append_audit_event,
    export_audit_json,
    record_audit_event,
    verify_audit_export,
)
from services.ledger_service import get_all_drafts, get_draft_with_versions
from services.fact_service import get_all_facts, get_fact_versions
from services.approval_service import submit_draft_for_review
from services.guideline_service import (
    create_campaign_brief, create_guideline, deactivate_brief, get_all_briefs,
    get_all_guidelines, import_campaign_briefs_json, set_guideline_active,
    update_campaign_brief, update_guideline,
)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "brands.sqlite"))
    apply_schema()


def _principal(username: str, password: str, role: UserRole, email: str):
    create_user(username, password, role, email=email)
    return resolve_session(login(username, password))


def test_brand_creator_becomes_owner_and_membership_is_scoped():
    owner = _principal("owner", "secret", UserRole.CREATOR, "owner@example.com")
    brand = create_brand(owner, "Acme Beauty")

    assert list_brands(owner)[0]["role"] == BrandRole.OWNER.value
    assert list_members(owner, brand["id"])[0]["role"] == BrandRole.OWNER.value


def test_initial_owner_registration_is_single_use_and_email_gated():
    assert can_register_initial_owner()
    account = register_initial_owner(
        "first-owner", "owner@example.com", "a-very-long-owner-password", "Acme Beauty"
    )
    assert not can_register_initial_owner()
    with pytest.raises(ValueError, match="registration is closed"):
        register_initial_owner(
            "second-owner", "second@example.com", "a-very-long-owner-password", "Other Brand"
        )
    with pytest.raises(AuthenticationError):
        login("first-owner", "a-very-long-owner-password")
    assert verify_email(account["token"])
    principal = resolve_session(login("first-owner", "a-very-long-owner-password"))
    brand = list_brands(principal)[0]
    assert brand["role"] == BrandRole.OWNER.value
    assert verify_audit_export(export_audit_json(principal, brand["id"]))["valid"]


def test_first_owner_can_register_with_a_verified_oidc_identity():
    account = register_initial_owner(
        "google-owner", "google-owner@example.com", None, "Google Brand",
        oidc_subject="google-subject-owner", oidc_email_verified=True,
    )
    assert account["token"] is None
    principal = resolve_session(
        login_oidc_identity("google-subject-owner", "google-owner@example.com", True)
    )
    brand = list_brands(principal)[0]
    assert brand["role"] == BrandRole.OWNER.value
    assert verify_audit_export(export_audit_json(principal, brand["id"]))["valid"]


def test_new_verified_google_user_can_create_a_brand_after_demo_is_seeded():
    existing_owner = _principal(
        "demo-owner", "demo-password", UserRole.CREATOR, "demo-owner@example.com"
    )
    create_brand(existing_owner, "Existing Demo Brand")

    account = register_oidc_brand_owner(
        "new-google-owner",
        "new-google-owner@example.com",
        "My Brand",
        oidc_subject="new-google-subject",
        oidc_email_verified=True,
    )
    principal = resolve_session(
        login_oidc_identity(
            "new-google-subject", "new-google-owner@example.com", True
        )
    )

    assert account["token"] is None
    assert [(brand["name"], brand["role"]) for brand in list_brands(principal)] == [
        ("My Brand", BrandRole.OWNER.value)
    ]
    assert verify_audit_export(
        export_audit_json(principal, account["brand_id"])
    )["valid"]


def test_new_google_brand_registration_requires_verified_identity():
    with pytest.raises(ValueError, match="verified OIDC identity"):
        register_oidc_brand_owner(
            "unverified-owner",
            "unverified@example.com",
            "Unverified Brand",
            oidc_subject="unverified-subject",
            oidc_email_verified=False,
        )
    with get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM users WHERE username = 'unverified-owner'"
        ).fetchone()[0] == 0


def test_sso_required_blocks_password_owner_registration(monkeypatch):
    monkeypatch.setenv("BRL_REQUIRE_SSO", "1")

    with pytest.raises(PermissionError, match="requires configured SSO"):
        register_initial_owner(
            "password-owner", "owner@example.com", "a-very-long-owner-password",
            "Password Brand",
        )

    account = register_initial_owner(
        "oidc-owner", "oidc-owner@example.com", None, "SSO Brand",
        oidc_subject="oidc-owner-subject", oidc_email_verified=True,
    )
    assert account["token"] is None


def test_owner_invites_and_invitee_accepts_single_use_invitation():
    owner = _principal("owner", "secret", UserRole.CREATOR, "owner@example.com")
    reviewer = _principal("reviewer", "secret", UserRole.REVIEWER, "reviewer@example.com")
    brand = create_brand(owner, "Acme Beauty")
    invitation = invite_member(
        owner, brand["id"], "reviewer@example.com", BrandRole.REVIEWER
    )

    accepted = accept_invitation(reviewer, invitation["token"])

    assert accepted["id"] == brand["id"]
    assert list_brands(reviewer)[0]["role"] == BrandRole.REVIEWER.value
    with pytest.raises(ValueError, match="invalid or has already"):
        accept_invitation(reviewer, invitation["token"])


def test_non_owner_member_cannot_promote_themselves_to_new_workspace_owner():
    owner = _principal("owner", "secret", UserRole.CREATOR, "owner@example.com")
    reviewer = _principal("reviewer", "secret", UserRole.REVIEWER, "reviewer@example.com")
    brand = create_brand(owner, "Review Workspace")
    invitation = invite_member(
        owner, brand["id"], "reviewer@example.com", BrandRole.REVIEWER
    )
    accept_invitation(reviewer, invitation["token"])

    with pytest.raises(BrandAccessError, match="Only a current Brand Owner"):
        create_brand(reviewer, "Escalated Workspace")


def test_invited_email_can_register_with_single_use_invitation():
    owner = _principal("owner", "secret", UserRole.CREATOR, "owner@example.com")
    brand = create_brand(owner, "Acme Beauty")
    invitation = invite_member(
        owner, brand["id"], "newreviewer@example.com", BrandRole.REVIEWER
    )
    user_id = register_invited_user(
        invitation["token"], "new-reviewer", "a-long-registration-password"
    )
    principal = resolve_session(login("newreviewer@example.com", "a-long-registration-password"))
    joined = accept_invitation(principal, invitation["token"])

    assert user_id == principal.user_id
    assert joined["id"] == brand["id"]
    assert list_brands(principal)[0]["role"] == BrandRole.REVIEWER.value


def test_google_invitee_registration_requires_verified_invitation_email():
    owner = _principal("owner", "secret", UserRole.CREATOR, "owner@example.com")
    brand = create_brand(owner, "Google Invite Brand")
    invite = invite_member(owner, brand["id"], "google-invite@example.com", BrandRole.FACT_OWNER)
    listed = get_pending_invitations_for_email("google-invite@example.com")
    assert len(listed) == 1
    with pytest.raises(ValueError, match="verified OIDC identity"):
        register_invited_user_with_oidc(
            listed[0]["id"], "google-invite", "google-subject-invite",
            "google-invite@example.com", email_verified=False,
        )

    user_id = register_invited_user_with_oidc(
        listed[0]["id"], "google-invite", "google-subject-invite",
        "google-invite@example.com", email_verified=True,
    )
    principal = resolve_session(
        login_oidc_identity("google-subject-invite", "google-invite@example.com", True)
    )
    joined = accept_invitation_for_verified_email(principal, listed[0]["id"])

    assert user_id == principal.user_id
    assert joined["id"] == brand["id"]
    assert list_brands(principal)[0]["role"] == BrandRole.FACT_OWNER.value


def test_invitation_expiry_and_email_binding_are_enforced():
    owner = _principal("owner", "secret", UserRole.CREATOR, "owner@example.com")
    wrong_user = _principal("wrong", "secret", UserRole.CREATOR, "wrong@example.com")
    brand = create_brand(owner, "Acme Beauty")
    invitation = invite_member(owner, brand["id"], "invitee@example.com", BrandRole.CREATOR)

    with pytest.raises(BrandAccessError, match="different email"):
        accept_invitation(wrong_user, invitation["token"])

    with pytest.raises(ValueError, match="expiry"):
        invite_member(
            owner, brand["id"], "expired@example.com", BrandRole.CREATOR,
            expires_in=timedelta(seconds=-1),
        )


def test_owner_cannot_assign_owner_or_remove_last_owner():
    owner = _principal("owner", "secret", UserRole.CREATOR, "owner@example.com")
    brand = create_brand(owner, "Acme Beauty")

    with pytest.raises(ValueError, match="cannot be assigned"):
        invite_member(owner, brand["id"], "other@example.com", BrandRole.OWNER)
    with pytest.raises(ValueError, match="last Brand Owner"):
        remove_member(owner, brand["id"], owner.user_id)


def test_ownership_transfer_is_atomic_audited_and_retains_outgoing_owner():
    owner = _principal("owner", "secret", UserRole.CREATOR, "owner@example.com")
    reviewer = _principal("reviewer", "secret", UserRole.REVIEWER, "reviewer@example.com")
    brand = create_brand(owner, "Transfer Brand")
    invitation = invite_member(owner, brand["id"], "reviewer@example.com", BrandRole.REVIEWER)
    accept_invitation(reviewer, invitation["token"])

    transfer_brand_ownership(owner, brand["id"], reviewer.user_id)

    roles = {member["user_id"]: member["role"] for member in list_members(reviewer, brand["id"])}
    assert roles == {
        owner.user_id: BrandRole.REVIEWER.value,
        reviewer.user_id: BrandRole.OWNER.value,
    }
    package = verify_audit_export(export_audit_json(reviewer, brand["id"]))
    assert package["valid"] is True
    with pytest.raises(BrandAccessError):
        list_members(owner, brand["id"])


def test_ownership_transfer_rejects_nonmembers_and_keeps_current_owner():
    owner = _principal("owner", "secret", UserRole.CREATOR, "owner@example.com")
    outsider = _principal("outsider", "secret", UserRole.CREATOR, "outsider@example.com")
    brand = create_brand(owner, "Transfer Brand")

    with pytest.raises(ValueError, match="already be a member"):
        transfer_brand_ownership(owner, brand["id"], outsider.user_id)

    assert list_members(owner, brand["id"])[0]["role"] == BrandRole.OWNER.value


def test_only_owner_can_change_or_remove_members_and_brands_are_isolated():
    owner = _principal("owner", "secret", UserRole.CREATOR, "owner@example.com")
    reviewer = _principal("reviewer", "secret", UserRole.REVIEWER, "reviewer@example.com")
    brand_a = create_brand(owner, "Brand A")
    brand_b = create_brand(reviewer, "Brand B")
    invitation = invite_member(owner, brand_a["id"], "reviewer@example.com", BrandRole.REVIEWER)
    accept_invitation(reviewer, invitation["token"])

    with pytest.raises(BrandAccessError):
        list_members(reviewer, brand_a["id"])
    with pytest.raises(BrandAccessError):
        change_member_role(reviewer, brand_a["id"], owner.user_id, BrandRole.REVIEWER)
    with pytest.raises(BrandAccessError):
        list_members(owner, brand_b["id"])


def test_brand_audit_export_is_scoped_and_verifiable():
    owner = _principal("owner", "secret", UserRole.CREATOR, "owner@example.com")
    brand = create_brand(owner, "Scoped Brand")
    package = verify_audit_export(export_audit_json(owner, brand["id"]))

    assert package["valid"] is True
    assert package["record_count"] >= 1


def test_brand_audit_events_require_active_membership():
    owner = _principal("audit-owner", "secret", UserRole.CREATOR, "audit-owner@example.com")
    outsider = _principal("audit-outsider", "secret", UserRole.CREATOR, "audit-outsider@example.com")
    brand = create_brand(owner, "Audit Authorization Brand")

    record_audit_event(
        owner,
        action="AUTHORIZED_EVENT",
        object_type="brand",
        brand_id=brand["id"],
    )
    with pytest.raises(PermissionError, match="Active brand membership"):
        record_audit_event(
            outsider,
            action="UNAUTHORIZED_EVENT",
            object_type="brand",
            brand_id=brand["id"],
        )
    with get_connection() as conn:
        conn.execute("UPDATE brands SET is_active = 0 WHERE id = ?", (brand["id"],))
        conn.commit()
        with pytest.raises(PermissionError, match="Active brand membership"):
            append_audit_event(
                conn,
                owner,
                action="INACTIVE_BRAND_EVENT",
                object_type="brand",
                brand_id=brand["id"],
            )

    with pytest.raises(PermissionError):
        export_audit_json(owner, brand["id"])


def test_branded_records_cannot_be_read_or_mutated_across_memberships():
    owner_a = _principal("owner-a", "secret", UserRole.CREATOR, "owner-a@example.com")
    owner_b = _principal("owner-b", "secret", UserRole.CREATOR, "owner-b@example.com")
    brand_a = create_brand(owner_a, "Brand A")
    brand_b = create_brand(owner_b, "Brand B")
    with get_connection() as conn:
        product_a = conn.execute(
            "INSERT INTO products (name, brand, brand_id) VALUES (?, ?, ?)",
            ("Product A", "Brand A", brand_a["id"]),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO facts (product_id, name, category, markets)
            VALUES (?, ?, ?, ?)
            """,
            (product_a, "Private fact", "claim", "[]"),
        )
        draft_a = conn.execute(
            """
            INSERT INTO drafts (product_id, brand_id, title, submitted_by, submitted_by_user_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (product_a, brand_a["id"], "Private A", owner_a.username, owner_a.user_id),
        ).lastrowid
        version_a = conn.execute(
            "INSERT INTO draft_versions (draft_id, version_number, raw_text) VALUES (?, 1, ?)",
            (draft_a, "Brand A content"),
        ).lastrowid
        conn.commit()

    assert get_draft_with_versions(draft_a, owner_a, brand_a["id"])["id"] == draft_a
    assert get_all_drafts(owner_b, brand_b["id"]) == []
    with pytest.raises(PermissionError):
        get_draft_with_versions(draft_a, owner_b, brand_b["id"])
    with pytest.raises(PermissionError):
        submit_draft_for_review(draft_a, version_a, principal=owner_b, brand_id=brand_b["id"])
    assert get_all_facts(principal=owner_b, brand_id=brand_b["id"]) == []


def test_authenticated_brand_reads_fail_closed_for_unscoped_history():
    principal = _principal("historical-reader", "secret", UserRole.CREATOR, "historical@example.com")
    brand = create_brand(principal, "Scoped Brand")
    with get_connection() as conn:
        product_id = conn.execute(
            "INSERT INTO products (name, brand) VALUES (?, ?)",
            ("Legacy Product", "Legacy Brand"),
        ).lastrowid
        fact_id = conn.execute(
            "INSERT INTO facts (product_id, name, category, markets) VALUES (?, ?, ?, ?)",
            (product_id, "Legacy Fact", "claim", "[]"),
        ).lastrowid
        conn.commit()

    assert get_all_facts(principal=principal, brand_id=brand["id"]) == []
    with pytest.raises(PermissionError):
        get_fact_versions(fact_id, principal=principal, brand_id=brand["id"])


def test_guidelines_and_campaigns_are_bound_to_the_selected_brand():
    owner_a = _principal("guide-owner-a", "secret", UserRole.CREATOR, "guide-a@example.com")
    owner_b = _principal("guide-owner-b", "secret", UserRole.CREATOR, "guide-b@example.com")
    brand_a = create_brand(owner_a, "Guideline Brand A")
    brand_b = create_brand(owner_b, "Guideline Brand B")
    with get_connection() as conn:
        product_a = conn.execute(
            "INSERT INTO products (name, brand, brand_id) VALUES (?, ?, ?)",
            ("Product A", "Brand A", brand_a["id"]),
        ).lastrowid
        conn.commit()

    guideline_id = create_guideline(
        "Private guideline", "Brand A wording", "Source A", product_id=product_a,
        principal=owner_a, brand_id=brand_a["id"],
    )
    assert len(get_all_guidelines(principal=owner_a, brand_id=brand_a["id"])) == 1
    assert get_all_guidelines(principal=owner_b, brand_id=brand_b["id"]) == []
    with pytest.raises(BrandAccessError):
        update_guideline(
            guideline_id, "Changed", "Changed", "Source A", "1.0", None, "manual", None,
            principal=owner_b, brand_id=brand_b["id"],
        )
    with pytest.raises(BrandAccessError):
        set_guideline_active(
            guideline_id, False, principal=owner_b, brand_id=brand_b["id"]
        )
    with pytest.raises(BrandAccessError):
        create_campaign_brief(
            "Cross-brand brief", product_a, "", "", "", ["Required message"], [], [],
            principal=owner_b, brand_id=brand_b["id"],
        )
    assert get_all_briefs(principal=owner_b, brand_id=brand_b["id"]) == []


def test_guideline_and_campaign_changes_are_audited_and_import_is_brand_scoped():
    owner_a = _principal("guide-audit-a", "secret", UserRole.CREATOR, "audit-a@example.com")
    owner_b = _principal("guide-audit-b", "secret", UserRole.CREATOR, "audit-b@example.com")
    brand_a = create_brand(owner_a, "Audit Brand A")
    brand_b = create_brand(owner_b, "Audit Brand B")
    with get_connection() as conn:
        product_a = conn.execute(
            "INSERT INTO products (name, brand, brand_id) VALUES (?, ?, ?)",
            ("Audit Product A", "Audit A", brand_a["id"]),
        ).lastrowid
        product_b = conn.execute(
            "INSERT INTO products (name, brand, brand_id) VALUES (?, ?, ?)",
            ("Audit Product B", "Audit B", brand_b["id"]),
        ).lastrowid
        conn.commit()

    guideline_id = create_guideline(
        "Audited guideline", "Approved copy", "Source", product_id=product_a,
        principal=owner_a, brand_id=brand_a["id"],
    )
    update_guideline(
        guideline_id, "Updated guideline", "Updated copy", "Source", "1.1",
        product_a, "manual", None, principal=owner_a, brand_id=brand_a["id"],
    )
    set_guideline_active(
        guideline_id, False, principal=owner_a, brand_id=brand_a["id"]
    )
    brief_id = create_campaign_brief(
        "Audited campaign", product_a, "Objective", "Warm", "Adults",
        ["Required message"], ["US"], ["Web"],
        principal=owner_a, brand_id=brand_a["id"],
    )
    update_campaign_brief(
        brief_id, "Updated campaign", product_a, "Objective", "Warm", "Adults",
        ["Required message"], ["US"], ["Web"],
        principal=owner_a, brand_id=brand_a["id"],
    )
    deactivate_brief(brief_id, principal=owner_a, brand_id=brand_a["id"])

    cross_brand_import = import_campaign_briefs_json(
        json.dumps([{
            "name": "Cross-brand", "product_id": product_b,
            "objectives": "", "tone_guidance": "", "target_audience": "",
            "key_messages": ["Required"], "markets": [], "channels": [],
        }]),
        principal=owner_a, brand_id=brand_a["id"],
    )
    assert cross_brand_import["imported"] == 0
    assert cross_brand_import["errors"] == ["Item 1: product_id is not a known product."]

    valid_import = import_campaign_briefs_json(
        json.dumps([{
            "name": "Imported campaign", "product_id": product_a,
            "objectives": "", "tone_guidance": "", "target_audience": "",
            "key_messages": ["Required"], "markets": [], "channels": [],
        }]),
        principal=owner_a, brand_id=brand_a["id"],
    )
    assert valid_import == {"imported": 1, "errors": []}
    assert len(get_all_briefs(principal=owner_a, brand_id=brand_a["id"])) == 1
    assert get_all_briefs(principal=owner_b, brand_id=brand_b["id"]) == []

    with get_connection() as conn:
        events = conn.execute(
            "SELECT action, brand_id FROM audit_events WHERE brand_id = ? ORDER BY id",
            (brand_a["id"],),
        ).fetchall()
    assert [row["action"] for row in events] == [
        "BRAND_CREATED", "GUIDELINE_CREATED", "GUIDELINE_UPDATED", "GUIDELINE_STATUS_CHANGED",
        "CAMPAIGN_BRIEF_CREATED", "CAMPAIGN_BRIEF_UPDATED",
        "CAMPAIGN_BRIEF_DEACTIVATED", "CAMPAIGN_BRIEF_IMPORTED",
    ]
    assert all(row["brand_id"] == brand_a["id"] for row in events)
