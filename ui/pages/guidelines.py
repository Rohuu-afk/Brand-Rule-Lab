"""Guideline, rule-proposal, terminology, and campaign governance."""

from __future__ import annotations

import json

import streamlit as st

from engines.rule_engine import run_self_tests
from models.enums import BrandRole, RuleType, UserRole
from services.document_parser import MAX_BYTES, ParseError, parse_file
from services.guideline_service import (
    approve_rule,
    create_campaign_brief,
    create_guideline,
    create_rule_proposal,
    deactivate_brief,
    deactivate_rule,
    get_all_briefs,
    get_all_guidelines,
    get_all_products,
    get_all_rules,
    get_rule_test_results,
    import_campaign_briefs_json,
    reject_rule,
    set_guideline_active,
    update_campaign_brief,
    update_guideline,
    update_rule_proposal,
)
from services.auth_service import AuthenticatedPrincipal
from services.brand_service import require_brand_membership
from ui.presentation import humanize
from ui.components import page_header, section_heading
from ui.styles import demo_banner, inject_styles


def _management_access(role: UserRole | str | None) -> bool:
    value = role.value if isinstance(role, UserRole) else role
    return value in {
        BrandRole.OWNER.value, BrandRole.REVIEWER.value, BrandRole.FACT_OWNER.value,
        UserRole.REVIEWER.value, UserRole.FACT_OWNER.value,
    }


def render(
    role: UserRole | None = None,
    principal: AuthenticatedPrincipal | None = None,
):
    inject_styles()
    page_header("Workspace", "Brand Guidelines & Rules")
    demo_banner()
    brand_id = None
    if principal is not None:
        brand_id = st.session_state.get("active_brand_id")
        membership = require_brand_membership(principal, brand_id)
        role = membership["role"]
    can_manage = _management_access(role)
    if not can_manage:
        st.info("Guideline and rule decisions are available to Reviewer/Fact Owner demo roles.")

    products = get_all_products(principal=principal, brand_id=brand_id)
    product_options = {"All Products": None} | {p["name"]: p["id"] for p in products}
    tabs = st.tabs(["Guidelines", "Rules & Terminology", "Campaign Briefs"])

    with tabs[0]:
        selected_name = st.selectbox("Filter guidelines by product", list(product_options), key="guideline_filter")
        for guideline in get_all_guidelines(
            product_id=product_options[selected_name], principal=principal, brand_id=brand_id
        ):
            with st.expander(f"{guideline['title']} — {guideline.get('source_ref') or 'No source reference'}"):
                st.write(guideline["content"])
                st.caption(
                    f"Product: {guideline.get('product_name') or 'All'} · "
                    f"Version: {guideline.get('version') or '1.0'} · "
                    f"Source: {guideline.get('source_type', 'manual')} · "
                    f"Section/page: {guideline.get('section_ref') or 'Not specified'}"
                )
                if can_manage:
                    product_names = list(product_options)
                    current_product = next(
                        (name for name, value in product_options.items() if value == guideline["product_id"]),
                        "All Products",
                    )
                    with st.form(f"edit_guideline_{guideline['id']}"):
                        edit_title = st.text_input("Title", value=guideline["title"])
                        edit_product = st.selectbox(
                            "Product scope", product_names,
                            index=product_names.index(current_product),
                            key=f"guideline_product_{guideline['id']}",
                        )
                        edit_version = st.text_input("Version", value=guideline.get("version") or "1.0")
                        edit_source = st.text_input("Source reference", value=guideline.get("source_ref") or "")
                        edit_section = st.text_input("Page or section", value=guideline.get("section_ref") or "")
                        edit_content = st.text_area("Content", value=guideline["content"], height=120)
                        save_guideline = st.form_submit_button("Save guideline changes")
                    if save_guideline:
                        update_guideline(
                            guideline["id"], edit_title, edit_content, edit_source,
                            edit_version, product_options[edit_product],
                            guideline.get("source_type") or "manual", edit_section,
                            principal=principal, brand_id=brand_id,
                        )
                        st.success("Guideline updated.")
                        st.rerun()
                    if guideline["is_active"] and st.button(
                        "Deactivate guideline", key=f"deactivate_guideline_{guideline['id']}"
                    ):
                        set_guideline_active(
                            guideline["id"], False, principal=principal, brand_id=brand_id
                        )
                        st.success("Guideline deactivated.")
                        st.rerun()

        if can_manage:
            section_heading("Define or import a guideline")
            with st.form("create_guideline_form"):
                title = st.text_input("Guideline title")
                product_name = st.selectbox("Product scope", list(product_options), key="guideline_new_product")
                version = st.text_input("Version", value="1.0")
                source_ref = st.text_input("Source reference")
                section_ref = st.text_input("Page or section")
                upload = st.file_uploader("Import PDF, DOCX, or TXT (10 MB max)", type=["pdf", "docx", "txt"])
                pasted = st.text_area("Or paste guideline text", height=140)
                save = st.form_submit_button("Save guideline")
            if save:
                try:
                    content = pasted.strip()
                    source_type = "manual"
                    if upload is not None:
                        if upload.size > MAX_BYTES:
                            raise ParseError(
                                f"File exceeds the {MAX_BYTES // (1024 * 1024)} MB upload limit."
                            )
                        parsed = parse_file(upload.name, upload.getvalue())
                        if not parsed["text"].strip():
                            raise ValueError("No text could be extracted; guideline was not saved.")
                        content = parsed["text"]
                        source_type = parsed["source_type"]
                    create_guideline(
                        title, content, source_ref, version,
                        product_options[product_name], source_type, section_ref,
                        principal=principal, brand_id=brand_id,
                    )
                except (ParseError, ValueError) as exc:
                    st.error(str(exc))
                else:
                    st.success("Guideline saved.")
                    st.rerun()

    with tabs[1]:
        selected_name = st.selectbox("Filter rules by product", list(product_options), key="rule_filter")
        rules = get_all_rules(
            product_id=product_options[selected_name], principal=principal, brand_id=brand_id
        )
        guidelines = get_all_guidelines(
            product_id=product_options[selected_name], principal=principal, brand_id=brand_id
        )
        guideline_options = {"No linked guideline": None} | {
            f"{item['title']} (#{item['id']})": item["id"] for item in guidelines
        }

        for rule in rules:
            status = rule.get("review_status", "APPROVED" if rule["owner_approved"] else "PROPOSED")
            indicator = "Active" if rule["is_active"] and rule["owner_approved"] else status.title()
            with st.expander(f"{indicator} · {humanize(rule['rule_type'])} · {rule['name']}"):
                st.write(rule.get("description") or "")
                st.caption(f"Severity: {rule['severity']} · Guideline: {rule.get('guideline_title') or 'None'}")
                with st.expander("Rule specification and test cases"):
                    st.code(json.dumps(json.loads(rule["parameters"]), indent=2), language="json")
                    test_results = get_rule_test_results(
                        rule["id"], principal=principal, brand_id=brand_id
                    )
                    if test_results:
                        for item in test_results:
                            st.write(
                                f"{humanize('PASS' if item['passed'] else 'FAIL')} · "
                                f"{item['test_type']}: {item['test_text']} — {item['explanation']}"
                            )
                    else:
                        st.warning("No positive/negative test cases are stored.")
                if can_manage and status == "PROPOSED":
                    product_names = list(product_options)
                    existing_product = next(
                        (name for name, value in product_options.items() if value == rule["product_id"]),
                        "All Products",
                    )
                    existing_guideline = next(
                        (label for label, value in guideline_options.items() if value == rule["guideline_id"]),
                        "No linked guideline",
                    )
                    rule_types = [item.value for item in RuleType]
                    severities = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
                    with st.form(f"edit_rule_proposal_{rule['id']}"):
                        edit_rule_name = st.text_input("Name", value=rule["name"])
                        edit_description = st.text_input("Description", value=rule.get("description") or "")
                        edit_type = st.selectbox(
                            "Predicate", rule_types,
                            index=rule_types.index(rule["rule_type"]),
                            key=f"rule_type_{rule['id']}",
                            format_func=humanize,
                        )
                        edit_product = st.selectbox(
                            "Product scope", product_names,
                            index=product_names.index(existing_product),
                            key=f"rule_product_{rule['id']}",
                        )
                        edit_guideline = st.selectbox(
                            "Source guideline", list(guideline_options),
                            index=list(guideline_options).index(existing_guideline),
                            key=f"rule_guideline_{rule['id']}",
                        )
                        edit_severity = st.selectbox(
                            "Severity", severities,
                            index=severities.index(rule["severity"]),
                            key=f"rule_severity_{rule['id']}",
                        )
                        edit_parameters = st.text_area(
                            "Parameters (JSON)", value=rule["parameters"],
                            key=f"rule_params_{rule['id']}",
                        )
                        edit_scope = st.text_area(
                            "Scope (JSON)", value=rule["scope"],
                            key=f"rule_scope_{rule['id']}",
                        )
                        edit_positive = st.text_area(
                            "Positive tests (one per line)",
                            value="\n".join(json.loads(rule["positive_tests"] or "[]")),
                            key=f"rule_positive_{rule['id']}",
                        )
                        edit_negative = st.text_area(
                            "Negative tests (one per line)",
                            value="\n".join(json.loads(rule["negative_tests"] or "[]")),
                            key=f"rule_negative_{rule['id']}",
                        )
                        save_proposal = st.form_submit_button("Save proposal edits")
                    if save_proposal:
                        try:
                            update_rule_proposal(
                                rule["id"], edit_rule_name, edit_description, edit_type,
                                edit_parameters, edit_positive.splitlines(), edit_negative.splitlines(),
                                product_options[edit_product], guideline_options[edit_guideline],
                                edit_scope, edit_severity,
                                principal=principal, brand_id=brand_id,
                            )
                        except (ValueError, PermissionError) as exc:
                            st.error(str(exc))
                        else:
                            st.success("Proposal updated and remains inactive.")
                            st.rerun()
                    left, right = st.columns(2)
                    if left.button("Approve and activate", key=f"activate_rule_{rule['id']}"):
                        try:
                            approve_rule(rule["id"], principal=principal, brand_id=brand_id)
                        except (ValueError, PermissionError) as exc:
                            st.error(str(exc))
                        else:
                            st.success("Rule approved and activated.")
                            st.rerun()
                    if right.button("Reject proposal", key=f"reject_rule_{rule['id']}"):
                        try:
                            reject_rule(rule["id"], principal=principal, brand_id=brand_id)
                        except PermissionError as exc:
                            st.error(str(exc))
                        else:
                            st.info("Rule proposal rejected and retained.")
                            st.rerun()
                elif can_manage and status == "APPROVED" and rule["is_active"]:
                    if st.button("Deactivate rule", key=f"deactivate_rule_{rule['id']}"):
                        try:
                            deactivate_rule(rule["id"], principal=principal, brand_id=brand_id)
                        except PermissionError as exc:
                            st.error(str(exc))
                        else:
                            st.success("Rule deactivated.")
                            st.rerun()

        if can_manage:
            section_heading("Propose a rule or terminology entry")
            st.caption("Proposals remain inactive until a reviewer tests and approves them.")
            with st.form("create_rule_proposal_form"):
                rule_name = st.text_input("Rule name")
                description = st.text_input("What should this rule govern?")
                rule_type = st.selectbox(
                    "Predicate",
                    [item.value for item in RuleType],
                    format_func=humanize,
                )
                rule_product = st.selectbox("Product scope", list(product_options), key="rule_new_product")
                linked_guideline = st.selectbox("Source guideline", list(guideline_options))
                severity = st.selectbox("Severity", ["LOW", "MEDIUM", "HIGH", "CRITICAL"], index=2)
                parameters = st.text_area(
                    "Parameters (JSON object)",
                    value='{"mode":"banned","terms":["example"],"suggested_correction":"Use approved term"}',
                )
                scope = st.text_area("Scope (JSON object)", value='{"market":["IN"],"channel":["all"]}')
                positive = st.text_area("Positive tests — one passing example per line")
                negative = st.text_area("Negative tests — one failing example per line")
                create_rule = st.form_submit_button("Create inactive proposal")
            if create_rule:
                try:
                    create_rule_proposal(
                        rule_name, description, rule_type, parameters,
                        positive.splitlines(), negative.splitlines(),
                        product_options[rule_product], guideline_options[linked_guideline],
                        scope, severity, principal=principal, brand_id=brand_id,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("Rule proposal saved inactive. Review its cases above before approval.")
                    st.rerun()

    with tabs[2]:
        if can_manage:
            st.caption(
                "Campaign JSON import expects an array with name, product_id, objectives, "
                "tone_guidance, target_audience, key_messages, markets, and channels."
            )
            json_upload = st.file_uploader("Import campaign briefs (JSON)", type=["json"])
            if st.button("Validate and import campaign JSON", key="import_campaign_json"):
                if json_upload is None:
                    st.error("Choose a JSON file first.")
                else:
                    try:
                        import_text = json_upload.getvalue().decode("utf-8-sig")
                    except UnicodeDecodeError:
                        st.error("Campaign JSON must be UTF-8 encoded.")
                    else:
                        outcome = import_campaign_briefs_json(
                            import_text, principal=principal, brand_id=brand_id
                        )
                        if outcome["errors"]:
                            for error in outcome["errors"]:
                                st.error(error)
                        else:
                            st.success(f"Imported {outcome['imported']} active campaign brief(s).")
                            st.rerun()

        briefs = get_all_briefs(
            include_inactive=True, principal=principal, brand_id=brand_id
        )
        if not briefs:
            st.info("No campaign briefs have been added.")
        for brief in briefs:
            with st.expander(f"{brief['name']} — {'Active' if brief['is_active'] else 'Inactive'}"):
                st.write(f"Objective: {brief.get('objectives') or 'Not specified'}")
                st.write(f"Tone: {brief.get('tone_guidance') or 'Not specified'}")
                st.write(f"Audience: {brief.get('target_audience') or 'Not specified'}")
                st.write("Required messages: " + ", ".join(json.loads(brief.get("key_messages") or "[]")))
                st.caption(
                    f"Product: {brief.get('product_name') or 'All'} · "
                    f"Markets: {brief.get('markets') or '[]'} · Channels: {brief.get('channels') or '[]'}"
                )
                if can_manage:
                    message_values = json.loads(brief.get("key_messages") or "[]")
                    market_values = json.loads(brief.get("markets") or "[]")
                    channel_values = json.loads(brief.get("channels") or "[]")
                    product_index = next(
                        (index for index, item in enumerate(products) if item["id"] == brief["product_id"]),
                        0,
                    )
                    with st.form(f"edit_campaign_{brief['id']}"):
                        edit_product = st.selectbox(
                            "Product", [item["name"] for item in products],
                            index=product_index, key=f"brief_product_{brief['id']}",
                        )
                        edit_name = st.text_input("Campaign name", value=brief["name"])
                        edit_objective = st.text_area("Objective", value=brief.get("objectives") or "")
                        edit_tone = st.text_area("Tone guidance", value=brief.get("tone_guidance") or "")
                        edit_audience = st.text_input(
                            "Target audience", value=brief.get("target_audience") or ""
                        )
                        edit_messages = st.text_area(
                            "Required key messages (one per line)",
                            value="\n".join(message_values),
                        )
                        edit_markets = st.multiselect(
                            "Markets", ["IN", "US", "GB", "AU", "SG"], default=market_values,
                            key=f"brief_markets_{brief['id']}",
                        )
                        edit_channels = st.multiselect(
                            "Channels", ["instagram", "email", "website", "print_ad", "tv", "radio"],
                            default=channel_values, key=f"brief_channels_{brief['id']}",
                        )
                        save_brief = st.form_submit_button("Save campaign changes")
                    if save_brief:
                        try:
                            new_product_id = next(item["id"] for item in products if item["name"] == edit_product)
                            update_campaign_brief(
                                brief["id"], edit_name, new_product_id, edit_objective, edit_tone,
                                edit_audience, edit_messages.splitlines(), edit_markets, edit_channels,
                                principal=principal, brand_id=brand_id,
                            )
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            st.success("Campaign brief updated.")
                            st.rerun()
                if can_manage and brief["is_active"]:
                    if st.button("Deactivate brief", key=f"deactivate_brief_{brief['id']}"):
                        deactivate_brief(brief["id"], principal=principal, brand_id=brand_id)
                        st.success("Brief deactivated.")
                        st.rerun()
        if can_manage:
            section_heading("Create a campaign brief")
            with st.form("create_campaign_brief_form"):
                if products:
                    brief_product = st.selectbox("Product", [item["name"] for item in products])
                    brief_name = st.text_input("Campaign name")
                    objective = st.text_area("Objective")
                    tone = st.text_area("Tone guidance")
                    audience = st.text_input("Target audience")
                    key_messages = st.text_area("Required key messages — one per line")
                    markets = st.multiselect("Markets", ["IN", "US", "GB", "AU", "SG"])
                    channels = st.multiselect(
                        "Channels", ["instagram", "email", "website", "print_ad", "tv", "radio"]
                    )
                    create_brief = st.form_submit_button("Save campaign brief")
                else:
                    st.info("Add a product before creating campaign briefs.")
                    create_brief = False
            if create_brief:
                try:
                    product_id = next(item["id"] for item in products if item["name"] == brief_product)
                    create_campaign_brief(
                        brief_name, product_id, objective, tone, audience,
                        key_messages.splitlines(), markets, channels,
                        principal=principal, brand_id=brand_id,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("Campaign brief saved.")
                    st.rerun()
