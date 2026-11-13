"""Read-only runner for stored tests on active, approved brand rules."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import streamlit as st

from engines.rule_engine import run_self_tests
from services.guideline_service import get_all_rules
from ui.components import page_header
from ui.styles import demo_banner, inject_styles


def _decode_test_list(value: Any, field_name: str) -> list[str]:
    if value is None or value == "":
        return []
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise ValueError(f"{field_name} must be a JSON list of text examples.")
    return decoded


def load_rule_test_cases(rules: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return stored positive and negative examples without executing them."""
    loaded = []
    for rule in rules:
        positive = _decode_test_list(rule.get("positive_tests"), "positive_tests")
        negative = _decode_test_list(rule.get("negative_tests"), "negative_tests")
        loaded.append({
            "rule_id": rule.get("id"),
            "rule_name": rule.get("name", "Unnamed rule"),
            "positive_examples": positive,
            "negative_examples": negative,
            "no_tests_recorded": not (positive or negative),
        })
    return loaded


def evaluate_rule_test_cases(rules: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Run each rule's stored positive/negative examples through the rule engine."""
    reports = []
    for rule, stored in zip(rules, load_rule_test_cases(rules)):
        if stored["no_tests_recorded"]:
            reports.append({**stored, "cases": []})
            continue
        cases = []
        for result in run_self_tests(dict(rule)):
            cases.append({
                "test_type": result["test_type"],
                "test_text": result["test_text"],
                "expected": "PASS" if result["test_type"] == "positive" else "FAIL",
                "actual": result["result"].value,
                "explanation": result["explanation"],
                "passed": result["passed"],
            })
        reports.append({**stored, "cases": cases})
    return reports


def render(principal=None) -> None:
    inject_styles()
    page_header("Governance", "Rule Tests")
    demo_banner()
    st.caption("Read-only checks of the positive and negative examples stored with approved rules.")

    active_rules = [
        rule for rule in get_all_rules(
            principal=principal, brand_id=st.session_state.get("active_brand_id")
        )
        if rule.get("is_active") and rule.get("owner_approved")
    ]
    if not active_rules:
        st.info("No active approved rules are available.")
        return

    for rule in active_rules:
        try:
            stored = load_rule_test_cases([rule])[0]
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            st.error(f"Could not read stored tests for {rule.get('name', 'unnamed rule')}: {exc}")
            continue
        with st.expander(rule.get("name") or f"Rule {rule.get('id')}"):
            st.caption(
                f"{rule.get('rule_type', 'unknown')} · "
                f"{rule.get('product_name') or 'All products'}"
            )
            if stored["no_tests_recorded"]:
                st.info("no tests recorded")
            else:
                st.write("Expected to pass")
                st.write(stored["positive_examples"] or ["No positive tests recorded"])
                st.write("Expected to fail")
                st.write(stored["negative_examples"] or ["No negative tests recorded"])

    if st.button("Run tests", type="primary"):
        try:
            reports = evaluate_rule_test_cases(active_rules)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            st.error(f"Could not execute stored rule tests: {exc}")
            return

        all_cases = [
            case
            for report in reports
            for case in report["cases"]
        ]
        passed_count = sum(case["passed"] for case in all_cases)
        st.metric("Cases passing their expected result", f"{passed_count}/{len(all_cases)}")
        if not all_cases:
            st.info("No tests recorded")
        for report in reports:
            with st.expander(f"Results: {report['rule_name']}"):
                if report["no_tests_recorded"]:
                    st.info("no tests recorded")
                    continue
                for case in report["cases"]:
                    status = "PASS" if case["passed"] else "FAIL"
                    st.write(
                        f"**{status}** · {case['test_type']} example · "
                        f"expected {case['expected']}, got {case['actual']}"
                    )
                    st.code(case["test_text"])
                    st.caption(case["explanation"])
