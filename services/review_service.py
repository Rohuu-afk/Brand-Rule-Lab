"""
services/review_service.py
--------------------------
Orchestrates the full review pipeline:
  1. Store draft text as a draft_version
  2. Call AI to extract claim spans
  3. Run deterministic rule engine on each claim (and full text)
  4. Call AI for tone/contextual findings
  5. Call AI to rank eligible fact versions per claim
  6. Persist all findings to the database
  7. Update draft status to CHECKED
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from decimal import Decimal
from typing import Any

from ai.base import AIProviderError
from db.connection import get_connection
from models.enums import DraftStatus, FindingResult, FindingStatus, Severity, EvidenceRelationship, UserRole
from models.schemas import (
    FACT_RANKING_SCHEMA,
    TONE_FINDINGS_SCHEMA,
    validate_ai_response,
    validate_claim_extraction_response,
)
from ai.prompts import (
    MAX_TONE_DRAFT_CHARS,
    build_claim_extraction_prompt,
    build_tone_assessment_prompt,
    build_fact_ranking_prompt,
)
from ai.base import safe_error_summary
from engines.rule_engine import RegexExecutionBudget, run_rule
from services.fact_service import get_eligible_fact_versions
from services.guideline_service import get_all_products, get_guidelines_summary
from services.asset_service import get_approved_asset
from services.claim_extraction import build_claim_candidates

logger = logging.getLogger("brand_rule_lab.review")
MAX_AI_REVIEW_CHARS = 30000


class _AIConsentNotGranted(RuntimeError):
    """Raised locally when a review attempts AI work without user consent."""


class _AIReviewTooLarge(RuntimeError):
    """Raised locally when a draft exceeds the bounded claim-extraction input size."""


def _generate_review_response(provider: Any, ai_consent: bool, prompt: str, **kwargs: Any) -> str:
    if not ai_consent:
        raise _AIConsentNotGranted
    return provider.generate_text(prompt, **kwargs)


def _ai_operation_label(provider: Any) -> str:
    provider_name = (
        getattr(provider, "last_provider_used", None)
        or getattr(provider, "provider_name", None)
        or provider.__class__.__name__.removesuffix("Provider")
    )
    model_name = getattr(provider, "last_model_used", None)
    return f"{provider_name} / {model_name}" if model_name else str(provider_name)


_TYPOGRAPHIC_TRANSLATION = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u02bc": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-",
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
})


def _normalized_text_map(text: str) -> tuple[str, list[int], list[int]]:
    """Normalize typography/whitespace while mapping each output char to source indices."""
    normalized_chars: list[str] = []
    source_starts: list[int] = []
    source_ends: list[int] = []
    index = 0

    while index < len(text):
        cluster_start = index
        index += 1
        while index < len(text) and unicodedata.combining(text[index]):
            index += 1
        normalized_cluster = unicodedata.normalize("NFKC", text[cluster_start:index])
        normalized_cluster = normalized_cluster.casefold().translate(_TYPOGRAPHIC_TRANSLATION)
        for char in normalized_cluster:
            if char.isspace():
                if normalized_chars and normalized_chars[-1] == " ":
                    source_ends[-1] = index
                    continue
                char = " "
            normalized_chars.append(char)
            source_starts.append(cluster_start)
            source_ends.append(index)

    return "".join(normalized_chars), source_starts, source_ends


def _all_exact_occurrences(text: str, quote: str) -> list[tuple[int, int]]:
    matches: list[tuple[int, int]] = []
    offset = 0
    while quote and (start := text.find(quote, offset)) >= 0:
        matches.append((start, start + len(quote)))
        offset = start + 1
    return matches


def _normalized_occurrences(text: str, quote: str) -> list[tuple[int, int]]:
    source, starts, ends = _normalized_text_map(text)
    normalized_quote, _, _ = _normalized_text_map(quote)
    matches: list[tuple[int, int]] = []
    offset = 0
    while normalized_quote and (found := source.find(normalized_quote, offset)) >= 0:
        last = found + len(normalized_quote) - 1
        if found < len(starts) and last < len(ends):
            matches.append((starts[found], ends[last]))
        offset = found + 1
    return list(dict.fromkeys(matches))


def _strip_quote_wrapper(quote: str) -> str:
    pairs = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019"}
    stripped = quote.strip()
    if len(stripped) >= 2 and pairs.get(stripped[0]) == stripped[-1]:
        return stripped[1:-1]
    return stripped


def _context_matches(
    source_text: str,
    span: tuple[int, int],
    before: str,
    after: str,
) -> bool:
    start, end = span
    if before:
        source_before = source_text[max(0, start - len(before) - 16):start]
        if not _normalized_text_map(source_before)[0].endswith(_normalized_text_map(before)[0]):
            return False
    if after:
        source_after = source_text[end:end + len(after) + 16]
        if not _normalized_text_map(source_after)[0].startswith(_normalized_text_map(after)[0]):
            return False
    return True


def _resolve_claim_span(source_text: str, claim: dict) -> tuple[dict | None, str | None]:
    """Resolve a model quote locally; never trust generated offsets as the source of truth."""
    model_quote = claim.get("claim_text", "")
    matches = list(dict.fromkeys(
        _all_exact_occurrences(source_text, model_quote)
        + _normalized_occurrences(source_text, model_quote)
    ))
    if not matches:
        unwrapped_quote = _strip_quote_wrapper(model_quote)
        if unwrapped_quote != model_quote:
            matches = list(dict.fromkeys(
                _all_exact_occurrences(source_text, unwrapped_quote)
                + _normalized_occurrences(source_text, unwrapped_quote)
            ))

    before = claim.get("source_context_before", "")
    after = claim.get("source_context_after", "")
    if before or after:
        matches = [
            span for span in matches
            if _context_matches(source_text, span, before, after)
        ]

    if not matches:
        return None, "The extracted wording could not be found in the original draft."
    if len(matches) != 1:
        return None, "The extracted wording occurs more than once and its source location is ambiguous."

    start, end = matches[0]
    resolved = dict(claim)
    resolved["claim_text"] = source_text[start:end]
    resolved["start_char"] = start
    resolved["end_char"] = end
    return resolved, None


class ReviewError(RuntimeError):
    """Raised when the review pipeline encounters a fatal error."""


_DURATION_RE = re.compile(
    r"\b(?P<value>\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s*[- ]?\s*(?P<unit>hours?|days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"(?<!\w)(\d+(?:\.\d+)?)\s*%")
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_INGREDIENT_PATTERNS = {
    "benzoyl peroxide": re.compile(r"\b(?:bpo|benzoyl\s+peroxide)\b", re.IGNORECASE),
    "vitamin c": re.compile(r"\b(?:vitamin[\s-]*c|l?[-\s]*ascorbic\s+acid)\b", re.IGNORECASE),
    "salicylic acid": re.compile(r"\b(?:salicylic\s+acid|bha)\b", re.IGNORECASE),
    "niacinamide": re.compile(r"\bniacinamide\b", re.IGNORECASE),
    "retinol": re.compile(r"\bretinol\b", re.IGNORECASE),
}
_INGREDIENT_SENTENCE_SPLIT_RE = re.compile(r"[;!?]|\.(?=\s|$)")
_INGREDIENT_ITEM_SPLIT_RE = re.compile(r"(,|\band\b|&|\+|/)", re.IGNORECASE)
_PERCENT_BEFORE_INGREDIENT_GAP_RE = re.compile(
    r"\s*(?:(?:of|active(?:\s+ingredient)?|pure|the|stabili[sz]ed|w/w|w/v|v/v|"
    r"concentration(?:\s+of)?|strength(?:\s+of)?)\s*)*",
    re.IGNORECASE,
)
_PERCENT_AFTER_INGREDIENT_GAP_RE = re.compile(
    r"\s*[([]?\s*(?:(?:at\s+(?:a\s+)?(?:active\s+)?"
    r"(?:concentration|strength)(?:\s+of)?|(?:at|is|(?:active\s+)?concentration|"
    r"(?:active\s+)?strength|content|level)(?:\s+of)?)\s*)?[\s:=]*",
    re.IGNORECASE,
)
_EFFECT_PATTERNS = {
    "blemishes": re.compile(r"\b(?:acne|blemish\w*|breakout\w*|pimple\w*|spots?)\b", re.IGNORECASE),
    "sebum": re.compile(r"\b(?:sebum|oiliness|oily)\b", re.IGNORECASE),
    "skin clarity": re.compile(r"\b(?:clear(?:s|ing)?\s+skin|skin\s+clarity)\b", re.IGNORECASE),
}
_EFFICACY_ACTION_PATTERNS = {
    "prevent": re.compile(r"\bprevent\w*\b", re.IGNORECASE),
    "cure": re.compile(r"\bcure\w*\b", re.IGNORECASE),
    "eliminate": re.compile(r"\beliminat\w*\b", re.IGNORECASE),
    "treat": re.compile(r"\btreat\w*\b", re.IGNORECASE),
    "reduce": re.compile(r"\breduc\w*\b", re.IGNORECASE),
    "improve": re.compile(r"\bimprov\w*\b", re.IGNORECASE),
    "clear": re.compile(r"\bclear(?:s|ed|ing)?\b", re.IGNORECASE),
}
_UNIVERSAL_SCOPE_RE = re.compile(
    r"\b(?:all|every|everyone|everybody|100\s*%|without exception)\b",
    re.IGNORECASE,
)
_LIMITED_SCOPE_RE = re.compile(
    r"\b(?:some|certain|a subset of|study participants|participants|subjects)\b",
    re.IGNORECASE,
)
_CONDITION_RE = re.compile(
    r"\b(?:when|if|only when|provided that|while using|used with)\b[^,.;!?]*",
    re.IGNORECASE,
)


def _duration_in_days(text: str) -> float | None:
    """Convert a stated duration to days for like-for-like evidence checks."""
    match = _DURATION_RE.search(text)
    if not match:
        return None

    raw_value = match.group("value").lower()
    value = float(_NUMBER_WORDS[raw_value]) if raw_value in _NUMBER_WORDS else float(raw_value)
    unit = match.group("unit").lower().rstrip("s")
    factors = {"hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}
    return value * factors[unit]


def _fact_explicitly_excludes_earlier_results(text: str) -> bool:
    duration_match = _DURATION_RE.search(text)
    if not duration_match:
        return False

    before_duration = text[max(0, duration_match.start() - 80):duration_match.start()]
    return bool(
        re.search(
            r"\b(?:only\s+after|not\s+until|no\s+earlier\s+than|not\s+before)\s*$"
            r"|\bno\s+(?:measurable\s+)?(?:results?|improvement|benefit|reduction)\b"
            r".{0,40}\bbefore\s*$"
            r"|\bnot\s+(?:seen|observed|reported|noticed)\s+before\s*$",
            before_duration,
            re.IGNORECASE,
        )
    )


def _effect_concepts(text: str) -> set[str]:
    return {
        concept
        for concept, pattern in _EFFECT_PATTERNS.items()
        if pattern.search(text)
    }


def _efficacy_action(text: str) -> str | None:
    return next(
        (action for action, pattern in _EFFICACY_ACTION_PATTERNS.items() if pattern.search(text)),
        None,
    )


def _efficacy_strength(text: str) -> int:
    """Return a conservative claim-strength rank; larger values are stronger."""
    if re.search(
        r"\b(?:completely|totally|guarantee\w*|cure\w*|eliminat\w*|permanent\w*|forever|never again)\b",
        text,
        re.IGNORECASE,
    ):
        return 3
    if re.search(
        r"\b(?:may|might|can|could|helps?|support\w*|some users?)\b",
        text,
        re.IGNORECASE,
    ):
        return 1
    return 2


def _efficacy_scope_compatible(claim_text: str, fact_text: str) -> bool:
    if _UNIVERSAL_SCOPE_RE.search(claim_text) and not _UNIVERSAL_SCOPE_RE.search(fact_text):
        return False
    if _LIMITED_SCOPE_RE.search(fact_text) and not _LIMITED_SCOPE_RE.search(claim_text):
        return False

    fact_conditions = [
        " ".join(re.findall(r"[a-z0-9]+", match.group().casefold()))
        for match in _CONDITION_RE.finditer(fact_text)
    ]
    claim_normalized = " ".join(re.findall(r"[a-z0-9]+", claim_text.casefold()))
    return all(condition in claim_normalized for condition in fact_conditions)


def _efficacy_profile_compatible(claim_text: str, fact_text: str) -> bool:
    """Require matching outcomes and no stronger scope or qualification in the claim."""
    claim_effects = _effect_concepts(claim_text)
    fact_effects = _effect_concepts(fact_text)
    if not claim_effects or not claim_effects.intersection(fact_effects):
        return False
    claim_action = _efficacy_action(claim_text)
    fact_action = _efficacy_action(fact_text)
    if not claim_action or claim_action != fact_action:
        return False
    if _efficacy_strength(claim_text) > _efficacy_strength(fact_text):
        return False
    return _efficacy_scope_compatible(claim_text, fact_text)


def _ingredient_concepts(text: str) -> set[str]:
    return {
        concept
        for concept, pattern in _INGREDIENT_PATTERNS.items()
        if pattern.search(text)
    }


def _ingredient_concentration_associations(
    text: str,
) -> tuple[dict[str, set[Decimal]], set[str], int]:
    concentrations: dict[str, set[Decimal]] = {}
    ambiguous_concepts: set[str] = set()
    unassociated_percentages = 0

    for sentence in _INGREDIENT_SENTENCE_SPLIT_RE.split(text):
        split_items = _INGREDIENT_ITEM_SPLIT_RE.split(sentence)
        items: list[dict[str, Any]] = []
        sentence_concepts: set[str] = set()

        for item_index in range(0, len(split_items), 2):
            item_text = split_items[item_index]
            percentage_matches = list(_PERCENT_RE.finditer(item_text))
            ingredient_occurrences = [
                (match.start(), match.end(), concept)
                for concept, pattern in _INGREDIENT_PATTERNS.items()
                for match in pattern.finditer(item_text)
            ]
            item_concepts = {concept for _, _, concept in ingredient_occurrences}
            sentence_concepts.update(item_concepts)
            separator_before = split_items[item_index - 1] if item_index else ""
            items.append({
                "text": item_text,
                "separator_before": separator_before.casefold(),
                "percentages": percentage_matches,
                "occurrences": ingredient_occurrences,
                "concepts": item_concepts,
            })

            if not percentage_matches:
                continue
            if not item_concepts:
                unassociated_percentages += len(percentage_matches)
                continue
            if len(percentage_matches) != 1 or len(item_concepts) != 1:
                ambiguous_concepts.update(item_concepts)
                unassociated_percentages += len(percentage_matches)
                continue

            percentage_match = percentage_matches[0]
            associated_concepts: set[str] = set()
            for occurrence_start, occurrence_end, concept in ingredient_occurrences:
                if percentage_match.end() <= occurrence_start:
                    gap = item_text[percentage_match.end():occurrence_start]
                    if _PERCENT_BEFORE_INGREDIENT_GAP_RE.fullmatch(gap):
                        associated_concepts.add(concept)
                elif occurrence_end <= percentage_match.start():
                    gap = item_text[occurrence_end:percentage_match.start()]
                    if _PERCENT_AFTER_INGREDIENT_GAP_RE.fullmatch(gap):
                        associated_concepts.add(concept)

            if len(associated_concepts) != 1:
                ambiguous_concepts.update(item_concepts)
                unassociated_percentages += 1
                continue

            concept = next(iter(associated_concepts))
            concentrations.setdefault(concept, set()).add(
                Decimal(percentage_match.group(1))
            )

        if any(item["percentages"] and not item["concepts"] for item in items):
            ambiguous_concepts.update(sentence_concepts)

        for previous_item, current_item in zip(items, items[1:]):
            if (
                current_item["separator_before"] in {"and", "&"}
                and previous_item["concepts"]
                and not previous_item["percentages"]
                and current_item["concepts"]
                and current_item["percentages"]
                and min(start for start, _, _ in current_item["occurrences"])
                < current_item["percentages"][0].start()
            ):
                ambiguous_concepts.update(previous_item["concepts"])
                ambiguous_concepts.update(current_item["concepts"])

    return concentrations, ambiguous_concepts, unassociated_percentages


def _ingredient_match(claim_text: str, fact: dict) -> bool:
    """Require each stated concentration to match its specific ingredient."""
    fact_text = fact.get("statement", "")
    claim_concepts = _ingredient_concepts(claim_text)
    fact_concepts = _ingredient_concepts(fact_text)
    if not claim_concepts or not claim_concepts.intersection(fact_concepts):
        return False

    if not _PERCENT_RE.search(claim_text):
        return True

    claim_concentrations, claim_ambiguities, unassociated_claim_percentages = (
        _ingredient_concentration_associations(claim_text)
    )
    if unassociated_claim_percentages or claim_concepts.intersection(claim_ambiguities):
        return False

    fact_concentrations, fact_ambiguities, _ = _ingredient_concentration_associations(
        fact_text
    )
    for concept in claim_concepts:
        requested_values = claim_concentrations.get(concept)
        if not requested_values:
            continue
        approved_values = fact_concentrations.get(concept, set())
        if (
            concept in fact_ambiguities
            or len(requested_values) != 1
            or len(approved_values) != 1
            or requested_values != approved_values
        ):
            return False
    return True


def _fact_relationship_for_claim(
    claim_text: str,
    fact: dict,
    *,
    selected_product_id: int | None = None,
    all_products: list[dict] | None = None,
) -> str | None:
    """Return a relationship only when the fact directly demonstrates it."""
    other_product = _explicitly_named_other_product(
        claim_text, selected_product_id, all_products or []
    )
    if other_product:
        return EvidenceRelationship.NO_MATCH.value
    direct_relationship = _exact_fact_statement_relationship(claim_text, fact)
    if direct_relationship:
        return direct_relationship

    fact_text = fact.get("statement", "")
    claim_duration = _duration_in_days(claim_text)
    fact_duration = _duration_in_days(fact_text)
    shared_effects = _effect_concepts(claim_text).intersection(_effect_concepts(fact_text))

    if _ingredient_concepts(claim_text):
        if _ingredient_match(claim_text, fact):
            return EvidenceRelationship.SUPPORTS.value
        return EvidenceRelationship.RELATED_NOT_EVIDENCE.value if _fact_is_related_to_claim(
            claim_text, fact
        ) else None
    if shared_effects and _efficacy_profile_compatible(claim_text, fact_text):
        if claim_duration == fact_duration:
            return EvidenceRelationship.SUPPORTS.value
        if (
            claim_duration is not None
            and fact_duration is not None
            and claim_duration < fact_duration
            and _fact_explicitly_excludes_earlier_results(fact_text)
        ):
            return EvidenceRelationship.CONTRADICTS.value
        return EvidenceRelationship.RELATED_NOT_EVIDENCE.value
    if shared_effects or _fact_is_related_to_claim(claim_text, fact):
        return EvidenceRelationship.RELATED_NOT_EVIDENCE.value
    return None


def _exact_fact_statement_relationship(claim_text: str, fact: dict) -> str | None:
    """Match an exact approved statement, optionally introduced by the selected product name."""
    claim_words = " ".join(re.findall(r"[a-z0-9]+", claim_text.casefold()))
    fact_words = " ".join(re.findall(r"[a-z0-9]+", fact.get("statement", "").casefold()))
    if len(fact_words.split()) < 3:
        return None

    if claim_words == fact_words:
        return EvidenceRelationship.SUPPORTS.value

    product_words = " ".join(
        re.findall(r"[a-z0-9]+", fact.get("product_name", "").casefold())
    )
    if product_words and claim_words in {
        f"meet {product_words} {fact_words}",
        f"introducing {product_words} {fact_words}",
    }:
        return EvidenceRelationship.SUPPORTS.value

    return None


def _fact_is_related_to_claim(claim_text: str, fact: dict) -> bool:
    if _ingredient_concepts(claim_text) and fact.get("fact_category") == "efficacy_claim":
        return True
    claim_effects = _effect_concepts(claim_text)
    fact_effects = _effect_concepts(fact.get("statement", ""))
    if claim_effects.intersection(fact_effects):
        return True
    if (
        "skin clarity" in claim_effects | fact_effects
        and "blemishes" in claim_effects | fact_effects
        and claim_effects
        and fact_effects
    ):
        return True
    claim_terms = {
        term for term in re.findall(r"[a-z0-9]+", claim_text.lower()) if len(term) > 3
    }
    fact_terms = set(re.findall(r"[a-z0-9]+", fact.get("statement", "").lower()))
    return bool(claim_terms.intersection(fact_terms))


def _explicitly_named_other_product(
    claim_text: str,
    selected_product_id: int | None,
    products: list[dict],
) -> str | None:
    if selected_product_id is None:
        return None
    for product in products:
        if product.get("id") == selected_product_id:
            continue
        aliases = product.get("sku_aliases") or []
        if isinstance(aliases, str):
            try:
                aliases = json.loads(aliases)
            except json.JSONDecodeError:
                aliases = []
        labels = [product.get("name"), *(aliases if isinstance(aliases, list) else [])]
        for label in labels:
            if not isinstance(label, str) or len(label.strip()) < 3:
                continue
            if re.search(
                rf"(?<!\w){re.escape(label.strip())}(?!\w)",
                claim_text,
                re.IGNORECASE,
            ):
                return label.strip()
    return None


def _deterministic_fact_relationship(
    claim_text: str,
    claim_type_hint: str | None,
    eligible_facts: list[dict],
    *,
    selected_product_id: int | None = None,
    all_products: list[dict] | None = None,
) -> tuple[str, dict | None, str] | None:
    """Resolve high-confidence ingredient and timeline relationships without ranking guesses."""
    other_product = _explicitly_named_other_product(
        claim_text, selected_product_id, all_products or []
    )
    if other_product:
        return (
            EvidenceRelationship.NO_MATCH.value,
            None,
            f"The claim names {other_product}, which is not the selected product; "
            "approved evidence for the selected product was not linked.",
        )
    for fact in eligible_facts:
        relationship = _exact_fact_statement_relationship(claim_text, fact)
        if relationship:
            return (
                relationship,
                fact,
                f"The claim directly repeats approved evidence: "
                f"\"{fact['statement']}\" [{fact.get('evidence_ref', '')}].",
            )

    ingredient_claim = bool(_ingredient_concepts(claim_text)) or (
        claim_type_hint == "ingredient" and bool(_PERCENT_RE.search(claim_text))
    )
    if ingredient_claim:
        matching = [fact for fact in eligible_facts if _ingredient_match(claim_text, fact)]
        if matching:
            fact = matching[0]
            return (
                "SUPPORTS",
                fact,
                f"The approved ingredient evidence directly matches the stated ingredient and concentration: "
                f"\"{fact['statement']}\" [{fact.get('evidence_ref', '')}].",
            )

        related = next(
            (
                fact for fact in eligible_facts
                if fact.get("fact_category") == "efficacy_claim"
            ),
            None,
        )
        if related:
            return (
                "RELATED_NOT_EVIDENCE",
                None,
                f"Approved evidence describes efficacy, not the ingredient or its concentration: "
                f"\"{related['statement']}\" [Fact Version #{related['fact_version_id']}; "
                f"{related.get('evidence_ref', '')}]. "
                "Ask the Fact Owner for an approved ingredient specification.",
            )
        return (
            "NO_MATCH",
            None,
            "No approved fact verifies this ingredient or its stated concentration.",
        )

    claim_duration = _duration_in_days(claim_text)
    claim_effects = _effect_concepts(claim_text)
    related_effect_facts = [
        fact for fact in eligible_facts if _fact_is_related_to_claim(claim_text, fact)
    ]
    for fact in related_effect_facts:
        fact_text = fact.get("statement", "")
        if (
            _efficacy_profile_compatible(claim_text, fact_text)
            and claim_duration == _duration_in_days(fact_text)
        ):
            return (
                EvidenceRelationship.SUPPORTS.value,
                fact,
                f"The claim's outcome, qualification, scope, and timeframe match approved evidence: "
                f"\"{fact_text}\" [{fact.get('evidence_ref', '')}].",
            )
    for fact in related_effect_facts:
        fact_text = fact.get("statement", "")
        fact_duration = _duration_in_days(fact_text)
        if (
            claim_duration is not None
            and fact_duration is not None
            and claim_duration < fact_duration
            and _efficacy_profile_compatible(claim_text, fact_text)
            and _fact_explicitly_excludes_earlier_results(fact_text)
        ):
            return (
                EvidenceRelationship.CONTRADICTS.value,
                fact,
                f"The approved evidence explicitly rules out earlier results and specifies "
                f"{fact_duration:g} days: \"{fact_text}\" [{fact.get('evidence_ref', '')}].",
            )
    if related_effect_facts:
        fact = related_effect_facts[0]
        fact_duration = _duration_in_days(fact.get("statement", ""))
        timing_note = (
            f"It specifies {fact_duration:g} days, not the claim's {claim_duration:g}-day timeframe. "
            if fact_duration is not None and claim_duration is not None and fact_duration != claim_duration
            else ""
        )
        return (
            EvidenceRelationship.RELATED_NOT_EVIDENCE.value,
            None,
            f"The approved fact is related but does not establish this claim's outcome strength, "
            f"scope, qualifications, or conditions. {timing_note}"
            f"\"{fact['statement']}\" [{fact.get('evidence_ref', '')}].",
        )
    return None


def is_rule_applicable(
    rule: dict,
    product_id: int | None,
    market: str | None = None,
    channel: str | None = None,
) -> bool:
    """
    Determine if a brand rule applies to the given review context.

    Rules:
    - Active & owner-approved only.
    - Global rules (product_id is None) apply to all products.
    - Product-specific rules apply ONLY if product_id matches rule['product_id'].
      Never execute another product's rules.
    - Market-specific rules apply ONLY if market matches one of the rule's scoped markets.
      If a rule restricts markets, it must not execute when market is unspecified or different.
    - Channel-specific rules apply ONLY if channel matches (if scoped).
    """
    if not rule.get("is_active") or not rule.get("owner_approved"):
        return False
    if rule.get("source_verified") is False:
        return False
    if (
        rule.get("readiness_class")
        and rule.get("readiness_class") != "CODE_CHECKABLE"
    ):
        return False

    rule_pid = rule.get("product_id")
    if rule_pid is not None:
        if product_id is None or rule_pid != product_id:
            return False

    scope = rule.get("scope") or {}
    if isinstance(scope, str):
        try:
            scope = json.loads(scope)
        except (json.JSONDecodeError, TypeError):
            scope = {}

    rule_markets = rule.get("markets")
    if rule_markets is None:
        rule_markets = scope.get("market", []) or scope.get("markets", [])
    if isinstance(rule_markets, str):
        try:
            rule_markets = json.loads(rule_markets)
        except (json.JSONDecodeError, TypeError):
            rule_markets = [rule_markets]
    if rule_markets and "all" not in rule_markets:
        if not market or market not in rule_markets:
            return False

    rule_channels = rule.get("channels")
    if rule_channels is None:
        rule_channels = scope.get("channel", []) or scope.get("channels", [])
    if isinstance(rule_channels, str):
        try:
            rule_channels = json.loads(rule_channels)
        except (json.JSONDecodeError, TypeError):
            rule_channels = [rule_channels]
    if rule_channels and "all" not in rule_channels:
        if not channel or channel not in rule_channels:
            return False

    return True


def _finding_source_location(
    page_map: dict, start_char: int | None, end_char: int | None, base_offset: int = 0
) -> dict[str, int | None]:
    if start_char is None or end_char is None:
        return {"source_page": None, "start_char": None, "end_char": None}
    absolute_start = base_offset + start_char
    absolute_end = base_offset + end_char
    page_starts = []
    for offset, page_number in (page_map or {}).items():
        try:
            page_starts.append((int(offset), int(page_number)))
        except (TypeError, ValueError):
            continue
    source_page = max(
        (item for item in page_starts if item[0] <= absolute_start),
        default=(0, None),
    )[1]
    return {
        "source_page": source_page,
        "start_char": absolute_start,
        "end_char": absolute_end,
    }


def apply_draft_correction(
    current_text: str,
    target_phrase: str | None,
    replacement: str,
    mode: str,
) -> str:
    """
    Safely update draft text for a revision.

    Modes:
    - 'phrase': Replaces the single exact occurrence of target_phrase within current_text.
                Guarantees surrounding text is preserved and fails explicitly if target_phrase
                is not found (never appends text silently).
    - 'full_draft': Replaces the entire draft with replacement text.

    Raises ValueError on validation failure.
    """
    if mode == "phrase":
        if not target_phrase or not target_phrase.strip():
            raise ValueError("Target phrase cannot be empty for phrase replacement.")
        if target_phrase not in current_text:
            raise ValueError(
                f"Target phrase {target_phrase!r} not found in the current draft text. "
                "The draft may have been edited previously. Please supply a complete revised draft."
            )
        # Replace only the first occurrence of the offending phrase
        return current_text.replace(target_phrase, replacement.strip(), 1)

    elif mode == "full_draft":
        if not replacement or not replacement.strip():
            raise ValueError("Replacement draft text cannot be empty.")
        return replacement.strip()

    else:
        raise ValueError(f"Unknown correction mode: {mode!r}. Expected 'phrase' or 'full_draft'.")


def run_review(
    draft_id: int,
    raw_text: str,
    source_type: str,
    page_map: dict,
    product_id: int | None,
    market: str | None,
    channel: str | None,
    brief_id: int | None,
    ai_provider,
    visual_metadata: dict | None = None,
    draft_version_id: int | None = None,
    content_type: str | None = None,
    ai_consent: bool = False,
) -> dict[str, Any]:
    """
    Main entry point for the review pipeline.

    Parameters
    ----------
    draft_id         : existing draft record id
    raw_text         : extracted text from the document
    source_type      : 'paste' | 'pdf' | 'docx' | 'txt' | 'image' | 'edit'
    page_map         : {char_offset: page_number}
    product_id       : selected product (may be None)
    market           : e.g. 'IN' (may be None)
    channel          : e.g. 'instagram' (may be None)
    brief_id         : optional campaign brief id
    ai_provider      : AIProvider instance
    visual_metadata  : optional visual extraction parameters
    draft_version_id : optional specific existing draft_version_id to evaluate (avoids duplicate version creation)

    Returns
    -------
    {
        "draft_version_id": int,
        "findings": list[dict],
        "ai_errors": list[str],  # non-fatal AI errors
        "claim_count": int,
    }
    """
    ai_errors: list[str] = []
    ai_warnings: list[str] = []
    ai_operations: dict[str, str] = {}
    ai_operation_status: dict[str, str] = {}
    ai_response_metadata: dict[str, Any] = {}
    fact_ranking_circuit_open = False
    regex_budget = RegexExecutionBudget()
    claim_extraction_findings: list[dict] = []
    visual_metadata_provided = visual_metadata is not None
    visual_metadata = dict(visual_metadata or {})
    visual_metadata["external_ai_consent"] = bool(ai_consent)
    if content_type is None:
        with get_connection() as conn:
            draft_scope = conn.execute(
                "SELECT content_type FROM drafts WHERE id = ?", (draft_id,)
            ).fetchone()
        content_type = draft_scope["content_type"] if draft_scope else None
    asset_check: dict[str, Any] | None = None
    asset_phash_check: dict[str, Any] | None = None
    asset_id = visual_metadata.get("asset_id")
    if source_type == "image":
        if asset_id is None:
            asset_check = {
                "result": FindingResult.NOT_OBSERVED.value,
                "explanation": "No approved image was selected; image identity could not be checked.",
                "suggested_correction": "Select an active approved image asset for this product to compare exact file identity.",
            }
        elif not visual_metadata.get("submitted_hash"):
            asset_check = {
                "result": FindingResult.NOT_OBSERVED.value,
                "explanation": "The submitted image hash is unavailable; image identity could not be checked.",
                "suggested_correction": "Re-upload a valid PNG or JPEG image to calculate its SHA-256 identity.",
            }
        else:
            approved_asset = get_approved_asset(int(asset_id), product_id)
            if approved_asset is None:
                asset_check = {
                    "result": FindingResult.NOT_OBSERVED.value,
                    "explanation": "Selected asset is not an active, approved image for this product; identity comparison was not performed.",
                    "suggested_correction": "Choose an active approved asset registered for the selected product.",
                }
            else:
                visual_metadata["approved_hash"] = approved_asset.get("sha256_hash")
                asset_result = run_rule(
                    {
                        "rule_type": "asset_hash",
                        "is_active": 1,
                        "owner_approved": 1,
                        "parameters": {
                            "submitted_hash": visual_metadata["submitted_hash"],
                            "approved_hash": visual_metadata["approved_hash"],
                            "hash_type": "sha256",
                        },
                    },
                    "",
                    regex_budget=regex_budget,
                )
                asset_check = {
                    "result": asset_result.result.value,
                    "explanation": (
                        f"Image-to-image exact SHA-256 comparison: {asset_result.explanation}"
                    ),
                    "suggested_correction": asset_result.suggested_correction,
                }
                phash_result = run_rule(
                    {
                        "rule_type": "logo_phash",
                        "is_active": 1,
                        "owner_approved": 1,
                        "parameters": {
                            "submitted_image_bytes": visual_metadata.get("submitted_image_bytes"),
                            "approved_phash": approved_asset.get("perceptual_hash"),
                            "max_hamming_distance": 10,
                        },
                    },
                    "",
                    regex_budget=regex_budget,
                )
                asset_phash_check = {
                    "result": phash_result.result.value,
                    "explanation": phash_result.explanation,
                    "suggested_correction": phash_result.suggested_correction,
                }
    elif asset_id is not None:
        asset_check = {
            "result": FindingResult.NOT_OBSERVED.value,
            "explanation": (
                "Approved asset identity comparison supports PNG/JPEG image uploads only; "
                f"the submitted {source_type.upper()} file was not compared to an image."
            ),
            "suggested_correction": "Upload the artwork itself as PNG or JPEG for exact image identity comparison.",
        }

    logger.info(
        "Initiating review pipeline: draft_id=%s, product_id=%s, market=%s, channel=%s",
        draft_id,
        product_id,
        market,
        channel,
    )

    # ------------------------------------------------------------------
    # 1. Persist or attach draft version
    # ------------------------------------------------------------------
    with get_connection() as conn:
        if draft_version_id is None:
            # Version number = count of existing versions + 1
            row = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_v "
                "FROM draft_versions WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
            version_number = row["next_v"]

            cur = conn.execute(
                "INSERT INTO draft_versions "
                "(draft_id, version_number, raw_text, source_type, page_map, visual_metadata) "
                "VALUES (?,?,?,?,?,?)",
                (
                    draft_id, version_number, raw_text, source_type,
                    json.dumps(page_map), json.dumps(visual_metadata or {}),
                ),
            )
            draft_version_id = cur.lastrowid
        else:
            # Use specified draft_version_id; clean up any preexisting findings on this version to prevent duplicates
            row = conn.execute(
                "SELECT version_number, visual_metadata FROM draft_versions WHERE id = ?",
                (draft_version_id,),
            ).fetchone()
            version_number = row["version_number"] if row else 1
            if not visual_metadata_provided and row:
                try:
                    visual_metadata = json.loads(row["visual_metadata"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    visual_metadata = {}
                if not isinstance(visual_metadata, dict):
                    visual_metadata = {}
            visual_metadata["external_ai_consent"] = bool(ai_consent)
            conn.execute(
                "UPDATE draft_versions SET visual_metadata = ? WHERE id = ?",
                (json.dumps(visual_metadata), draft_version_id),
            )
            conn.execute("DELETE FROM findings WHERE draft_version_id = ?", (draft_version_id,))
            conn.execute("DELETE FROM claim_mentions WHERE draft_version_id = ?", (draft_version_id,))

        # Update draft status to CHECKED
        conn.execute(
            "UPDATE drafts SET status = 'CHECKED', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
            "WHERE id = ?",
            (draft_id,),
        )
        conn.commit()

    logger.info("Step 1: Using draft_version_id=%d (version %d)", draft_version_id, version_number)

    # ------------------------------------------------------------------
    # 2. Extract claim spans via AI
    # ------------------------------------------------------------------
    claim_mentions: list[dict] = []
    claim_extraction_size_limited = len(raw_text) > MAX_AI_REVIEW_CHARS
    candidates = [] if claim_extraction_size_limited else build_claim_candidates(raw_text)
    candidates_by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    if claim_extraction_size_limited:
        ai_warnings.append(
            f"Claim extraction was not sent to AI because the draft exceeds the {MAX_AI_REVIEW_CHARS:,}-character limit. Deterministic checks still cover the full draft."
        )
    try:
        logger.info("Step 2: Calling AI for claim extraction...")
        if not ai_consent:
            raise _AIConsentNotGranted
        if claim_extraction_size_limited:
            raise _AIReviewTooLarge
        prompt = build_claim_extraction_prompt(raw_text, candidates)
        raw_response = _generate_review_response(
            ai_provider,
            ai_consent,
            prompt,
            temperature=0.1,
            max_output_tokens=2048,
            json_mode=True,
        )
        response_metadata = getattr(ai_provider, "last_response_metadata", None)
        if response_metadata is not None:
            ai_response_metadata["claim_extraction"] = response_metadata
    except _AIConsentNotGranted:
        ai_operation_status["claim_extraction"] = FindingResult.NOT_OBSERVED.value
        claim_extraction_findings.append({
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": None,
            "fact_version_id": None,
            "finding_type": "claim_extraction",
            "result": FindingResult.NOT_OBSERVED.value,
            "status": FindingStatus.OPEN.value,
            "severity": Severity.LOW.value,
            "quoted_text": "",
            "explanation": (
                "AI-assisted claim extraction was not run because external-AI consent "
                "was not provided. This is not a pass."
            ),
            "suggested_correction": None,
        })
    except _AIReviewTooLarge:
        ai_operation_status["claim_extraction"] = FindingResult.NOT_OBSERVED.value
        claim_extraction_findings.append({
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": None,
            "fact_version_id": None,
            "finding_type": "claim_extraction",
            "result": FindingResult.NOT_OBSERVED.value,
            "status": FindingStatus.OPEN.value,
            "severity": Severity.LOW.value,
            "quoted_text": "",
            "explanation": (
                "AI-assisted claim extraction was not run because the draft exceeds "
                f"the {MAX_AI_REVIEW_CHARS:,}-character limit. This is not a pass."
            ),
            "suggested_correction": None,
        })
    except Exception as exc:
        sanitized_err = safe_error_summary(exc)
        logger.warning(
            "Step 2: AI claim extraction request failed: %s.",
            sanitized_err,
        )
        ai_errors.append(f"Claim extraction unavailable: {sanitized_err}")
        ai_operation_status["claim_extraction"] = FindingResult.NOT_OBSERVED.value
        claim_extraction_findings.append({
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": None,
            "fact_version_id": None,
            "finding_type": "claim_extraction",
            "result": FindingResult.NOT_OBSERVED.value,
            "status": FindingStatus.OPEN.value,
            "severity": Severity.LOW.value,
            "quoted_text": "",
            "explanation": "AI-assisted claim extraction could not be completed. No claims were accepted from this operation.",
            "suggested_correction": None,
        })
    else:
        try:
            valid_claims, invalid_claims = validate_claim_extraction_response(raw_response)
        except Exception as exc:
            sanitized_err = safe_error_summary(exc)
            logger.warning("Step 2: Claim extraction response was invalid: %s", sanitized_err)
            ai_errors.append(f"Claim extraction response invalid: {sanitized_err}")
            ai_operation_status["claim_extraction"] = FindingResult.ERROR.value
            claim_extraction_findings.append({
                "draft_version_id": draft_version_id,
                "claim_mention_id": None,
                "rule_id": None,
                "fact_version_id": None,
                "finding_type": "claim_extraction",
                "result": FindingResult.ERROR.value,
                "status": FindingStatus.OPEN.value,
                "severity": Severity.LOW.value,
                "quoted_text": "",
                "explanation": "The claim-extraction response could not be validated. No claims were accepted from this operation.",
                "suggested_correction": None,
            })
        else:
            invalid_count = len(invalid_claims)
            for idx, validation_error in invalid_claims:
                ai_errors.append(
                    f"Claim {idx + 1} response invalid: {validation_error}"
                )
                claim_extraction_findings.append({
                    "draft_version_id": draft_version_id,
                    "claim_mention_id": None,
                    "rule_id": None,
                    "fact_version_id": None,
                    "finding_type": "claim_extraction",
                    "result": FindingResult.ERROR.value,
                    "status": FindingStatus.OPEN.value,
                    "severity": Severity.LOW.value,
                    "quoted_text": "",
                    "explanation": (
                        "One extracted claim could not be validated and was skipped. "
                        "Other verified claims can still be checked."
                    ),
                    "suggested_correction": None,
                })

            seen_candidate_ids: set[str] = set()
            seen_spans: set[tuple[int, int]] = set()
            for idx, selection in valid_claims:
                candidate_id = selection["candidate_id"]
                if candidate_id in seen_candidate_ids:
                    invalid_count += 1
                    message = "A candidate ID was selected more than once."
                elif candidate_id not in candidates_by_id:
                    invalid_count += 1
                    message = "The selected candidate ID was not generated from this draft."
                else:
                    candidate = candidates_by_id[candidate_id]
                    span = (candidate["start_char"], candidate["end_char"])
                    if span in seen_spans:
                        invalid_count += 1
                        message = "The selected source span duplicates an already selected claim."
                    else:
                        seen_candidate_ids.add(candidate_id)
                        seen_spans.add(span)
                        claim_mentions.append(
                            {
                                "claim_text": raw_text[span[0]:span[1]],
                                "start_char": span[0],
                                "end_char": span[1],
                                "claim_type_hint": selection["claim_type_hint"],
                                "page": None,
                                "paragraph": None,
                            }
                        )
                        continue

                ai_errors.append(f"Claim selection {idx + 1} rejected: {message}")
                claim_extraction_findings.append({
                    "draft_version_id": draft_version_id,
                    "claim_mention_id": None,
                    "rule_id": None,
                    "fact_version_id": None,
                    "finding_type": "claim_extraction",
                    "result": FindingResult.ERROR.value,
                    "status": FindingStatus.OPEN.value,
                    "severity": Severity.MEDIUM.value,
                    "quoted_text": "",
                    "explanation": (
                        "A model-selected candidate was rejected because its ID was unknown, duplicated, "
                        "or did not identify a unique source span. No model-generated wording was stored."
                    ),
                    "suggested_correction": None,
                })

            operation_label = _ai_operation_label(ai_provider)
            if operation_label:
                ai_operations["claim_extraction"] = operation_label
            if invalid_count:
                ai_operation_status["claim_extraction"] = (
                    "PARTIAL" if claim_mentions else FindingResult.ERROR.value
                )
            elif claim_mentions:
                ai_operation_status["claim_extraction"] = "SUCCESS"
            else:
                ai_operation_status["claim_extraction"] = FindingResult.NOT_OBSERVED.value
                claim_extraction_findings.append({
                    "draft_version_id": draft_version_id,
                    "claim_mention_id": None,
                    "rule_id": None,
                    "fact_version_id": None,
                    "finding_type": "claim_extraction",
                    "result": FindingResult.NOT_OBSERVED.value,
                    "status": FindingStatus.OPEN.value,
                    "severity": Severity.LOW.value,
                    "quoted_text": "",
                    "explanation": (
                        "No claim candidates were selected from the source-preserved draft candidates; "
                        "claim extraction was not observed, not passed."
                    ),
                    "suggested_correction": None,
                })
            logger.info(
                "Step 2: Verified %d source candidates; %d invalid selections (%s)",
                len(claim_mentions),
                invalid_count,
                operation_label or "AI",
            )

    # Persist claim mentions
    claim_mention_ids: dict[int, int] = {}  # index -> db id
    with get_connection() as conn:
        for idx, claim in enumerate(claim_mentions):
            cur = conn.execute(
                "INSERT INTO claim_mentions "
                "(draft_version_id, claim_text, start_char, end_char, "
                " claim_type_hint, page, paragraph) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    draft_version_id,
                    claim.get("claim_text", "")[:2000],
                    claim.get("start_char", 0),
                    claim.get("end_char", 0),
                    claim.get("claim_type_hint"),
                    claim.get("page"),
                    claim.get("paragraph"),
                ),
            )
            claim_mention_ids[idx] = cur.lastrowid
        conn.commit()

    # ------------------------------------------------------------------
    # 3. Load active rules (product & market filtered)
    # ------------------------------------------------------------------
    with get_connection() as conn:
        if product_id is not None:
            rules_query = """
                SELECT br.*, g.title AS guideline_title, g.source_ref AS guideline_source_ref,
                       g.version AS guideline_version,
                       CASE WHEN
                           (g.id IS NOT NULL AND length(trim(g.content)) > 0)
                           OR (br.source_document_id IS NOT NULL
                               AND br.source_page IS NOT NULL
                               AND length(trim(COALESCE(br.source_quote, ''))) > 0)
                           THEN 1 ELSE 0 END AS source_verified
                FROM brand_rules br
                LEFT JOIN guidelines g ON g.id = br.guideline_id
                WHERE br.is_active = 1 AND br.owner_approved = 1
                  AND ((g.id IS NOT NULL AND length(trim(g.content)) > 0)
                       OR (br.source_document_id IS NOT NULL
                           AND br.source_page IS NOT NULL
                           AND length(trim(COALESCE(br.source_quote, ''))) > 0))
                  AND br.readiness_class = 'CODE_CHECKABLE'
                  AND (br.product_id = ? OR br.product_id IS NULL)
            """
            rules = [dict(r) for r in conn.execute(rules_query, (product_id,)).fetchall()]
        else:
            rules_query = """
                SELECT br.*, g.title AS guideline_title, g.source_ref AS guideline_source_ref,
                       g.version AS guideline_version,
                       CASE WHEN
                           (g.id IS NOT NULL AND length(trim(g.content)) > 0)
                           OR (br.source_document_id IS NOT NULL
                               AND br.source_page IS NOT NULL
                               AND length(trim(COALESCE(br.source_quote, ''))) > 0)
                           THEN 1 ELSE 0 END AS source_verified
                FROM brand_rules br
                LEFT JOIN guidelines g ON g.id = br.guideline_id
                WHERE br.is_active = 1 AND br.owner_approved = 1
                  AND ((g.id IS NOT NULL AND length(trim(g.content)) > 0)
                       OR (br.source_document_id IS NOT NULL
                           AND br.source_page IS NOT NULL
                           AND length(trim(COALESCE(br.source_quote, ''))) > 0))
                  AND br.readiness_class = 'CODE_CHECKABLE'
                  AND br.product_id IS NULL
            """
            rules = [dict(r) for r in conn.execute(rules_query).fetchall()]

    # ------------------------------------------------------------------
    # 4. Run deterministic rule engine
    # ------------------------------------------------------------------
    all_findings: list[dict] = claim_extraction_findings
    if not claim_mentions:
        ai_operation_status["fact_ranking"] = FindingResult.NOT_OBSERVED.value
        if not ai_consent:
            fact_ranking_reason = (
                "AI-assisted fact ranking was not run because external-AI consent "
                "was not provided. This is not a pass."
            )
        else:
            fact_ranking_reason = (
                "AI-assisted fact ranking was not run because no validated claims "
                "were available. This is not a pass."
            )
        all_findings.append({
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": None,
            "fact_version_id": None,
            "finding_type": "fact_ranking",
            "result": FindingResult.NOT_OBSERVED.value,
            "status": FindingStatus.OPEN.value,
            "severity": Severity.LOW.value,
            "quoted_text": "",
            "explanation": fact_ranking_reason,
            "suggested_correction": None,
        })
    seen_rule_violations: set[tuple[int, str]] = set()
    contrast_result = run_rule(
        {
            "rule_type": "wcag_contrast",
            "is_active": 1,
            "owner_approved": 1,
            "parameters": {
                "foreground": visual_metadata.get("foreground") or "",
                "background": visual_metadata.get("background") or "",
                "min_ratio": 4.5,
            },
        },
        "",
        regex_budget=regex_budget,
    )
    contrast_suggestion = contrast_result.suggested_correction
    if contrast_result.result == FindingResult.NOT_OBSERVED and not contrast_suggestion:
        contrast_suggestion = "Provide both foreground and background hex colours to check the 4.5:1 normal-text baseline."
    all_findings.append({
        "draft_version_id": draft_version_id,
        "claim_mention_id": None,
        "rule_id": None,
        "fact_version_id": None,
        "finding_type": "accessibility_contrast",
        "result": contrast_result.result.value,
        "status": FindingStatus.OPEN.value,
        "severity": Severity.LOW.value,
        "quoted_text": "",
        "explanation": contrast_result.explanation,
        "suggested_correction": contrast_suggestion,
    })

    if source_type == "image":
        alt_result = run_rule(
            {
                "rule_type": "alt_text",
                "is_active": 1,
                "owner_approved": 1,
                "parameters": {"alt_text": visual_metadata.get("alt_text")},
            },
            "",
                regex_budget=regex_budget,
        )
        alt_suggestion = alt_result.suggested_correction
        if alt_result.result == FindingResult.NOT_OBSERVED and not alt_suggestion:
            alt_suggestion = "Provide image alt-text metadata to check this accessibility baseline."
        all_findings.append({
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": None,
            "fact_version_id": None,
            "finding_type": "accessibility_alt_text",
            "result": alt_result.result.value,
            "status": FindingStatus.OPEN.value,
            "severity": Severity.LOW.value,
            "quoted_text": alt_result.offending_text or "",
            "explanation": alt_result.explanation,
            "suggested_correction": alt_suggestion,
        })

    if asset_check:
        all_findings.append({
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": None,
            "fact_version_id": None,
            "finding_type": "asset_identity",
            "result": asset_check["result"],
            "status": FindingStatus.OPEN.value,
            "severity": Severity.LOW.value,
            "quoted_text": "",
            "explanation": asset_check["explanation"],
            "suggested_correction": asset_check["suggested_correction"],
        })

    if asset_phash_check:
        all_findings.append({
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": None,
            "fact_version_id": None,
            "finding_type": "asset_perceptual_identity",
            "result": asset_phash_check["result"],
            "status": FindingStatus.OPEN.value,
            "severity": Severity.LOW.value,
            "quoted_text": "",
            "explanation": asset_phash_check["explanation"],
            "suggested_correction": asset_phash_check["suggested_correction"],
        })

    # Run rules against full text first
    for rule in rules:
        if not is_rule_applicable(rule, product_id=product_id, market=market, channel=channel):
            continue

        rule_to_run = dict(rule)
        if visual_metadata and rule.get("rule_type") in ("wcag_contrast", "asset_hash"):
            params = json.loads(rule.get("parameters") or "{}") if isinstance(rule.get("parameters"), str) else dict(rule.get("parameters") or {})
            if rule.get("rule_type") == "wcag_contrast":
                if "foreground" in visual_metadata:
                    params["foreground"] = visual_metadata["foreground"]
                if "background" in visual_metadata:
                    params["background"] = visual_metadata["background"]
            elif rule.get("rule_type") == "asset_hash":
                if "submitted_hash" in visual_metadata:
                    params["submitted_hash"] = visual_metadata["submitted_hash"]
                if "approved_hash" in visual_metadata:
                    params["approved_hash"] = visual_metadata["approved_hash"]
            rule_to_run["parameters"] = json.dumps(params)

        rule_res = run_rule(rule_to_run, raw_text, regex_budget=regex_budget)
        result, explanation = rule_res

        # Only create findings for non-PASS
        if result == FindingResult.PASS:
            continue

        offending = getattr(rule_res, "offending_text", None)
        corr = getattr(rule_res, "suggested_correction", None)
        quoted = offending if offending else raw_text[:200]
        source_location = _finding_source_location(
            page_map,
            getattr(rule_res, "start_char", None),
            getattr(rule_res, "end_char", None),
        )

        seen_rule_violations.add((rule["id"], (offending or "").lower()))

        finding = {
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "fact_version_id": None,
            "finding_type": "rule_check",
            "result": result.value,
            "status": FindingStatus.OPEN.value,
            "severity": rule.get("severity", Severity.MEDIUM.value),
            "quoted_text": quoted,
            **source_location,
            "explanation": explanation,
            "suggested_correction": corr,
        }
        all_findings.append(finding)

    # Run rules against individual claim spans too
    for idx, claim in enumerate(claim_mentions):
        claim_text = claim.get("claim_text", "")
        cm_id = claim_mention_ids.get(idx)

        for rule in rules:
            if not is_rule_applicable(rule, product_id=product_id, market=market, channel=channel):
                continue

            rule_res = run_rule(rule, claim_text, regex_budget=regex_budget)
            result, explanation = rule_res
            if result == FindingResult.PASS:
                continue

            offending = getattr(rule_res, "offending_text", None)
            corr = getattr(rule_res, "suggested_correction", None)

            # Avoid duplicate if already caught on full text with same offending snippet
            if offending and (rule["id"], offending.lower()) in seen_rule_violations:
                continue

            quoted = offending if offending else claim_text[:200]
            source_location = _finding_source_location(
                page_map,
                getattr(rule_res, "start_char", None),
                getattr(rule_res, "end_char", None),
                claim.get("start_char", 0),
            )

            finding = {
                "draft_version_id": draft_version_id,
                "claim_mention_id": cm_id,
                "rule_id": rule["id"],
                "rule_name": rule["name"],
                "fact_version_id": None,
                "finding_type": "rule_check",
                "result": result.value,
                "status": FindingStatus.OPEN.value,
                "severity": rule.get("severity", Severity.MEDIUM.value),
                "quoted_text": quoted,
                **source_location,
                "explanation": explanation,
                "suggested_correction": corr,
            }
            all_findings.append(finding)

    logger.info("Step 3 & 4: Deterministic rule execution complete. %d rule findings generated.", len(all_findings))

    # ------------------------------------------------------------------
    # 5. Fact matching per claim (filter deterministically, rank via AI)
    # ------------------------------------------------------------------
    eligible_facts = get_eligible_fact_versions(draft_id=draft_id, product_id=product_id, market=market)
    all_products = get_all_products()
    fact_context_length = len(
        "\n".join(
            f"  ID {fact['fact_version_id']}: {fact['statement']} [{fact.get('evidence_ref', '')}]"
            for fact in eligible_facts
        )
    )
    if fact_context_length > 4000:
        ai_warnings.append(
            "Fact-ranking context is capped at 4,000 characters per claim; some lower-priority eligible fact text may be omitted."
        )
    eligible_fv_id_set = {fv["fact_version_id"] for fv in eligible_facts}
    logger.info(
        "Step 5: Matching %d claims against %d eligible facts (fact_ranking_circuit_open=%s)...",
        len(claim_mentions),
        len(eligible_facts),
        fact_ranking_circuit_open,
    )
    fact_ranking_successes = 0
    fact_ranking_failures = 0

    for idx, claim in enumerate(claim_mentions):
        claim_text = claim.get("claim_text", "")
        cm_id = claim_mention_ids.get(idx)

        if not eligible_facts:
            # No approved facts at all → UNLINKED
            finding = {
                "draft_version_id": draft_version_id,
                "claim_mention_id": cm_id,
                "rule_id": None,
                "fact_version_id": None,
                "finding_type": "fact_match",
                "evidence_relationship": EvidenceRelationship.NO_MATCH.value,
                "result": FindingResult.UNLINKED.value,
                "status": FindingStatus.OPEN.value,
                "severity": Severity.MEDIUM.value,
                "quoted_text": claim_text[:200],
                "explanation": "No approved facts found for this product and market.",
                "suggested_correction": None,
            }
            all_findings.append(finding)
            continue

        rank_parsed: dict | None = None
        is_ai_ranked = False
        rank_error: str | None = None

        if not fact_ranking_circuit_open:
            try:
                rank_prompt = build_fact_ranking_prompt(claim_text, eligible_facts)
                rank_response = _generate_review_response(
                    ai_provider,
                    ai_consent,
                    rank_prompt,
                    temperature=0.1,
                    max_output_tokens=1024,
                    json_mode=True,
                )
                response_metadata = getattr(ai_provider, "last_response_metadata", None)
                if response_metadata is not None:
                    ai_response_metadata.setdefault("fact_ranking", []).append(
                        response_metadata
                    )
            except Exception as exc:
                rank_error = safe_error_summary(exc)
                fact_ranking_circuit_open = True
                logger.warning(
                    "Step 5: Fact ranking request failed for claim_index=%d: %s. "
                    "Skipping further fact-ranking requests for this review.",
                    idx + 1,
                    rank_error,
                )
            else:
                try:
                    rank_parsed = validate_ai_response(rank_response, FACT_RANKING_SCHEMA)
                except Exception as exc:
                    rank_error = safe_error_summary(exc)
                    logger.warning(
                        "Step 5: Fact ranking response invalid for claim_index=%d: %s",
                        idx + 1,
                        rank_error,
                    )
                else:
                    is_ai_ranked = True
                    fact_ranking_successes += 1
                    ai_operations["fact_ranking"] = _ai_operation_label(ai_provider)
        else:
            rank_error = "Fact ranking was skipped after an earlier ranking request failed."

        if rank_error:
            fact_ranking_failures += 1
            ai_errors.append(f"Fact ranking unavailable: {rank_error}")

        deterministic_match = _deterministic_fact_relationship(
            claim_text,
            claim.get("claim_type_hint"),
            eligible_facts,
            selected_product_id=product_id,
            all_products=all_products,
        )
        relationship = EvidenceRelationship.NO_MATCH.value
        matched_fv_id: int | None = None
        confidence = 0.0
        explanation = "No approved evidence directly matches this claim for the selected product and market."
        suggested_corr: str | None = None

        if deterministic_match:
            relationship, fact_obj, explanation = deterministic_match
            if relationship in (
                EvidenceRelationship.SUPPORTS.value,
                EvidenceRelationship.CONTRADICTS.value,
            ) and fact_obj:
                matched_fv_id = fact_obj["fact_version_id"]
                confidence = 1.0
            elif relationship == EvidenceRelationship.RELATED_NOT_EVIDENCE.value:
                suggested_corr = "Ask the Fact Owner to verify this claim against an approved source."
                if is_ai_ranked and rank_parsed:
                    ai_exp = (rank_parsed.get("explanation") or "").strip()
                    if ai_exp:
                        explanation = f"{explanation} {ai_exp}".strip()
                    if rank_parsed.get("suggested_correction"):
                        suggested_corr = rank_parsed.get("suggested_correction")
        elif is_ai_ranked and rank_parsed:
            facts_by_id = {fact["fact_version_id"]: fact for fact in eligible_facts}
            evals = [
                evaluation for evaluation in rank_parsed.get("evaluations", [])
                if evaluation.get("fact_version_id") in eligible_fv_id_set
            ]
            evals.sort(
                key=lambda evaluation: float(evaluation.get("confidence", 0.0) or 0.0),
                reverse=True,
            )
            confidence_threshold = 0.70

            accepted = next(
                (
                    evaluation for evaluation in evals
                    if float(evaluation.get("confidence", 0.0) or 0.0) >= confidence_threshold
                    and evaluation.get("relationship")
                    == _fact_relationship_for_claim(
                        claim_text,
                        facts_by_id[evaluation["fact_version_id"]],
                        selected_product_id=product_id,
                        all_products=all_products,
                    )
                    and evaluation.get("relationship")
                    in (EvidenceRelationship.SUPPORTS.value, EvidenceRelationship.CONTRADICTS.value)
                ),
                None,
            )
            if accepted:
                relationship = accepted["relationship"]
                matched_fv_id = accepted["fact_version_id"]
                confidence = float(accepted.get("confidence", 0.0) or 0.0)
                fact_obj = facts_by_id[matched_fv_id]
                explanation = (
                    ("Conflicts with" if relationship == EvidenceRelationship.CONTRADICTS.value else "Supported by")
                    + f" approved evidence: \"{fact_obj['statement']}\" "
                    + f"[{fact_obj.get('evidence_ref', '')}]. "
                    + accepted.get("relevance_note", "")
                ).strip()
            else:
                best_fact_id = rank_parsed.get("matched_fact_version_id")
                related = next(
                    (
                        evaluation for evaluation in evals
                        if evaluation.get("relationship") == EvidenceRelationship.RELATED_NOT_EVIDENCE.value
                        and _fact_is_related_to_claim(
                            claim_text,
                            facts_by_id[evaluation["fact_version_id"]],
                        )
                    ),
                    None,
                )
                if (
                    related is None
                    and rank_parsed.get("best_relationship") == EvidenceRelationship.RELATED_NOT_EVIDENCE.value
                    and best_fact_id in facts_by_id
                    and _fact_is_related_to_claim(claim_text, facts_by_id[best_fact_id])
                ):
                    related = {"fact_version_id": best_fact_id}

                if related:
                    related_fact = facts_by_id[related["fact_version_id"]]
                    relationship = EvidenceRelationship.RELATED_NOT_EVIDENCE.value
                    confidence = float(rank_parsed.get("confidence", 0.0) or 0.0)
                    explanation = (
                        "This approved fact is related to the topic, but does not substantiate this specific claim: "
                        f"\"{related_fact['statement']}\" [Fact Version #{related_fact['fact_version_id']}; "
                        f"{related_fact.get('evidence_ref', '')}]."
                    )
                    suggested_corr = "Ask the Fact Owner to verify this claim against an approved source."
                else:
                    explanation = "No approved evidence directly matches this claim for the selected product and market."

                if relationship == EvidenceRelationship.NO_MATCH:
                    ranked_items = rank_parsed.get("ranked_fact_version_ids", [])
                    for item in ranked_items:
                        candidate = facts_by_id.get(item.get("fact_version_id"))
                        if not candidate:
                            continue
                        candidate_relationship = _fact_relationship_for_claim(
                            claim_text,
                            candidate,
                            selected_product_id=product_id,
                            all_products=all_products,
                        )
                        if candidate_relationship not in (
                            EvidenceRelationship.SUPPORTS.value,
                            EvidenceRelationship.CONTRADICTS.value,
                        ):
                            continue
                        relationship = candidate_relationship
                        matched_fv_id = candidate["fact_version_id"]
                        confidence = 0.85
                        explanation = (
                            ("Conflicts with" if relationship == EvidenceRelationship.CONTRADICTS.value else "Supported by")
                            + f" approved evidence: \"{candidate['statement']}\" "
                            + f"[{candidate.get('evidence_ref', '')}]."
                        )
                        break

        # Map relationship to FindingResult and Severity
        if relationship == EvidenceRelationship.CONTRADICTS.value:
            finding_res = FindingResult.FAIL.value
            severity = Severity.HIGH.value
        elif relationship == EvidenceRelationship.SUPPORTS.value:
            finding_res = FindingResult.PASS.value
            severity = Severity.LOW.value
        elif relationship == EvidenceRelationship.RELATED_NOT_EVIDENCE.value:
            finding_res = FindingResult.UNLINKED.value
            severity = Severity.MEDIUM.value
        else: # NO_MATCH
            finding_res = FindingResult.UNLINKED.value
            severity = Severity.MEDIUM.value

        finding = {
            "draft_version_id": draft_version_id,
            "claim_mention_id": cm_id,
            "rule_id": None,
            "fact_version_id": matched_fv_id,
            "finding_type": "fact_match",
            "evidence_relationship": relationship,
            "result": finding_res,
            "status": FindingStatus.OPEN.value,
            "severity": severity,
            "quoted_text": claim_text[:200],
            "explanation": explanation,
            "suggested_correction": suggested_corr,
        }
        all_findings.append(finding)
        if rank_error:
            all_findings.append({
                "draft_version_id": draft_version_id,
                "claim_mention_id": cm_id,
                "rule_id": None,
                "fact_version_id": None,
                "finding_type": "fact_ranking",
                "result": FindingResult.NOT_OBSERVED.value,
                "status": FindingStatus.OPEN.value,
                "severity": Severity.LOW.value,
                "quoted_text": claim_text[:200],
                "explanation": (
                    "AI-assisted evidence ranking could not be completed for this verified claim. "
                    "Any separate deterministic result is shown independently."
                ),
                "suggested_correction": None,
            })

    if claim_mentions:
        if fact_ranking_successes and fact_ranking_failures:
            ai_operation_status["fact_ranking"] = "PARTIAL"
        elif fact_ranking_successes:
            ai_operation_status["fact_ranking"] = "SUCCESS"
        else:
            ai_operation_status["fact_ranking"] = FindingResult.NOT_OBSERVED.value

    # ------------------------------------------------------------------
    # 6. Tone / contextual findings via AI
    # ------------------------------------------------------------------
    logger.info("Step 6: Starting independent contextual tone assessment...")
    guidelines_summary = get_guidelines_summary(
        product_id=product_id,
        market=market,
        channel=channel,
        content_type=content_type,
    )
    brief_messages: list[str] = []
    brief_context: dict | None = None
    campaign_context_issue: str | None = None
    if brief_id:
        with get_connection() as conn:
            brief = conn.execute(
                "SELECT * FROM campaign_briefs WHERE id = ? AND is_active = 1", (brief_id,)
            ).fetchone()
            if brief:
                try:
                    brief_markets = json.loads(brief["markets"] or "[]")
                    brief_channels = json.loads(brief["channels"] or "[]")
                    brief_messages = json.loads(brief["key_messages"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    brief_markets, brief_channels, brief_messages = [], [], []
                scope_matches = (
                    brief["product_id"] == product_id
                    and bool(market)
                    and (not brief_markets or "all" in brief_markets or market in brief_markets)
                    and (not brief_channels or "all" in brief_channels or channel in brief_channels)
                )
                if scope_matches:
                    brief_context = dict(brief)
                else:
                    brief_messages = []
                    campaign_context_issue = (
                        "Selected campaign brief is inactive or does not match the draft's product, market, or channel; "
                        "campaign alignment was not assessed."
                    )
            else:
                campaign_context_issue = "Selected campaign brief was not found or is inactive; campaign alignment was not assessed."
    else:
        campaign_context_issue = "No campaign brief selected; campaign messaging alignment was not assessed."

    tone_error: str | None = None
    tone_not_observed = False
    tone_response: str | None = None
    tone_input_truncated = len(raw_text) > MAX_TONE_DRAFT_CHARS
    if tone_input_truncated:
        ai_warnings.append(
            f"Tone assessment receives only the first {MAX_TONE_DRAFT_CHARS:,} characters of the draft."
        )
    try:
        tone_prompt = build_tone_assessment_prompt(
            raw_text, guidelines_summary, brief_messages, brief_context
        )
        tone_response = _generate_review_response(
            ai_provider,
            ai_consent,
            tone_prompt,
            temperature=0.2,
            max_output_tokens=1024,
            json_mode=True,
        )
        response_metadata = getattr(ai_provider, "last_response_metadata", None)
        if response_metadata is not None:
            ai_response_metadata["tone_assessment"] = response_metadata
    except _AIConsentNotGranted:
        tone_not_observed = True
    except Exception as exc:
        tone_error = safe_error_summary(exc)
        logger.warning("Step 6: Tone assessment request failed: %s", tone_error)

    if tone_response is not None:
        try:
            tone_parsed = validate_ai_response(tone_response, TONE_FINDINGS_SCHEMA)
        except Exception as exc:
            tone_error = safe_error_summary(exc)
            logger.warning("Step 6: Tone assessment response was invalid: %s", tone_error)
        else:
            model_tag = _ai_operation_label(ai_provider)
            ai_operations["tone_assessment"] = model_tag
            ai_operation_status["tone_assessment"] = (
                "PARTIAL" if tone_input_truncated else "SUCCESS"
            )

            for tf in tone_parsed.get("tone_findings", []):
                supplied_quote = str(tf.get("quoted_text", ""))
                quote_start = raw_text.find(supplied_quote) if supplied_quote else -1
                quote_end = quote_start + len(supplied_quote) if quote_start >= 0 else None
                quote_is_unique = (
                    quote_start >= 0
                    and raw_text.find(supplied_quote, quote_start + 1) < 0
                )
                if quote_is_unique:
                    source_location = _finding_source_location(
                        page_map, quote_start, quote_end
                    )
                    verified_quote = supplied_quote
                else:
                    source_location = _finding_source_location(page_map, None, None)
                    verified_quote = ""
                explanation = f"[AI Generated ({model_tag})] {tf.get('issue', '')}\n{tf.get('explanation', '')}"
                if not quote_is_unique:
                    explanation += (
                        "\nThe supplied quotation could not be verified as one unique exact "
                        "span in the submitted draft; this is an unquoted advisory."
                    )
                finding = {
                    "draft_version_id": draft_version_id,
                    "claim_mention_id": None,
                    "rule_id": None,
                    "fact_version_id": None,
                    "finding_type": "tone",
                    "result": FindingResult.REVIEW_REQUIRED.value,
                    "status": FindingStatus.OPEN.value,
                    "severity": tf.get("severity", Severity.MEDIUM.value),
                    "quoted_text": verified_quote[:200],
                    **source_location,
                    "explanation": explanation,
                    "suggested_correction": tf.get("suggested_correction", "")[:1000],
                }
                all_findings.append(finding)
    if tone_not_observed:
        ai_operation_status["tone_assessment"] = FindingResult.NOT_OBSERVED.value
        all_findings.append({
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": None,
            "fact_version_id": None,
            "finding_type": "tone",
            "result": FindingResult.NOT_OBSERVED.value,
            "status": FindingStatus.OPEN.value,
            "severity": Severity.LOW.value,
            "quoted_text": "",
            "explanation": (
                "AI-assisted tone assessment was not run because external-AI consent "
                "was not provided. This is not a pass."
            ),
            "suggested_correction": None,
        })
    elif tone_error:
        ai_errors.append(f"Tone assessment unavailable: {tone_error}")
        ai_operation_status["tone_assessment"] = FindingResult.NOT_OBSERVED.value
        all_findings.append({
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": None,
            "fact_version_id": None,
            "finding_type": "tone",
            "result": FindingResult.NOT_OBSERVED.value,
            "status": FindingStatus.OPEN.value,
            "severity": Severity.LOW.value,
            "quoted_text": "",
            "explanation": (
                "AI-assisted tone assessment could not be completed. This is not a pass. "
                + (
                    "The tone request failed after the configured model attempts."
                    if tone_response is None
                    else "The response could not be validated."
                )
            ),
            "suggested_correction": None,
        })

    if campaign_context_issue:
        all_findings.append({
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": None,
            "fact_version_id": None,
            "finding_type": "campaign_alignment",
            "result": FindingResult.NOT_OBSERVED.value,
            "status": FindingStatus.OPEN.value,
            "severity": Severity.LOW.value,
            "quoted_text": "",
            "explanation": campaign_context_issue,
            "suggested_correction": None,
        })

    unobserved_pages = (visual_metadata or {}).get("unobserved_pages", [])
    for page_number in unobserved_pages:
        all_findings.append({
            "draft_version_id": draft_version_id,
            "claim_mention_id": None,
            "rule_id": None,
            "fact_version_id": None,
            "finding_type": "document_ocr",
            "result": FindingResult.NOT_OBSERVED.value,
            "status": FindingStatus.OPEN.value,
            "severity": Severity.LOW.value,
            "quoted_text": f"Page {page_number}",
            "explanation": f"PDF page {page_number} had no extractable text and OCR could not check it.",
            "suggested_correction": None,
        })

    # ------------------------------------------------------------------
    # 7. Persist all findings
    # ------------------------------------------------------------------
    logger.info("Step 7: Persisting %d total findings to SQLite...", len(all_findings))
    with get_connection() as conn:
        for finding in all_findings:
            if "evidence_relationship" not in finding:
                finding["evidence_relationship"] = None
            finding.setdefault("source_page", None)
            finding.setdefault("start_char", None)
            finding.setdefault("end_char", None)
            conn.execute(
                """
                INSERT INTO findings
                    (draft_version_id, claim_mention_id, rule_id, fact_version_id,
                     finding_type, evidence_relationship, result, status, severity,
                     source_page, start_char, end_char,
                     quoted_text, explanation, suggested_correction)
                VALUES
                    (:draft_version_id, :claim_mention_id, :rule_id, :fact_version_id,
                     :finding_type, :evidence_relationship, :result, :status, :severity,
                     :source_page, :start_char, :end_char,
                     :quoted_text, :explanation, :suggested_correction)
                """,
                finding,
            )
        conn.commit()

    logger.info(
        "Review pipeline complete: draft_version_id=%d, total_findings=%d, ai_errors=%d, claims=%d",
        draft_version_id,
        len(all_findings),
        len(ai_errors),
        len(claim_mentions),
    )

    used_fb = getattr(ai_provider, "used_fallback", False)
    last_mod = getattr(ai_provider, "last_model_used", None)
    fallback_reason = getattr(ai_provider, "fallback_reason", None)

    if not ai_consent:
        ai_status = "not_consented"
        ai_summary = (
            "AI-assisted assessments were not run because explicit external-AI consent "
            "was not provided; deterministic checks still ran."
        )
    elif ai_operations and ai_errors:
        ai_status = "partial"
        ai_summary = "Some AI-assisted checks could not be completed; completed checks and deterministic findings are available."
    elif len(ai_operations) >= 2 or (claim_mentions and not ai_errors):
        if used_fb:
            ai_status = "fallback_success"
            if fallback_reason and "NVIDIA NIM" in fallback_reason:
                ai_summary = (
                    f"AI review used Google Gemini after NVIDIA NIM was unavailable "
                    f"(model '{last_mod}')."
                )
            else:
                ai_summary = f"AI review completed with fallback model '{last_mod}'."
        else:
            ai_status = "success"
            ai_summary = "AI-assisted checks completed."
    elif ai_operations:
        ai_status = "partial"
        ai_summary = f"AI partially assisted by '{last_mod}'."
    else:
        ai_status = "unavailable"
        ai_summary = "AI service unavailable (quota/timeout). Deterministic brand rules completed authoritatively."

    return {
        "draft_version_id": draft_version_id,
        "findings": all_findings,
        "ai_errors": ai_errors,
        "ai_warnings": list(dict.fromkeys(ai_warnings)),
        "claim_count": len(claim_mentions),
        "ai_status": ai_status,
        "ai_summary": ai_summary,
        "ai_operations": ai_operations,
        "ai_operation_status": ai_operation_status,
        "last_model_used": last_mod,
        "used_fallback": used_fb,
        "ai_fallback_reason": fallback_reason,
        "ai_response_metadata": ai_response_metadata,
    }


def update_finding_status(
    finding_id: int,
    status: str,
    creator_note: str | None = None,
    suggested_correction: str | None = None,
) -> None:
    """Update a finding's status and optional creator note / correction."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE findings
            SET status = ?,
                creator_note = COALESCE(?, creator_note),
                suggested_correction = COALESCE(?, suggested_correction),
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (status, creator_note, suggested_correction, finding_id),
        )
        conn.commit()


def record_finding_override(
    finding_id: int,
    decision: str,
    reason: str,
    reviewer: str = "demo_reviewer",
) -> None:
    """Append a Reviewer decision about a Creator dismissal of a high-impact issue."""
    decision = decision.strip().upper()
    if decision not in ("ACCEPT", "REJECT"):
        raise ValueError("Override decision must be ACCEPT or REJECT.")
    if not reason.strip():
        raise ValueError("A documented Reviewer reason is required.")
    if not reviewer.strip():
        raise ValueError("Reviewer identity is required.")

    with get_connection() as conn:
        finding = conn.execute(
            """
            SELECT f.status, f.severity, f.result, f.finding_type,
                   f.creator_note, dv.draft_id
            FROM findings f
            JOIN draft_versions dv ON dv.id = f.draft_version_id
            WHERE f.id = ?
            """,
            (finding_id,),
        ).fetchone()
        if finding is None:
            raise ValueError("Finding does not exist.")
        if finding["status"] != FindingStatus.DISMISSED.value or not finding["creator_note"]:
            raise ValueError("Only a Creator dismissal with a documented reason can be reviewed.")
        if finding["finding_type"] == "fact_match":
            raise ValueError("Evidence and claim findings cannot be bypassed by dismissal overrides.")
        if finding["severity"] not in (Severity.HIGH.value, Severity.CRITICAL.value) or finding["result"] not in (
            FindingResult.FAIL.value,
            FindingResult.REVIEW_REQUIRED.value,
            FindingResult.WARNING.value,
        ):
            raise ValueError("Only unresolved HIGH/CRITICAL findings can receive an override decision.")

        review = conn.execute(
            "SELECT id FROM reviews WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
            (finding["draft_id"],),
        ).fetchone()
        if review is None:
            review_id = conn.execute(
                "INSERT INTO reviews (draft_id) VALUES (?)",
                (finding["draft_id"],),
            ).lastrowid
        else:
            review_id = review["id"]

        normalized_reason = reason.strip()
        conn.execute(
            """
            INSERT INTO finding_overrides (finding_id, reviewer, decision, reason)
            VALUES (?, ?, ?, ?)
            """,
            (finding_id, reviewer.strip(), decision, normalized_reason),
        )
        conn.execute(
            """
            INSERT INTO review_events (review_id, finding_id, event_type, actor, note)
            VALUES (?, ?, 'OVERRIDE', ?, ?)
            """,
            (
                review_id,
                finding_id,
                reviewer.strip(),
                f"{decision} Creator dismissal: {normalized_reason}",
            ),
        )
        conn.commit()


def get_findings_for_draft_version(draft_version_id: int) -> list[dict]:
    """Return all findings for a draft version, joined with rule, guideline, and fact info."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT f.*,
                   (
                       SELECT fo.decision FROM finding_overrides fo
                       WHERE fo.finding_id = f.id
                       ORDER BY fo.id DESC LIMIT 1
                   ) AS override_decision,
                   (
                       SELECT fo.reason FROM finding_overrides fo
                       WHERE fo.finding_id = f.id
                       ORDER BY fo.id DESC LIMIT 1
                   ) AS override_reason,
                   (
                       SELECT fo.reviewer FROM finding_overrides fo
                       WHERE fo.finding_id = f.id
                       ORDER BY fo.id DESC LIMIT 1
                   ) AS override_reviewer,
                   br.name AS rule_name,
                   br.rule_type AS rule_type,
                   br.description AS rule_description,
                   g.id AS guideline_id,
                   g.title AS guideline_title,
                   g.source_ref AS guideline_source_ref,
                   g.version AS guideline_version,
                   cm.claim_text,
                   cm.start_char AS claim_start_char,
                   cm.end_char AS claim_end_char,
                   cm.page AS claim_page,
                   cm.paragraph AS claim_paragraph,
                   fact.id AS fact_id,
                   fact.name AS fact_name,
                   fv.statement AS fact_statement,
                   fv.version_number AS fact_version_number,
                   fv.evidence_ref AS fact_evidence_ref
            FROM findings f
            LEFT JOIN brand_rules br ON br.id = f.rule_id
            LEFT JOIN guidelines g ON g.id = br.guideline_id
            LEFT JOIN claim_mentions cm ON cm.id = f.claim_mention_id
            LEFT JOIN fact_versions fv ON fv.id = f.fact_version_id
            LEFT JOIN facts fact ON fact.id = fv.fact_id
            WHERE f.draft_version_id = ?
            ORDER BY
                CASE f.severity
                    WHEN 'CRITICAL' THEN 1
                    WHEN 'HIGH' THEN 2
                    WHEN 'MEDIUM' THEN 3
                    WHEN 'LOW' THEN 4
                END,
                f.id
            """,
            (draft_version_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_draft_revision(
    draft_id: int,
    updated_text: str,
    source_type: str = "edit",
    finding_id: int | None = None,
    creator_note: str | None = None,
    suggested_correction: str | None = None,
    auto_rereview: bool = False,
    ai_provider: Any = None,
    ai_consent: bool = False,
    *,
    actor_role: UserRole | str | None = None,
) -> int:
    """
    Create a new draft_version for an edited draft, preserving revision history.
    If finding_id is provided, updates that finding's status to EDITED.
    If auto_rereview is True, automatically runs the review pipeline on the new version.
    Returns the new draft_version_id.
    """
    role_value = actor_role.value if isinstance(actor_role, UserRole) else str(actor_role or "")
    if role_value.casefold() != UserRole.CREATOR.value.casefold():
        raise PermissionError("Creator role required to create draft revisions.")
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(version_number), 0) + 1 AS next_v
            FROM draft_versions WHERE draft_id = ?
            """,
            (draft_id,),
        ).fetchone()
        next_v = row["next_v"]
        previous = conn.execute(
            "SELECT visual_metadata FROM draft_versions WHERE draft_id = ? "
            "ORDER BY version_number DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        try:
            visual_metadata = json.loads(previous["visual_metadata"] or "{}") if previous else {}
        except (json.JSONDecodeError, TypeError):
            visual_metadata = {}
        if not isinstance(visual_metadata, dict):
            visual_metadata = {}
        visual_metadata["external_ai_consent"] = bool(ai_consent)

        cur = conn.execute(
            "INSERT INTO draft_versions "
            "(draft_id, version_number, raw_text, source_type, page_map, visual_metadata) "
            "VALUES (?, ?, ?, ?, '{}', ?)",
            (draft_id, next_v, updated_text, source_type, json.dumps(visual_metadata)),
        )
        new_version_id = cur.lastrowid

        # Update draft status to DRAFT and timestamp
        conn.execute(
            "UPDATE drafts SET status = 'DRAFT', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (draft_id,),
        )

        # If a finding was edited, update its status
        if finding_id:
            conn.execute(
                """
                UPDATE findings
                SET status = 'EDITED',
                    creator_note = COALESCE(?, creator_note),
                    suggested_correction = COALESCE(?, suggested_correction),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                WHERE id = ?
                """,
                (creator_note, suggested_correction, finding_id),
            )

        # Record review event if review exists
        review = conn.execute(
            "SELECT id FROM reviews WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if review:
            conn.execute(
                "INSERT INTO review_events (review_id, event_type, actor, note) VALUES (?, 'COMMENT', 'demo_creator', ?)",
                (review["id"], f"Draft revision v{next_v} created: {creator_note or 'Creator updated copy'}"),
            )

        conn.commit()

    if auto_rereview:
        with get_connection() as conn:
            draft = conn.execute(
                "SELECT product_id, market, channel, brief_id, content_type FROM drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()

        if ai_provider is None and ai_consent:
            try:
                from ai.provider_factory import get_provider
                ai_provider = get_provider()
            except Exception:
                class _OfflineAI:
                    def generate_text(self, prompt, **kw):
                        from ai.provider_factory import AIProviderError
                        raise AIProviderError("AI Provider offline.")
                ai_provider = _OfflineAI()

        try:
            run_review(
                draft_id=draft_id,
                raw_text=updated_text,
                source_type=source_type,
                page_map={},
                product_id=draft["product_id"] if draft else None,
                market=draft["market"] if draft else None,
                channel=draft["channel"] if draft else None,
                content_type=draft["content_type"] if draft else None,
                brief_id=draft["brief_id"] if draft else None,
                ai_provider=ai_provider,
                visual_metadata=visual_metadata,
                draft_version_id=new_version_id,
                ai_consent=ai_consent,
            )
        except Exception as exc:
            logger.error(
                "Automatic re-review failed on draft_version_id=%d (%s)",
                new_version_id,
                safe_error_summary(exc),
            )
            raise

    return new_version_id


def record_review_event(
    review_id: int,
    event_type: str,
    actor: str,
    note: str | None = None,
) -> None:
    """Record an audit trail event in review_events."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO review_events (review_id, event_type, actor, note) VALUES (?, ?, ?, ?)",
            (review_id, event_type, actor, note),
        )
        conn.commit()


def request_guideline_clarification(
    draft_id: int,
    finding_id: int,
    note: str,
    actor: str = "demo_creator",
) -> None:
    """Record an explicit, auditable clarification request and escalation."""
    if not note.strip():
        raise ValueError("A clarification request needs a documented reason.")
    with get_connection() as conn:
        finding = conn.execute(
            """
            SELECT f.id FROM findings f
            JOIN draft_versions dv ON dv.id = f.draft_version_id
            WHERE f.id = ? AND dv.draft_id = ?
            """,
            (finding_id, draft_id),
        ).fetchone()
        if finding is None:
            raise ValueError("Finding does not belong to this draft.")
        review = conn.execute(
            "SELECT id FROM reviews WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if review is None:
            review_id = conn.execute(
                "INSERT INTO reviews (draft_id) VALUES (?)", (draft_id,)
            ).lastrowid
        else:
            review_id = review["id"]
        event_note = f"GUIDELINE_CLARIFICATION: Finding #{finding_id}: {note.strip()}"
        conn.execute(
            "INSERT INTO review_events (review_id, event_type, actor, note) "
            "VALUES (?, 'ESCALATED', ?, ?)",
            (review_id, actor, event_note),
        )
        conn.execute(
            """
            UPDATE findings SET status = 'ESCALATED', creator_note = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
            """,
            (note.strip(), finding_id),
        )
        conn.commit()
