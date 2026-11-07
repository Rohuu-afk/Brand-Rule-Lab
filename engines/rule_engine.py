"""
engines/rule_engine.py
-----------------------
Deterministic predicate executor.

The rule engine NEVER calls the AI model — it implements all checks in pure Python.
Only rules that are:
  - is_active = 1
  - owner_approved = 1
  - schema-valid (parameters parse as JSON with required keys)
...may execute.  Any rule that fails schema validation is skipped with an ERROR
result logged.

Supported rule_types (from RuleType enum):
  - term_list
  - regex
  - numeric_range
  - date_window
  - required_text
  - wcag_contrast      (pure Python relative luminance calculation per WCAG 2.1)
  - asset_hash         (SHA-256 / perceptual hash comparison)
  - alt_text           (Accessibility baseline check for image alt text)

Demonstration rules covered:
  - V-04: Calm, credible tone; flag absolute promises / unsupported guarantees ("guaranteed", "miracle", "100%", "cure").
  - T-12: Prohibit abbreviation "BPO" in consumer copy, suggest "Benzoyl Peroxide".
  - F-112: Approved clinical evidence supports a four-week timeline, not a three-day result ("3 days", "in days", "overnight").
  - Unsupported Superlatives: Prohibit unverified superlatives ("the best", "#1") for GlowUp.

Visual & Accessibility baseline notes:
  - Relative luminance calculation for hex colors, ratio >= 4.5:1 for normal text (WCAG AA).
  - SHA-256 asset matching against approved brand assets.
  - Alt-text presence and descriptive quality checks.
  - Note: Baseline accessibility check; does not claim advanced computer vision or full WCAG compliance.
"""

from __future__ import annotations

import hashlib
import json
import re
import contextvars
import time
from datetime import date, datetime
from typing import Callable

import regex as timeout_regex

from models.enums import FindingResult, RuleType


# ---------------------------------------------------------------------------
# Result class & Type alias for predicate functions
# ---------------------------------------------------------------------------
class RuleExecutionResult:
    """
    Result of a single rule execution.
    Unpacks as a 2-tuple (result, explanation) for backwards compatibility,
    while also exposing offending_text, suggested_correction, start_char, and end_char.
    """
    def __init__(
        self,
        result: FindingResult,
        explanation: str,
        offending_text: str | None = None,
        suggested_correction: str | None = None,
        start_char: int | None = None,
        end_char: int | None = None,
    ):
        self.result = result
        self.explanation = explanation
        self.offending_text = offending_text
        self.suggested_correction = suggested_correction
        self.start_char = start_char
        self.end_char = end_char

    def __iter__(self):
        yield self.result
        yield self.explanation

    def __getitem__(self, index):
        return (self.result, self.explanation)[index]

    def __len__(self):
        return 2

    def __eq__(self, other):
        if isinstance(other, (tuple, list)) and len(other) == 2:
            return (self.result, self.explanation) == (other[0], other[1])
        if isinstance(other, RuleExecutionResult):
            return (
                self.result == other.result
                and self.explanation == other.explanation
                and self.offending_text == other.offending_text
                and self.suggested_correction == other.suggested_correction
            )
        return False

    def __repr__(self) -> str:
        return (
            f"RuleExecutionResult(result={self.result.value!r}, explanation={self.explanation!r}, "
            f"offending_text={self.offending_text!r}, suggested_correction={self.suggested_correction!r}, "
            f"start_char={self.start_char!r}, end_char={self.end_char!r})"
        )


PredicateFn = Callable[[str, dict], RuleExecutionResult]

MAX_REGEX_PATTERN_CHARS = 512
MAX_REGEX_INPUT_CHARS = 100_000
MAX_REGEX_CALL_SECONDS = 0.05
MAX_REGEX_REVIEW_SECONDS = 1.5
MAX_REGEX_CALLS_PER_REVIEW = 128


class RegexExecutionError(ValueError):
    """A configured regex was rejected, timed out, or exceeded its review budget."""


class RegexExecutionBudget:
    def __init__(
        self,
        max_seconds: float = MAX_REGEX_REVIEW_SECONDS,
        max_calls: int = MAX_REGEX_CALLS_PER_REVIEW,
    ) -> None:
        self.started_at = time.monotonic()
        self.max_seconds = max_seconds
        self.max_calls = max_calls
        self.calls = 0

    def next_timeout(self) -> float:
        elapsed = time.monotonic() - self.started_at
        remaining = self.max_seconds - elapsed
        if self.calls >= self.max_calls or remaining <= 0:
            raise RegexExecutionError("Aggregate regex execution budget exceeded for this review.")
        self.calls += 1
        return min(MAX_REGEX_CALL_SECONDS, remaining)


_ACTIVE_REGEX_BUDGET: contextvars.ContextVar[RegexExecutionBudget | None] = (
    contextvars.ContextVar("brand_rule_lab_regex_budget", default=None)
)


def validate_regex_pattern(pattern: str) -> timeout_regex.Pattern:
    """Enforce a bounded pattern size and compile with the timeout-capable engine."""
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("Regex pattern must be a non-empty string.")
    if len(pattern) > MAX_REGEX_PATTERN_CHARS:
        raise ValueError(
            f"Regex pattern exceeds the {MAX_REGEX_PATTERN_CHARS}-character limit."
        )
    try:
        return timeout_regex.compile(pattern)
    except timeout_regex.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc


def _configured_regex_search(pattern: str, text: str, flags: int = 0):
    if len(text) > MAX_REGEX_INPUT_CHARS:
        raise RegexExecutionError(
            f"Regex input exceeds the {MAX_REGEX_INPUT_CHARS:,}-character processing limit."
        )
    validate_regex_pattern(pattern)
    budget = _ACTIVE_REGEX_BUDGET.get() or RegexExecutionBudget()
    try:
        return timeout_regex.search(
            pattern, text, flags=flags, timeout=budget.next_timeout()
        )
    except TimeoutError as exc:
        raise RegexExecutionError(
            f"Regex exceeded its {MAX_REGEX_CALL_SECONDS:.2f}-second execution timeout."
        ) from exc
    except timeout_regex.error as exc:
        raise RegexExecutionError(f"Invalid regex pattern: {exc}") from exc


def _configured_regex_finditer(pattern: str, text: str) -> list:
    if len(text) > MAX_REGEX_INPUT_CHARS:
        raise RegexExecutionError(
            f"Regex input exceeds the {MAX_REGEX_INPUT_CHARS:,}-character processing limit."
        )
    validate_regex_pattern(pattern)
    budget = _ACTIVE_REGEX_BUDGET.get() or RegexExecutionBudget()
    try:
        return list(timeout_regex.finditer(pattern, text, timeout=budget.next_timeout()))
    except TimeoutError as exc:
        raise RegexExecutionError(
            f"Regex exceeded its {MAX_REGEX_CALL_SECONDS:.2f}-second execution timeout."
        ) from exc
    except timeout_regex.error as exc:
        raise RegexExecutionError(f"Invalid regex pattern: {exc}") from exc


# ---------------------------------------------------------------------------
# Relative Luminance & WCAG Contrast Helpers (Pure Python)
# ---------------------------------------------------------------------------

def calculate_relative_luminance(hex_color: str) -> float:
    """
    Calculate WCAG 2.1 relative luminance for an sRGB hex color.
    Formula: L = 0.2126 * R + 0.7152 * G + 0.0722 * B
    where R, G, B are linearized components.
    """
    clean_hex = hex_color.lstrip("#").strip()
    if len(clean_hex) == 3:
        clean_hex = "".join(ch * 2 for ch in clean_hex)
    if len(clean_hex) != 6:
        raise ValueError(f"Invalid hex color format: '{hex_color}'")

    r_srgb = int(clean_hex[0:2], 16) / 255.0
    g_srgb = int(clean_hex[2:4], 16) / 255.0
    b_srgb = int(clean_hex[4:6], 16) / 255.0

    def _linearize(val: float) -> float:
        return val / 12.92 if val <= 0.04045 else ((val + 0.055) / 1.055) ** 2.4

    r_lin = _linearize(r_srgb)
    g_lin = _linearize(g_srgb)
    b_lin = _linearize(b_srgb)

    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def calculate_contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """
    Calculate WCAG 2.1 contrast ratio: (L1 + 0.05) / (L2 + 0.05),
    where L1 is the relative luminance of the lighter color.
    """
    l1 = calculate_relative_luminance(fg_hex)
    l2 = calculate_relative_luminance(bg_hex)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# Individual predicates
# ---------------------------------------------------------------------------

def _check_term_list(text: str, params: dict) -> RuleExecutionResult:
    """
    params:
        mode           : 'banned' | 'required'
        terms          : list[str]
        case_sensitive : bool (default False)
        suggested_correction : optional str
    """
    mode = params.get("mode", "banned")
    terms: list[str] = params.get("terms", [])
    case_sensitive: bool = params.get("case_sensitive", False)
    suggested_correction: str | None = params.get("suggested_correction")

    haystack = text if case_sensitive else text.lower()

    if mode == "banned":
        # Find all matching terms with exact positions in the original text
        matches: list[tuple[int, int, str, str]] = []
        for term in terms:
            target = term if case_sensitive else term.lower()
            # Find earliest occurrence
            idx = haystack.find(target)
            if idx != -1:
                offending_slice = text[idx : idx + len(term)]
                matches.append((idx, idx + len(term), offending_slice, term))

        if matches:
            # Sort by earliest appearance in text
            matches.sort(key=lambda m: m[0])
            start_char, end_char, offending, _ = matches[0]
            found_terms = [m[3] for m in matches]
            custom_exp = params.get("explanation")
            base_exp = f"Banned term(s) found: {', '.join(repr(t) for t in found_terms)}"
            exp_text = f"{base_exp}. {custom_exp}" if custom_exp else base_exp
            return RuleExecutionResult(
                FindingResult.FAIL,
                exp_text,
                offending_text=offending,
                suggested_correction=suggested_correction,
                start_char=start_char,
                end_char=end_char,
            )
        return RuleExecutionResult(FindingResult.PASS, "No banned terms found.")

    elif mode == "required":
        missing = [t for t in terms if (t if case_sensitive else t.lower()) not in haystack]
        if missing:
            return RuleExecutionResult(
                FindingResult.FAIL,
                f"Required term(s) missing: {', '.join(repr(t) for t in missing)}",
                offending_text=None,
                suggested_correction=suggested_correction or (f"Include required term: '{missing[0]}'"),
                start_char=None,
                end_char=None,
            )
        return RuleExecutionResult(FindingResult.PASS, "All required terms present.")
    else:
        return RuleExecutionResult(FindingResult.ERROR, f"Unknown term_list mode: {mode!r}")


def _check_regex(text: str, params: dict) -> RuleExecutionResult:
    """
    params:
        pattern       : str  — Python regex
        require_match : bool — True: match is PASS; False: match is FAIL
        suggested_correction : optional str
    """
    pattern = params.get("pattern", "")
    require_match: bool = params.get("require_match", True)
    suggested_correction: str | None = params.get("suggested_correction")

    try:
        match = _configured_regex_search(pattern, text)
    except RegexExecutionError as exc:
        return RuleExecutionResult(FindingResult.ERROR, str(exc))

    matched = match is not None

    if require_match:
        if matched:
            return RuleExecutionResult(
                FindingResult.PASS,
                f"Required pattern found: {match.group()!r}",
                start_char=match.start(),
                end_char=match.end(),
            )
        return RuleExecutionResult(
            FindingResult.FAIL,
            f"Required pattern not found in text. Pattern: {pattern!r}",
            offending_text=None,
            suggested_correction=suggested_correction,
            start_char=None,
            end_char=None,
        )
    else:
        if matched:
            return RuleExecutionResult(
                FindingResult.FAIL,
                f"Disallowed pattern matched: {match.group()!r}",
                offending_text=match.group(),
                suggested_correction=suggested_correction,
                start_char=match.start(),
                end_char=match.end(),
            )
        return RuleExecutionResult(FindingResult.PASS, "Disallowed pattern not present.")


def _check_numeric_range(text: str, params: dict) -> RuleExecutionResult:
    """
    params:
        extract_pattern : str   — regex with ONE capture group for the number
        min             : float — inclusive lower bound (or None)
        max             : float — inclusive upper bound (or None)
        suggested_correction : optional str
    """
    pattern = params.get("extract_pattern", "")
    min_val = params.get("min")
    max_val = params.get("max")
    suggested_correction: str | None = params.get("suggested_correction")

    try:
        matches = _configured_regex_finditer(pattern, text)
    except RegexExecutionError as exc:
        return RuleExecutionResult(FindingResult.ERROR, f"Invalid/unsafe regex in numeric_range: {exc}")

    if not matches:
        return RuleExecutionResult(FindingResult.PASS, "Numeric pattern not found in text; rule not applicable.")

    violations = []
    offending = None
    start_char = None
    end_char = None

    for m in matches:
        raw = m.group(1) if m.groups() else m.group(0)
        try:
            value = float(raw)
        except ValueError:
            violations.append(f"Could not parse {raw!r} as a number.")
            if offending is None:
                offending = m.group(0)
                start_char = m.start()
                end_char = m.end()
            continue
        if min_val is not None and value < min_val:
            violations.append(f"{value} < minimum {min_val}")
            if offending is None:
                offending = m.group(0)
                start_char = m.start()
                end_char = m.end()
        if max_val is not None and value > max_val:
            violations.append(f"{value} > maximum {max_val}")
            if offending is None:
                offending = m.group(0)
                start_char = m.start()
                end_char = m.end()

    if violations:
        corr = suggested_correction
        if not corr and min_val is not None and max_val is not None:
            if min_val == max_val:
                corr = f"Use approved value: {min_val}"
            else:
                corr = f"Use approved value between {min_val} and {max_val}"
        return RuleExecutionResult(
            FindingResult.FAIL,
            "Numeric range violation: " + "; ".join(violations),
            offending_text=offending,
            suggested_correction=corr,
            start_char=start_char,
            end_char=end_char,
        )
    return RuleExecutionResult(FindingResult.PASS, f"All numeric values in range [{min_val}, {max_val}].")


def _check_date_window(text: str, params: dict) -> RuleExecutionResult:
    """
    params:
        extract_pattern : str   — regex with ONE capture group for the date string
        date_format     : str   — strptime format, e.g. '%Y-%m-%d'
        earliest        : str   — ISO date or None
        latest          : str   — ISO date or None
        suggested_correction : optional str
    """
    pattern = params.get("extract_pattern", "")
    date_fmt = params.get("date_format", "%Y-%m-%d")
    earliest_str = params.get("earliest")
    latest_str = params.get("latest")
    suggested_correction: str | None = params.get("suggested_correction")

    earliest = datetime.strptime(earliest_str, "%Y-%m-%d").date() if earliest_str else None
    latest = datetime.strptime(latest_str, "%Y-%m-%d").date() if latest_str else None

    try:
        matches = _configured_regex_finditer(pattern, text)
    except RegexExecutionError as exc:
        return RuleExecutionResult(FindingResult.ERROR, f"Invalid/unsafe date_window regex: {exc}")

    if not matches:
        return RuleExecutionResult(FindingResult.PASS, "Date pattern not found; rule not applicable.")

    violations = []
    offending = None
    start_char = None
    end_char = None

    for m in matches:
        raw = m.group(1) if m.groups() else m.group(0)
        try:
            d = datetime.strptime(raw, date_fmt).date()
        except ValueError:
            violations.append(f"Cannot parse date {raw!r} with format {date_fmt!r}.")
            if offending is None:
                offending = m.group(0)
                start_char = m.start()
                end_char = m.end()
            continue
        if earliest and d < earliest:
            violations.append(f"{d} is before earliest allowed date {earliest}.")
            if offending is None:
                offending = m.group(0)
                start_char = m.start()
                end_char = m.end()
        if latest and d > latest:
            violations.append(f"{d} is after latest allowed date {latest}.")
            if offending is None:
                offending = m.group(0)
                start_char = m.start()
                end_char = m.end()

    if violations:
        return RuleExecutionResult(
            FindingResult.FAIL,
            "Date window violation: " + "; ".join(violations),
            offending_text=offending,
            suggested_correction=suggested_correction,
            start_char=start_char,
            end_char=end_char,
        )
    return RuleExecutionResult(FindingResult.PASS, "All dates within allowed window.")


def _check_required_text(text: str, params: dict) -> RuleExecutionResult:
    """
    Conditional required-text check.
    params:
        trigger_on  : str regex — if this pattern matches, the main pattern is required
        pattern     : str regex — the required pattern
        require_match: bool     — if False, pattern must NOT match
        suggested_correction : optional str
    """
    trigger_pattern = params.get("trigger_on", "")
    main_pattern = params.get("pattern", "")
    require_match = params.get("require_match", True)
    suggested_correction: str | None = params.get("suggested_correction")

    trigger_match = None
    if trigger_pattern:
        try:
            trigger_match = _configured_regex_search(
                trigger_pattern, text, timeout_regex.IGNORECASE
            )
            if not trigger_match:
                return RuleExecutionResult(FindingResult.PASS, "Trigger pattern not present; rule not applicable.")
        except RegexExecutionError as exc:
            return RuleExecutionResult(FindingResult.ERROR, f"Invalid/unsafe trigger regex: {exc}")

    sub_res = _check_regex(
        text,
        {
            "pattern": main_pattern,
            "require_match": require_match,
            "suggested_correction": suggested_correction,
        },
    )
    if sub_res.result == FindingResult.FAIL and trigger_match:
        return RuleExecutionResult(
            FindingResult.FAIL,
            sub_res.explanation,
            offending_text=trigger_match.group(),
            suggested_correction=suggested_correction or "Add approved clinical study reference to substantiate this claim.",
            start_char=trigger_match.start(),
            end_char=trigger_match.end(),
        )
    return sub_res


def _check_wcag_contrast(text: str, params: dict) -> RuleExecutionResult:
    """
    Checks WCAG 2.1 contrast ratio between foreground and background hex colours.
    Evaluates relative luminance: (L1 + 0.05) / (L2 + 0.05).
    Minimum required ratio defaults to 4.5:1 (WCAG AA for normal text).

    Note: Baseline accessibility check; does not claim advanced computer vision
    or full WCAG compliance.

    params:
        foreground : str — hex colour e.g. '#FFFFFF'
        background : str — hex colour e.g. '#000000'
        min_ratio  : float — minimum acceptable contrast ratio (default 4.5)
    """
    fg = params.get("foreground", "").strip()
    bg = params.get("background", "").strip()
    min_ratio: float = params.get("min_ratio", 4.5)

    if not fg or not bg:
        return RuleExecutionResult(FindingResult.NOT_OBSERVED, "Colour values not provided; contrast not checked.")

    try:
        ratio = calculate_contrast_ratio(fg, bg)
        if ratio >= min_ratio:
            return RuleExecutionResult(FindingResult.PASS, f"Contrast ratio {ratio:.2f}:1 meets minimum {min_ratio}:1.")
        return RuleExecutionResult(
            FindingResult.FAIL,
            f"Contrast ratio {ratio:.2f}:1 is below the required {min_ratio}:1 (foreground={fg}, background={bg}).",
            offending_text=f"fg:{fg} on bg:{bg}",
            suggested_correction=f"Increase contrast between foreground {fg} and background {bg} to at least {min_ratio}:1",
        )
    except ValueError as exc:
        return RuleExecutionResult(FindingResult.ERROR, f"Contrast check error: {exc}")
    except Exception as exc:
        return RuleExecutionResult(FindingResult.ERROR, f"Contrast check error: {exc}")


def _check_asset_hash(text: str, params: dict) -> RuleExecutionResult:
    """
    Compares a submitted asset's hash against the approved value.
    params:
        submitted_hash : str — SHA-256 hex of the submitted asset
        approved_hash  : str — SHA-256 hex of the approved asset (from brand_assets)
        hash_type      : str — 'sha256' | 'perceptual' (default sha256)
    """
    submitted = params.get("submitted_hash", "").strip()
    approved = params.get("approved_hash", "").strip()
    hash_type = params.get("hash_type", "sha256")

    if not submitted or not approved:
        return RuleExecutionResult(FindingResult.NOT_OBSERVED, "Asset hash not provided; check not performed.")

    if hash_type == "sha256":
        if submitted.lower() == approved.lower():
            return RuleExecutionResult(FindingResult.PASS, "Asset SHA-256 hash matches approved version.")
        return RuleExecutionResult(
            FindingResult.FAIL,
            f"Asset hash mismatch. Submitted: {submitted[:16]}… Approved: {approved[:16]}…",
            offending_text=f"Hash: {submitted[:16]}…",
            suggested_correction=f"Use the approved asset version with hash {approved[:16]}…",
        )
    elif hash_type == "perceptual":
        try:
            a = int(submitted, 16)
            b = int(approved, 16)
            hamming = bin(a ^ b).count("1")
            threshold = params.get("max_hamming", 10)
            if hamming <= threshold:
                return RuleExecutionResult(FindingResult.PASS, f"Perceptual hash similarity OK (distance={hamming}).")
            return RuleExecutionResult(
                FindingResult.FAIL,
                f"Perceptual hash distance {hamming} exceeds threshold {threshold}. Asset may differ significantly from the approved version.",
                offending_text=f"Perceptual distance: {hamming}",
                suggested_correction="Verify the asset matches the brand-approved visual guidelines.",
            )
        except Exception as exc:
            return RuleExecutionResult(FindingResult.ERROR, f"Perceptual hash comparison error: {exc}")
    else:
        return RuleExecutionResult(FindingResult.ERROR, f"Unknown hash_type: {hash_type!r}")


def _check_alt_text(text: str, params: dict) -> RuleExecutionResult:
    """
    Accessibility baseline: checks whether image assets include descriptive alt-text.
    Note: Baseline accessibility check; does not claim advanced computer vision
    or full WCAG compliance.

    params:
        alt_text    : str | None
        min_length  : int (default 5)
        banned_terms: list[str] (e.g. placeholder words)
    """
    alt = params.get("alt_text")
    if alt is None:
        return RuleExecutionResult(
            FindingResult.NOT_OBSERVED,
            "Alt text metadata not provided; image accessibility check not performed.",
        )

    alt_str = str(alt).strip()
    if not alt_str:
        return RuleExecutionResult(
            FindingResult.FAIL,
            "Image asset is missing descriptive alt-text (WCAG 1.1.1 Non-text Content).",
            offending_text="[Missing alt-text]",
            suggested_correction="Provide descriptive alt-text explaining the content and purpose of the visual asset.",
        )

    min_len = params.get("min_length", 5)
    banned_placeholders = params.get("banned_terms", ["image", "picture", "photo", "logo", "asset"])

    if len(alt_str) < min_len or alt_str.lower() in [p.lower() for p in banned_placeholders]:
        return RuleExecutionResult(
            FindingResult.FAIL,
            f"Alt-text '{alt_str}' is insufficient or a placeholder.",
            offending_text=alt_str,
            suggested_correction="Replace placeholder alt-text with a concise description of the visual asset.",
        )

    return RuleExecutionResult(
        FindingResult.PASS,
        f"Alt-text is present and meets baseline accessibility criteria: '{alt_str}'.",
    )


# ---------------------------------------------------------------------------
# Predicate dispatcher
# ---------------------------------------------------------------------------

_PREDICATES: dict[str, PredicateFn] = {
    RuleType.TERM_LIST:      _check_term_list,
    RuleType.REGEX:          _check_regex,
    RuleType.NUMERIC_RANGE:  _check_numeric_range,
    RuleType.DATE_WINDOW:    _check_date_window,
    RuleType.REQUIRED_TEXT:  _check_required_text,
    RuleType.WCAG_CONTRAST:  _check_wcag_contrast,
    RuleType.ASSET_HASH:     _check_asset_hash,
    "alt_text":              _check_alt_text,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_rule(
    rule_row: dict,
    text: str,
    *,
    regex_budget: RegexExecutionBudget | None = None,
) -> RuleExecutionResult:
    """
    Execute a single rule against *text*.

    Parameters
    ----------
    rule_row : dict-like row from brand_rules table (must have rule_type, parameters)
    text     : the draft text or claim span to check

    Returns
    -------
    RuleExecutionResult (unpacks as (FindingResult, explanation_string))
    """
    if not rule_row.get("is_active") or not rule_row.get("owner_approved"):
        return RuleExecutionResult(FindingResult.PASS, "Rule is inactive or not owner-approved; skipped.")
    if rule_row.get("source_verified") is False:
        return RuleExecutionResult(FindingResult.PASS, "Rule has no verified source; skipped.")
    readiness_class = rule_row.get("readiness_class")
    if readiness_class and readiness_class != "CODE_CHECKABLE":
        return RuleExecutionResult(
            FindingResult.PASS,
            f"{readiness_class} guidance is not enforced as a deterministic predicate.",
        )

    rule_type = rule_row["rule_type"]
    predicate = _PREDICATES.get(rule_type)
    if predicate is None:
        return RuleExecutionResult(FindingResult.ERROR, f"No predicate implemented for rule_type={rule_type!r}")

    try:
        params = json.loads(rule_row["parameters"]) if isinstance(rule_row["parameters"], str) else rule_row["parameters"]
    except (json.JSONDecodeError, TypeError) as exc:
        return RuleExecutionResult(FindingResult.ERROR, f"Rule parameters are invalid JSON: {exc}")

    token = _ACTIVE_REGEX_BUDGET.set(regex_budget or RegexExecutionBudget())
    try:
        return predicate(text, params)
    except Exception as exc:
        return RuleExecutionResult(FindingResult.ERROR, f"Predicate raised an exception: {type(exc).__name__}: {exc}")
    finally:
        _ACTIVE_REGEX_BUDGET.reset(token)


def check_ocr_extracted_text(ocr_text: str, rules: list[dict]) -> list[RuleExecutionResult]:
    """
    Run applicable brand rules against text extracted from images via OCR.
    Enables visual copy to be validated against the same brand safety rules
    as draft copy.
    """
    results: list[RuleExecutionResult] = []
    regex_budget = RegexExecutionBudget()
    for rule in rules:
        res = run_rule(rule, ocr_text, regex_budget=regex_budget)
        results.append(res)
    return results


def run_self_tests(rule_row: dict) -> list[dict]:
    """
    Run the positive_tests and negative_tests embedded in the rule definition.
    Returns a list of test result dicts for display/logging.
    Used during rule creation to validate the rule parameters.
    """
    results = []

    def run_example(test_text: str) -> RuleExecutionResult:
        candidate = dict(rule_row)
        params = candidate.get("parameters", {})
        if isinstance(params, str):
            params = json.loads(params)
        else:
            params = dict(params)
        if candidate.get("rule_type") == RuleType.WCAG_CONTRAST.value:
            colors = test_text.split("|")
            if len(colors) != 2:
                return RuleExecutionResult(
                    FindingResult.ERROR,
                    "WCAG test example must be foreground|background hex colors.",
                )
            params.update({"foreground": colors[0].strip(), "background": colors[1].strip()})
        elif candidate.get("rule_type") == RuleType.ASSET_HASH.value:
            params["submitted_hash"] = test_text.strip()
        elif candidate.get("rule_type") == "alt_text":
            params["alt_text"] = test_text
        candidate["parameters"] = params
        return run_rule(candidate, test_text)

    for test_text in json.loads(rule_row.get("positive_tests", "[]")):
        result, explanation = run_example(test_text)
        results.append({
            "test_type": "positive",
            "test_text": test_text,
            "result": result,
            "explanation": explanation,
            "passed": result == FindingResult.PASS,
        })

    for test_text in json.loads(rule_row.get("negative_tests", "[]")):
        result, explanation = run_example(test_text)
        results.append({
            "test_type": "negative",
            "test_text": test_text,
            "result": result,
            "explanation": explanation,
            "passed": result == FindingResult.FAIL,
        })

    return results
