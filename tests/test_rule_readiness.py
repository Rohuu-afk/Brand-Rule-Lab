from __future__ import annotations

import json

import pytest

from db.connection import apply_schema, get_connection
from services.knowledge_onboarding import (
    add_manual_entry,
    confirm_source_support as _confirm_source_support,
    create_onboarding_product,
    edit_pending_entry as _edit_pending_entry,
    save_source_document,
    verify_source_quote_location as _verify_source_quote_location,
)
from services.review_service import run_review
from services.rule_readiness import (
    approve_rule_proposal as _approve_rule_proposal,
    get_rule_proposals,
    save_rule_classification as _save_rule_classification,
    validate_predicate,
)


def confirm_source_support(entry_id, actor, note=""):
    return _confirm_source_support(entry_id, actor, note, actor_role=actor)


def edit_pending_entry(*args, **kwargs):
    actor = args[5] if len(args) > 5 else kwargs["actor"]
    kwargs.setdefault("actor_role", actor)
    return _edit_pending_entry(*args, **kwargs)


def verify_source_quote_location(entry_id, actor):
    return _verify_source_quote_location(entry_id, actor, actor_role=actor)


def save_rule_classification(entry_id, classification, actor, **kwargs):
    return _save_rule_classification(
        entry_id, classification, actor, actor_role=actor, **kwargs
    )


def approve_rule_proposal(entry_id, actor):
    return _approve_rule_proposal(entry_id, actor, actor_role=actor)


@pytest.fixture(autouse=True)
def isolated_rule_database(tmp_path, monkeypatch):
    database = tmp_path / "rule-readiness.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("UPLOADS_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("BRL_ENABLE_LEGACY_TEST_ACTORS", "1")
    apply_schema()
    yield database


def _source_proposal(
    category: str = "BRAND_RULE",
    quote: str = "Never describe results as a miracle.",
    source: bool = True,
) -> tuple[int, int]:
    product_id = create_onboarding_product(
        "Test Cleanser", "Test Brand", "Fictional test product", "IN"
    )
    document_id = None
    page_number = None
    if source:
        document_id = save_source_document(
            "test-rules.txt",
            (quote + "\n").encode(),
            "Brand guidelines",
            "Rule readiness tests",
            "test user",
            product_id=product_id,
            market="IN",
        )
        page_number = 1
    entry_id = add_manual_entry(
        category,
        quote,
        "Rule proposal for isolated testing.",
        product_id=product_id,
        market="IN",
        document_id=document_id,
        source_page=page_number,
        source_quote=quote if source else None,
    )
    if source:
        verify_source_quote_location(entry_id, "Reviewer")
        confirm_source_support(entry_id, "Reviewer")
    return entry_id, product_id


def _save_code_rule(entry_id: int, clarification: str = "") -> None:
    save_rule_classification(
        entry_id,
        "CODE_CHECKABLE",
        "Reviewer",
        rule_type="term_list",
        parameters={"mode": "banned", "terms": ["miracle"]},
        scope={"market": ["IN"]},
        severity="HIGH",
        positive_tests=["This cleanser supports a fresh feel."],
        negative_tests=["This is a miracle cleanser."],
        clarification_note=clarification,
    )


def _draft(product_id: int, text: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO drafts (title, product_id, market, channel) VALUES (?, ?, 'IN', 'website')",
            ("Readiness test", product_id),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _review(draft_id: int, product_id: int, text: str) -> dict:
    return run_review(
        draft_id, text, "paste", {}, product_id, "IN", "website", None,
        ai_provider=None, ai_consent=False,
    )


def test_unapproved_rule_proposal_is_not_enforced_until_human_approval():
    entry_id, product_id = _source_proposal()
    _save_code_rule(entry_id)
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM brand_rules").fetchone()[0] == 0
        entry = conn.execute(
            "SELECT status, rule_readiness_classification FROM knowledge_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        assert tuple(entry) == ("PENDING_APPROVAL", "CODE_CHECKABLE")

    draft_id = _draft(product_id, "This is a miracle cleanser.")
    before = _review(draft_id, product_id, "This is a miracle cleanser.")
    assert not any(item["finding_type"] == "rule_check" for item in before["findings"])

    approved = approve_rule_proposal(entry_id, "Reviewer")
    assert approved["classification"] == "CODE_CHECKABLE"
    after = _review(draft_id, product_id, "This is a miracle cleanser.")
    assert any(
        item["finding_type"] == "rule_check" and item["result"] == "FAIL"
        for item in after["findings"]
    )


def test_rule_proposals_return_warnings_as_a_list():
    entry_id, _ = _source_proposal()
    proposal = next(item for item in get_rule_proposals() if item["id"] == entry_id)
    assert proposal["warnings"] == []


def test_rule_actor_name_does_not_authorize_without_explicit_role():
    entry_id, _ = _source_proposal()
    with pytest.raises(PermissionError, match="Reviewer"):
        _approve_rule_proposal(entry_id, "Reviewer")
    with pytest.raises(PermissionError, match="Reviewer"):
        _save_rule_classification(entry_id, "AI_ASSISTED", "Reviewer")
    with get_connection() as conn:
        proposal = conn.execute(
            "SELECT status, rule_readiness_classification FROM knowledge_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
    assert tuple(proposal) == ("PENDING_APPROVAL", "UNCLEAR")


def test_source_less_rule_cannot_be_approved_or_enforced():
    entry_id, _ = _source_proposal(source=False)
    _save_code_rule(entry_id)
    with pytest.raises(ValueError, match="source document, page, and exact quotation"):
        approve_rule_proposal(entry_id, "Reviewer")
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM brand_rules").fetchone()[0] == 0
        assert conn.execute(
            "SELECT status FROM knowledge_entries WHERE id = ?", (entry_id,)
        ).fetchone()["status"] == "PENDING_APPROVAL"


def test_rule_quote_location_does_not_replace_explicit_support_confirmation():
    quote = "Never describe results as a miracle."
    product_id = create_onboarding_product("Unconfirmed Rule", "Rule Brand", market="IN")
    document_id = save_source_document(
        "unconfirmed-rule.txt", (quote + "\n").encode(), "Brand guidelines",
        "Rule provenance test", "test user", product_id=product_id, market="IN",
    )
    entry_id = add_manual_entry(
        "BRAND_RULE", quote, product_id=product_id, market="IN",
        document_id=document_id, source_page=1, source_quote=quote,
    )
    save_rule_classification(
        entry_id, "AI_ASSISTED", "Reviewer"
    )
    with get_connection() as conn:
        state = conn.execute(
            "SELECT source_quote_verified, source_support_confirmed "
            "FROM knowledge_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
    assert tuple(state) == (1, 0)

    with pytest.raises(ValueError, match="reviewer must confirm"):
        approve_rule_proposal(entry_id, "Reviewer")
    confirm_source_support(entry_id, "Reviewer", "The quote directly states this restriction.")
    assert approve_rule_proposal(entry_id, "Reviewer")["classification"] == "AI_ASSISTED"


def test_editing_rule_proposal_resets_readiness_and_source_provenance():
    entry_id, product_id = _source_proposal()
    _save_code_rule(entry_id)
    edit_pending_entry(
        entry_id, "Never describe an outcome as guaranteed.", "Edited restriction.",
        product_id, "US", "Reviewer",
    )
    with get_connection() as conn:
        entry = conn.execute(
            "SELECT rule_readiness_classification, rule_type, source_page, source_quote, "
            "source_quote_verified, source_support_confirmed "
            "FROM knowledge_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        versions = conn.execute(
            "SELECT content, source_quote, source_page, source_quote_verified, "
            "source_support_confirmed FROM knowledge_entry_versions "
            "WHERE entry_id = ? ORDER BY version_number",
            (entry_id,),
        ).fetchall()

    assert entry["rule_readiness_classification"] == "UNCLEAR"
    assert entry["rule_type"] is None
    assert (entry["source_page"], entry["source_quote"]) == (None, None)
    assert (entry["source_quote_verified"], entry["source_support_confirmed"]) == (0, 0)
    assert versions[0]["source_quote"] == "Never describe results as a miracle."
    assert versions[0]["source_page"] == "1"
    assert (versions[0]["source_quote_verified"], versions[0]["source_support_confirmed"]) == (1, 1)
    assert versions[1]["content"] == "Never describe an outcome as guaranteed."
    assert versions[1]["source_quote"] is None
    assert (versions[1]["source_quote_verified"], versions[1]["source_support_confirmed"]) == (0, 0)
    with pytest.raises(ValueError, match="Clarify and reclassify"):
        approve_rule_proposal(entry_id, "Reviewer")


def test_generic_knowledge_approval_cannot_bypass_rule_readiness():
    entry_id, _ = _source_proposal()
    from services.knowledge_onboarding import decide_knowledge_entry

    with pytest.raises(ValueError, match="Rule Readiness"):
        decide_knowledge_entry(entry_id, "APPROVE", "Reviewer", actor_role="Reviewer")


def test_unknown_predicates_and_parameters_are_rejected():
    with pytest.raises(ValueError, match="Unsupported deterministic predicate"):
        validate_predicate("python", {"code": "pass"})
    with pytest.raises(ValueError, match="Unsupported parameter"):
        validate_predicate(
            "term_list",
            {"mode": "banned", "terms": ["miracle"], "execute": "unsafe"},
        )


def test_regex_readiness_rejects_patterns_over_the_configured_length_limit():
    with pytest.raises(ValueError, match="character limit"):
        validate_predicate("regex", {"pattern": "a" * 513})


def test_regex_execution_is_bounded_and_reports_errors_instead_of_passes():
    from engines.rule_engine import MAX_REGEX_INPUT_CHARS, run_rule

    base = {
        "rule_type": "regex",
        "is_active": 1,
        "owner_approved": 1,
        "parameters": {"pattern": r"\bbrand\b", "require_match": True},
    }
    assert run_rule(base, "brand guidance").result.value == "PASS"

    invalid = {**base, "parameters": {"pattern": "(", "require_match": True}}
    assert run_rule(invalid, "brand").result.value == "ERROR"

    long_pattern = {
        **base,
        "parameters": {"pattern": "a" * 513, "require_match": True},
    }
    assert run_rule(long_pattern, "brand").result.value == "ERROR"

    large_input = run_rule(base, "x" * (MAX_REGEX_INPUT_CHARS + 1))
    assert large_input.result.value == "ERROR"
    assert "input exceeds" in large_input.explanation.lower()

    pathological = {
        **base,
        "parameters": {"pattern": r"(a|aa)+$", "require_match": True},
    }
    timed_out = run_rule(pathological, "a" * 10000 + "!")
    assert timed_out.result.value == "ERROR"
    assert "timeout" in timed_out.explanation.lower()


def test_regex_aggregate_budget_exhaustion_is_an_error_not_a_pass():
    from engines.rule_engine import RegexExecutionBudget, run_rule

    conditional = {
        "rule_type": "required_text",
        "is_active": 1,
        "owner_approved": 1,
        "parameters": {
            "trigger_on": "acne",
            "pattern": "approved wording",
            "require_match": True,
        },
    }
    result = run_rule(
        conditional, "acne copy", regex_budget=RegexExecutionBudget(max_calls=1)
    )
    assert result.result.value == "ERROR"
    assert "aggregate regex execution budget" in result.explanation.lower()
    with pytest.raises(ValueError, match="exactly one capture group"):
        validate_predicate(
            "numeric_range",
            {"extract_pattern": r"(\d+)(\.\d+)", "min": 0, "max": 10},
        )


@pytest.mark.parametrize(
    ("classification", "expected_guideline"),
    [("AI_ASSISTED", True), ("HUMAN_JUDGEMENT", False)],
)
def test_non_code_classes_are_never_compiled_as_deterministic_rules(
    classification, expected_guideline
):
    entry_id, product_id = _source_proposal(category="TONE")
    save_rule_classification(entry_id, classification, "Reviewer")
    result = approve_rule_proposal(entry_id, "Reviewer")
    assert result["classification"] == classification
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM brand_rules").fetchone()[0] == 0
        status = conn.execute(
            "SELECT status, linked_record_type FROM knowledge_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        assert tuple(status) == ("APPROVED", result["linked_record_type"])
        guideline_count = conn.execute(
            "SELECT COUNT(*) FROM guidelines WHERE readiness_class = 'AI_ASSISTED'"
        ).fetchone()[0]
    assert bool(guideline_count) is expected_guideline


def test_unclear_rule_needs_written_clarification_and_reclassification():
    entry_id, _ = _source_proposal()
    save_rule_classification(
        entry_id, "UNCLEAR", "Reviewer",
        clarification_note="Does this apply to consumer copy or internal training?",
    )
    with pytest.raises(ValueError, match="Clarify and reclassify"):
        approve_rule_proposal(entry_id, "Reviewer")
    with pytest.raises(ValueError, match="Record how the requested clarification"):
        _save_code_rule(entry_id)
    _save_code_rule(
        entry_id,
        clarification="Brand owner confirmed this prohibition applies to consumer copy.",
    )
    assert approve_rule_proposal(entry_id, "Reviewer")["classification"] == "CODE_CHECKABLE"


def test_approved_rule_provenance_and_history_survive_schema_reapplication(
    isolated_rule_database,
):
    entry_id, _ = _source_proposal()
    _save_code_rule(entry_id)
    result = approve_rule_proposal(entry_id, "Reviewer")
    assert result["linked_record_type"] == "brand_rule"
    with get_connection() as conn:
        rule = conn.execute(
            "SELECT source_document_id, source_page, source_quote, readiness_class, "
            "is_active, owner_approved FROM brand_rules WHERE id = ?",
            (result["linked_record_id"],),
        ).fetchone()
        assert rule["source_document_id"] is not None
        assert rule["source_page"] == "1"
        assert rule["source_quote"] == "Never describe results as a miracle."
        assert rule["readiness_class"] == "CODE_CHECKABLE"
        assert (rule["is_active"], rule["owner_approved"]) == (1, 1)
        assert conn.execute(
            "SELECT event_type FROM brand_rule_history WHERE rule_id = ?",
            (result["linked_record_id"],),
        ).fetchone()["event_type"] == "APPROVED"
        assert [row["version"] for row in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )] == list(range(1, 18))

    apply_schema()
    with get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM brand_rule_history WHERE rule_id = ?",
            (result["linked_record_id"],),
        ).fetchone()[0] == 1
        history = conn.execute(
            "SELECT source_quote FROM rule_readiness_history "
            "WHERE entry_id = ? AND event_type = 'APPROVED'",
            (entry_id,),
        ).fetchone()
        assert history["source_quote"] == "Never describe results as a miracle."
        with pytest.raises(Exception, match="append-only"):
            conn.execute(
                "UPDATE brand_rule_history SET actor = 'tampered' WHERE rule_id = ?",
                (result["linked_record_id"],),
            )


def test_guideline_activation_failure_rolls_back_rule_proposal_and_history():
    entry_id, _ = _source_proposal(category="TONE")
    save_rule_classification(entry_id, "AI_ASSISTED", "Reviewer")
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TRIGGER fail_knowledge_approval
            BEFORE INSERT ON knowledge_decisions
            WHEN NEW.entry_id = %d AND NEW.decision = 'APPROVE'
            BEGIN SELECT RAISE(ABORT, 'injected decision-write failure'); END
            """ % entry_id
        )
        conn.commit()

    with pytest.raises(Exception, match="injected decision-write failure"):
        approve_rule_proposal(entry_id, "Reviewer")

    with get_connection() as conn:
        proposal = conn.execute(
            "SELECT status, linked_record_id FROM knowledge_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        guideline_count = conn.execute(
            "SELECT COUNT(*) FROM guidelines WHERE title LIKE 'Approved AI-assisted rule%'"
        ).fetchone()[0]
        approved_events = conn.execute(
            "SELECT COUNT(*) FROM rule_readiness_history "
            "WHERE entry_id = ? AND event_type = 'APPROVED'",
            (entry_id,),
        ).fetchone()[0]
    assert tuple(proposal) == ("PENDING_APPROVAL", None)
    assert guideline_count == 0
    assert approved_events == 0


def test_wcag_and_asset_hash_self_tests_use_sample_inputs():
    from engines.rule_engine import run_self_tests

    contrast = {
        "rule_type": "wcag_contrast",
        "parameters": {"foreground": "#000000", "background": "#FFFFFF", "min_ratio": 4.5},
        "positive_tests": json.dumps(["#000000|#FFFFFF"]),
        "negative_tests": json.dumps(["#777777|#FFFFFF"]),
        "is_active": 1,
        "owner_approved": 1,
    }
    results = run_self_tests(contrast)
    assert [item["passed"] for item in results] == [True, True]

    asset = {
        "rule_type": "asset_hash",
        "parameters": {"approved_hash": "0" * 64, "hash_type": "sha256"},
        "positive_tests": json.dumps(["0" * 64]),
        "negative_tests": json.dumps(["1" * 64]),
        "is_active": 1,
        "owner_approved": 1,
    }
    results = run_self_tests(asset)
    assert [item["passed"] for item in results] == [True, True]
