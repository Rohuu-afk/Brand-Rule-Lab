"""Safe local storage and governance for reusable image assets."""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import uuid
from pathlib import Path

from db.connection import get_connection
from models.enums import BrandRole, UserRole
from services.document_parser import MAX_BYTES, validate_image_dimensions
from services.auth_service import (
    AuthenticatedPrincipal,
    require_legacy_test_actor,
    require_principal,
)
from services.audit_service import append_audit_event
from services.brand_service import require_record_brand


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
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> int:
    """Validate an image, compute hashes from bytes, and register it pending approval."""
    if not name.strip() or not product_id:
        raise ValueError("Asset name and product are required.")
    actor = require_principal(principal, action="register brand assets") if principal else None
    if actor is not None and brand_id is None:
        raise ValueError("A brand is required to register an asset.")
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
            if actor is not None:
                require_record_brand(
                    connection, actor, brand_id, "products", product_id,
                    roles=(BrandRole.OWNER, BrandRole.REVIEWER, BrandRole.FACT_OWNER),
                )
            cursor = connection.execute(
                """
                INSERT INTO brand_assets
                    (product_id, name, asset_type, file_path, perceptual_hash,
                     sha256_hash, metadata, is_active, owner_approved, brand_id)
                VALUES (?, ?, ?, ?, ?, ?, '{}', 0, 0, ?)
                """,
                (
                    product_id, name.strip(), asset_type,
                    str(
                        target.relative_to(_DATA_ROOT.resolve())
                        if upload_root == (_DATA_ROOT / "uploads").resolve()
                        else target.relative_to(upload_root)
                    ),
                    perceptual_hash, digest, brand_id if actor else None,
                ),
            )
            if staged_files is not None:
                staged_files.append(target)
            if actor is not None:
                append_audit_event(
                    connection,
                    actor,
                    action="ASSET_REGISTERED",
                    object_type="brand_asset",
                    object_id=cursor.lastrowid,
                    brand_id=brand_id if actor is not None else None,
                    details={"name": name.strip(), "product_id": product_id},
                )
            return int(cursor.lastrowid)
        with get_connection() as conn:
            if actor is not None:
                require_record_brand(
                    conn, actor, brand_id, "products", product_id,
                    roles=(BrandRole.OWNER, BrandRole.REVIEWER, BrandRole.FACT_OWNER),
                )
            cursor = conn.execute(
                """
                INSERT INTO brand_assets
                    (product_id, name, asset_type, file_path, perceptual_hash,
                     sha256_hash, metadata, is_active, owner_approved, brand_id)
                VALUES (?, ?, ?, ?, ?, ?, '{}', 0, 0, ?)
                """,
                (
                    product_id, name.strip(), asset_type,
                    str(
                        target.relative_to(_DATA_ROOT.resolve())
                        if upload_root == (_DATA_ROOT / "uploads").resolve()
                        else target.relative_to(upload_root)
                    ),
                    perceptual_hash, digest, brand_id if actor else None,
                ),
            )
            if actor is not None:
                append_audit_event(
                    conn,
                    actor,
                    action="ASSET_REGISTERED",
                    object_type="brand_asset",
                    object_id=cursor.lastrowid,
                    brand_id=brand_id if actor is not None else None,
                    details={"name": name.strip(), "product_id": product_id},
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
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> None:
    """Approve an uploaded asset or disable it; decisions are explicit."""
    actor = require_principal(principal, action="decide brand assets") if principal else None
    if principal is None:
        require_legacy_test_actor("decide brand assets")
        role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
        if role_value.casefold() not in {
            UserRole.REVIEWER.value.casefold(), UserRole.FACT_OWNER.value.casefold()
        }:
            raise PermissionError("Reviewer or Fact Owner role required to decide brand assets.")
    with get_connection() as conn:
        if actor is not None:
            if brand_id is None:
                raise ValueError("A brand is required to decide this asset.")
            require_record_brand(
                conn, actor, brand_id, "brand_assets", asset_id,
                roles=(BrandRole.OWNER, BrandRole.REVIEWER, BrandRole.FACT_OWNER),
            )
        if approved:
            cursor = conn.execute(
                "UPDATE brand_assets SET owner_approved = 1, is_active = 1 WHERE id = ?"
                + (" AND brand_id = ?" if actor is not None else ""),
                (asset_id, brand_id) if actor is not None else (asset_id,),
            )
        else:
            cursor = conn.execute(
                "UPDATE brand_assets SET is_active = 0 WHERE id = ?"
                + (" AND brand_id = ?" if actor is not None else ""),
                (asset_id, brand_id) if actor is not None else (asset_id,),
            )
        if cursor.rowcount != 1:
            raise ValueError(f"Asset {asset_id} does not exist.")
        if actor is not None:
            append_audit_event(
                conn,
                actor,
                action="ASSET_APPROVED" if approved else "ASSET_DISABLED",
                object_type="brand_asset",
                object_id=asset_id,
                brand_id=brand_id if actor is not None else None,
            )
        conn.commit()


def get_approved_asset(
    asset_id: int,
    product_id: int | None,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> dict | None:
    if principal is None:
        require_legacy_test_actor("read approved asset identity")
    elif brand_id is None:
        raise ValueError("A brand is required to read an approved asset.")
    if product_id is None:
        return None
    with get_connection() as conn:
        if principal is not None:
            require_record_brand(conn, principal, brand_id, "products", product_id)
        row = conn.execute(
            """
            SELECT id, product_id, sha256_hash, perceptual_hash
            FROM brand_assets
            WHERE id = ? AND product_id = ? AND owner_approved = 1 AND is_active = 1
              AND (? IS NULL OR brand_id = ?)
            """,
            (asset_id, product_id, brand_id if principal is not None else None,
             brand_id if principal is not None else None),
        ).fetchone()
        return dict(row) if row else None


def get_approved_packaging_reference(
    asset_id: int,
    product_id: int | None,
    *,
    principal: AuthenticatedPrincipal | None = None,
    brand_id: int | None = None,
) -> dict | None:
    """Return validated local image bytes for an active packaging reference."""
    if principal is None:
        require_legacy_test_actor("read an approved packaging reference")
    elif brand_id is None:
        raise ValueError("A brand is required to read a packaging reference.")
    if product_id is None:
        return None
    with get_connection() as conn:
        if principal is not None:
            require_record_brand(conn, principal, brand_id, "products", product_id)
        row = conn.execute(
            """
            SELECT id, name, file_path
            FROM brand_assets
            WHERE id = ? AND product_id = ? AND asset_type = 'packaging'
              AND owner_approved = 1 AND is_active = 1
              AND (? IS NULL OR brand_id = ?)
            """,
            (asset_id, product_id, brand_id if principal is not None else None,
             brand_id if principal is not None else None),
        ).fetchone()
    if row is None:
        return None

    relative_path = Path(row["file_path"])
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    roots = (_DATA_ROOT.resolve(), _upload_root())
    for root in roots:
        candidate = (root / relative_path).resolve()
        if root not in candidate.parents or not candidate.is_file():
            continue
        try:
            if candidate.stat().st_size > MAX_BYTES:
                return None
            data = candidate.read_bytes()
        except OSError:
            return None
        extension = candidate.suffix.lower()
        expected = _ALLOWED.get(extension)
        if not expected:
            return None
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                if image.format != expected:
                    return None
                image.verify()
            validate_image_dimensions(data)
        except Exception:
            return None
        return {"id": int(row["id"]), "name": row["name"], "filename": candidate.name, "data": data}
    return None
