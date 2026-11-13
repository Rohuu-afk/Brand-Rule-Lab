from __future__ import annotations

import pytest

from db.connection import apply_schema, get_connection
from db.provision_demo_accounts import provision_demo_accounts
from db.seed import seed
from models.enums import UserRole
from services.auth_service import authenticate_user, create_user, get_user


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "accounts.sqlite"))
    monkeypatch.delenv("DEMO_CREATOR_PASSWORD", raising=False)
    monkeypatch.delenv("DEMO_REVIEWER_PASSWORD", raising=False)
    monkeypatch.delenv("DEMO_FACT_OWNER_PASSWORD", raising=False)
    monkeypatch.delenv("DEMO_OWNER_PASSWORD", raising=False)
    apply_schema()


def test_provisioning_creates_expected_accounts_without_logging_passwords(capsys):
    results = provision_demo_accounts(
        passwords={
            "demo_creator": "creator-secret",
            "demo_reviewer": "reviewer-secret",
            "demo_fact_owner": "fact-owner-secret",
            "demo_owner": "owner-secret",
        }
    )

    assert results == {
        "demo_creator": "created",
        "demo_reviewer": "created",
        "demo_fact_owner": "created",
        "demo_owner": "created",
    }
    assert get_user("demo_creator")["role"] == UserRole.CREATOR.value
    assert get_user("demo_reviewer")["role"] == UserRole.REVIEWER.value
    assert authenticate_user("demo_creator", "creator-secret")
    assert authenticate_user("demo_reviewer", "reviewer-secret")
    assert get_user("demo_fact_owner")["role"] == UserRole.FACT_OWNER.value
    assert authenticate_user("demo_fact_owner", "fact-owner-secret")
    assert get_user("demo_owner")["role"] == UserRole.CREATOR.value
    assert authenticate_user("demo_owner", "owner-secret")
    with get_connection() as conn:
        demo_brand_id = conn.execute(
            "SELECT id FROM brands WHERE slug = 'demo-brand'"
        ).fetchone()["id"]
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM products WHERE brand_id = ?",
            (demo_brand_id,),
        ).fetchone()["n"] >= 1
        memberships = {
            row["username"]: row["role"]
            for row in conn.execute(
                """
                SELECT u.username, bm.role FROM brand_memberships bm
                JOIN users u ON u.id = bm.user_id WHERE bm.brand_id = ?
                """,
                (demo_brand_id,),
            )
        }
        assert memberships == {
            "demo_creator": "Creator",
            "demo_reviewer": "Reviewer",
            "demo_fact_owner": "Fact Owner",
            "demo_owner": "Brand Owner",
        }
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM facts WHERE brand_id = ?",
            (demo_brand_id,),
        ).fetchone()["n"] >= 1
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM campaign_briefs WHERE brand_id = ?",
            (demo_brand_id,),
        ).fetchone()["n"] >= 1
        assert conn.execute(
            """
            SELECT COUNT(*) AS n FROM brand_rules
            WHERE brand_id = ? AND is_active = 1 AND owner_approved = 1
              AND review_status = 'APPROVED' AND readiness_class = 'CODE_CHECKABLE'
            """,
            (demo_brand_id,),
        ).fetchone()["n"] >= 1
    assert "creator-secret" not in capsys.readouterr().out
    assert "reviewer-secret" not in capsys.readouterr().out


def test_provisioning_is_idempotent_and_does_not_overwrite_existing_accounts():
    create_user("demo_creator", "original-secret", UserRole.CREATOR)
    create_user("demo_reviewer", "reviewer-secret", UserRole.REVIEWER)
    create_user("demo_fact_owner", "fact-owner-secret", UserRole.FACT_OWNER)
    create_user("demo_owner", "owner-secret", UserRole.CREATOR)

    results = provision_demo_accounts(
        passwords={
            "demo_creator": "replacement-secret",
            "demo_reviewer": "replacement-secret",
            "demo_fact_owner": "replacement-secret",
            "demo_owner": "replacement-secret",
        }
    )

    assert results == {
        "demo_creator": "existing",
        "demo_reviewer": "existing",
        "demo_fact_owner": "existing",
        "demo_owner": "existing",
    }
    assert authenticate_user("demo_creator", "original-secret")
    assert authenticate_user("demo_reviewer", "reviewer-secret")
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 4


def test_missing_password_is_securely_prompted():
    prompts = iter(
        [
            "creator-secret", "creator-secret",
            "reviewer-secret", "reviewer-secret",
            "fact-owner-secret", "fact-owner-secret",
            "owner-secret", "owner-secret",
        ]
    )

    results = provision_demo_accounts(
        prompt=lambda _: next(prompts),
    )

    assert results == {
        "demo_creator": "created",
        "demo_reviewer": "created",
        "demo_fact_owner": "created",
        "demo_owner": "created",
    }
    assert authenticate_user("demo_creator", "creator-secret")
    assert authenticate_user("demo_reviewer", "reviewer-secret")
    assert authenticate_user("demo_fact_owner", "fact-owner-secret")
    assert authenticate_user("demo_owner", "owner-secret")


def test_seed_repairs_active_approved_legacy_rules_for_execution():
    provision_demo_accounts(
        passwords={
            "demo_creator": "creator-secret",
            "demo_reviewer": "reviewer-secret",
            "demo_fact_owner": "fact-owner-secret",
            "demo_owner": "owner-secret",
        }
    )
    with get_connection() as conn:
        conn.execute(
            "UPDATE brand_rules SET review_status = 'PROPOSED' "
            "WHERE brand_id = (SELECT id FROM brands WHERE slug = 'demo-brand') "
            "AND is_active = 1 AND owner_approved = 1"
        )
        conn.commit()

    seed(force=False, verbose=False)

    with get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM brand_rules WHERE brand_id = "
            "(SELECT id FROM brands WHERE slug = 'demo-brand') "
            "AND is_active = 1 AND owner_approved = 1 AND review_status = 'PROPOSED'"
        ).fetchone()[0] == 0
