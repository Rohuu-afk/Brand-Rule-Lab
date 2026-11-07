import json

from ui.pages.rule_tests import evaluate_rule_test_cases


def test_rule_test_helper_evaluates_cases_and_keeps_empty_rules_unobserved():
    rules = [
        {
            "id": 1,
            "name": "Banned phrase",
            "rule_type": "term_list",
            "is_active": 1,
            "owner_approved": 1,
            "parameters": json.dumps({"mode": "banned", "terms": ["miracle"]}),
            "positive_tests": json.dumps(["Supports a fresh feel."]),
            "negative_tests": json.dumps(["A miracle cleanser."]),
        },
        {
            "id": 2,
            "name": "Rule without examples",
            "rule_type": "term_list",
            "is_active": 1,
            "owner_approved": 1,
            "parameters": json.dumps({"mode": "banned", "terms": ["miracle"]}),
            "positive_tests": "[]",
            "negative_tests": "[]",
        },
    ]

    reports = evaluate_rule_test_cases(rules)

    assert [case["passed"] for case in reports[0]["cases"]] == [True, True]
    assert reports[0]["cases"][0]["actual"] == "PASS"
    assert reports[0]["cases"][1]["actual"] == "FAIL"
    assert reports[1]["no_tests_recorded"] is True
    assert reports[1]["cases"] == []
