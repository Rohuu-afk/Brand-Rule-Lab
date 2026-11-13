"""Configuration helpers for explicitly enabled synthetic demo access."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


_DEMO_PASSWORDS = {
    "demo_creator": "DEMO_CREATOR_PASSWORD",
    "demo_reviewer": "DEMO_REVIEWER_PASSWORD",
    "demo_fact_owner": "DEMO_FACT_OWNER_PASSWORD",
    "demo_owner": "DEMO_OWNER_PASSWORD",
}
_STREAMLIT_SETTINGS = (
    "DATABASE_PATH",
    "BRL_REQUIRE_SSO",
    "BRL_DEMO_ACCESS",
    *_DEMO_PASSWORDS.values(),
)


def load_streamlit_demo_settings(secrets: Mapping[str, object]) -> None:
    """Load allowlisted deployment settings without overriding environment values."""
    for name in _STREAMLIT_SETTINGS:
        if os.getenv(name, "").strip():
            continue
        value = str(secrets.get(name, "")).strip()
        if value:
            os.environ[name] = value


def demo_access_enabled() -> bool:
    return os.getenv("BRL_DEMO_ACCESS", "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def demo_database_is_isolated(database_path: str | Path | None = None) -> bool:
    configured_path = Path(
        database_path or os.getenv("DATABASE_PATH", "data/brand_rule_lab.db")
    ).resolve()
    default_path = (
        Path(__file__).resolve().parents[1] / "data" / "brand_rule_lab.db"
    ).resolve()
    return (
        configured_path != default_path
        and "judge_demo" in configured_path.stem.casefold()
    )


def configured_demo_passwords() -> dict[str, str] | None:
    passwords = {
        username: os.getenv(environment_name, "")
        for username, environment_name in _DEMO_PASSWORDS.items()
    }
    return passwords if all(value.strip() for value in passwords.values()) else None
