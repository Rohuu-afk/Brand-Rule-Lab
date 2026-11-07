"""
models/schemas.py
-----------------
Pydantic-free JSON schemas used to validate AI responses before they touch
the database.  Every AI call must pass its response through validate_ai_response()
before the result is used anywhere.

We use jsonschema (already in requirements.txt) so there is no extra dependency.
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema

# ---------------------------------------------------------------------------
# Schema: claim extraction result
# ---------------------------------------------------------------------------
# Python generates candidates and retains their source offsets. The AI returns
# only candidate IDs and categories; source text always comes from Python.

CLAIM_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["claims"],
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["candidate_id", "claim_type_hint"],
                "additionalProperties": False,
                "properties": {
                    "candidate_id": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": r".*\S.*",
                    },
                    "claim_type_hint": {
                        "type": "string",
                        "enum": [
                            "factual",
                            "efficacy",
                            "ingredient",
                            "numerical",
                            "general_promotional",
                        ],
                    },
                },
            },
        }
    },
}

_CLAIM_EXTRACTION_ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["claims"],
    "additionalProperties": False,
    "properties": {"claims": {"type": "array"}},
}
_CLAIM_EXTRACTION_ITEM_SCHEMA = CLAIM_EXTRACTION_SCHEMA["properties"]["claims"]["items"]

# ---------------------------------------------------------------------------
# Schema: proposed brand knowledge extracted from source documents
# ---------------------------------------------------------------------------

KNOWLEDGE_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["entries"],
    "additionalProperties": False,
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "category", "content", "explanation", "page_number",
                    "source_quote", "confidence", "warnings",
                ],
                "additionalProperties": False,
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "BRAND_RULE", "TONE", "TERMINOLOGY", "PRODUCT_FACT",
                            "EVIDENCE", "RESTRICTION", "ASSET",
                        ],
                    },
                    "content": {"type": "string", "minLength": 1},
                    "explanation": {"type": "string"},
                    "page_number": {"type": ["integer", "null"], "minimum": 1},
                    "source_quote": {"type": "string", "minLength": 1},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "warnings": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}
_KNOWLEDGE_EXTRACTION_ENVELOPE_SCHEMA = {
    "type": "object",
    "required": ["entries"],
    "additionalProperties": False,
    "properties": {"entries": {"type": "array"}},
}
_KNOWLEDGE_EXTRACTION_ITEM_SCHEMA = KNOWLEDGE_EXTRACTION_SCHEMA["properties"]["entries"]["items"]

# ---------------------------------------------------------------------------
# Schema: fact ranking & semantic evidence evaluation result
# ---------------------------------------------------------------------------
# The AI receives a filtered list of eligible fact versions and evaluates each
# for evidence relationship (SUPPORTS, CONTRADICTS, RELATED_NOT_EVIDENCE, NO_MATCH),
# confidence (0.0 - 1.0), and clear explanation.
#
# Accepts both the enhanced semantic evaluation format and legacy ranked list.

FACT_RANKING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "best_relationship": {
            "type": "string",
            "enum": ["SUPPORTS", "CONTRADICTS", "RELATED_NOT_EVIDENCE", "NO_MATCH"],
        },
        "matched_fact_version_id": {"type": ["integer", "null"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "explanation": {"type": "string"},
        "suggested_correction": {"type": ["string", "null"]},
        "evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["fact_version_id", "relationship"],
                "additionalProperties": True,
                "properties": {
                    "fact_version_id": {"type": "integer"},
                    "relationship": {
                        "type": "string",
                        "enum": ["SUPPORTS", "CONTRADICTS", "RELATED_NOT_EVIDENCE", "NO_MATCH"],
                    },
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "relevance_note": {"type": "string"},
                },
            },
        },
        "ranked_fact_version_ids": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["fact_version_id"],
                "additionalProperties": True,
                "properties": {
                    "fact_version_id": {"type": "integer"},
                    "relevance_note": {"type": "string"},
                    "relationship": {"type": "string"},
                },
            },
        },
    },
}

# ---------------------------------------------------------------------------
# Schema: tone / contextual findings result
# ---------------------------------------------------------------------------
# The AI returns zero or more tone/messaging findings.

TONE_FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["tone_findings"],
    "additionalProperties": False,
    "properties": {
        "tone_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["quoted_text", "issue", "explanation", "suggested_correction"],
                "additionalProperties": False,
                "properties": {
                    "quoted_text": {"type": "string"},
                    "issue": {"type": "string"},
                    "explanation": {"type": "string"},
                    "suggested_correction": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    },
                },
            },
        }
    },
}

# ---------------------------------------------------------------------------
# Schema: correction suggestion for a single finding
# ---------------------------------------------------------------------------

CORRECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["suggested_correction", "explanation"],
    "additionalProperties": False,
    "properties": {
        "suggested_correction": {"type": "string", "minLength": 1},
        "explanation": {"type": "string"},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
    },
}


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

class AIResponseValidationError(ValueError):
    """Raised when an AI response does not conform to the expected schema."""


def validate_ai_response(raw_text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """
    Parse *raw_text* as JSON and validate it against *schema*.

    Returns the parsed dict on success.
    Raises AIResponseValidationError with a human-readable message on failure.
    The caller must handle this exception and show a UI message.
    """
    data = _parse_ai_json(raw_text)

    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        raise AIResponseValidationError(
            f"AI response failed schema validation: {exc.message}\n"
            f"Path: {list(exc.absolute_path)}"
        ) from exc

    return data  # type: ignore[return-value]


def validate_claim_extraction_response(
    raw_text: str,
) -> tuple[list[tuple[int, dict[str, Any]]], list[tuple[int, str]]]:
    """Validate extraction items independently so one malformed claim cannot discard valid ones."""
    data = _parse_ai_json(raw_text)
    try:
        jsonschema.validate(instance=data, schema=_CLAIM_EXTRACTION_ENVELOPE_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise AIResponseValidationError(
            f"AI claim-extraction response has an invalid envelope: {exc.message}\n"
            f"Path: {list(exc.absolute_path)}"
        ) from exc

    valid_claims: list[tuple[int, dict[str, Any]]] = []
    invalid_claims: list[tuple[int, str]] = []
    for index, claim in enumerate(data["claims"]):
        try:
            jsonschema.validate(instance=claim, schema=_CLAIM_EXTRACTION_ITEM_SCHEMA)
        except jsonschema.ValidationError as exc:
            invalid_claims.append((index, exc.message))
        else:
            valid_claims.append((index, claim))
    return valid_claims, invalid_claims


def validate_knowledge_extraction_response(
    raw_text: str,
) -> tuple[list[tuple[int, dict[str, Any]]], list[tuple[int, str]]]:
    """Validate extracted entries independently to retain valid items in mixed output."""
    data = _parse_ai_json(raw_text)
    try:
        jsonschema.validate(instance=data, schema=_KNOWLEDGE_EXTRACTION_ENVELOPE_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise AIResponseValidationError(
            f"AI knowledge response has an invalid envelope: {exc.message}"
        ) from exc

    valid_entries: list[tuple[int, dict[str, Any]]] = []
    invalid_entries: list[tuple[int, str]] = []
    for index, entry in enumerate(data["entries"]):
        try:
            jsonschema.validate(instance=entry, schema=_KNOWLEDGE_EXTRACTION_ITEM_SCHEMA)
        except jsonschema.ValidationError as exc:
            invalid_entries.append((index, exc.message))
        else:
            valid_entries.append((index, entry))
    return valid_entries, invalid_entries


def _parse_ai_json(raw_text: str) -> Any:
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AIResponseValidationError(
            f"AI returned invalid JSON: {exc}\n\nRaw response (first 500 chars):\n{raw_text[:500]}"
        ) from exc
