"""Provision local demo accounts without changing existing credentials."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import apply_schema, get_connection
from db.seed import seed
from models.enums import UserRole
from services.auth_service import create_user, get_user


_DEMO_ACCOUNTS = (
    ("demo_creator", "DEMO_CREATOR_PASSWORD", UserRole.CREATOR),
    ("demo_reviewer", "DEMO_REVIEWER_PASSWORD", UserRole.REVIEWER),
    ("demo_fact_owner", "DEMO_FACT_OWNER_PASSWORD", UserRole.FACT_OWNER),
    ("demo_owner", "DEMO_OWNER_PASSWORD", UserRole.CREATOR),
)


def _prompt_for_password(username: str, prompt: Callable[[str], str]) -> str:
    password = prompt(f"Password for {username}: ")
    confirmation = prompt(f"Confirm password for {username}: ")
    if not password:
        raise ValueError(f"A password is required for {username}.")
    if password != confirmation:
        raise ValueError(f"Passwords for {username} do not match.")
    return password


def _password_for(
    username: str,
    environment_name: str,
    passwords: dict[str, str] | None,
    prompt: Callable[[str], str],
) -> str:
    if passwords and username in passwords:
        password = passwords[username]
    else:
        password = os.getenv(environment_name)
    if password:
        return password
    return _prompt_for_password(username, prompt)


def provision_demo_accounts(
    *,
    passwords: dict[str, str] | None = None,
    prompt: Callable[[str], str] = getpass.getpass,
) -> dict[str, str]:
    """Create missing demo accounts and preserve existing accounts unchanged."""
    apply_schema()
    # The demo workspace binding below must run after seed data exists. This
    # keeps the documented one-command local setup usable on a fresh database.
    seed(force=False, verbose=False)
    results: dict[str, str] = {}
    for username, environment_name, role in _DEMO_ACCOUNTS:
        if get_user(username) is not None:
            results[username] = "existing"
            continue
        password = _password_for(username, environment_name, passwords, prompt)
        create_user(username, password, role)
        results[username] = "created"
    _ensure_demo_brand()
    return results


def _ensure_demo_brand() -> None:
    """Create the local demo workspace and memberships without changing accounts."""
    with get_connection() as conn:
        users = {
            row["username"]: row["id"]
            for row in conn.execute(
                "SELECT id, username FROM users WHERE username IN "
                "('demo_creator','demo_reviewer','demo_fact_owner','demo_owner')"
            ).fetchall()
        }
        emails = {
            "demo_creator": "demo_creator@example.invalid",
            "demo_reviewer": "demo_reviewer@example.invalid",
            "demo_fact_owner": "demo_fact_owner@example.invalid",
            "demo_owner": "demo_owner@example.invalid",
        }
        for username, email in emails.items():
            conn.execute(
                "UPDATE users SET email = ? WHERE id = ? AND (email IS NULL OR email = '')",
                (email, users[username]),
            )
        brand = conn.execute(
            "SELECT id FROM brands WHERE slug = 'demo-brand'"
        ).fetchone()
        if brand is None:
            brand_id = conn.execute(
                "INSERT INTO brands (name, slug, created_by_user_id) VALUES (?, ?, ?) RETURNING id",
                ("Brand Rule Lab Demo", "demo-brand", users["demo_owner"]),
            ).fetchone()["id"]
        else:
            brand_id = brand["id"]
        roles = {
            "demo_creator": "Creator",
            "demo_reviewer": "Reviewer",
            "demo_fact_owner": "Fact Owner",
            "demo_owner": "Brand Owner",
        }
        for username, role in roles.items():
            conn.execute(
                """
                INSERT INTO brand_memberships (brand_id, user_id, role)
                VALUES (?, ?, ?)
                ON CONFLICT(brand_id, user_id) DO UPDATE SET role = excluded.role
                """,
                (brand_id, users[username], role),
            )
        conn.execute(
            "UPDATE products SET brand_id = ? WHERE brand_id IS NULL AND brand = ?",
            (brand_id, "Monsoon Beauty Co."),
        )
        for table in (
            "guidelines", "facts", "brand_assets", "drafts", "campaign_briefs",
        ):
            conn.execute(
                f"""
                UPDATE {table} SET brand_id = ?
                WHERE brand_id IS NULL AND product_id IN
                    (SELECT id FROM products WHERE brand_id = ?)
                """,
                (brand_id, brand_id),
            )
        conn.execute(
            """
            UPDATE brand_rules SET brand_id = ?
            WHERE brand_id IS NULL AND product_id IN
                (SELECT id FROM products WHERE brand_id = ?)
            """,
            (brand_id, brand_id),
        )
        conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create missing local Brand Rule Lab demo accounts."
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Fail if a missing account password is not supplied by its environment variable.",
    )
    args = parser.parse_args()

    try:
        apply_schema()
        if args.non_interactive:
            missing = [
                environment_name
                for username, environment_name, _ in _DEMO_ACCOUNTS
                if get_user(username) is None and not os.getenv(environment_name)
            ]
            if missing:
                names = ", ".join(missing)
                raise ValueError(f"Missing required environment variable(s): {names}.")
        results = provision_demo_accounts()
    except (OSError, ValueError) as exc:
        print(f"[error] Demo account provisioning failed: {exc}", file=sys.stderr)
        return 1

    for username, status in results.items():
        print(f"[ok] {username}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
