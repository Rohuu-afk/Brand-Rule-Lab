"""Welcome and getting-started landing page."""

import streamlit as st

from models.enums import UserRole
from services.knowledge_onboarding import get_setup_readiness
from ui.components import page_header, section_heading
from ui.styles import ai_disclosure, demo_banner, inject_styles


def _navigate(
    page: str,
    role: str | None = None,
    load_sample_review: bool = False,
) -> None:
    if role:
        st.session_state["sidebar_role"] = role
    if load_sample_review:
        st.session_state["load_sample_review"] = True
    st.session_state["sidebar_page"] = page


def render() -> None:
    inject_styles()
    readiness = get_setup_readiness()
    page_header("Overview", "Brand Rule Lab")
    st.subheader("Your pre-publication brand compliance workspace.")
    st.write(
        "Organise your brand knowledge, review marketing content against approved rules "
        "and evidence, correct issues, and manage human approvals before publication."
    )
    demo_banner()
    st.info(
        "The demo workspace is available without signing in. The role selector is a "
        "demonstration control, not authentication or real access control."
    )

    section_heading("Try the core workflow")
    st.write(
        "Submit marketing copy → inspect rule-based findings and evidence → correct the draft "
        "and make a human decision. You can try this now without setting up a brand first."
    )
    action_cols = st.columns(3)
    action_cols[0].button(
        "Try a guided sample review",
        type="primary",
        width="stretch",
        on_click=_navigate,
        args=("New Review", UserRole.CREATOR.value, True),
    )
    action_cols[1].button(
        "Review my own content",
        width="stretch",
        on_click=_navigate,
        args=("New Review", UserRole.CREATOR.value),
    )
    action_cols[2].button(
        "Set up brand knowledge",
        width="stretch",
        on_click=_navigate,
        args=("Brand Knowledge",),
    )

    with st.expander("AI provider connection diagnostics", expanded=False):
        from ai.base import AIProviderError, safe_error_summary
        from ai.provider_factory import get_provider_diagnostics

        diagnostics = get_provider_diagnostics()
        st.caption(
            "Each test sends a fixed, non-draft prompt directly to one provider. "
            "Credential values are never displayed."
        )
        st.write(f"Provider selected: **{diagnostics['selected_provider']}**")
        nvidia_column, gemini_column = st.columns(2)
        provider_columns = (
            (nvidia_column, "NVIDIA NIM", "nvidia"),
            (gemini_column, "Google Gemini", "gemini"),
        )
        for column, provider_name, provider_key in provider_columns:
            config = diagnostics["providers"][provider_name]
            with column:
                st.markdown(f"**{provider_name}**")
                st.write(
                    "Credential configured: "
                    + ("**YES**" if config["credential_configured"] else "**NO**")
                )
                st.write("Provider selected: " + ("**YES**" if config["selected"] else "**NO**"))
                st.write(f"Model configured: `{config['model']}`")
                if st.button(
                    f"Test {provider_name} connection",
                    key=f"test_{provider_key}_provider_connection",
                    disabled=not config["credential_configured"],
                ):
                    try:
                        if provider_key == "nvidia":
                            from ai.nvidia_provider import NvidiaNimProvider

                            provider = NvidiaNimProvider()
                        else:
                            from ai.gemini_provider import GeminiProvider

                            provider = GeminiProvider()
                        provider.test_connection()
                        st.session_state[f"{provider_key}_connection_result"] = {
                            "success": True,
                            "model": provider.last_model_used or config["model"],
                        }
                    except AIProviderError as exc:
                        st.session_state[f"{provider_key}_connection_result"] = {
                            "success": False,
                            "category": safe_error_summary(exc),
                        }

                result = st.session_state.get(f"{provider_key}_connection_result")
                if result:
                    if result["success"]:
                        st.success(f"Connection result: SUCCESS · model: {result['model']}")
                    else:
                        st.error(f"Connection result: {result['category']}")

    st.markdown("---")
    section_heading("Optional workspace setup")
    st.caption(
        "These steps help customise the workspace but are not required to try the content review."
    )
    completed = sum(item["done"] for item in readiness["checklist"])
    total = len(readiness["checklist"])
    st.progress(completed / total if total else 0)
    st.caption(f"{completed} of {total} first-use steps complete. Progress is based on saved workspace data.")
    metric_rows = (
        (
            ("Active guidelines", readiness["rules"]),
            ("Approved facts", readiness["facts"]),
            ("Approved terminology", readiness["terminology"]),
        ),
        (
            ("Approved assets", readiness["assets"]),
            ("Unresolved conflicts", readiness["conflicts"]),
            ("Awaiting knowledge review", readiness["pending_entries"]),
        ),
    )
    for row in metric_rows:
        columns = st.columns(3)
        for column, (label, value) in zip(columns, row):
            column.metric(label, value)
    if readiness["documents_attention"]:
        st.warning(
            f"{readiness['documents_attention']} document(s) need attention. "
            "Inspect extraction warnings and correct text before using it as a source."
        )
    if not readiness["brand_identity"]:
        st.info("Brand identity has not been saved yet. You can still start a review with the available demo knowledge.")

    section_heading("Getting started")
    for index, item in enumerate(readiness["checklist"], start=1):
        status = "Complete" if item["done"] else "Next"
        st.markdown(f"**{index}. {item['label']}** · {status}")
    st.caption("You can skip irrelevant steps and return later. A partially configured brand can still use available review checks.")

    steps = [
        (
            "1. Access your workspace",
            "Open the demo without an account or sign-in. No real user accounts or role-based access are implemented here.",
            ("Brand Knowledge", None, "Open Brand Knowledge"),
        ),
        (
            "2. Set up your brand",
            "Upload supported documents, inspect scanned-page extraction, or enter brand information manually. Add product facts, terminology and visual assets as needed.",
            ("Brand Knowledge", None, "Set up Brand Knowledge"),
        ),
        (
            "3. Verify proposed knowledge",
            "AI can organise extracted text into proposals. Compare source quotations with the original and approve each entry using the appropriate simulated reviewer role.",
            ("Brand Knowledge", None, "Review knowledge proposals"),
        ),
        (
            "4. Create a content review",
            "Submit marketing copy, product descriptions or supported campaign content for checks against available approved information.",
            ("New Review", UserRole.CREATOR.value, "Create a review"),
        ),
        (
            "5. Understand review results",
            "Review findings, severity, source-linked evidence, suggested corrections and checks that could not be completed. AI suggestions are not approvals.",
            ("Review Results", UserRole.CREATOR.value, "Open Review Results"),
        ),
        (
            "6. Request human approval",
            "After making corrections and rechecking the draft, submit it to the simulated Reviewer queue for a human decision.",
            ("Draft Approvals", UserRole.REVIEWER.value, "Open Draft Approvals"),
        ),
        (
            "7. Monitor claims and patterns",
            "Use the Claim Ledger and Review History to inspect claims and drift, and Insights to find recurring compliance problems.",
            ("Claim Ledger", UserRole.REVIEWER.value, "Open Claim Ledger"),
        ),
    ]
    for step_index, (title, description, (page, role, action)) in enumerate(steps, start=1):
        with st.expander(title):
            st.write(description)
            if title.startswith("3."):
                left, right = st.columns(2)
                left.button(
                    action, key="welcome_knowledge_approval",
                    on_click=_navigate, args=(page,),
                )
                right.button(
                    "Open Fact Approvals", key="welcome_fact_approval",
                    on_click=_navigate, args=("Fact Approvals", UserRole.FACT_OWNER.value),
                )
            else:
                st.button(
                    action, key=f"welcome_step_{step_index}",
                    on_click=_navigate, args=(page, role),
                )

    st.markdown("---")
    section_heading("Where do I find things?")
    navigation = [
        ("Overview / Welcome", "Learn how to use Brand Rule Lab", "Overview", None),
        ("Brand Knowledge", "Set up a brand and review extracted proposals", "Brand Knowledge", None),
        ("Brand Guidelines", "Manage saved brand guidelines", "Brand Guidelines", None),
        ("Products & Facts", "Add products and manage product facts", "Products & Facts", None),
        ("Brand Assets", "Register and manage visual assets", "Brand Assets", None),
        ("New Review", "Submit content for checking", "New Review", UserRole.CREATOR.value),
        ("Review Results", "Understand findings and recommended corrections", "Review Results", UserRole.CREATOR.value),
        ("Draft Approvals", "Handle submitted draft approval requests", "Draft Approvals", UserRole.REVIEWER.value),
        ("Fact Approvals", "Approve or reject product fact versions", "Fact Approvals", UserRole.FACT_OWNER.value),
        ("Claim Ledger", "Review approved claims and their status", "Claim Ledger", UserRole.REVIEWER.value),
        ("Review History", "Browse previous reviews", "Review History", None),
        ("Insights", "Monitor recurring review outcomes", "Insights", None),
    ]
    for display, purpose, page, role in navigation:
        left, right = st.columns([3, 1])
        left.write(f"**{display}** — {purpose}")
        right.button(
            "Open", key=f"where_{page}",
            on_click=_navigate, args=(page, role),
        )

    with st.expander("What information may be sent to AI?"):
        ai_disclosure()
        st.write(
            "For document onboarding, extracted text is sent only after you select the explicit "
            "consent checkbox and request AI extraction. Raw binary documents are parsed and "
            "stored locally; they are not sent as files."
        )
