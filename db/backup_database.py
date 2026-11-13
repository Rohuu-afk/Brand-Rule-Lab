"""Create a verified, SQLite-consistent backup of the database and uploads."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from db.connection import get_db_path


_FORMAT_VERSION = 1
_MAX_ARCHIVE_BYTES = 20 * 1024**3


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_database(path: Path) -> tuple[str, int]:
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok":
            raise ValueError("Database failed SQLite integrity_check.")
        if foreign_keys:
            raise ValueError(
                f"Database contains {len(foreign_keys)} foreign-key violation(s)."
            )
        return integrity, len(foreign_keys)
    finally:
        conn.close()


def _upload_files(upload_root: Path) -> list[Path]:
    if not upload_root.exists():
        return []
    if not upload_root.is_dir():
        raise ValueError("Configured uploads path is not a directory.")
    paths: list[Path] = []
    for directory, subdirs, files in os.walk(upload_root, followlinks=False):
        current = Path(directory)
        subdirs[:] = [name for name in subdirs if not (current / name).is_symlink()]
        for name in files:
            candidate = current / name
            if candidate.is_symlink():
                continue
            if candidate.is_file():
                paths.append(candidate)
    return sorted(paths, key=lambda item: item.relative_to(upload_root).as_posix())


def create_backup(
    source_database: Path,
    upload_root: Path,
    destination: Path,
) -> dict:
    """Write a new backup archive without modifying the source database.

    Existing destinations are never overwritten. SQLite's backup API produces
    a consistent snapshot, including committed WAL content, while leaving the
    source file and its sidecars untouched.
    """
    source_database = Path(source_database).resolve(strict=True)
    upload_root = Path(upload_root).resolve(strict=False)
    destination = Path(destination).resolve(strict=False)
    if not source_database.is_file():
        raise ValueError("Source database must be a regular file.")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Backup destination already exists; choose a new path.")
    if destination == source_database:
        raise ValueError("Backup destination cannot be the source database.")
    if destination == upload_root or upload_root in destination.parents:
        raise ValueError("Backup archive must be outside the uploads directory.")
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="brl-backup-") as staging:
        snapshot = Path(staging) / "database.sqlite"
        source = sqlite3.connect(f"{source_database.as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(snapshot)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()
        _check_database(snapshot)

        archive_tmp = destination.parent / f".{destination.name}.{os.getpid()}.tmp"
        if archive_tmp.exists():
            raise FileExistsError("A backup staging file already exists.")
        try:
            upload_entries: dict[str, str] = {}
            with zipfile.ZipFile(
                archive_tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                archive.write(snapshot, "database.sqlite")
                for file_path in _upload_files(upload_root):
                    relative = file_path.relative_to(upload_root).as_posix()
                    archive_name = f"uploads/{relative}"
                    digest = hashlib.sha256()
                    with file_path.open("rb") as source, archive.open(
                        archive_name, "w", force_zip64=True
                    ) as archived_file:
                        while chunk := source.read(1024 * 1024):
                            digest.update(chunk)
                            archived_file.write(chunk)
                    upload_entries[relative] = digest.hexdigest()
                manifest = {
                    "format_version": _FORMAT_VERSION,
                    "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    "database_sha256": _hash_file(snapshot),
                    "uploads": upload_entries,
                }
                archive.writestr(
                    "backup_manifest.json",
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                )
            # Hard-link creation is atomic and fails if a destination appeared
            # after the initial existence check; it never replaces that file.
            os.link(archive_tmp, destination)
            archive_tmp.unlink()
        except Exception:
            archive_tmp.unlink(missing_ok=True)
            raise

    return {
        "destination": destination,
        "upload_count": len(upload_entries),
        "database_sha256": manifest["database_sha256"],
    }


def _safe_archive_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    windows_path = PureWindowsPath(name)
    if (
        not name
        or "\0" in name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or windows_path.drive
        or windows_path.root
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("Backup contains an unsafe archive path.")
    return path


def restore_backup(
    archive_path: Path,
    destination_database: Path,
    destination_uploads: Path,
) -> dict:
    """Restore a backup into two new paths; never overwrite existing data."""
    archive_path = Path(archive_path).resolve(strict=True)
    destination_database = Path(destination_database).resolve(strict=False)
    destination_uploads = Path(destination_uploads).resolve(strict=False)
    if (
        destination_database.exists() or destination_database.is_symlink()
        or destination_uploads.exists() or destination_uploads.is_symlink()
    ):
        raise FileExistsError("Restore destinations must not already exist.")
    if (
        destination_database == destination_uploads
        or destination_database in destination_uploads.parents
        or destination_uploads in destination_database.parents
    ):
        raise ValueError("Database and uploads restore paths must not overlap.")
    destination_database.parent.mkdir(parents=True, exist_ok=True)
    destination_uploads.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="brl-restore-db-", dir=destination_database.parent) as db_temp:
        with tempfile.TemporaryDirectory(prefix="brl-restore-uploads-", dir=destination_uploads.parent) as uploads_temp:
            staged_database = Path(db_temp) / "database.sqlite"
            staged_uploads = Path(uploads_temp) / "uploads"
            staged_uploads.mkdir()
            try:
                with zipfile.ZipFile(archive_path, "r") as backup:
                    infos = backup.infolist()
                    if sum(item.file_size for item in infos) > _MAX_ARCHIVE_BYTES:
                        raise ValueError("Backup archive exceeds the restore size limit.")
                    names = [_safe_archive_name(item.filename) for item in infos]
                    name_strings = [str(name) for name in names]
                    if len(set(name_strings)) != len(name_strings):
                        raise ValueError("Backup contains duplicate archive paths.")
                    if any(item.is_dir() for item in infos):
                        raise ValueError("Backup contains an unexpected directory entry.")
                    for info in infos:
                        mode = info.external_attr >> 16
                        if stat.S_ISLNK(mode):
                            raise ValueError("Backup contains a symbolic link.")

                    if "database.sqlite" not in name_strings or "backup_manifest.json" not in name_strings:
                        raise ValueError("Backup is missing required database or manifest files.")
                    manifest = json.loads(backup.read("backup_manifest.json"))
                    if manifest.get("format_version") != _FORMAT_VERSION:
                        raise ValueError("Backup format version is unsupported.")
                    expected_uploads = manifest.get("uploads")
                    if not isinstance(expected_uploads, dict):
                        raise ValueError("Backup manifest has an invalid upload list.")
                    expected_names = {"database.sqlite", "backup_manifest.json"}
                    expected_names.update(f"uploads/{name}" for name in expected_uploads)
                    if {str(name) for name in names} != expected_names:
                        raise ValueError("Backup contents do not match the manifest.")

                    with backup.open("database.sqlite") as source, staged_database.open("wb") as target:
                        digest = hashlib.sha256()
                        while chunk := source.read(1024 * 1024):
                            digest.update(chunk)
                            target.write(chunk)
                    if digest.hexdigest() != manifest.get("database_sha256"):
                        raise ValueError("Backup database checksum does not match.")

                    for relative, expected_hash in expected_uploads.items():
                        archive_name = f"uploads/{relative}"
                        safe_name = _safe_archive_name(archive_name)
                        if len(safe_name.parts) < 2 or safe_name.parts[0] != "uploads":
                            raise ValueError("Backup contains an invalid upload path.")
                        output = staged_uploads.joinpath(*safe_name.parts[1:])
                        output.parent.mkdir(parents=True, exist_ok=True)
                        digest = hashlib.sha256()
                        with backup.open(archive_name) as source, output.open("xb") as target:
                            while chunk := source.read(1024 * 1024):
                                digest.update(chunk)
                                target.write(chunk)
                        if digest.hexdigest() != expected_hash:
                            raise ValueError(f"Backup upload checksum failed: {relative}")
            except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValueError("Backup archive is invalid or incomplete.") from exc

            _check_database(staged_database)
            published_database = False
            published_upload_files: list[Path] = []
            published_upload_dirs: list[Path] = []
            try:
                with staged_database.open("rb") as source, destination_database.open("xb") as target:
                    published_database = True
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())

                destination_uploads.mkdir()
                published_upload_dirs.append(destination_uploads)
                for directory, subdirs, files in os.walk(staged_uploads, followlinks=False):
                    source_dir = Path(directory)
                    subdirs.sort()
                    files.sort()
                    relative_dir = source_dir.relative_to(staged_uploads)
                    target_dir = destination_uploads / relative_dir
                    for subdir in subdirs:
                        child = target_dir / subdir
                        child.mkdir()
                        published_upload_dirs.append(child)
                    for filename in files:
                        source_file = source_dir / filename
                        target_file = target_dir / filename
                        with source_file.open("rb") as source, target_file.open("xb") as target:
                            published_upload_files.append(target_file)
                            while chunk := source.read(1024 * 1024):
                                target.write(chunk)
                            target.flush()
                            os.fsync(target.fileno())
            except Exception:
                for path in reversed(published_upload_files):
                    path.unlink(missing_ok=True)
                for path in reversed(published_upload_dirs):
                    try:
                        path.rmdir()
                    except OSError:
                        pass
                if published_database:
                    destination_database.unlink(missing_ok=True)
                raise

    return {
        "database": destination_database,
        "uploads": destination_uploads,
        "upload_count": len(expected_uploads),
        "integrity": "ok",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path, help="new .zip backup path")
    parser.add_argument("--database", type=Path, default=get_db_path(), help="source SQLite database")
    parser.add_argument(
        "--uploads", type=Path,
        default=Path(os.getenv("UPLOADS_PATH", str(Path(__file__).resolve().parent.parent / "data" / "uploads"))),
        help="uploads directory to include",
    )
    args = parser.parse_args()
    result = create_backup(args.database, args.uploads, args.destination)
    print(f"Backup created: {result['destination']}")
    print(f"Uploads included: {result['upload_count']}")
    print(f"Database SHA-256: {result['database_sha256']}")


if __name__ == "__main__":
    main()
