from __future__ import annotations

import pytest
from datetime import date, timedelta

from db.connection import apply_schema, get_connection
from models.enums import UserRole
from services.brand_service import create_brand
from services.approval_service import (
    approve_submitted_draft,
    submit_draft_for_review,
)
from services.review_service import create_draft_revision, record_finding_override
from services.auth_service import (
    AuthenticatedPrincipal,
    create_user,
    login,
    resolve_session,
)
from services.ledger_service import get_insights
from services.fact_service import (
    create_fact,
    create_fact_version,
    flag_claims_for_new_version,
    get_all_facts,
    get_eligible_fact_versions,
)
from services.guideline_service import get_all_products
from services.approval_service import get_approved_claims
from services.knowledge_onboarding import (
    add_manual_entry, decide_knowledge_entry, get_source_documents,
)
from services.asset_service import (
    get_approved_asset,
    get_approved_packaging_reference,
)
from services.guideline_service import create_product
from services.rule_readiness import get_rule_proposals
from ui.pages.insights import _query_db_insights


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "workflow.sqlite"))
    apply_schema()


def _draft_for_creator(user_id: int, brand_id: int) -> tuple[int, int]:
    with get_connection() as conn:
        draft_id = conn.execute(
            """
            INSERT INTO drafts
                (title, status, submitted_by, submitted_by_user_id, brand_id)
            VALUES ('Authenticated workflow', 'CHECKED', 'creator', ?, ?)
            """,
            (user_id, brand_id),
        ).lastrowid
        version_id = conn.execute(
            """
            INSERT INTO draft_versions (draft_id, version_number, raw_text)
            VALUES (?, 1, 'Eligible draft')
            """,
            (draft_id,),
        ).lastrowid
        conn.commit()
    return int(draft_id), int(version_id)


def _principal(username: str, password: str, role: UserRole) -> AuthenticatedPrincipal:
    create_user(username, password, role)
    return resolve_session(login(username, password))


def test_unauthenticated_submission_is_rejected():
    with get_connection() as conn:
        draft_id = conn.execute(
            "INSERT INTO drafts (title, status) VALUES ('Unauthenticated', 'CHECKED')"
        ).lastrowid
        version_id = conn.execute(
            "INSERT INTO draft_versions (draft_id, raw_text) VALUES (?, 'Draft')",
            (draft_id,),
        ).lastrowid

    with pytest.raises(PermissionError):
        submit_draft_for_review(draft_id, version_id, principal=None)


def test_role_string_compatibility_is_disabled_outside_opted_in_tests(monkeypatch):
    monkeypatch.delenv("BRL_ENABLE_LEGACY_TEST_ACTORS", raising=False)
    monkeypatch.setenv("BRL_ENABLE_SYNTHETIC_DEMO_ACTORS", "1")
    with pytest.raises(PermissionError, match="valid authenticated session"):
        submit_draft_for_review(
            1, 1, actor_role=UserRole.CREATOR, principal=None,
        )

    with pytest.raises(PermissionError, match="valid authenticated session"):
        flag_claims_for_new_version(old_fact_version_id=1)
    with pytest.raises(PermissionError, match="valid authenticated session"):
        _query_db_insights()


def test_creator_cannot_approve_and_reviewer_cannot_submit():
    creator = _principal("creator", "creator-password", UserRole.CREATOR)
    reviewer = _principal("reviewer", "reviewer-password", UserRole.REVIEWER)
    brand = create_brand(creator, "Workflow Brand")
    with get_connection() as conn:
        conn.execute(
            "UPDATE brand_memberships SET role = 'Creator' WHERE brand_id = ? AND user_id = ?",
            (brand["id"], creator.user_id),
        )
        conn.commit()
    draft_id, version_id = _draft_for_creator(creator.user_id, brand["id"])

    with pytest.raises(PermissionError):
        approve_submitted_draft(
            draft_id, version_id, "Creator attempt", principal=creator,
            brand_id=brand["id"],
        )
    with pytest.raises(PermissionError):
        submit_draft_for_review(
            draft_id, version_id, principal=reviewer, brand_id=brand["id"],
        )


def test_forged_principal_role_is_rejected():
    creator = _principal("creator", "creator-password", UserRole.CREATOR)
    brand = create_brand(creator, "Forged Role Brand")
    draft_id, version_id = _draft_for_creator(creator.user_id, brand["id"])
    forged = AuthenticatedPrincipal(
        creator.user_id,
        creator.username,
        UserRole.REVIEWER.value,
        creator._session_token,
    )

    with pytest.raises(PermissionError):
        submit_draft_for_review(draft_id, version_id, principal=forged, brand_id=brand["id"])


def test_creator_cannot_approve_own_submission_but_different_reviewer_can():
    creator = _principal("creator", "creator-password", UserRole.CREATOR)
    reviewer = _principal("reviewer", "reviewer-password", UserRole.REVIEWER)
    brand = create_brand(creator, "Approval Brand")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO brand_memberships (brand_id, user_id, role) VALUES (?, ?, 'Reviewer')",
            (brand["id"], reviewer.user_id),
        )
        conn.commit()
    draft_id, version_id = _draft_for_creator(creator.user_id, brand["id"])

    submit_draft_for_review(draft_id, version_id, principal=creator, brand_id=brand["id"])

    with pytest.raises(PermissionError):
        approve_submitted_draft(
            draft_id, version_id, "Self approval", principal=creator,
            brand_id=brand["id"],
        )
    assert approve_submitted_draft(
        draft_id, version_id, "Independent approval", principal=reviewer,
        brand_id=brand["id"],
    ) == 0
    with pytest.raises(ValueError, match="Only a draft submitted as READY_FOR_REVIEW"):
        approve_submitted_draft(
            draft_id, version_id, "Duplicate approval", principal=reviewer,
            brand_id=brand["id"],
        )

    with get_connection() as conn:
        draft = conn.execute(
            "SELECT status, submitted_by_user_id FROM drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
        review = conn.execute(
            "SELECT status, reviewer FROM reviews WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
    assert draft["status"] == "APPROVED"
    assert draft["submitted_by_user_id"] == creator.user_id
    assert review["status"] == "APPROVED"
    assert review["reviewer"] == reviewer.username


def test_finding_override_requires_reviewer_membership_and_records_tenant_audit():
    creator = _principal("creator", "creator-password", UserRole.CREATOR)
    reviewer = _principal("reviewer", "reviewer-password", UserRole.REVIEWER)
    brand = create_brand(creator, "Override Brand")
    with get_connection() as conn:
        conn.execute(
            "UPDATE brand_memberships SET role = 'Creator' WHERE brand_id = ? AND user_id = ?",
            (brand["id"], creator.user_id),
        )
        conn.execute(
            "INSERT INTO brand_memberships (brand_id, user_id, role) VALUES (?, ?, 'Reviewer')",
            (brand["id"], reviewer.user_id),
        )
        draft_id = conn.execute(
            "INSERT INTO drafts (title, status, submitted_by_user_id, brand_id) "
            "VALUES ('Override workflow', 'CHECKED', ?, ?)",
            (creator.user_id, brand["id"]),
        ).lastrowid
        version_id = conn.execute(
            "INSERT INTO draft_versions (draft_id, raw_text) VALUES (?, 'Copy')",
            (draft_id,),
        ).lastrowid
        finding_id = conn.execute(
            """INSERT INTO findings
                (draft_version_id, finding_type, result, status, severity, creator_note)
                VALUES (?, 'rule_check', 'FAIL', 'DISMISSED', 'HIGH', 'Creator reason')""",
            (version_id,),
        ).lastrowid
        conn.commit()

    with pytest.raises(PermissionError, match="brand role"):
        record_finding_override(
            finding_id, "ACCEPT", "Not a reviewer", principal=creator,
            brand_id=brand["id"],
        )

    record_finding_override(
        finding_id, "ACCEPT", "Reviewer checked the source", principal=reviewer,
        brand_id=brand["id"],
    )
    with get_connection() as conn:
        override = conn.execute(
            "SELECT reviewer, decision FROM finding_overrides WHERE finding_id = ?",
            (finding_id,),
        ).fetchone()
        audit = conn.execute(
            """SELECT action, actor_user_id FROM audit_events
               WHERE brand_id = ? AND object_id = ?
                 AND action = 'FINDING_OVERRIDE_RECORDED'""",
            (brand["id"], str(finding_id)),
        ).fetchone()
    assert dict(override) == {"reviewer": "reviewer", "decision": "ACCEPT"}
    assert audit["actor_user_id"] == reviewer.user_id


def test_insights_are_tenant_scoped_and_unscoped_reads_fail_closed():
    owner = _principal("insights-owner", "insights-owner-password", UserRole.CREATOR)
    own_brand = create_brand(owner, "Insights Private Brand")
    other_brand = create_brand(owner, "Insights Other Brand")
    with get_connection() as conn:
        for brand_id, title, claim_id, drift in (
            (own_brand["id"], "Own draft", "own-claim-01", "CURRENT"),
            (other_brand["id"], "Other draft", "other-claim-01", "EXPIRED"),
        ):
            draft_id = conn.execute(
                "INSERT INTO drafts (title, status, brand_id) VALUES (?, 'APPROVED', ?)",
                (title, brand_id),
            ).lastrowid
            conn.execute(
                "INSERT INTO approved_claims "
                "(stable_claim_id, draft_id, approved_text, approved_text_hash, drift_status) "
                "VALUES (?, ?, 'Approved copy', 'test-hash', ?)",
                (claim_id, draft_id, drift),
            )
        conn.commit()

    insights = get_insights(owner, own_brand["id"])
    assert insights["total_drafts"] == 1
    assert insights["total_approved_claims"] == 1
    assert insights["approved_claims_by_drift"] == {"CURRENT": 1}
    with pytest.raises(PermissionError, match="valid authenticated session"):
        get_insights()
    for read in (
        get_all_products, get_all_facts, get_approved_claims, get_source_documents,
    ):
        with pytest.raises(PermissionError, match="valid authenticated session"):
            read()
    with pytest.raises(PermissionError, match="valid authenticated session"):
        get_rule_proposals()


def test_approved_asset_reads_prove_product_tenant_ownership():
    owner = _principal("asset-scope-owner", "asset-scope-password", UserRole.CREATOR)
    first = create_brand(owner, "Asset Scope One")
    second = create_brand(owner, "Asset Scope Two")
    product_id = create_product(
        "Scoped Product", principal=owner, brand_id=first["id"],
    )

    with pytest.raises(PermissionError):
        get_approved_asset(
            1, product_id, principal=owner, brand_id=second["id"],
        )
    with pytest.raises(PermissionError):
        get_approved_packaging_reference(
            1, product_id, principal=owner, brand_id=second["id"],
        )


def test_fact_lookup_expires_and_audits_only_the_selected_brand():
    owner = _principal("expiry-owner", "expiry-owner-password", UserRole.CREATOR)
    first_brand = create_brand(owner, "Expiry Tenant One")
    second_brand = create_brand(owner, "Expiry Tenant Two")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    fact_versions = {}
    product_ids = {}
    for brand in (first_brand, second_brand):
        product_id = create_product(
            f"Product {brand['id']}", principal=owner, brand_id=brand["id"],
        )
        product_ids[brand["id"]] = product_id
        fact_id = create_fact(
            product_id, f"Fact {brand['id']}", "other", ["IN"],
            principal=owner, brand_id=brand["id"],
        )
        version_id = create_fact_version(
            fact_id, f"Expired statement {brand['id']}", "Fixture evidence",
            valid_until=yesterday, principal=owner, brand_id=brand["id"],
        )
        fact_versions[brand["id"]] = version_id

    with get_connection() as conn:
        conn.executemany(
            "UPDATE fact_versions SET approval_status = 'APPROVED' WHERE id = ?",
            [(version_id,) for version_id in fact_versions.values()],
        )
        conn.commit()

    get_eligible_fact_versions(
        product_id=product_ids[first_brand["id"]], market="IN",
        principal=owner, brand_id=first_brand["id"],
    )

    with get_connection() as conn:
        statuses = {
            row["id"]: row["approval_status"]
            for row in conn.execute(
                "SELECT id, approval_status FROM fact_versions WHERE id IN (?, ?)",
                tuple(fact_versions.values()),
            )
        }
        expiry_events = conn.execute(
            "SELECT action, object_id, actor_username FROM audit_events "
            "WHERE brand_id = ? AND action = 'FACT_VERSION_AUTO_EXPIRED'",
            (first_brand["id"],),
        ).fetchall()

    assert statuses[fact_versions[first_brand["id"]]] == "EXPIRED"
    assert statuses[fact_versions[second_brand["id"]]] == "APPROVED"
    assert [dict(row) for row in expiry_events] == [{
        "action": "FACT_VERSION_AUTO_EXPIRED",
        "object_id": str(fact_versions[first_brand["id"]]),
        "actor_username": "system:fact-expiry",
    }]


def test_knowledge_proposal_and_decision_are_written_to_brand_audit_chain():
    owner = _principal("knowledge-audit-owner", "secret", UserRole.CREATOR)
    brand = create_brand(owner, "Knowledge Audit Brand")
    entry_id = add_manual_entry(
        "TERMINOLOGY", "Use the approved product name.",
        principal=owner, brand_id=brand["id"],
    )

    result = decide_knowledge_entry(
        entry_id, "REJECT", "ignored actor", principal=owner, brand_id=brand["id"],
    )
    assert result == {"decision": "REJECT", "linked_record": None}
    with get_connection() as conn:
        events = conn.execute(
            "SELECT action, object_id, brand_id FROM audit_events "
            "WHERE brand_id = ? ORDER BY id",
            (brand["id"],),
        ).fetchall()
    assert [row["action"] for row in events] == [
        "BRAND_CREATED", "KNOWLEDGE_PROPOSAL_CREATED", "KNOWLEDGE_REJECT",
    ]
    assert events[-1]["object_id"] == str(entry_id)
    assert all(row["brand_id"] == brand["id"] for row in events)


def test_creator_remediation_reruns_checks_with_the_authenticated_brand_scope():
    creator = _principal("remediation-owner", "remediation-secret", UserRole.CREATOR)
    brand = create_brand(creator, "Remediation Workspace")
    with get_connection() as conn:
        draft_id = conn.execute(
            "INSERT INTO drafts (title, status, submitted_by, submitted_by_user_id, brand_id) "
            "VALUES (?, 'CHECKED', ?, ?, ?)",
            ("Authenticated remediation", creator.username, creator.user_id, brand["id"]),
        ).lastrowid
        conn.execute(
            "INSERT INTO draft_versions (draft_id, version_number, raw_text, source_type) "
            "VALUES (?, 1, ?, 'paste')",
            (draft_id, "Original draft copy."),
        )
        conn.commit()

    new_version_id = create_draft_revision(
        draft_id, "Corrected draft copy.", source_type="edit",
        creator_note="Corrected the wording.", auto_rereview=True,
        ai_consent=False, principal=creator, brand_id=brand["id"],
    )

    with get_connection() as conn:
        draft = conn.execute(
            "SELECT status FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        version = conn.execute(
            "SELECT visual_metadata FROM draft_versions WHERE id = ?", (new_version_id,)
        ).fetchone()
        findings = conn.execute(
            "SELECT COUNT(*) FROM findings WHERE draft_version_id = ?",
            (new_version_id,),
        ).fetchone()[0]
        audit = conn.execute(
            "SELECT action, brand_id FROM audit_events WHERE object_type = 'draft' "
            "AND object_id = ? ORDER BY id",
            (str(draft_id),),
        ).fetchall()

    assert draft["status"] == "CHECKED"
    assert version["visual_metadata"] == '{"external_ai_consent": false}'
    assert findings > 0
    assert [row["action"] for row in audit] == ["DRAFT_REMEDIATION_PROPOSED"]
    assert all(row["brand_id"] == brand["id"] for row in audit)
