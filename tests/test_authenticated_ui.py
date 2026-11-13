from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from db.connection import apply_schema, get_connection
from models.enums import UserRole
from services.auth_service import create_user, login, resolve_session
from services.brand_service import accept_invitation, create_brand, invite_member
from models.enums import BrandRole
from ui.pages.new_review import _visual_metadata
from ui.pages.review_results import _get_approval_blockers, _highlighted_copy_html


_UI_ROOT = Path(__file__).parents[1] / "ui"


def test_authenticated_ui_has_no_simulated_role_instructions():
    ui_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in _UI_ROOT.rglob("*.py")
    ).casefold()

    obsolete_phrases = (
        "available without signing in",
        "switching to the **reviewer** simulated role",
        "role is simulated for this demo",
        "there is no sign-in or real role-based access control",
        "simulated fact owner role",
        "simulated reviewer role",
    )
    for phrase in obsolete_phrases:
        assert phrase not in ui_text


def test_approval_blockers_distinguish_evidence_acknowledgement_and_errors():
    blockers = _get_approval_blockers(
        [
            {
                "severity": "HIGH",
                "result": "FAIL",
                "finding_type": "rule",
            },
            {
                "severity": "LOW",
                "result": "UNLINKED",
                "finding_type": "fact_match",
                "evidence_relationship": "NO_MATCH",
                "fact_version_id": None,
            },
            {
                "severity": "LOW",
                "result": "NOT_OBSERVED",
                "finding_type": "accessibility_contrast",
            },
            {
                "severity": "LOW",
                "result": "ERROR",
                "finding_type": "tone",
            },
        ]
    )

    assert blockers == {
        "high_priority": 1,
        "missing_evidence": 1,
        "not_observed": 1,
        "errors": 1,
    }


def test_approval_blockers_report_eligible_findings():
    assert _get_approval_blockers(
        [
            {
                "severity": "LOW",
                "result": "PASS",
                "finding_type": "rule",
            }
        ]
    ) == {
        "high_priority": 0,
        "missing_evidence": 0,
        "not_observed": 0,
        "errors": 0,
    }


def test_review_copy_highlights_only_exact_stored_claim_spans():
    raw_text = "Claim: clear skin in 3 days. <script>alert(1)</script>"
    start = raw_text.index("clear skin")
    exact_finding = {
        "claim_start_char": start,
        "claim_end_char": start + len("clear skin in 3 days"),
        "claim_text": "clear skin in 3 days",
    }

    rendered = _highlighted_copy_html(raw_text, [exact_finding])

    assert '<mark class="brl-copy-highlight"' in rendered
    assert "&lt;script&gt;" in rendered
    assert "clear skin in 3 days</mark>" in rendered

    exact_finding["claim_text"] = "different text"
    assert "<mark" not in _highlighted_copy_html(raw_text, [exact_finding])


def test_video_sampling_and_unobserved_pages_are_kept_when_optional_checks_are_off(
    monkeypatch,
):
    monkeypatch.setattr("ui.pages.new_review.st.checkbox", lambda *args, **kwargs: False)
    monkeypatch.setattr("ui.pages.new_review.st.warning", lambda *args, **kwargs: None)
    metadata = _visual_metadata(
        principal=None,
        brand_id=1,
        product_id=None,
        parsed_result={
            "source_type": "video",
            "video_metadata": {"sampled_keyframes": 1},
            "page_records": [
                {
                    "frame_number": 2,
                    "timestamp": "00:00:02",
                    "confidence": 0.9,
                    "warnings": [],
                }
            ],
            "unobserved_pages": [4],
        },
    )

    assert metadata["video_analysis"] == {"sampled_keyframes": 1}
    assert metadata["video_frames"][0]["frame_number"] == 2
    assert metadata["unobserved_pages"] == [4]


def test_new_review_uses_guided_context_content_and_settings_steps(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "new-review-wizard.sqlite"))
    apply_schema()
    create_user("wizard-creator", "long-local-password", UserRole.CREATOR)
    token = login("wizard-creator", "long-local-password")
    principal = resolve_session(token)
    brand = create_brand(principal, "Wizard Test Brand")

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=10)
    app.session_state["brl_session_token"] = token
    app.session_state["active_brand_id"] = brand["id"]
    app.session_state["sidebar_page"] = "New Review"
    app.run(timeout=10)

    assert not app.exception
    assert "What type of content are you reviewing?" in [item.value for item in app.subheader]
    assert not any("Demo workspace" in item.value for item in app.markdown)
    next(
        button for button in app.button
        if button.key == "new_review_kind_advertisement"
    ).click().run(timeout=10)
    next(button for button in app.button if button.label == "Continue").click().run(timeout=10)
    assert "Add the review context" in [item.value for item in app.subheader]

    next(field for field in app.text_input if field.label == "Review name").set_value(
        "Launch post"
    ).run(timeout=10)
    next(button for button in app.button if button.label == "Continue").click().run(timeout=10)
    assert "Add the content to review" in [item.value for item in app.subheader]

    next(field for field in app.text_area if field.label == "Marketing copy").set_value(
        "A short launch post for our new product."
    ).run(timeout=10)
    next(
        button for button in app.button if button.label == "Continue to review settings"
    ).click().run(timeout=10)

    assert not app.exception
    assert "Confirm checks and run your review" in [item.value for item in app.subheader]
    assert any("Advertisement ·" in item.value for item in app.caption)
    assert any(item.label.startswith("I agree to send the extracted text") for item in app.checkbox)
    assert any(item.label == "Run brand check" for item in app.button)
    assert not next(item for item in app.checkbox if item.label.startswith("I agree to send")).value

    next(button for button in app.button if button.label == "Back").click().run(timeout=10)
    assert next(field for field in app.text_area if field.label == "Marketing copy").value == (
        "A short launch post for our new product."
    )
    next(button for button in app.button if button.label == "Back").click().run(timeout=10)
    assert next(field for field in app.text_input if field.label == "Review name").value == "Launch post"
    app.session_state["sidebar_page"] = "Review Results"
    app.run(timeout=10)
    assert not app.exception
    assert not any("Demo workspace" in item.value for item in app.markdown)


def test_workspace_overview_shows_role_relevant_next_steps(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "overview.sqlite"))
    apply_schema()
    create_user("workspace-reviewer", "long-local-password", UserRole.REVIEWER)
    token = login("workspace-reviewer", "long-local-password")
    from services.auth_service import resolve_session

    principal = resolve_session(token)
    brand = create_brand(principal, "Overview Test Brand")
    with get_connection() as conn:
        conn.execute(
            "UPDATE brand_memberships SET role = 'Reviewer' WHERE brand_id = ? AND user_id = ?",
            (brand["id"], principal.user_id),
        )
        conn.commit()

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=10)
    app.session_state["brl_session_token"] = token
    app.session_state["active_brand_id"] = brand["id"]
    app.run(timeout=10)

    assert not app.exception
    assert any(item.value.startswith("Good ") for item in app.title)
    assert "Awaiting approval" in [item.label for item in app.metric]
    assert "Decide on submitted content" in [button.label for button in app.button]
    assert "Review proposed rules" in [button.label for button in app.button]


def test_dashboard_metrics_are_derived_from_selected_brand_records(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "dashboard-metrics.sqlite"))
    apply_schema()
    create_user("metrics-owner", "long-local-password", UserRole.CREATOR)
    owner = resolve_session(login("metrics-owner", "long-local-password"))
    brand = create_brand(owner, "Metrics Brand")
    with get_connection() as conn:
        approved_draft_id = None
        for title, status in (
            ("In progress", "DRAFT"),
            ("Waiting for reviewer", "READY_FOR_REVIEW"),
            ("Approved this month", "APPROVED"),
        ):
            draft_id = conn.execute(
                "INSERT INTO drafts (title, submitted_by, brand_id, status) "
                "VALUES (?, ?, ?, ?)",
                (title, owner.username, brand["id"], status),
            ).lastrowid
            if status == "APPROVED":
                approved_draft_id = draft_id
        review_id = conn.execute(
            "INSERT INTO reviews (draft_id, reviewer, status) VALUES (?, ?, 'APPROVED')",
            (approved_draft_id, "metrics-reviewer"),
        ).lastrowid
        conn.execute(
            "INSERT INTO review_events (review_id, event_type, actor) "
            "VALUES (?, 'APPROVED', ?)",
            (review_id, "metrics-reviewer"),
        )
        conn.commit()

    from ui.pages.overview import _dashboard_counts

    assert _dashboard_counts(brand["id"]) == (2, 1, 0, 1)


def test_brand_owner_team_page_exposes_confirmed_ownership_transfer(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "team-page.sqlite"))
    apply_schema()
    create_user("team-owner", "long-local-password", UserRole.CREATOR, email="owner@example.com")
    owner = resolve_session(login("team-owner", "long-local-password"))
    create_user("team-reviewer", "long-local-password", UserRole.REVIEWER, email="reviewer@example.com")
    reviewer = resolve_session(login("team-reviewer", "long-local-password"))
    brand = create_brand(owner, "Team Page Brand")
    invite = invite_member(owner, brand["id"], "reviewer@example.com", BrandRole.REVIEWER)
    accept_invitation(reviewer, invite["token"])

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=10)
    app.session_state["brl_session_token"] = owner._session_token
    app.session_state["active_brand_id"] = brand["id"]
    app.session_state["sidebar_page"] = "Team & Permissions"
    app.run(timeout=10)

    assert not app.exception
    assert "Transfer brand ownership" in [item.label for item in app.expander]
    assert "New Brand Owner" in [item.label for item in app.selectbox]
    transfer = next(button for button in app.button if button.label == "Transfer ownership")
    assert transfer.disabled

    next(
        field for field in app.text_input
        if field.label == 'Type "TRANSFER OWNERSHIP" to confirm'
    ).set_value("TRANSFER OWNERSHIP").run()
    transfer = next(button for button in app.button if button.label == "Transfer ownership")
    assert not transfer.disabled
    transfer.click().run()

    assert not app.exception
    with get_connection() as conn:
        roles = {
            row["username"]: row["role"]
            for row in conn.execute(
                """
                SELECT u.username, bm.role FROM brand_memberships bm
                JOIN users u ON u.id = bm.user_id WHERE bm.brand_id = ?
                """,
                (brand["id"],),
            )
        }
        transfer_event = conn.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE brand_id = ? AND action = 'BRAND_OWNERSHIP_TRANSFERRED'",
            (brand["id"],),
        ).fetchone()[0]
    assert roles == {"team-owner": BrandRole.REVIEWER.value, "team-reviewer": BrandRole.OWNER.value}
    assert transfer_event == 1


def test_brand_owner_can_create_an_additional_brand_from_navigation(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "additional-brand-page.sqlite"))
    apply_schema()
    create_user("additional-brand-owner", "long-local-password", UserRole.CREATOR)
    principal = resolve_session(login("additional-brand-owner", "long-local-password"))
    first_brand = create_brand(principal, "First Brand")

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=10)
    app.session_state["brl_session_token"] = principal._session_token
    app.session_state["active_brand_id"] = first_brand["id"]
    app.session_state["sidebar_page"] = "Create Brand"
    app.run(timeout=10)

    assert not app.exception
    assert any("Create a brand workspace" in item.value for item in app.markdown)
    next(item for item in app.text_input if item.label == "Brand name").set_value("Second Brand")
    next(item for item in app.button if item.label == "Create brand").click().run(timeout=10)

    assert not app.exception
    with get_connection() as conn:
        brands = conn.execute(
            "SELECT name FROM brands ORDER BY name"
        ).fetchall()
        memberships = conn.execute(
            "SELECT bm.role FROM brand_memberships bm "
            "JOIN brands b ON b.id = bm.brand_id WHERE b.name = 'Second Brand'"
        ).fetchall()
    assert [row["name"] for row in brands] == ["First Brand", "Second Brand"]
    assert [row["role"] for row in memberships] == [BrandRole.OWNER.value]


def test_brand_owner_audit_page_offers_export_and_local_verification(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "audit-page.sqlite"))
    apply_schema()
    create_user("audit-owner", "long-local-password", UserRole.CREATOR)
    principal = resolve_session(login("audit-owner", "long-local-password"))
    brand = create_brand(principal, "Audit Page Brand")
    from services.audit_service import record_audit_event

    record_audit_event(
        principal,
        action="TEST_AUDIT_EVENT",
        object_type="test",
        brand_id=brand["id"],
    )

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=10)
    app.session_state["brl_session_token"] = principal._session_token
    app.session_state["active_brand_id"] = brand["id"]
    app.session_state["sidebar_page"] = "Audit & Exports"
    app.run(timeout=10)

    assert not app.exception
    assert "Verify an exported audit JSON" in [item.value for item in app.subheader]
    assert "Choose a Brand Rule Lab audit JSON file" in [
        item.label for item in app.file_uploader
    ]
    assert any("Current export verified" in item.value for item in app.success)


def test_brand_owner_can_create_a_product_from_the_products_page(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "product-page.sqlite"))
    apply_schema()
    create_user("product-page-owner", "long-local-password", UserRole.CREATOR)
    principal = resolve_session(login("product-page-owner", "long-local-password"))
    brand = create_brand(principal, "Product Page Brand")

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=10)
    app.session_state["brl_session_token"] = principal._session_token
    app.session_state["active_brand_id"] = brand["id"]
    app.session_state["sidebar_page"] = "Products & Facts"
    app.run(timeout=10)
    assert not app.exception

    assert "Add a product" in [item.label for item in app.expander]
    assert "Create a product before adding facts." in [item.value for item in app.info]
    assert "Create Fact" not in [item.label for item in app.button]
    next(item for item in app.text_input if item.label == "Product name").set_value("Page Product")
    next(item for item in app.text_input if item.label == "SKU or aliases (comma separated)").set_value("PP-01")
    next(item for item in app.multiselect if item.label == "Markets served").set_value(["IN"])
    next(item for item in app.button if item.label == "Create product").click().run(timeout=10)

    assert not app.exception
    assert next(item for item in app.selectbox if item.label == "Product").value == "Page Product"
    with get_connection() as conn:
        product = conn.execute(
            "SELECT name, brand_id, markets, sku_aliases FROM products WHERE name = 'Page Product'"
        ).fetchone()
    assert product is not None
    assert product["brand_id"] == brand["id"]
    assert product["markets"] == '["IN"]'
    assert product["sku_aliases"] == '["PP-01"]'
