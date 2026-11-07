"""Welcome and getting-started landing page."""

import streamlit as st

from models.enums import UserRole
from services.knowledge_onboarding import get_setup_readiness
from ui.components import page_header, section_heading
from ui.styles import ai_disclosure, demo_banner, inject_styles


def _navigate(page: str, role: str | None = None) -> None:
    if role:
        st.session_state["sidebar_role"] = role
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

    action_cols = st.columns(3)
    action_cols[0].button(
        "Get Started", type="primary", width="stretch",
        on_click=_navigate, args=("Brand Knowledge",),
    )
    action_cols[1].button(
        "Continue to a saved review", width="stretch",
        on_click=_navigate, args=("Review History",),
    )
    action_cols[2].button(
        "Explore the demo workspace", width="stretch",
        on_click=_navigate, args=("New Review", UserRole.CREATOR.value),
    )

    st.markdown("---")
    section_heading("Your setup progress")
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
