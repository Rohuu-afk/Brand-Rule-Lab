import json

from db.connection import get_connection
from db.seed import seed
from engines.rule_engine import run_rule


def test_seeded_d02_rule_rejects_expired_and_accepts_active_offer(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "offer-date-demo.sqlite"))
    seed(force=False, verbose=False)

    with get_connection() as conn:
        rule_row = conn.execute(
            "SELECT * FROM brand_rules WHERE name = ?",
            ("D-02: Monsoon Offer Date Window",),
        ).fetchone()
        assert rule_row is not None
        rule = dict(rule_row)

        fact_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM facts WHERE product_id = ?",
                (rule["product_id"],),
            ).fetchall()
        }
        assert "[DEMO] Monsoon Expired Offer Date" in fact_names
        assert "[DEMO] Monsoon Active Offer Date" in fact_names

        first_count = conn.execute(
            "SELECT COUNT(*) AS count FROM brand_rules WHERE name = ?",
            ("D-02: Monsoon Offer Date Window",),
        ).fetchone()["count"]

    seed(force=False, verbose=False)

    with get_connection() as conn:
        repeated_count = conn.execute(
            "SELECT COUNT(*) AS count FROM brand_rules WHERE name = ?",
            ("D-02: Monsoon Offer Date Window",),
        ).fetchone()["count"]
    assert repeated_count == first_count == 1

    assert run_rule(
        rule,
        "Monsoon offer valid through 2000-01-01.",
    ).result.value == "FAIL"
    assert run_rule(
        rule,
        "Monsoon offer valid through 2099-12-31.",
    ).result.value == "PASS"
    assert json.loads(rule["positive_tests"]) == [
        "Monsoon offer valid through 2099-12-31."
    ]
    assert json.loads(rule["negative_tests"]) == [
        "Monsoon offer valid through 2000-01-01."
    ]
