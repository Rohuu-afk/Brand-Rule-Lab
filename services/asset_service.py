"""Safe local storage and governance for reusable image assets."""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import uuid
from pathlib import Path

from db.connection import get_connection
from models.enums import UserRole
from services.document_parser import MAX_BYTES, validate_image_dimensions


_ALLOWED = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}
_DATA_ROOT = Path(__file__).resolve().parent.parent / "data"


def _upload_root() -> Path:
    return Path(os.getenv("UPLOADS_PATH", str(_DATA_ROOT / "uploads"))).resolve()


def register_image_asset(
    name: str,
    asset_type: str,
    filename: str,
    data: bytes,
    product_id: int,
    *,
    connection: sqlite3.Connection | None = None,
    staged_files: list[Path] | None = None,
) -> int:
    """Validate an image, compute hashes from bytes, and register it pending approval."""
    if not name.strip() or not product_id:
        raise ValueError("Asset name and product are required.")
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Asset must be non-empty and no larger than the configured upload limit.")
    extension = Path(filename).suffix.lower()
    expected_format = _ALLOWED.get(extension)
    if not expected_format:
        raise ValueError("Only PNG and JPEG image assets are supported.")
    validate_image_dimensions(data)

    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            if image.format != expected_format:
                raise ValueError("Uploaded bytes do not match the selected image extension.")
            image.verify()
        import imagehash

        with Image.open(io.BytesIO(data)) as image:
            perceptual_hash = str(imagehash.phash(image))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Image could not be validated or hashed ({type(exc).__name__}).") from exc

    digest = hashlib.sha256(data).hexdigest()
    upload_root = _upload_root()
    upload_root.mkdir(parents=True, exist_ok=True)
    target = (upload_root / f"{uuid.uuid4().hex}{extension}").resolve()
    if upload_root not in target.parents:
        raise ValueError("Resolved upload path is outside the configured uploads directory.")
    with target.open("xb") as asset_file:
        asset_file.write(data)

    try:
        if connection is not None:
            cursor = connection.execute(
                """
                INSERT INTO brand_assets
                    (product_id, name, asset_type, file_path, perceptual_hash,
                     sha256_hash, metadata, is_active, owner_approved)
                VALUES (?, ?, ?, ?, ?, ?, '{}', 0, 0)
                """,
                (
                    product_id, name.strip(), asset_type,
                    str(
                        target.relative_to(_DATA_ROOT.resolve())
                        if upload_root == (_DATA_ROOT / "uploads").resolve()
                        else target.relative_to(upload_root)
                    ),
                    perceptual_hash, digest,
                ),
            )
            if staged_files is not None:
                staged_files.append(target)
            return int(cursor.lastrowid)
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO brand_assets
                    (product_id, name, asset_type, file_path, perceptual_hash,
                     sha256_hash, metadata, is_active, owner_approved)
                VALUES (?, ?, ?, ?, ?, ?, '{}', 0, 0)
                """,
                (
                    product_id, name.strip(), asset_type,
                    str(
                        target.relative_to(_DATA_ROOT.resolve())
                        if upload_root == (_DATA_ROOT / "uploads").resolve()
                        else target.relative_to(upload_root)
                    ),
                    perceptual_hash, digest,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def decide_asset(
    asset_id: int,
    approved: bool,
    *,
    actor_role: UserRole | str | None = None,
) -> None:
    """Approve an uploaded asset or disable it; decisions are explicit."""
    role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
    if role_value.casefold() not in {
        UserRole.REVIEWER.value.casefold(), UserRole.FACT_OWNER.value.casefold()
    }:
        raise PermissionError("Reviewer or Fact Owner role required to decide brand assets.")
    with get_connection() as conn:
        if approved:
            cursor = conn.execute(
                "UPDATE brand_assets SET owner_approved = 1, is_active = 1 WHERE id = ?",
                (asset_id,),
            )
        else:
            cursor = conn.execute(
                "UPDATE brand_assets SET is_active = 0 WHERE id = ?",
                (asset_id,),
            )
        if cursor.rowcount != 1:
            raise ValueError(f"Asset {asset_id} does not exist.")
        conn.commit()


def get_approved_asset(asset_id: int, product_id: int | None) -> dict | None:
    if product_id is None:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, product_id, sha256_hash, perceptual_hash
            FROM brand_assets
            WHERE id = ? AND product_id = ? AND owner_approved = 1 AND is_active = 1
            """,
            (asset_id, product_id),
        ).fetchone()
        return dict(row) if row else None
