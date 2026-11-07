"""
models/enums.py
---------------
Single source of truth for every status and result code used in the app.
Import from here — never use raw strings for status values in other modules.
"""

from enum import Enum


class DraftStatus(str, Enum):
    """Lifecycle of a submitted draft."""
    DRAFT = "DRAFT"
    CHECKED = "CHECKED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class FindingStatus(str, Enum):
    """
    What action a Creator has taken on a finding.
    OPEN        — newly generated, no action yet.
    ACCEPTED    — Creator accepted the suggested correction.
    EDITED      — Creator provided their own correction (re-check required).
    DISMISSED   — Creator dismissed with a reason (Reviewer may override).
    ESCALATED   — Reviewer escalated; requires documented override.
    """
    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"
    EDITED = "EDITED"
    DISMISSED = "DISMISSED"
    ESCALATED = "ESCALATED"


class FindingResult(str, Enum):
    """
    What the check engine concluded.
    PASS            — deterministic predicate passed.
    FAIL            — deterministic predicate failed.
    WARNING         — potential issue, human judgement needed.
    UNLINKED        — no approved fact found; does not mean false.
    REVIEW_REQUIRED — AI or rule flagged; human must decide.
    NOT_OBSERVED    — external/live asset state not checked.
    ERROR           — engine raised an exception during evaluation.
    """
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    UNLINKED = "UNLINKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_OBSERVED = "NOT_OBSERVED"
    ERROR = "ERROR"


class EvidenceRelationship(str, Enum):
    """
    Semantic relationship between an extracted claim and an approved fact version.
    SUPPORTS             — The approved fact directly supports the specific claim.
    CONTRADICTS          — The approved fact provides evidence inconsistent with the claim.
    RELATED_NOT_EVIDENCE — The fact is related to the subject/product but does not substantiate the claim.
    NO_MATCH             — No eligible fact provides meaningful evidence.
    """
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    RELATED_NOT_EVIDENCE = "RELATED_NOT_EVIDENCE"
    NO_MATCH = "NO_MATCH"


class ClaimDriftStatus(str, Enum):
    """
    Health of an approved claim relative to its linked fact versions.
    CURRENT         — all linked fact versions still active.
    REVIEW_REQUIRED — at least one linked fact version changed.
    EXPIRED         — at least one linked fact version expired.
    """
    CURRENT = "CURRENT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    EXPIRED = "EXPIRED"


class FactApprovalStatus(str, Enum):
    """
    Approval lifecycle of a fact version.
    PENDING_APPROVAL — newly submitted, awaiting Fact Owner sign-off.
    APPROVED         — active and usable in checks.
    REJECTED         — rejected by Fact Owner; not usable.
    EXPIRED          — manually expired by Fact Owner; flags dependent claims.
    """
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class RuleType(str, Enum):
    """Deterministic predicate types supported by the rule engine."""
    TERM_LIST = "term_list"
    REGEX = "regex"
    NUMERIC_RANGE = "numeric_range"
    DATE_WINDOW = "date_window"
    REQUIRED_TEXT = "required_text"
    WCAG_CONTRAST = "wcag_contrast"
    ASSET_HASH = "asset_hash"
    LOGO_PHASH = "logo_phash"


class Severity(str, Enum):
    """Finding severity level, used for escalation thresholds."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class UserRole(str, Enum):
    """Simulated demo roles — no real authentication."""
    CREATOR = "Creator"
    REVIEWER = "Reviewer"
    FACT_OWNER = "Fact Owner"


class ReviewEventType(str, Enum):
    """Types of events recorded in the review_events audit trail."""
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    ESCALATED = "ESCALATED"
    OVERRIDE = "OVERRIDE"
    COMMENT = "COMMENT"
