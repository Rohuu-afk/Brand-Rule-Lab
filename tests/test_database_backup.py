from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest

from db.backup_database import create_backup, restore_backup
from db.connection import apply_schema, get_connection


def _make_database(path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(path))
    apply_schema()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO products (name, brand) VALUES (?, ?)",
            ("Restore test product", "Synthetic workspace"),
        )
        conn.commit()
    finally:
        conn.close()


def test_backup_restore_preserves_database_and_uploaded_files(tmp_path, monkeypatch):
    source_db = tmp_path / "source.sqlite"
    uploads = tmp_path / "source_uploads"
    uploads.mkdir()
    (uploads / "nested").mkdir()
    (uploads / "nested" / "artwork.bin").write_bytes(b"synthetic artwork bytes")
    _make_database(source_db, monkeypatch)
    original_mtime = source_db.stat().st_mtime_ns
    archive = tmp_path / "backups" / "full-backup.zip"

    backup = create_backup(source_db, uploads, archive)

    assert backup["upload_count"] == 1
    assert source_db.stat().st_mtime_ns == original_mtime
    restored_db = tmp_path / "recovered" / "restored.sqlite"
    restored_uploads = tmp_path / "recovered" / "uploads"
    restored = restore_backup(archive, restored_db, restored_uploads)

    conn = sqlite3.connect(restored_db)
    try:
        assert conn.execute("SELECT name FROM products").fetchone()[0] == "Restore test product"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
    assert (restored_uploads / "nested" / "artwork.bin").read_bytes() == b"synthetic artwork bytes"
    assert restored["integrity"] == "ok"
    assert restored["upload_count"] == 1


def test_backup_and_restore_refuse_to_overwrite_existing_paths(tmp_path, monkeypatch):
    source_db = tmp_path / "source.sqlite"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _make_database(source_db, monkeypatch)
    archive = tmp_path / "backup.zip"
    archive.write_bytes(b"keep existing file")

    with pytest.raises(FileExistsError, match="already exists"):
        create_backup(source_db, uploads, archive)
    assert archive.read_bytes() == b"keep existing file"

    source_archive = tmp_path / "valid.zip"
    create_backup(source_db, uploads, source_archive)
    existing_database = tmp_path / "existing.sqlite"
    existing_database.write_bytes(b"keep existing database")
    with pytest.raises(FileExistsError, match="must not already exist"):
        restore_backup(source_archive, existing_database, tmp_path / "new_uploads")
    assert existing_database.read_bytes() == b"keep existing database"


def test_restore_rejects_tampered_upload_and_unsafe_archive_paths(tmp_path, monkeypatch):
    source_db = tmp_path / "source.sqlite"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "asset.bin").write_bytes(b"original")
    _make_database(source_db, monkeypatch)
    valid_archive = tmp_path / "valid.zip"
    create_backup(source_db, uploads, valid_archive)

    tampered_archive = tmp_path / "tampered.zip"
    with zipfile.ZipFile(valid_archive) as source, zipfile.ZipFile(tampered_archive, "w") as target:
        for name in source.namelist():
            payload = source.read(name)
            if name == "uploads/asset.bin":
                payload = b"changed"
            target.writestr(name, payload)
    with pytest.raises(ValueError, match="checksum"):
        restore_backup(
            tampered_archive, tmp_path / "tampered.sqlite", tmp_path / "tampered_uploads"
        )
    assert not (tmp_path / "tampered.sqlite").exists()
    assert not (tmp_path / "tampered_uploads").exists()

    unsafe_archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe_archive, "w") as target:
        target.writestr("../escaped.txt", "not extracted")
    with pytest.raises(ValueError, match="unsafe archive path"):
        restore_backup(
            unsafe_archive, tmp_path / "unsafe.sqlite", tmp_path / "unsafe_uploads"
        )
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "unsafe.sqlite").exists()

    drive_archive = tmp_path / "drive-path.zip"
    with zipfile.ZipFile(drive_archive, "w") as target:
        target.writestr("C:/escaped.txt", "not extracted")
    with pytest.raises(ValueError, match="unsafe archive path"):
        restore_backup(
            drive_archive, tmp_path / "drive.sqlite", tmp_path / "drive_uploads"
        )
    assert not (tmp_path / "drive.sqlite").exists()
