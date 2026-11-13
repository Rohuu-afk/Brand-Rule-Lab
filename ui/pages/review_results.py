"""
ui/pages/review_results.py
--------------------------
User-friendly compliance review dashboard.
Translates technical IDs and enums into clear, actionable everyday language for
creators, brand managers, and reviewers.

Preserves full audit fidelity, exact fact-version linkages, and rule citations
via progressive disclosure expanders.
"""

from __future__ import annotations

import html
import json
import logging
import re
import streamlit as st

from ai.base import safe_error_summary
from services.review_service import (
    get_findings_for_draft_version,
    update_finding_status,
    create_draft_revision,
    apply_draft_correction,
    request_guideline_clarification,
    run_review,
    record_finding_override,
)
from services.ledger_service import get_all_drafts, get_draft_with_versions
from services.approval_service import (
    approve_submitted_draft,
    request_draft_changes,
    submit_draft_for_review,
)
from ui.styles import (
    demo_banner, ai_disclosure, inject_styles,
    SEVERITY_BADGE, RESULT_BADGE, EVIDENCE_BADGE,
)
from ui.presentation import humanize, explain_status
from ui.components import page_header
from db.connection import get_connection
from models.enums import FindingStatus, DraftStatus, UserRole, FindingResult, Severity, BrandRole
from services.auth_service import AuthenticatedPrincipal
from services.brand_service import require_brand_membership

logger = logging.getLogger("brand_rule_lab.ui.review_results")


def _get_open_high_impact(findings: list[dict]) -> list[dict]:
    return [
        f for f in findings
        if f["severity"] in ("HIGH", "CRITICAL")
        and f["result"] in ("FAIL", "REVIEW_REQUIRED", "WARNING")
        and not (
            f.get("status") == FindingStatus.DISMISSED.value
            and f.get("finding_type") != "fact_match"
            and f.get("override_decision") == "ACCEPT"
        )
    ]


def _get_approval_blockers(findings: list[dict]) -> dict[str, int]:
    """Summarise the checks that prevent or condition reviewer approval."""
    open_high = _get_open_high_impact(findings)
    missing_evidence = [
        finding
        for finding in findings
        if finding.get("finding_type") == "fact_match"
        and (
            finding.get("evidence_relationship") != "SUPPORTS"
            or not finding.get("fact_version_id")
        )
    ]
    not_observed = [
        finding
        for finding in findings
        if finding.get("result") == "NOT_OBSERVED"
    ]
    errors = [
        finding
        for finding in findings
        if finding.get("result") == "ERROR"
    ]
    return {
        "high_priority": len(open_high),
        "missing_evidence": len(missing_evidence),
        "not_observed": len(not_observed),
        "errors": len(errors),
    }


def _highlighted_copy_html(raw_text: str, findings: list[dict]) -> str:
    """Highlight only claim spans whose stored offsets match the exact source text."""
    spans = set()
    for finding in findings:
        start = finding.get("claim_start_char")
        end = finding.get("claim_end_char")
        claim = finding.get("claim_text")
        if (
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(end, int)
            and not isinstance(end, bool)
            and isinstance(claim, str)
            and 0 <= start < end <= len(raw_text)
            and raw_text[start:end] == claim
        ):
            spans.add((start, end))

    safe_text = html.escape(raw_text)
    if not spans:
        return safe_text

    output = []
    cursor = 0
    for start, end in sorted(spans):
        if start < cursor:
            continue
        output.append(html.escape(raw_text[cursor:start]))
        output.append(
            '<mark class="brl-copy-highlight" aria-label="Text referenced by a finding">'
            f"{html.escape(raw_text[start:end])}</mark>"
        )
        cursor = end
    output.append(html.escape(raw_text[cursor:]))
    return "".join(output)


def _derive_issue_title(quoted: str, rule_name: str | None, finding_type: str, evidence_rel: str | None) -> str:
    """Generate a clean, professional, human-understandable title for a finding."""
    q = (quoted or "").lower()
    r = (rule_name or "").lower()

    if finding_type == "claim_extraction":
        return "Claim could not be verified in the draft"
    if finding_type == "fact_ranking":
        return "Evidence check could not be completed"
    if evidence_rel == "CONTRADICTS" or "timeline" in r or "3 days" in q or "3-day" in q:
        return "Timeline conflicts with approved clinical evidence"
    if "forever" in q or "v-04" in r or "absolute" in r:
        return "Absolute promise in marketing copy (unsupported permanent guarantee)"
    if "bpo" in q or "t-12" in r or "abbreviation" in r:
        return "Disallowed active ingredient abbreviation (use full chemical name)"
    if "best" in q or "superlative" in r:
        return "Unsubstantiated superlative claim (requires objective evidence)"
    if finding_type == "accessibility_contrast":
        return "Text contrast needs checking"
    if finding_type == "accessibility_alt_text":
        return "Image description needs checking"
    if finding_type == "asset_identity":
        return "Approved image comparison"
    if finding_type == "packaging_reference_text":
        return "Packaging copy vs approved artwork"
    if finding_type == "campaign_alignment":
        return "Campaign message check not completed"
    if finding_type == "document_ocr":
        return "Some document text could not be read"
    if evidence_rel in ("UNLINKED", "NO_MATCH", "RELATED_NOT_EVIDENCE") or finding_type == "fact_match":
        return "No approved evidence linked for this claim"
    if finding_type == "tone":
        return "Messaging or tone concern"
    clean_rule_name = re.sub(r"^[A-Z]{1,6}-\d+\s*:\s*", "", rule_name or "")
    return clean_rule_name or "Brand guideline check"


def _group_related_findings(findings: list[dict]) -> list[dict]:
    """
    Group findings that refer to the same underlying text or issue
    so creators aren't overwhelmed by redundant cards.
    Preserves all sub-findings and audit data.
    """
    groups: dict[str, dict] = {}

    for f in findings:
        quoted = (f.get("quoted_text") or f.get("claim_text") or "").strip()
        q_norm = quoted.lower()

        title = _derive_issue_title(
            quoted,
            f.get("rule_name"),
            f.get("finding_type", ""),
            f.get("evidence_relationship"),
        )
        claim_id = f.get("claim_mention_id")
        group_key = (
            f"claim_{claim_id}"
            if claim_id is not None
            else f"issue_{title.lower()}_{(f.get('claim_text') or quoted).strip().lower() or f['id']}"
        )

        if group_key not in groups:
            groups[group_key] = {
                "key": group_key,
                "quoted_text": quoted,
                "title": title,
                "findings": [],
                "results": set(),
                "severities": set(),
                "statuses": set(),
                "evidence_rel": f.get("evidence_relationship"),
                "suggested_correction": f.get("suggested_correction"),
            }

        g = groups[group_key]
        g["findings"].append(f)
        g["results"].add(f.get("result", "OPEN"))
        g["severities"].add(f.get("severity", "MEDIUM"))
        g["statuses"].add(f.get("status", "OPEN"))

        if f.get("suggested_correction") and not g["suggested_correction"]:
            g["suggested_correction"] = f.get("suggested_correction")
        if f.get("evidence_relationship") and not g["evidence_rel"]:
            g["evidence_rel"] = f.get("evidence_relationship")

    # Finalize display properties for each group
    ordered_groups = []
    for g in groups.values():
        # Highest severity in group
        sev_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        g["top_severity"] = next((s for s in sev_order if s in g["severities"]), "MEDIUM")
        
        # Worst result in group
        res_order = ["FAIL", "WARNING", "REVIEW_REQUIRED", "UNLINKED", "ERROR", "NOT_OBSERVED", "PASS"]
        g["top_result"] = next((r for r in res_order if r in g["results"]), "REVIEW_REQUIRED")

        g["blocking_high"] = bool(_get_open_high_impact(g["findings"]))
        g["awaiting_override"] = any(
            finding.get("status") == FindingStatus.DISMISSED.value
            and finding.get("severity") in ("HIGH", "CRITICAL")
            and finding.get("result") in ("FAIL", "REVIEW_REQUIRED", "WARNING")
            and finding.get("finding_type") != "fact_match"
            and finding.get("override_decision") is None
            for finding in g["findings"]
        )
        g["is_open"] = (
            "OPEN" in g["statuses"]
            or g["blocking_high"]
            or g["awaiting_override"]
        )
        g["primary_id"] = g["findings"][0]["id"]
        ordered_groups.append(g)

    # Sort groups by severity
    sev_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    ordered_groups.sort(key=lambda x: sev_rank.get(x["top_severity"], 4))
    return ordered_groups


def render(
    principal: AuthenticatedPrincipal,
    role: UserRole | str | None = None,
):
    inject_styles()
    page_header("Content Review", "Compliance Review Results")
    brand_id = st.session_state.get("active_brand_id")
    membership = require_brand_membership(principal, brand_id)
    role = membership["role"]
    is_reviewer = role in {BrandRole.OWNER.value, BrandRole.REVIEWER.value}
    is_creator = role in {BrandRole.OWNER.value, BrandRole.CREATOR.value}
    demo_banner()
    with st.expander("AI and data use", expanded=False):
        ai_disclosure()
    review_notice = st.session_state.pop("review_notice", None)
    if review_notice:
        st.warning(review_notice)
    review_notice_details = st.session_state.pop("review_notice_technical", None)
    if review_notice_details:
        with st.expander("Technical details", expanded=False):
            st.code(review_notice_details)

    # Draft selector
    drafts = get_all_drafts(principal=principal, brand_id=brand_id)
    if not drafts:
        st.info("No drafts yet. Submit one via **New Review**.")
        return

    draft_options = {
        f"{d['title']} · {humanize(d['status'])}": d["id"]
        for d in drafts
    }

    # Pre-select last reviewed draft if available
    default_index = 0
    if "last_draft_id" in st.session_state:
        for i, did in enumerate(draft_options.values()):
            if did == st.session_state["last_draft_id"]:
                default_index = i
                break

    selected_draft_label = st.selectbox(
        "Select Draft to Inspect",
        list(draft_options.keys()),
        index=default_index,
        help="Choose any submitted draft to review its findings, revisions, and approval decisions.",
    )
    draft_id = draft_options[selected_draft_label]

    draft_data = get_draft_with_versions(draft_id, principal=principal, brand_id=brand_id)
    if not draft_data or not draft_data.get("versions"):
        st.info("This draft has not been checked yet. Run **New Review** first.")
        return

    versions = draft_data["versions"]
    latest_version = versions[-1]
    total_versions = len(versions)

    # Version selector with override support
    target_vid = st.session_state.pop("v_select_override", None)
    version_labels = []
    version_by_label = {}
    default_v_idx = 0

    for idx, v in enumerate(reversed(versions)):
        is_v_latest = (v["id"] == latest_version["id"])
        ts = (v.get("created_at") or "")[:19].replace("T", " ")
        lbl = f"Draft Version {v['version_number']}{' (Latest Active)' if is_v_latest else ' (Historical)'} — {ts}"
        version_labels.append(lbl)
        version_by_label[lbl] = v
        if target_vid and v["id"] == target_vid:
            default_v_idx = idx

    col_ver, col_info = st.columns([3, 2])
    with col_ver:
        selected_version_label = st.selectbox(
            "Draft Version History",
            version_labels,
            index=default_v_idx,
            help="Select any draft version to inspect its historical content and findings.",
            key=f"v_select_{draft_id}",
        )
    current_version = version_by_label[selected_version_label]
    draft_version_id = current_version["id"]
    is_latest = (current_version["id"] == latest_version["id"])
    try:
        version_visual_metadata = json.loads(current_version.get("visual_metadata") or "{}")
    except (TypeError, json.JSONDecodeError):
        version_visual_metadata = {}
    video_frame_lookup = {
        frame.get("frame_number"): frame.get("timestamp")
        for frame in version_visual_metadata.get("video_frames", [])
        if isinstance(frame, dict)
    }

    with col_info:
        badge = "Latest active version" if is_latest else "Historical revision"
        st.markdown(f"**{badge}**")
        st.caption(
            f"Viewing version **v{current_version['version_number']}** of {total_versions} | "
            f"Status: **{humanize(draft_data['status'])}**"
        )

    if total_versions > 1:
        with st.expander("Compare revisions", expanded=False):
            v_map = {f"Version {v['version_number']}": v for v in versions}
            diff_col1, diff_col2 = st.columns(2)
            with diff_col1:
                v1_key = st.selectbox("Original / Earlier Version", list(v_map.keys()), index=0, key=f"diff_v1_{draft_id}")
            with diff_col2:
                v2_key = st.selectbox("Revised / Comparison Version", list(v_map.keys()), index=len(versions) - 1, key=f"diff_v2_{draft_id}")

            t1 = v_map[v1_key]["raw_text"]
            t2 = v_map[v2_key]["raw_text"]
            if t1 == t2:
                st.info(f"Draft copy is identical between {v1_key} and {v2_key}.")
            else:
                c_a, c_b = st.columns(2)
                with c_a:
                    st.markdown(f"**{v1_key}**")
                    st.code(t1, language=None)
                with c_b:
                    st.markdown(f"**{v2_key}**")
                    st.code(t2, language=None)

    findings = get_findings_for_draft_version(
        draft_version_id, principal=principal, brand_id=brand_id
    )
    with st.container(border=True):
        st.markdown(f"**Submitted copy · version {current_version['version_number']}**")
        st.caption(
            "Highlighted text has an exact, verified source position for a finding. "
            "Other findings still show their quoted text below."
        )
        st.markdown(
            '<div class="brl-copy-preview">'
            + _highlighted_copy_html(current_version["raw_text"], findings)
            + "</div>",
            unsafe_allow_html=True,
        )

    if not findings:
        if draft_data["status"] in (DraftStatus.DRAFT.value, "AWAITING_RECHECK"):
            st.warning("Checks are pending for this revision. No result has been recorded yet.")
        else:
            st.success("No issues were found in the completed checks for this draft version.")
    else:
        # -------------------------------------------------------------
        # Part B.4.A: Review Summary Header
        # -------------------------------------------------------------
        n_pass = sum(1 for f in findings if f["result"] == "PASS")
        n_fail = sum(1 for f in findings if f["result"] == "FAIL")
        n_review = sum(
            1 for f in findings
            if f["result"] in ("REVIEW_REQUIRED", "UNLINKED", "WARNING")
        )
        n_not_observed = sum(
            1 for f in findings if f["result"] in ("NOT_OBSERVED", "ERROR")
        )
        n_open = sum(1 for f in findings if f["status"] == "OPEN")

        if n_fail > 0:
            st.error(
                f"Action needed: {n_fail} item(s) do not meet a guideline. "
                f"{n_review} item(s) also need human review."
            )
        elif n_review > 0:
            st.warning(
                f"{n_review} item(s) need a human review before publishing."
            )
        elif n_not_observed > 0:
            st.warning(
                f"{n_not_observed} check(s) could not be completed. An unchecked result is not a pass."
            )
        else:
            st.success("All completed checks meet the applicable guidelines.")

        # Metric cards with plain-English labels
        m1, m2 = st.columns(2)
        m1.metric("Looks good", n_pass)
        m2.metric("Needs a change", n_fail)
        m3, m4 = st.columns(2)
        m3.metric("Needs human review", n_review)
        m4.metric("Not checked", n_not_observed, help="An unchecked item is not a pass.")

        st.markdown("---")
        st.subheader("Review findings")

        # -------------------------------------------------------------
        # Part B.4.B & D: Scannable Finding Cards (Grouped by Topic)
        # -------------------------------------------------------------
        grouped_issues = _group_related_findings(findings)

        for g in grouped_issues:
            top_res = g["top_result"]
            top_sev = g["top_severity"]
            is_open = g["is_open"]
            res_badge = RESULT_BADGE.get(top_res, "")
            sev_badge = SEVERITY_BADGE.get(top_sev, "")

            if g["awaiting_override"]:
                status_icon = "Dismissed — awaiting Reviewer decision (not resolved)"
            elif any(
                finding.get("override_decision") == "ACCEPT"
                for finding in g["findings"]
            ):
                status_icon = "Reviewer accepted dismissal"
            elif any(
                finding.get("override_decision") == "REJECT"
                for finding in g["findings"]
            ):
                status_icon = "Reviewer rejected dismissal — still blocking"
            else:
                status_icon = "Needs attention" if is_open else "Resolved"

            with st.expander(
                f"{g['title']} — {humanize(top_sev)}",
                expanded=is_open and (top_res in ("FAIL", "REVIEW_REQUIRED")),
            ):
                # Header Badges
                st.markdown(
                    f"**Status:** {res_badge} &nbsp; **Priority:** {sev_badge} &nbsp; "
                    f"*{status_icon}*",
                    unsafe_allow_html=True,
                )

                # What was found in copy
                if g["quoted_text"]:
                    st.markdown("**What we detected:**")
                    st.markdown(
                        f'<div class="quote-box">"{html.escape(g["quoted_text"])}"</div>',
                        unsafe_allow_html=True,
                    )

                st.markdown("**What this means:** " + (explain_status(top_res) or humanize(top_res)))
                st.markdown("**Why it matters:**")
                main_explanation = ""
                for sf in g["findings"]:
                    expl = sf.get("explanation", "").strip()
                    expl = re.sub(r"^\[AI Generated \([^\]]+\)\]\s*", "", expl)
                    if expl and expl not in main_explanation:
                        main_explanation += f"{expl}\n\n"
                st.markdown(main_explanation.strip())

                evidence_sources = []
                seen_evidence = set()
                for sf in g["findings"]:
                    statement = str(sf.get("fact_statement") or "").strip()
                    evidence_ref = str(sf.get("fact_evidence_ref") or "").strip()
                    fact_version = sf.get("fact_version_id")
                    evidence_key = (fact_version, statement, evidence_ref)
                    if statement and evidence_key not in seen_evidence:
                        seen_evidence.add(evidence_key)
                        evidence_sources.append((sf, statement, evidence_ref))
                if evidence_sources:
                    st.markdown("**Approved source considered:**")
                    for sf, statement, evidence_ref in evidence_sources:
                        relationship = sf.get("evidence_relationship")
                        relationship_label = {
                            "SUPPORTS": "Linked as supporting evidence",
                            "CONTRADICTS": "Conflicts with this approved fact",
                            "RELATED_NOT_EVIDENCE": "Related, but not evidence for this claim",
                            "NO_MATCH": "No evidence match",
                            "UNLINKED": "No approved fact linked",
                        }.get(relationship, "Evidence relationship requires review")
                        source_html = (
                            '<div class="evidence-callout">'
                            f"<strong>{html.escape(relationship_label)}</strong><br>"
                            f"{html.escape(statement)}"
                        )
                        if evidence_ref:
                            source_html += (
                                "<br><small>Source reference: "
                                f"{html.escape(evidence_ref)}</small>"
                            )
                        source_html += "</div>"
                        st.markdown(source_html, unsafe_allow_html=True)

                # Next steps & Suggested correction
                corr_text = g.get("suggested_correction")
                if corr_text:
                    st.markdown(
                        f'<div class="action-guidance"><strong>Recommended correction:</strong> '
                        f'"{html.escape(str(corr_text))}"</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<div class="action-guidance"><strong>Recommended next step:</strong> '
                        'Ask the Fact Owner to add approved evidence or revise the wording, then run the review again.</div>',
                        unsafe_allow_html=True,
                    )
                st.caption(
                    "Next action: use the correction control below, or ask a Reviewer / Fact Owner to assess this item."
                )

                if is_reviewer and is_latest:
                    for dismissed in g["findings"]:
                        if (
                            dismissed.get("status") != FindingStatus.DISMISSED.value
                            or dismissed.get("severity") not in ("HIGH", "CRITICAL")
                            or dismissed.get("result") not in ("FAIL", "REVIEW_REQUIRED", "WARNING")
                        ):
                            continue
                        if dismissed.get("finding_type") == "fact_match":
                            st.error(
                                "Dismissal cannot override an evidence finding. "
                                "Correct the copy or link suitable approved evidence and re-check."
                            )
                            continue
                        previous_decision = dismissed.get("override_decision")
                        if previous_decision:
                            st.caption(
                                f"Latest Reviewer dismissal decision: {previous_decision} "
                                f"by {dismissed.get('override_reviewer')}: "
                                f"{dismissed.get('override_reason')}"
                            )
                        if is_reviewer:
                            with st.form(f"override_finding_{dismissed['id']}"):
                                st.markdown(
                                    f"**Reviewer decision for dismissed HIGH/CRITICAL finding #{dismissed['id']}**"
                                )
                                override_decision = st.radio(
                                    "Decision",
                                    ["REJECT", "ACCEPT"],
                                    format_func=lambda value: (
                                        "Reject dismissal — keep blocking"
                                        if value == "REJECT"
                                        else "Accept dismissal — resolve this non-evidence issue"
                                    ),
                                    key=f"override_choice_{dismissed['id']}",
                                )
                                override_reason = st.text_area(
                                    "Reviewer reason (required)",
                                    key=f"override_reason_{dismissed['id']}",
                                )
                                if st.form_submit_button("Record Reviewer decision"):
                                    try:
                                        record_finding_override(
                                            dismissed["id"],
                                            override_decision,
                                            override_reason,
                                            principal=principal,
                                            brand_id=brand_id,
                                        )
                                    except (PermissionError, ValueError) as exc:
                                        st.error(str(exc))
                                    else:
                                        st.success("Reviewer decision recorded in the audit trail.")
                                        st.rerun()
                        else:
                            st.caption("Only a Reviewer or Brand Owner can decide this dismissal.")

                # Progressive disclosure: Technical details
                with st.expander("Technical details", expanded=False):
                    for sf in g["findings"]:
                        st.markdown(
                            f"Finding ID `#{sf['id']}` · Type `{sf.get('finding_type')}` · "
                            f"Stored result `{sf.get('result')}` · Severity `{sf.get('severity')}`"
                        )
                        if sf.get("rule_id"):
                            st.caption(
                                f"Rule ID: `#{sf['rule_id']}` ({sf.get('rule_type', 'rule')}) | "
                                f"Guideline: {sf.get('guideline_title', 'N/A')} [{sf.get('guideline_source_ref', 'N/A')}]"
                            )
                        if sf.get("rule_name"):
                            st.caption(f"Rule source: {sf['rule_name']}")
                        if sf.get("fact_version_id"):
                            st.caption(
                                f"Linked Fact Version: `#{sf['fact_version_id']}` (Fact ID `#{sf.get('fact_id')}`) | "
                                f"Relationship: `{sf.get('evidence_relationship')}`"
                            )
                            st.caption(
                                f"Approved source: {sf.get('fact_statement', '')} "
                                f"[{sf.get('fact_evidence_ref', '')}]"
                            )
                        if sf.get("claim_text"):
                            st.caption(
                                f"Claim text: {sf['claim_text']} · Source position: "
                                f"{sf.get('claim_start_char', 'N/A')}–{sf.get('claim_end_char', 'N/A')}"
                            )
                        if sf.get("source_page") is not None:
                            if current_version.get("source_type") == "video":
                                frame_number = sf["source_page"]
                                timestamp = video_frame_lookup.get(frame_number)
                                location = f"Source frame: {frame_number}"
                                if timestamp:
                                    location += f" at {timestamp}"
                            else:
                                location = f"Source page: {sf['source_page']}"
                            st.caption(
                                f"{location} · Character span: "
                                f"{sf.get('start_char', 'N/A')}–{sf.get('end_char', 'N/A')}"
                            )
                        if sf.get("explanation"):
                            st.caption(f"Recorded explanation: {sf['explanation']}")
                        if sf.get("override_reason"):
                            st.caption(
                                f"Reviewer decision: {sf.get('override_decision')} · "
                                f"{sf.get('override_reviewer')}: {sf['override_reason']}"
                            )

                # -------------------------------------------------------------
                # Part B.5: Action Controls (Creators only on latest version)
                # -------------------------------------------------------------
                if not is_latest:
                    st.caption(
                        f"Viewing historical revision (v{current_version['version_number']}). "
                        f"Select latest version (v{latest_version['version_number']}) to apply edits."
                    )
                elif is_open and is_creator:
                    st.markdown("---")
                    st.markdown("**Take action on this issue**")

                    primary_fid = g["primary_id"]
                    target_span = g["quoted_text"].strip()
                    span_in_current = bool(target_span and target_span in current_version["raw_text"])

                    st.markdown("**Make a correction**")
                    st.caption(
                        "A saved correction creates a new revision and runs the checks again. "
                        "Your current draft and revision history are preserved."
                    )
                    correction_mode = st.radio(
                        "Choose what to replace",
                        ["Replace highlighted phrase", "Replace entire draft"],
                        index=0,
                        key=f"mode_{g['key']}",
                    )

                    phrase_replacement = ""
                    full_draft_replacement = ""
                    if correction_mode == "Replace highlighted phrase":
                        if span_in_current:
                            st.caption(f"Phrase to replace: {target_span}")
                            phrase_replacement = st.text_input(
                                "Replacement wording",
                                value=corr_text or "",
                                key=f"phrase_rep_{g['key']}",
                            )
                        else:
                            st.warning(
                                "This finding has no highlighted phrase in the current draft. "
                                "Choose “Replace entire draft” to make a full-copy change."
                            )
                    else:
                        full_draft_replacement = st.text_area(
                            "Revised draft",
                            value=current_version["raw_text"],
                            height=140,
                            key=f"full_draft_{g['key']}",
                        )

                    edit_note = st.text_input(
                        "Reason for correction (required)",
                        value=f"Correction for: {g['title']}",
                        key=f"edit_note_{g['key']}",
                    )

                    preview_text = current_version["raw_text"]
                    preview_error = None
                    try:
                        if correction_mode == "Replace highlighted phrase" and span_in_current:
                            if phrase_replacement:
                                preview_text = apply_draft_correction(
                                    current_version["raw_text"],
                                    target_span,
                                    phrase_replacement,
                                    "phrase",
                                )
                        elif correction_mode == "Replace entire draft" and full_draft_replacement:
                            preview_text = apply_draft_correction(
                                current_version["raw_text"],
                                None,
                                full_draft_replacement,
                                "full_draft",
                            )
                    except (ValueError, PermissionError) as exc:
                        preview_error = str(exc)
                    st.text_area(
                        "Preview of revised draft",
                        value=preview_text,
                        height=140,
                        disabled=True,
                        key=f"preview_{g['key']}",
                    )
                    st.caption("Nothing changes until you select the correction action below.")
                    correction_ai_consent = st.checkbox(
                        "I consent to send this revised draft to NVIDIA NIM (or the configured Google Gemini fallback, if invoked) for claim and tone analysis. Fact ranking sends each claim and eligible approved fact candidates.",
                        value=False,
                        key=f"correction_ai_consent_{current_version['id']}_{g['key']}",
                    )
                    if len(preview_text) > 8000:
                        st.warning(
                            "Tone assessment evaluates only the first 8,000 characters of this revised draft."
                        )
                    if len(preview_text) > 30000:
                        st.warning(
                            "AI claim extraction is skipped above 30,000 characters; deterministic checks still cover the complete draft."
                        )
                    if not correction_ai_consent:
                        st.caption(
                            "No external AI request will be made; deterministic checks will run and AI-only assessments will be NOT_OBSERVED."
                        )

                    if st.button(
                        "Apply correction and re-check",
                        type="primary",
                        key=f"apply_correction_{g['key']}",
                        disabled=bool(preview_error)
                        or not edit_note.strip()
                        or (correction_mode == "Replace highlighted phrase" and not span_in_current)
                        or (correction_mode == "Replace highlighted phrase" and not phrase_replacement.strip())
                        or (correction_mode == "Replace entire draft" and not full_draft_replacement.strip()),
                    ):
                        if preview_error:
                            st.error(preview_error)
                        else:
                            try:
                                updated_raw = preview_text
                                used_corr = (
                                    phrase_replacement.strip()
                                    if correction_mode == "Replace highlighted phrase"
                                    else full_draft_replacement.strip()
                                )
                                new_vid = create_draft_revision(
                                    draft_id=draft_id,
                                    updated_text=updated_raw,
                                    source_type="edit",
                                    finding_id=primary_fid,
                                    creator_note=edit_note.strip(),
                                    suggested_correction=used_corr,
                                    auto_rereview=True,
                                    ai_consent=correction_ai_consent,
                                    principal=principal,
                                    brand_id=brand_id,
                                )
                                for sf in g["findings"]:
                                    if sf["id"] != primary_fid:
                                        update_finding_status(
                                            sf["id"], FindingStatus.ACCEPTED.value,
                                            principal=principal, brand_id=brand_id,
                                        )
                                st.session_state["v_select_override"] = new_vid
                                st.success(
                                    f"Revision v{total_versions + 1} saved and checked. "
                                    "Review its updated findings before submitting it."
                                )
                                st.rerun()
                            except Exception as exc:
                                clean_error = safe_error_summary(exc)
                                logger.error(
                                    "Could not complete the Creator correction workflow (%s).",
                                    clean_error,
                                )
                                updated_draft = get_draft_with_versions(
                                    draft_id, principal=principal, brand_id=brand_id
                                )
                                updated_versions = updated_draft.get("versions", []) if updated_draft else []
                                if len(updated_versions) > total_versions:
                                    pending_version = updated_versions[-1]
                                    st.session_state["v_select_override"] = pending_version["id"]
                                    st.session_state["review_notice"] = (
                                        f"Revision v{pending_version['version_number']} was saved, but its checks "
                                        "could not be completed. The revision is pending re-evaluation; do not treat "
                                        "it as passed."
                                    )
                                    st.session_state["review_notice_technical"] = clean_error
                                    st.rerun()
                                st.error("The correction could not be saved or re-checked.")
                                with st.expander("Technical details", expanded=False):
                                    st.code(clean_error)

                    # Dismiss option in separate clean expander
                    with st.expander("Dismiss this finding with a documented reason", expanded=False):
                        with st.form(key=f"dismiss_{g['key']}"):
                            dismiss_reason = st.text_input("Reason for dismissal (required for audit)")
                            if st.form_submit_button("Dismiss Finding"):
                                if dismiss_reason.strip():
                                    for sf in g["findings"]:
                                        update_finding_status(
                                            sf["id"],
                                            FindingStatus.DISMISSED.value,
                                            creator_note=dismiss_reason.strip(),
                                            principal=principal, brand_id=brand_id,
                                        )
                                    st.info("Finding dismissed.")
                                    st.rerun()
                                else:
                                    st.error("A documented reason is required to dismiss a finding.")
                    with st.expander("Request guideline clarification", expanded=False):
                        with st.form(key=f"clarification_{g['key']}"):
                            clarification_note = st.text_area(
                                "What wording, source, or interpretation needs clarification?",
                                key=f"clarification_note_{g['key']}",
                            )
                            if st.form_submit_button("Escalate clarification request"):
                                try:
                                    request_guideline_clarification(
                                        draft_id, primary_fid, clarification_note,
                                        principal=principal, brand_id=brand_id,
                                    )
                                except ValueError as exc:
                                    st.error(str(exc))
                                else:
                                    st.success("Clarification request added to the review audit trail.")
                                    st.rerun()
                elif is_open:
                    st.info("Creator correction, dismissal, and escalation controls are available only to authenticated Creators.")

    st.markdown("---")

    # If viewing historical revision, governance decisions are disabled
    if not is_latest:
        st.info(
            f"You are viewing historical revision **v{current_version['version_number']}**. "
            f"Approval decisions apply only to the latest active version (**v{latest_version['version_number']}**)."
        )
        return

    # Option to manually trigger compliance re-check if draft is DRAFT or AWAITING_RECHECK
    if draft_data["status"] in (DraftStatus.DRAFT.value, "AWAITING_RECHECK"):
        st.warning("This draft version has edits pending compliance recheck.")
        recheck_consent_nonce = st.session_state.get(
            f"_recheck_ai_consent_nonce_{draft_id}", 0
        )
        recheck_ai_consent = st.checkbox(
            "I consent to send this revised draft to NVIDIA NIM (or the configured Google Gemini fallback, if invoked) for claim and tone analysis. Fact ranking sends each claim and eligible approved fact candidates.",
            value=False,
            key=f"recheck_external_ai_consent_{draft_id}_{recheck_consent_nonce}",
        )
        if len(latest_version["raw_text"]) > 8000:
            st.warning(
                "Tone assessment evaluates only the first 8,000 characters of this revised draft."
            )
        if len(latest_version["raw_text"]) > 30000:
            st.warning(
                "AI claim extraction is skipped above 30,000 characters; deterministic checks still cover the complete draft."
            )
        if not recheck_ai_consent:
            st.caption(
                "Without consent, only deterministic checks run; AI-only assessments are NOT_OBSERVED."
            )
        if st.button("Re-run brand check on latest revision", type="primary"):
            st.session_state[f"_recheck_ai_consent_nonce_{draft_id}"] = (
                recheck_consent_nonce + 1
            )
            with st.spinner("Re-evaluating draft against deterministic rules and brand facts..."):
                ai = None
                if recheck_ai_consent:
                    try:
                        from ai.provider_factory import get_provider
                        ai = get_provider()
                    except Exception:
                        class _OfflineAI:
                            def generate_text(self, prompt, **kw):
                                from ai.provider_factory import AIProviderError
                                raise AIProviderError("AI Provider offline.")
                        ai = _OfflineAI()

                try:
                    result = run_review(
                        draft_id=draft_id,
                        raw_text=latest_version["raw_text"],
                        source_type=latest_version.get("source_type", "edit"),
                        page_map={},
                        product_id=draft_data.get("product_id"),
                        market=draft_data.get("market"),
                        channel=draft_data.get("channel"),
                        content_type=draft_data.get("content_type"),
                        brief_id=draft_data.get("brief_id"),
                        ai_provider=ai,
                        draft_version_id=latest_version["id"],
                        ai_consent=recheck_ai_consent,
                        principal=principal,
                        brand_id=brand_id,
                    )
                except Exception as exc:
                    clean_error = safe_error_summary(exc)
                    logger.error("Manual draft re-evaluation failed (%s).", clean_error)
                    st.error("The review could not be completed. The checks remain pending.")
                    with st.expander("Technical details", expanded=False):
                        st.code(clean_error)
                else:
                    for warning in result.get("ai_warnings", []):
                        st.warning(warning)
                    if result["ai_status"] == "not_consented":
                        st.info(result["ai_summary"])
                    st.success("Checks were re-run. Review the new findings before approval.")
                    st.rerun()

    # ---- Reviewer Decision section — visible and actionable only to Reviewers ----
    if is_reviewer:
        st.subheader("Reviewer decision")
    else:
        st.subheader("Governance status")
        st.info(
            "**Reviewer actions** (approve, request changes, and audit comments) "
            "are available only to authenticated Reviewers."
        )

    approval_blockers = _get_approval_blockers(findings)
    open_high = _get_open_high_impact(findings)
    unlinked_claims = [
        finding
        for finding in findings
        if finding.get("finding_type") == "fact_match"
        and (
            finding.get("evidence_relationship") != "SUPPORTS"
            or not finding.get("fact_version_id")
        )
    ]

    if is_reviewer and open_high:
        st.error(
            f"Approval blocked: {len(open_high)} unresolved high-priority issue(s) remain. "
            "Resolve them before approving."
        )
    if is_reviewer and unlinked_claims:
        st.error(
            f"Approval blocked: {len(unlinked_claims)} claim(s) lack directly supporting "
            "approved evidence. Link evidence and re-check before approving."
        )
    if is_reviewer and approval_blockers["errors"]:
        st.error(
            f"Approval blocked: {approval_blockers['errors']} check(s) returned errors. "
            "Re-run the review and resolve the failed checks before approving."
        )
    if is_reviewer and not open_high and not unlinked_claims and not approval_blockers["errors"]:
        if not is_latest:
            st.info("Reviewer approval is available only for the latest draft revision.")
        elif draft_data["status"] != DraftStatus.READY_FOR_REVIEW.value:
            st.info(
                "Reviewer approval is available after the latest checked revision "
                "has been submitted for review."
            )
        elif approval_blockers["not_observed"]:
            st.warning(
                f"Approval pending acknowledgement: {approval_blockers['not_observed']} "
                "check(s) are NOT_OBSERVED. Review the findings and acknowledge them "
                "in the approval form."
            )
        else:
            st.success("Approval eligible: all required findings and evidence checks passed.")

    # Reviewer audit-comment form
    if is_reviewer:
        with st.expander("Add reviewer feedback or audit note", expanded=False):
            with st.form("reviewer_comment_form"):
                feedback_kind = st.selectbox(
                    "Audit event",
                    ["Comment", "Document override reason"],
                )
                comment_text = st.text_area("Reviewer feedback for creator")
                if st.form_submit_button("Record audit event"):
                    if comment_text.strip():
                        with get_connection() as conn:
                            from services.brand_service import require_record_brand
                            require_record_brand(
                                conn, principal, brand_id, "drafts", draft_id,
                                roles=(BrandRole.OWNER, BrandRole.REVIEWER),
                            )
                            review = conn.execute(
                                "SELECT id FROM reviews WHERE draft_id = ? ORDER BY id DESC LIMIT 1",
                                (draft_id,),
                            ).fetchone()
                            if not review:
                                cur = conn.execute("INSERT INTO reviews (draft_id) VALUES (?)", (draft_id,))
                                rev_id = cur.lastrowid
                            else:
                                rev_id = review["id"]
                            conn.execute(
                                "INSERT INTO review_events (review_id, event_type, actor, note) "
                                "VALUES (?, ?, ?, ?)",
                                (
                                    rev_id,
                                    "OVERRIDE" if feedback_kind == "Document override reason" else "COMMENT",
                                    principal.username,
                                    comment_text.strip(),
                                ),
                            )
                            conn.commit()
                        st.success(
                            "Override reason recorded; it does not bypass reviewer approval checks."
                            if feedback_kind == "Document override reason"
                            else "Comment recorded in audit ledger."
                        )
                        st.rerun()
                    else:
                        st.error("Comment cannot be empty.")

    col_a, col_b = st.columns(2)

    # Submit for Review — Creator action
    if (
        is_creator
        and is_latest
        and draft_data["status"] == DraftStatus.CHECKED.value
    ):
        if col_a.button("Submit draft for review", type="primary"):
            try:
                submit_draft_for_review(
                    draft_id, draft_version_id, principal=principal, brand_id=brand_id
                )
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.success("Draft submitted for review! Reviewer will be notified.")
                st.rerun()

    # Approve — Reviewer only
    if is_reviewer and draft_data["status"] == DraftStatus.READY_FOR_REVIEW.value and is_latest:
        if not open_high and not unlinked_claims and not approval_blockers["errors"]:
            with st.form("approve_draft_form"):
                approve_note = st.text_input("Approval note", value="Approved — compliance verified against brand guidelines.")
                not_observed = any(f.get("result") == "NOT_OBSERVED" for f in findings)
                acknowledged = False
                if not_observed:
                    acknowledged = st.checkbox(
                        "I acknowledge that one or more AI/metadata checks were NOT_OBSERVED.",
                        key=f"ack_not_observed_{draft_version_id}",
                    )
                if st.form_submit_button("Approve and register claims to ledger", type="primary"):
                    try:
                        claim_count = approve_submitted_draft(
                            draft_id=draft_id,
                            draft_version_id=draft_version_id,
                            reviewer_note=approve_note.strip(),
                            acknowledge_not_observed=acknowledged,
                            principal=principal,
                            brand_id=brand_id,
                        )
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))
                    else:
                        st.success(
                            f"Draft approved. {claim_count} claim(s) registered with immutable evidence snapshots."
                        )
                        st.rerun()

    # Request changes — Reviewer only
    if is_reviewer and draft_data["status"] == DraftStatus.READY_FOR_REVIEW.value and is_latest:
        with st.form("request_changes_form"):
            change_note = st.text_area("Request changes — feedback for creator")
            if st.form_submit_button("Request changes"):
                if change_note.strip():
                    try:
                        request_draft_changes(
                            draft_id, draft_version_id, change_note,
                            principal=principal, brand_id=brand_id,
                        )
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))
                    else:
                        st.warning("Changes requested from creator.")
                        st.rerun()
                else:
                    st.error("Please provide a note explaining the requested changes.")
