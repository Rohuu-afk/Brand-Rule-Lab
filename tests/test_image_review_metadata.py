import io
import json
from hashlib import sha256

from PIL import Image, ImageDraw

from db.connection import apply_schema, get_connection
from db.seed import seed
from services.asset_service import decide_asset, register_image_asset
from services.review_service import run_review


def test_image_review_keeps_bytes_out_of_saved_metadata(tmp_path, monkeypatch):
    database_path = tmp_path / "image-review.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("UPLOADS_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("BRL_ENABLE_LEGACY_TEST_ACTORS", "1")
    apply_schema()
    seed()

    image = Image.new("RGB", (64, 64), "white")
    ImageDraw.Draw(image).rectangle((8, 8, 55, 55), fill="black")
    image_buffer = io.BytesIO()
    image.save(image_buffer, format="PNG")
    submitted_image_bytes = image_buffer.getvalue()
    asset_id = register_image_asset(
        "Synthetic approved image",
        "logo",
        "approved.png",
        submitted_image_bytes,
        product_id=1,
    )
    decide_asset(asset_id, approved=True, actor_role="Fact Owner")

    with get_connection() as conn:
        draft_id = conn.execute(
            "INSERT INTO drafts (title, product_id, market, channel) "
            "VALUES ('Image metadata regression', 1, 'IN', 'website')"
        ).lastrowid

    visual_metadata = {
        "asset_id": asset_id,
        "submitted_hash": sha256(submitted_image_bytes).hexdigest(),
        "submitted_image_bytes": submitted_image_bytes,
    }
    result = run_review(
        draft_id,
        "A synthetic image review.",
        "image",
        {},
        1,
        "IN",
        "website",
        None,
        None,
        visual_metadata,
        ai_consent=False,
    )

    with get_connection() as conn:
        row = conn.execute(
            "SELECT visual_metadata FROM draft_versions WHERE id = ?",
            (result["draft_version_id"],),
        ).fetchone()

    saved_metadata = json.loads(row["visual_metadata"])
    assert "submitted_image_bytes" not in saved_metadata
    assert saved_metadata["asset_id"] == asset_id
    assert any(
        finding["finding_type"] == "asset_perceptual_identity"
        and finding["result"] == "PASS"
        for finding in result["findings"]
    )
    assert "submitted_image_bytes" in visual_metadata
