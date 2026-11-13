from __future__ import annotations

import io

from PIL import Image
import pytest

from db.connection import apply_schema, get_connection
from db.seed import seed
from services.asset_service import decide_asset, register_image_asset
from services.review_service import run_review


def _package_image() -> bytes:
    image = Image.new("RGB", (96, 96), "white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize(
    ("submitted_text", "expected_result"),
    [
        ("Gentle daily cleanser 100 ml", "PASS"),
        ("Gentle daily cleanser 100 ml NEW CLAIM", "FAIL"),
    ],
)
def test_package_review_compares_ocr_copy_to_approved_reference(
    tmp_path, monkeypatch, submitted_text, expected_result
):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "packaging.sqlite"))
    monkeypatch.setenv("UPLOADS_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("BRL_ENABLE_LEGACY_TEST_ACTORS", "1")
    apply_schema()
    seed()
    image_bytes = _package_image()
    asset_id = register_image_asset(
        "Approved package front", "packaging", "package.png", image_bytes, 1
    )
    decide_asset(asset_id, approved=True, actor_role="Fact Owner")
    with get_connection() as conn:
        draft_id = conn.execute(
            "INSERT INTO drafts (title, product_id) VALUES ('Package copy check', 1)"
        ).lastrowid

    monkeypatch.setattr(
        "services.review_service.parse_file",
        lambda *_args, **_kwargs: {"text": "Gentle daily cleanser 100 ml"},
    )
    result = run_review(
        draft_id,
        submitted_text,
        "image",
        {},
        1,
        "IN",
        "website",
        None,
        None,
        {"packaging_reference_asset_id": asset_id},
        ai_consent=False,
    )

    finding = next(
        item for item in result["findings"]
        if item["finding_type"] == "packaging_reference_text"
    )
    assert finding["result"] == expected_result
    if expected_result == "FAIL":
        assert "new" in finding["explanation"].casefold()
        assert "reviewer must confirm" in finding["explanation"].casefold()


def test_package_review_requires_an_active_approved_reference(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "packaging-state.sqlite"))
    monkeypatch.setenv("UPLOADS_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("BRL_ENABLE_LEGACY_TEST_ACTORS", "1")
    apply_schema()
    seed()
    image_bytes = _package_image()
    asset_id = register_image_asset(
        "Pending package", "packaging", "package.png", image_bytes, 1
    )
    with get_connection() as conn:
        draft_id = conn.execute(
            "INSERT INTO drafts (title, product_id) VALUES ('Package pending ref', 1)"
        ).lastrowid
    monkeypatch.setattr(
        "services.review_service.parse_file",
        lambda *_args, **_kwargs: {"text": "Reference text"},
    )

    result = run_review(
        draft_id,
        "Submitted text",
        "image",
        {},
        1,
        "IN",
        "website",
        None,
        None,
        {"packaging_reference_asset_id": asset_id},
        ai_consent=False,
    )
    finding = next(
        item for item in result["findings"]
        if item["finding_type"] == "packaging_reference_text"
    )
    assert finding["result"] == "NOT_OBSERVED"
    assert "not active, approved" in finding["explanation"]
