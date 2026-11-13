"""
ui/pages/insights.py
--------------------
📊 Real Database Insights:
100% computed from real SQLite records in drafts, reviews, review_events,
findings, brand_rules, and approved_claims.
No hardcoded or fabricated statistics.
"""

from __future__ import annotations

import streamlit as st
from db.connection import get_connection
from services.auth_service import AuthenticatedPrincipal, require_legacy_test_actor
from services.brand_service import require_brand_membership
from ui.styles import demo_banner, inject_styles
from ui.presentation import humanize
from ui.components import page_header


def _render_bar_visual(chart_dict: dict[str, int], color: str = "var(--brl-dark, #19777D)") -> None:
    """
    Render a clean horizontal bar distribution without pyarrow dependency.
    Prevents Windows Code Integrity / WDAC policy DLL blocks from interrupting UI rendering.
    """
    if not chart_dict:
        return
    max_val = max(chart_dict.values()) if chart_dict.values() else 1
    if max_val == 0:
        max_val = 1
    html = ['<div style="margin-top: 8px; margin-bottom: 12px;">']
    for label, count in chart_dict.items():
        pct = max(5, int((count / max_val) * 100))
        html.append(
            f'<div style="margin-bottom: 10px;">'
            f'  <div style="display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 2px;">'
            f'    <span style="font-weight: 500; color: var(--brl-text,#20383B);">{label}</span>'
            f'    <span style="color: var(--brl-text-secondary,#294448); font-weight: 600;">{count}</span>'
            f'  </div>'
            f'  <div style="background: var(--brl-bg-secondary,#D8E9E8); border-radius: 6px; height: 7px; width: 100%;">'
            f'    <div style="background: {color}; height: 7px; border-radius: 6px; width: {pct}%;"></div>'
            f'  </div>'
            f'</div>'
        )
    html.append('</div>')
    st.markdown("".join(html), unsafe_allow_html=True)


def _query_db_insights(
    principal: AuthenticatedPrincipal | None = None, brand_id: int | None = None,
) -> dict:
    """Execute real-time analytical aggregation queries directly on SQLite."""
    if principal is not None:
        if brand_id is None:
            raise ValueError("A brand is required for authenticated insights.")
        require_brand_membership(principal, brand_id)
        draft_filter = "d.brand_id = ?"
        draft_params = (brand_id,)
    else:
        # Retain this private legacy query only for explicitly opted-in pytest
        # fixtures. Every application route must provide a session principal.
        require_legacy_test_actor("read unscoped UI analytics")
        draft_filter = "1 = 1"
        draft_params = ()
    finding_join = (
        " JOIN draft_versions dv ON dv.id = f.draft_version_id"
        " JOIN drafts d ON d.id = dv.draft_id"
    )
    event_join = (
        " JOIN reviews r ON r.id = re.review_id"
        " JOIN drafts d ON d.id = r.draft_id"
    )
    with get_connection() as conn:
        # Totals
        total_drafts = conn.execute(
            "SELECT COUNT(*) AS n FROM drafts d WHERE " + draft_filter, draft_params
        ).fetchone()["n"]
        total_reviews = conn.execute(
            "SELECT COUNT(*) AS n FROM reviews r JOIN drafts d ON d.id = r.draft_id WHERE "
            + draft_filter, draft_params,
        ).fetchone()["n"]
        total_findings = conn.execute(
            "SELECT COUNT(*) AS n FROM findings f" + finding_join + " WHERE " + draft_filter,
            draft_params,
        ).fetchone()["n"]
        total_claims = conn.execute(
            "SELECT COUNT(*) AS n FROM approved_claims ac JOIN drafts d ON d.id = ac.draft_id WHERE "
            + draft_filter, draft_params,
        ).fetchone()["n"]

        # Findings by result
        rows = conn.execute(
            "SELECT f.result, COUNT(*) AS n FROM findings f" + finding_join +
            " WHERE " + draft_filter + " GROUP BY f.result ORDER BY n DESC", draft_params
        ).fetchall()
        findings_by_result = {r["result"]: r["n"] for r in rows}

        # Findings by severity
        rows = conn.execute(
            "SELECT f.severity, COUNT(*) AS n FROM findings f" + finding_join +
            " WHERE " + draft_filter + " GROUP BY f.severity ORDER BY n DESC", draft_params
        ).fetchall()
        findings_by_severity = {r["severity"]: r["n"] for r in rows}

        # Creator actions breakdown (Finding status)
        rows = conn.execute(
            "SELECT f.status, COUNT(*) AS n FROM findings f" + finding_join +
            " WHERE " + draft_filter + " GROUP BY f.status ORDER BY n DESC", draft_params
        ).fetchall()
        creator_actions = {r["status"]: r["n"] for r in rows}

        # Review outcomes (Reviews status)
        rows = conn.execute(
            "SELECT r.status, COUNT(*) AS n FROM reviews r JOIN drafts d ON d.id = r.draft_id WHERE "
            + draft_filter + " GROUP BY r.status ORDER BY n DESC", draft_params
        ).fetchall()
        review_outcomes = {r["status"]: r["n"] for r in rows}

        # Drafts by status
        rows = conn.execute(
            "SELECT d.status, COUNT(*) AS n FROM drafts d WHERE " + draft_filter +
            " GROUP BY d.status ORDER BY n DESC", draft_params
        ).fetchall()
        drafts_by_status = {r["status"]: r["n"] for r in rows}

        # Approved claims by drift status
        rows = conn.execute(
            "SELECT ac.drift_status, COUNT(*) AS n FROM approved_claims ac JOIN drafts d ON d.id = ac.draft_id WHERE "
            + draft_filter + " GROUP BY ac.drift_status ORDER BY n DESC", draft_params
        ).fetchall()
        claims_by_drift = {r["drift_status"]: r["n"] for r in rows}

        # Top violated rules (Recurring FAIL findings)
        rows = conn.execute(
            """
            SELECT br.id, br.name, br.severity, br.rule_type, COUNT(*) AS fail_count
            FROM findings f
            JOIN brand_rules br ON br.id = f.rule_id
            """ + finding_join + """
            WHERE f.result = 'FAIL' AND """ + draft_filter + """
            GROUP BY f.rule_id
            ORDER BY fail_count DESC
            LIMIT 5
            """, draft_params
        ).fetchall()
        top_violated_rules = [dict(r) for r in rows]

        # Top dismissed rules (creator dismissed finding linked to a rule)
        rows = conn.execute(
            """
            SELECT br.id, br.name, COUNT(*) AS dismiss_count
            FROM findings f
            JOIN brand_rules br ON br.id = f.rule_id
            """ + finding_join + """
            WHERE f.status = 'DISMISSED' AND """ + draft_filter + """
            GROUP BY f.rule_id
            ORDER BY dismiss_count DESC
            LIMIT 5
            """, draft_params
        ).fetchall()
        top_dismissed_rules = [dict(r) for r in rows]

        # Fact versions summary
        rows = conn.execute(
            "SELECT fv.approval_status, COUNT(*) AS n FROM fact_versions fv "
            "JOIN facts f ON f.id = fv.fact_id JOIN products p ON p.id = f.product_id "
            + ("WHERE p.brand_id = ? " if principal is not None else "")
            + "GROUP BY fv.approval_status", draft_params
        ).fetchall()
        fact_versions_by_status = {r["approval_status"]: r["n"] for r in rows}

        clarification_requests = conn.execute(
            """
            SELECT COUNT(*) AS n FROM review_events re
            """ + event_join + """
            WHERE event_type = 'ESCALATED'
              AND note LIKE 'GUIDELINE_CLARIFICATION:%' AND """ + draft_filter,
            draft_params,
        ).fetchone()["n"]
        override_reasons = conn.execute(
            """
            SELECT COUNT(*) AS n FROM review_events re
            """ + event_join + """
            WHERE event_type = 'OVERRIDE' AND length(trim(COALESCE(note, ''))) > 0
              AND """ + draft_filter,
            draft_params,
        ).fetchone()["n"]
        repeated_change_requests = conn.execute(
            """
            SELECT COUNT(*) AS n FROM (
                SELECT r.draft_id
                FROM review_events e JOIN reviews r ON r.id = e.review_id
                JOIN drafts d ON d.id = r.draft_id
                WHERE e.event_type = 'CHANGES_REQUESTED' AND """ + draft_filter + """
                GROUP BY r.draft_id
                HAVING COUNT(*) > 1
            )
            """, draft_params
        ).fetchone()["n"]
        repeated_dismissal_rules = conn.execute(
            """
            SELECT COUNT(*) AS n FROM (
                SELECT f.rule_id FROM findings f
                """ + finding_join + """
                WHERE f.status = 'DISMISSED' AND f.rule_id IS NOT NULL AND """ + draft_filter + """
                GROUP BY f.rule_id
                HAVING COUNT(*) > 1
            )
            """, draft_params
        ).fetchone()["n"]
        unlinked_claims = conn.execute(
            """
            SELECT COUNT(DISTINCT cm.id) AS n
            FROM claim_mentions cm
            JOIN findings f ON f.claim_mention_id = cm.id
            """ + finding_join + """
            WHERE f.finding_type = 'fact_match'
              AND (f.evidence_relationship IS NULL OR f.evidence_relationship != 'SUPPORTS'
                   OR f.fact_version_id IS NULL) AND """ + draft_filter,
            draft_params,
        ).fetchone()["n"]

    return {
        "total_drafts": total_drafts,
        "total_reviews": total_reviews,
        "total_findings": total_findings,
        "total_claims": total_claims,
        "findings_by_result": findings_by_result,
        "findings_by_severity": findings_by_severity,
        "creator_actions": creator_actions,
        "review_outcomes": review_outcomes,
        "drafts_by_status": drafts_by_status,
        "claims_by_drift": claims_by_drift,
        "top_violated_rules": top_violated_rules,
        "top_dismissed_rules": top_dismissed_rules,
        "fact_versions_by_status": fact_versions_by_status,
        "guideline_clarification_requests": clarification_requests,
        "documented_override_reasons": override_reasons,
        "drafts_with_repeated_change_requests": repeated_change_requests,
        "rules_with_repeated_dismissals": repeated_dismissal_rules,
        "unlinked_claims": unlinked_claims,
    }


def render(principal: AuthenticatedPrincipal) -> None:
    inject_styles()
    page_header("Governance", "Insights")
    demo_banner()

    st.markdown(
        "All metrics on this page are computed dynamically from stored SQLite records. "
        "No hardcoded statistics or synthetic values are used."
    )

    brand_id = st.session_state.get("active_brand_id")
    data = _query_db_insights(principal, brand_id)

    # ---- High Level Overview KPI Grid ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Drafts", data["total_drafts"])
    c2.metric("Total Reviews", data["total_reviews"])
    c3.metric("Total Findings", data["total_findings"])
    c4.metric("Approved Claims", data["total_claims"])

    st.markdown("---")

    # ---- Findings by Severity and Result ----
    col_sev, col_res = st.columns(2)

    with col_sev:
        st.subheader("⚠️ Findings by Severity")
        sev_data = data["findings_by_severity"]
        if sev_data:
            # Display sorted by severity level
            ordered_sevs = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
            sev_icons = {
                "CRITICAL": "🔴",
                "HIGH": "🟠",
                "MEDIUM": "🟡",
                "LOW": "🔵",
            }
            chart_dict = {}
            for sev in ordered_sevs:
                count = sev_data.get(sev, 0)
                if count > 0:
                    icon = sev_icons.get(sev, "⚪")
                    label = humanize(sev)
                    st.write(f"{icon} **{label}**: {count}")
                    chart_dict[label] = count
            # Show visual distribution bars
            if chart_dict:
                _render_bar_visual(chart_dict, color="#E53E3E")
        else:
            st.info("No findings recorded in database yet.")

    with col_res:
        st.subheader("🎯 Findings by Result")
        res_data = data["findings_by_result"]
        if res_data:
            res_icons = {
                "FAIL": "❌",
                "WARNING": "⚠️",
                "PASS": "✅",
                "NOT_OBSERVED": "👁️",
                "REVIEW_REQUIRED": "🔍",
                "UNLINKED": "🔗",
                "ERROR": "💥",
            }
            chart_dict = {}
            for res_name, count in res_data.items():
                icon = res_icons.get(res_name, "📌")
                label = humanize(res_name)
                st.write(f"{icon} **{label}**: {count}")
                chart_dict[label] = count
            if chart_dict:
                _render_bar_visual(chart_dict, color="#55555B")
        else:
            st.info("No finding results recorded yet.")

    st.markdown("---")

    # ---- Creator Actions & Review Outcomes ----
    col_act, col_rev = st.columns(2)

    with col_act:
        st.subheader("✍️ Creator Actions Breakdown")
        act_data = data["creator_actions"]
        if act_data:
            act_icons = {
                "ACCEPTED": "✅",
                "EDITED": "✏️",
                "DISMISSED": "🚫",
                "ESCALATED": "🚨",
                "OPEN": "🔓",
            }
            act_chart = {}
            for act_name, count in act_data.items():
                icon = act_icons.get(act_name, "📌")
                label = humanize(act_name)
                st.write(f"{icon} **{label}**: {count}")
                act_chart[label] = count
            if act_chart:
                _render_bar_visual(act_chart, color="#77777D")
        else:
            st.info("No creator actions logged on findings yet.")

    with col_rev:
        st.subheader("⚖️ Review Outcomes & Decisions")
        rev_outcomes = data["review_outcomes"]
        if rev_outcomes:
            rev_icons = {
                "APPROVED": "✅",
                "CHANGES_REQUESTED": "🔄",
                "OPEN": "📬",
                "ESCALATED": "🚨",
            }
            rev_chart = {}
            for outcome, count in rev_outcomes.items():
                icon = rev_icons.get(outcome, "⚖️")
                label = humanize(outcome)
                st.write(f"{icon} **{label}**: {count}")
                rev_chart[label] = count
            if rev_chart:
                _render_bar_visual(rev_chart, color="#5D5D63")
        else:
            st.info("No review decisions recorded yet.")

    st.markdown("---")

    # ---- Top Violated Rules & Claim Ledger Drift Summary ----
    col_rules, col_drift = st.columns(2)

    with col_rules:
        st.subheader("🏆 Top Violated Rules (Recurring)")
        violated = data["top_violated_rules"]
        if violated:
            for i, r in enumerate(violated, 1):
                st.markdown(
                    f"{i}. **{r['name']}** ({humanize(r['rule_type'])}) — "
                    f"**{r['fail_count']} guideline issue(s)** &nbsp; "
                    f"<span style='color:#C0392B;font-size:12px;'>[{humanize(r['severity'])}]</span>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("No rule violations (FAIL) recorded yet.")

        if data["top_dismissed_rules"]:
            st.markdown("#### 🚫 Most Dismissed Rules")
            for i, dr in enumerate(data["top_dismissed_rules"], 1):
                st.write(f"{i}. **{dr['name']}** — {dr['dismiss_count']} dismissal(s)")

    with col_drift:
        st.subheader("📜 Claim Ledger Drift Summary")
        drift_data = data["claims_by_drift"]
        total_claims = data["total_claims"]

        if total_claims > 0:
            n_curr = drift_data.get("CURRENT", 0)
            n_rev = drift_data.get("REVIEW_REQUIRED", 0)
            n_exp = drift_data.get("EXPIRED", 0)

            d1, d2, d3 = st.columns(3)
            d1.metric("🟢 Active and verified", n_curr)
            d2.metric("🟡 Needs re-verification", n_rev)
            d3.metric("🔴 Evidence expired", n_exp)

            if n_rev > 0:
                st.warning(f"{n_rev} approved claim(s) need re-verification after facts changed.")
            if n_exp > 0:
                st.error(f"{n_exp} approved claim(s) link to expired evidence.")
            if n_curr == total_claims:
                st.success(
                    "All ledger entries are currently marked CURRENT. Drift status is not a substitute "
                    "for checking each entry's evidence links."
                )
        else:
            st.info("No approved claims in ledger yet.")

        st.markdown("#### 🧪 Fact Catalog Health")
        fv_status = data["fact_versions_by_status"]
        if fv_status:
            fv_cols = st.columns(len(fv_status))
            for i, (f_st, count) in enumerate(fv_status.items()):
                fv_cols[i].metric(humanize(f_st), count)
        else:
            st.caption("No fact versions registered.")

    st.markdown("---")
    st.subheader("Clarification, escalation & recurring review signals")
    st.caption(
        "These counts require explicit stored events or repeated records; a high violation count alone "
        "is not interpreted as an unclear guideline."
    )
    signal_cols = st.columns(5)
    signal_cols[0].metric("Unlinked claims", data["unlinked_claims"])
    signal_cols[1].metric("Clarification requests", data["guideline_clarification_requests"])
    signal_cols[2].metric("Documented overrides", data["documented_override_reasons"])
    signal_cols[3].metric("Repeated change requests", data["drafts_with_repeated_change_requests"])
    signal_cols[4].metric("Rules with repeated dismissals", data["rules_with_repeated_dismissals"])
    if not any(
        data[key]
        for key in (
            "unlinked_claims", "guideline_clarification_requests",
            "documented_override_reasons", "drafts_with_repeated_change_requests",
            "rules_with_repeated_dismissals",
        )
    ):
        st.info("No clarification, override, unlinked-claim, or repeated-review signals recorded yet.")
