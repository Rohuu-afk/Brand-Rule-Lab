"""Restore a verified backup to new database and uploads paths."""

from __future__ import annotations

import argparse
from pathlib import Path

from db.backup_database import restore_backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", required=True, type=Path, help="backup ZIP file")
    parser.add_argument("--database", required=True, type=Path, help="new SQLite database path")
    parser.add_argument("--uploads", required=True, type=Path, help="new uploads directory path")
    args = parser.parse_args()
    result = restore_backup(args.backup, args.database, args.uploads)
    print(f"Database restored: {result['database']}")
    print(f"Uploads restored: {result['uploads']} ({result['upload_count']} files)")
    print(f"SQLite integrity: {result['integrity']}")
    print("Set DATABASE_PATH and UPLOADS_PATH to these new paths before starting the app.")


if __name__ == "__main__":
    main()
