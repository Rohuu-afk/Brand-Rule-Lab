"""
ui/presentation.py
------------------
Translates internal system enums and technical status identifiers into clear,
professional, everyday language for marketing, creator, and brand users.

Internal IDs remain in the database and code, but user-facing UI displays
everyday business terminology with helpful contextual explanations.
"""

from __future__ import annotations

from typing import Any


STATUS_TRANSLATIONS = {
    # Finding results
    "FAIL": "Needs a change",
    "PASS": "Looks good",
    "WARNING": "Check recommended",
    "REVIEW_REQUIRED": "Needs human review",
    "UNLINKED": "No supporting fact linked",
    "NOT_OBSERVED": "Not checked",
    "ERROR": "Check couldn't be completed",

    # Evidence relationships
    "SUPPORTS": "Supported by approved evidence",
    "CONTRADICTS": "Conflicts with approved evidence",
    "RELATED_NOT_EVIDENCE": "Related evidence, but not proof",
    "NO_MATCH": "No matching approved evidence",

    # Workflow statuses
    "OPEN": "Open — Needs attention",
    "ACCEPTED": "Correction accepted",
    "EDITED": "Edited (re-check required)",
    "DISMISSED": "Dismissed with rationale",
    "ESCALATED": "Escalated for senior review",
    "PENDING_APPROVAL": "Waiting for approval",
    "APPROVED": "Approved & verified",
    "REJECTED": "Rejected",
    "CHANGES_REQUESTED": "Changes requested",
    "CHECKED": "Review complete",
    "DRAFT": "Draft (in progress)",
    "READY_FOR_REVIEW": "Submitted for approval",
    "AWAITING_RECHECK": "Checks pending",

    # Claim drift & fact statuses
    "CURRENT": "Active & verified",
    "EXPIRED": "Evidence expired",
}

STATUS_EXPLANATIONS = {
    "NOT_OBSERVED": "This check was not completed or required information was unavailable. It is not a pass.",
    "UNLINKED": "No approved evidence in the brand library substantiates this claim. This does not mean the statement is false, but it cannot be published without verified evidence.",
    "CONTRADICTS": "Approved clinical evidence or regulatory specifications directly contradict this copy. This must be corrected before publication.",
    "RELATED_NOT_EVIDENCE": "An approved fact touches on this product or topic, but does not evidence this specific assertion (e.g., clinical study timing does not establish ingredient concentration).",
    "REVIEW_REQUIRED": "A brand manager or reviewer must verify this phrasing before publishing.",
    "FAIL": "The copy directly violates a brand rule or guideline.",
    "PASS": "The copy complies with the applicable brand rule or approved fact.",
    "ERROR": "This check could not be completed. Review the technical details or try the check again.",
}


def humanize(code: str | None, default: str = "") -> str:
    """Translate an internal enum or status code into everyday user-friendly text."""
    if not code:
        return default
    return STATUS_TRANSLATIONS.get(code.upper(), code.replace("_", " ").title())


def explain_status(code: str | None) -> str:
    """Return a brief context explanation for a technical status."""
    if not code:
        return ""
    return STATUS_EXPLANATIONS.get(code.upper(), "")


def get_badge_class(code: str | None) -> str:
    """Return the CSS class for styling badges."""
    c = (code or "").upper()
    if c in ("PASS", "SUPPORTS", "APPROVED", "CURRENT"):
        return "badge-pass"
    if c in ("FAIL", "CONTRADICTS", "EXPIRED", "REJECTED", "CRITICAL"):
        return "badge-fail"
    if c in ("WARNING", "REVIEW_REQUIRED", "PENDING_APPROVAL", "HIGH"):
        return "badge-warning"
    if c in ("UNLINKED", "RELATED_NOT_EVIDENCE", "NO_MATCH", "MEDIUM"):
        return "badge-unlinked"
    if c in ("NOT_OBSERVED", "LOW"):
        return "badge-review"
    return "badge-error"


def render_pill(label: str, badge_type: str = "review", tooltip: str = "") -> str:
    """Render a clean HTML pill badge with optional hover tooltip."""
    title_attr = f' title="{tooltip}"' if tooltip else ""
    return f'<span class="badge {badge_type}"{title_attr}>{label}</span>'
