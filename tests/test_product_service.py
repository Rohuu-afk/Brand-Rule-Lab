from __future__ import annotations

import json

import pytest

from db.connection import apply_schema, get_connection
from models.enums import UserRole
from services.auth_service import create_user, login, resolve_session
from services.brand_service import create_brand
from services.guideline_service import create_product, get_all_products
from services.fact_service import create_fact, get_all_facts, import_facts_csv


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "products.sqlite"))
    apply_schema()


def _principal(username: str, role: UserRole):
    create_user(username, "local-test-password", role)
    return resolve_session(login(username, "local-test-password"))


def test_owner_creates_product_scoped_to_brand_and_audited():
    owner = _principal("product-owner", UserRole.CREATOR)
    brand = create_brand(owner, "Product Brand")

    product_id = create_product(
        "Clear Skin Wash", sku_aliases=["CSW-01", " Clear Skin "],
        markets=["in", "US"], description="Synthetic acceptance product.",
        principal=owner, brand_id=brand["id"],
    )

    visible = get_all_products(principal=owner, brand_id=brand["id"])
    assert [item["id"] for item in visible] == [product_id]
    assert visible[0]["brand"] == brand["name"]
    assert json.loads(visible[0]["sku_aliases"]) == ["CSW-01", "Clear Skin"]
    assert json.loads(visible[0]["markets"]) == ["IN", "US"]
    with get_connection() as conn:
        event = conn.execute(
            "SELECT action, brand_id, object_id FROM audit_events "
            "WHERE action = 'PRODUCT_CREATED'"
        ).fetchone()
    assert dict(event) == {
        "action": "PRODUCT_CREATED",
        "brand_id": brand["id"],
        "object_id": str(product_id),
    }


def test_product_creation_rejects_non_owner_and_cross_tenant_access():
    owner = _principal("product-owner", UserRole.CREATOR)
    reviewer = _principal("product-reviewer", UserRole.REVIEWER)
    brand = create_brand(owner, "Product Brand")
    other_brand = create_brand(reviewer, "Other Product Brand")

    with pytest.raises(PermissionError):
        create_product(
            "Unauthorized", principal=reviewer, brand_id=brand["id"]
        )
    with pytest.raises(ValueError, match="two-letter"):
        create_product(
            "Bad Market", markets=["India"], principal=owner, brand_id=brand["id"]
        )
    assert get_all_products(principal=owner, brand_id=brand["id"]) == []
    assert get_all_products(principal=reviewer, brand_id=other_brand["id"]) == []


def test_duplicate_product_names_are_rejected_within_a_brand():
    owner = _principal("product-owner", UserRole.CREATOR)
    brand = create_brand(owner, "Product Brand")
    create_product("Clear Skin Wash", principal=owner, brand_id=brand["id"])

    with pytest.raises(ValueError, match="already exists"):
        create_product("clear skin wash", principal=owner, brand_id=brand["id"])


def test_authenticated_fact_create_and_csv_import_retain_tenant_scope():
    owner = _principal("fact-owner", UserRole.FACT_OWNER)
    brand = create_brand(owner, "Fact Import Brand")
    product_id = create_product(
        "Scoped Product", sku_aliases=["SP-01"], principal=owner,
        brand_id=brand["id"],
    )
    create_fact(
        product_id, "Direct fact", "efficacy_claim", ["IN"],
        principal=owner, brand_id=brand["id"],
    )
    csv_text = (
        "product_or_sku,fact_name,category,markets,statement,evidence_reference,valid_from,valid_until\n"
        "SP-01,Imported fact,efficacy_claim,IN,Supported wording,Study-01,,\n"
    )
    outcome = import_facts_csv(csv_text, principal=owner, brand_id=brand["id"])

    facts = get_all_facts(principal=owner, brand_id=brand["id"])
    assert outcome == {"imported": 1, "errors": []}
    assert {item["name"] for item in facts} == {"Direct fact", "Imported fact"}
    assert all(item["brand_id"] == brand["id"] for item in facts)
    with get_connection() as conn:
        actions = [
            row["action"]
            for row in conn.execute(
                "SELECT action FROM audit_events WHERE brand_id = ? ORDER BY id",
                (brand["id"],),
            )
        ]
    assert actions == ["BRAND_CREATED", "PRODUCT_CREATED", "FACT_CREATED", "FACT_IMPORTED"]
