from __future__ import annotations

import pytest

from db.connection import apply_schema, get_connection
from models.enums import UserRole
from services.audit_service import export_audit_json, verify_audit_export
from services.auth_service import create_user, login, resolve_session
from services.brand_service import create_brand
from services.review_service import create_draft_revision
from services.fact_service import approve_fact_version, create_fact_version


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "remediation.sqlite"))
    apply_schema()


def _principal(username: str, password: str, role: UserRole):
    create_user(username, password, role)
    return resolve_session(login(username, password))


def _draft(creator_id: int, brand_id: int) -> tuple[int, int, int]:
    with get_connection() as conn:
        draft_id = conn.execute(
            """
            INSERT INTO drafts (title, status, submitted_by, submitted_by_user_id, brand_id)
            VALUES ('Remediation test', 'CHECKED', 'creator', ?, ?)
            """,
            (creator_id, brand_id),
        ).lastrowid
        version_id = conn.execute(
            "INSERT INTO draft_versions (draft_id, raw_text) VALUES (?, ?)",
            (draft_id, "Original unsupported wording"),
        ).lastrowid
        finding_id = conn.execute(
            """
            INSERT INTO findings
                (draft_version_id, finding_type, result, status, severity,
                 quoted_text, explanation, suggested_correction)
            VALUES (?, 'rule_check', 'FAIL', 'OPEN', 'HIGH', ?, ?, ?)
            """,
            (
                version_id,
                "Original unsupported wording",
                "The wording is not supported.",
                "Use supported wording.",
            ),
        ).lastrowid
        conn.commit()
    return int(draft_id), int(version_id), int(finding_id)


def test_creator_remediation_preserves_original_and_records_audit():
    creator = _principal("creator", "creator-password", UserRole.CREATOR)
    reviewer = _principal("reviewer", "reviewer-password", UserRole.REVIEWER)
    brand = create_brand(creator, "Remediation Brand")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO brand_memberships (brand_id, user_id, role) VALUES (?, ?, 'Reviewer')",
            (brand["id"], reviewer.user_id),
        )
        conn.commit()
    draft_id, original_version_id, finding_id = _draft(creator.user_id, brand["id"])

    new_version_id = create_draft_revision(
        draft_id,
        "Revised supported wording",
        finding_id=finding_id,
        creator_note="Removed the unsupported promise.",
        suggested_correction="Revised supported wording",
        principal=creator,
        brand_id=brand["id"],
    )

    with get_connection() as conn:
        versions = conn.execute(
            "SELECT id, raw_text FROM draft_versions WHERE draft_id = ? ORDER BY version_number",
            (draft_id,),
        ).fetchall()
        finding = conn.execute(
            "SELECT status, creator_note, suggested_correction FROM findings WHERE id = ?",
            (finding_id,),
        ).fetchone()
        audit = conn.execute(
            "SELECT action, object_id FROM audit_events WHERE action = 'DRAFT_REMEDIATION_PROPOSED'"
        ).fetchall()

    assert [row["id"] for row in versions] == [original_version_id, new_version_id]
    assert versions[0]["raw_text"] == "Original unsupported wording"
    assert versions[1]["raw_text"] == "Revised supported wording"
    assert dict(finding) == {
        "status": "EDITED",
        "creator_note": "Removed the unsupported promise.",
        "suggested_correction": "Revised supported wording",
    }
    assert [dict(row) for row in audit] == [{
        "action": "DRAFT_REMEDIATION_PROPOSED",
        "object_id": str(draft_id),
    }]
    assert verify_audit_export(export_audit_json(reviewer, brand_id=brand["id"]))["valid"] is True


def test_remediation_requires_reason_and_creator_identity():
    creator = _principal("creator", "creator-password", UserRole.CREATOR)
    reviewer = _principal("reviewer", "reviewer-password", UserRole.REVIEWER)
    brand = create_brand(creator, "Remediation Authorization Brand")
    with get_connection() as conn:
        conn.execute(
            "UPDATE brand_memberships SET role = 'Creator' WHERE brand_id = ? AND user_id = ?",
            (brand["id"], creator.user_id),
        )
        conn.commit()
    draft_id, _, finding_id = _draft(creator.user_id, brand["id"])

    with pytest.raises(PermissionError):
        create_draft_revision(
            draft_id, "Forged revision", finding_id=finding_id,
            creator_note="forge", principal=reviewer, brand_id=brand["id"],
        )
    with pytest.raises(ValueError, match="reason"):
        create_draft_revision(
            draft_id, "No reason", finding_id=finding_id, principal=creator,
            brand_id=brand["id"],
        )


def test_fact_owner_principal_controls_evidence_approval_and_audits_it():
    owner = _principal("owner", "owner-password", UserRole.FACT_OWNER)
    reviewer = _principal("reviewer", "reviewer-password", UserRole.REVIEWER)
    brand = create_brand(owner, "Fact Approval Brand")
    with get_connection() as conn:
        product_id = conn.execute(
            "INSERT INTO products (name, brand, brand_id) "
            "VALUES ('Test product', 'Test brand', ?)",
            (brand["id"],),
        ).lastrowid
        fact_id = conn.execute(
            """
            INSERT INTO facts (product_id, name, category, markets, brand_id)
            VALUES (?, 'Test fact', 'claim', '["IN"]', ?)
            """,
            (product_id, brand["id"]),
        ).lastrowid
        conn.commit()

    version_id = create_fact_version(
        fact_id, "Approved statement", "Synthetic evidence", principal=owner,
        brand_id=brand["id"],
    )
    with pytest.raises(PermissionError):
        approve_fact_version(version_id, principal=reviewer, brand_id=brand["id"])
    approve_fact_version(version_id, principal=owner, brand_id=brand["id"])
    with get_connection() as conn:
        actions = [
            row["action"]
            for row in conn.execute(
                "SELECT action FROM audit_events WHERE object_type = 'fact_version' ORDER BY id"
            )
        ]
    assert actions == ["FACT_VERSION_CREATED", "FACT_VERSION_APPROVED"]
