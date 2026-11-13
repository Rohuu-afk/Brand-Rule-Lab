"""Classify source-backed brand-rule proposals before they enter review."""

from __future__ import annotations

import json

import streamlit as st

from models.enums import RuleType, UserRole
from models.enums import BrandRole
from services.auth_service import AuthenticatedPrincipal
from services.brand_service import require_brand_membership
from services.knowledge_onboarding import (
    confirm_source_support,
    decide_knowledge_entry,
    get_original_page_preview,
    verify_source_quote_location,
)
from services.rule_readiness import (
    READINESS_CLASSES,
    RULE_CATEGORIES,
    approve_rule_proposal,
    get_rule_proposals,
    save_rule_classification,
)
from ui.components import page_header
from ui.styles import demo_banner, inject_styles


_PREDICATE_DEFAULTS = {
    "term_list": {
        "parameters": {"mode": "banned", "terms": ["miracle"]},
        "positive": ["This cleanser supports a fresh feel."],
        "negative": ["This cleanser is a miracle solution."],
    },
    "regex": {
        "parameters": {"pattern": r"(?i)\bmiracle\b", "require_match": False},
        "positive": ["This cleanser feels fresh."],
        "negative": ["This cleanser is a miracle."],
    },
    "numeric_range": {
        "parameters": {
            "extract_pattern": r"(?i)\b(\d+(?:\.\d+)?)%",
            "min": 0,
            "max": 100,
        },
        "positive": ["Contains 2% niacinamide."],
        "negative": ["Contains 120% niacinamide."],
    },
    "date_window": {
        "parameters": {
            "extract_pattern": r"(\d{4}-\d{2}-\d{2})",
            "date_format": "%Y-%m-%d",
            "earliest": "2026-01-01",
            "latest": "2026-12-31",
        },
        "positive": ["Effective 2026-06-01."],
        "negative": ["Effective 2027-01-01."],
    },
    "required_text": {
        "parameters": {
            "pattern": r"(?i)\bstudy\s+ref",
            "trigger_on": r"(?i)\bclinical\b",
            "require_match": True,
        },
        "positive": ["Clinical study ref: ABC-1."],
        "negative": ["Clinical result shown."],
    },
    "wcag_contrast": {
        "parameters": {
            "foreground": "#000000",
            "background": "#FFFFFF",
            "min_ratio": 4.5,
        },
        "positive": ["#000000|#FFFFFF"],
        "negative": ["#777777|#FFFFFF"],
    },
    "asset_hash": {
        "parameters": {
            "approved_hash": "0" * 64,
            "hash_type": "sha256",
        },
        "positive": ["0" * 64],
        "negative": ["1" * 64],
    },
    "logo_phash": {
        "parameters": {
            "approved_phash": "0" * 16,
            "max_hamming_distance": 10,
        },
        "positive": ["0" * 16],
        "negative": ["f" * 16],
    },
}


def _render_source(
    entry: dict, actor: str, role_value: str | None,
    principal: AuthenticatedPrincipal, brand_id: int,
) -> None:
    if role_value is None:
        role_value = actor
    if not entry.get("source_quote"):
        st.warning("No exact source quotation is attached. This rule cannot be approved.")
        return
    st.info(f"Source quotation: “{entry['source_quote']}”")
    if not entry.get("source_quote_verified"):
        st.warning("Quotation location has not been verified on the selected page.")
        if st.button("Verify quotation location", key=f"verify_rule_quote_{entry['id']}"):
            try:
                verify_source_quote_location(
                    entry["id"], actor, actor_role=role_value,
                    principal=principal, brand_id=brand_id,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("Exact quotation located on the selected page.")
                st.rerun()
        return
    st.success(f"Exact quotation located on page {entry['source_page']}.")
    if not entry.get("source_support_confirmed"):
        st.warning("The quotation's support for this rule has not been confirmed.")
        with st.form(f"confirm_rule_support_{entry['id']}"):
            confirmed = st.checkbox(
                "I reviewed this quotation and confirm that it supports the proposed rule."
            )
            note = st.text_input("Reviewer note (optional)")
            if st.form_submit_button("Confirm rule source support"):
                if not confirmed:
                    st.error("Confirm the statement above before recording source support.")
                else:
                    try:
                        confirm_source_support(
                            entry["id"], actor, note, actor_role=role_value,
                            principal=principal, brand_id=brand_id,
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.success("Rule source support confirmation recorded.")
                        st.rerun()
    else:
        st.success(
            "Reviewer confirmed source support"
            + (f" ({entry['source_support_confirmed_by']})." if entry.get("source_support_confirmed_by") else ".")
        )
    if entry.get("document_id") and entry.get("source_page"):
        preview = get_original_page_preview(
            entry["document_id"], int(entry["source_page"]),
            principal=principal, brand_id=brand_id,
        )
        if preview:
            st.image(
                preview,
                caption=f"{entry.get('source_filename') or 'Source'} · page {entry['source_page']}",
                width="stretch",
            )


def render(principal: AuthenticatedPrincipal) -> None:
    inject_styles()
    brand_id = st.session_state.get("active_brand_id")
    try:
        membership = require_brand_membership(
            principal, brand_id, (BrandRole.OWNER, BrandRole.REVIEWER)
        )
    except PermissionError:
        st.warning("Only workspace Reviewers can classify or approve rules.")
        return
    role_value = membership["role"]
    page_header("Governance", "Rule Readiness Audit")
    st.write(
        "Classify each source-backed rule before it can affect a content review. "
        "Approval is a human decision; model suggestions never activate a rule."
    )
    st.info(
        "**CODE-CHECKABLE** rules use a supported deterministic predicate. "
        "**AI-ASSISTED** guidance is advisory and requires human review. "
        "**HUMAN JUDGEMENT** is stored for people and is not sent to automated enforcement. "
        "**UNCLEAR** proposals remain inactive until clarified."
    )
    if role_value not in {BrandRole.OWNER.value, BrandRole.REVIEWER.value}:
        st.warning("Only authenticated Reviewers can classify or approve rules.")
        return

    proposals = [
        item for item in get_rule_proposals(principal, brand_id)
        if item["category"] in RULE_CATEGORIES
    ]
    pending = [
        item for item in proposals
        if item["status"] in {"PENDING_APPROVAL", "NEEDS_CLARIFICATION", "UNRESOLVED"}
    ]
    if not pending:
        st.success("No rule proposals are waiting for readiness review.")

    for entry in pending:
        current = entry.get("rule_readiness_classification") or "UNCLEAR"
        title = f"Proposal #{entry['id']} · {entry['category'].replace('_', ' ').title()} · {entry['status'].replace('_', ' ').title()}"
        with st.expander(title, expanded=False):
            st.caption(
                f"Source: {entry.get('source_filename') or 'None'}"
                + (f" · page {entry['source_page']}" if entry.get("source_page") else "")
                + (f" · product {entry['product_name']}" if entry.get("product_name") else "")
                + (f" · market {entry['market']}" if entry.get("market") else "")
            )
            st.write(entry["content"])
            _render_source(
                entry, actor=principal.username, role_value=role_value,
                principal=principal, brand_id=brand_id,
            )
            for warning in entry.get("warnings", []):
                st.warning(warning)
            st.caption(f"Current method: {entry.get('rule_checking_method') or 'Not classified'}")

            classification = st.selectbox(
                "Readiness classification",
                ["UNCLEAR", "CODE_CHECKABLE", "AI_ASSISTED", "HUMAN_JUDGEMENT"],
                index=["UNCLEAR", "CODE_CHECKABLE", "AI_ASSISTED", "HUMAN_JUDGEMENT"].index(current),
                key=f"readiness_class_{entry['id']}",
            )
            clarification = st.text_area(
                "Clarification or reviewer note",
                value=entry.get("clarification_note") or "",
                key=f"readiness_note_{entry['id']}",
                help="Required for UNCLEAR items and when resolving an earlier clarification request.",
            )
            rule_type = None
            parameters: dict = {}
            scope: dict = {}
            severity = "MEDIUM"
            positive_tests: list[str] = []
            negative_tests: list[str] = []
            if classification == "CODE_CHECKABLE":
                rule_type = st.selectbox(
                    "Supported deterministic predicate",
                    [item.value for item in RuleType],
                    key=f"readiness_type_{entry['id']}",
                )
                defaults = _PREDICATE_DEFAULTS[rule_type]
                parameter_text = st.text_area(
                    "Predicate parameters (JSON)",
                    value=json.dumps(
                        entry.get("rule_parameters") or defaults["parameters"],
                        ensure_ascii=False,
                    ),
                    key=f"readiness_params_{entry['id']}",
                    help="Only documented parameters for the selected predicate are accepted.",
                )
                default_scope = {}
                if entry.get("market"):
                    default_scope["market"] = [entry["market"]]
                if entry.get("channel"):
                    default_scope["channel"] = [entry["channel"]]
                scope_text = st.text_area(
                    "Additional scope (JSON)",
                    value=json.dumps(entry.get("rule_scope") or default_scope),
                    key=f"readiness_scope_{entry['id']}",
                )
                severity = st.selectbox(
                    "Finding severity",
                    ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    index=["LOW", "MEDIUM", "HIGH", "CRITICAL"].index(
                        entry.get("rule_severity") or "MEDIUM"
                    ),
                    key=f"readiness_severity_{entry['id']}",
                )
                positive_text = st.text_area(
                    "Examples that should pass (one per line)",
                    value="\n".join(defaults["positive"]),
                    key=f"readiness_positive_{entry['id']}",
                )
                negative_text = st.text_area(
                    "Examples that should fail (one per line)",
                    value="\n".join(defaults["negative"]),
                    key=f"readiness_negative_{entry['id']}",
                )
                try:
                    parameters = json.loads(parameter_text)
                    scope = json.loads(scope_text)
                except json.JSONDecodeError:
                    st.error("Predicate parameters and scope must be valid JSON.")
                positive_tests = [value for value in positive_text.splitlines() if value.strip()]
                negative_tests = [value for value in negative_text.splitlines() if value.strip()]

            save_col, approve_col, reject_col = st.columns(3)
            if save_col.button("Save classification", key=f"save_readiness_{entry['id']}"):
                try:
                    save_rule_classification(
                        entry["id"], classification, role_value,
                        actor_role=role_value,
                        principal=principal, brand_id=brand_id,
                        rule_type=rule_type, parameters=parameters, scope=scope,
                        severity=severity, positive_tests=positive_tests,
                        negative_tests=negative_tests, clarification_note=clarification,
                    )
                except (ValueError, PermissionError, TypeError) as exc:
                    st.error(str(exc))
                else:
                    st.success("Classification saved to the proposal history.")
                    st.rerun()

            approve_col.button(
                "Approve classified rule",
                key=f"approve_readiness_{entry['id']}",
                disabled=(
                    entry.get("rule_readiness_classification") not in READINESS_CLASSES
                    or entry.get("rule_readiness_classification") == "UNCLEAR"
                    or not entry.get("source_quote_verified")
                    or not entry.get("source_support_confirmed")
                ),
                on_click=_approve,
                args=(entry["id"], principal, brand_id, role_value),
            )
            if reject_col.button("Reject proposal", key=f"reject_readiness_{entry['id']}"):
                try:
                    decide_knowledge_entry(
                        entry["id"], "REJECT", principal.username,
                        actor_role=role_value, principal=principal, brand_id=brand_id,
                    )
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.info("Proposal rejected and retained in its source history.")
                    st.rerun()

    with st.expander("Approved readiness decisions"):
        for item in proposals:
            if item["status"] != "APPROVED":
                continue
            st.markdown(
                f"**Proposal #{item['id']} · {item['rule_readiness_classification']}** — "
                f"{item['rule_checking_method'] or 'No automated method'}"
            )
            st.caption(
                f"Linked record: {item['linked_record_type'] or 'none'} "
                f"#{item['linked_record_id'] or ''} · source {item['source_filename'] or 'unavailable'}"
            )


def _approve(entry_id: int, principal: AuthenticatedPrincipal, brand_id: int, actor: str) -> None:
    try:
        result = approve_rule_proposal(
            entry_id, actor, actor_role=actor, principal=principal, brand_id=brand_id
        )
    except (ValueError, PermissionError, TypeError, json.JSONDecodeError) as exc:
        st.error(str(exc))
    else:
        st.success(
            f"Approved as {result['classification']}; linked to "
            f"{result['linked_record_type']} #{result['linked_record_id']}."
        )
        st.rerun()
