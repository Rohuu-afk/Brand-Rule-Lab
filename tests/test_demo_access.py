from __future__ import annotations

import os

from services.demo_access import (
    configured_demo_passwords,
    demo_database_is_isolated,
    load_streamlit_demo_settings,
)


def test_streamlit_demo_settings_load_only_missing_environment_values(monkeypatch):
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("BRL_DEMO_ACCESS", raising=False)
    monkeypatch.setenv("DEMO_CREATOR_PASSWORD", "environment-password")

    load_streamlit_demo_settings(
        {
            "DATABASE_PATH": "data/judge_demo_round3.db",
            "BRL_DEMO_ACCESS": "1",
            "DEMO_CREATOR_PASSWORD": "secret-password",
        }
    )

    assert os.environ["DATABASE_PATH"] == "data/judge_demo_round3.db"
    assert os.environ["BRL_DEMO_ACCESS"] == "1"
    assert os.environ["DEMO_CREATOR_PASSWORD"] == "environment-password"


def test_public_demo_requires_isolated_database_and_all_passwords(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "judge_demo_round3.db"))
    assert demo_database_is_isolated()
    assert configured_demo_passwords() is None

    for username, password in (
        ("CREATOR", "creator-pass"),
        ("REVIEWER", "reviewer-pass"),
        ("FACT_OWNER", "fact-owner-pass"),
        ("OWNER", "owner-pass"),
    ):
        monkeypatch.setenv(f"DEMO_{username}_PASSWORD", password)

    passwords = configured_demo_passwords()
    assert passwords is not None
    assert set(passwords) == {
        "demo_creator",
        "demo_reviewer",
        "demo_fact_owner",
        "demo_owner",
    }


def test_default_database_is_never_an_isolated_public_demo(monkeypatch):
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    assert not demo_database_is_isolated()
