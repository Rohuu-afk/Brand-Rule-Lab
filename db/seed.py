"""
db/seed.py
----------
Populates the database with clearly-labelled DEMO data for the
Brand Rule Lab hackathon MVP.

All content is fictional.  Run this script once:
    python db/seed.py

It is idempotent — it checks whether seed data already exists before
inserting, so running it twice will not duplicate rows.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import apply_schema, get_connection


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _already_seeded(conn) -> bool:
    row = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()
    return row["n"] > 0


# ---------------------------------------------------------------------------
# Seed data — all fictional / demo content
# ---------------------------------------------------------------------------

PRODUCTS = [
    {
        "name": "GlowUp Vitamin-C Serum",
        "brand": "GlowUp Naturals",
        "sku_aliases": json.dumps(["GU-VC-30", "glowup-serum", "Vitamin C Serum 30ml"]),
        "markets": json.dumps(["IN", "US", "GB"]),
        "description": "[DEMO] Daily brightening serum with 15% stabilised L-Ascorbic Acid.",
    },
    {
        "name": "PureBalance SPF50 Moisturiser",
        "brand": "GlowUp Naturals",
        "sku_aliases": json.dumps(["PB-SPF50", "purebalance-spf"]),
        "markets": json.dumps(["IN", "US"]),
        "description": "[DEMO] Lightweight SPF50 PA+++ moisturiser for daily protection.",
    },
    {
        "name": "MintFresh Charcoal Cleanser",
        "brand": "MintFresh Co.",
        "sku_aliases": json.dumps(["MF-CC-150", "charcoal-cleanser"]),
        "markets": json.dumps(["IN", "GB"]),
        "description": "[DEMO] Deep-pore charcoal gel cleanser with peppermint extract.",
    },
    {
        "name": "Monsoon Clear Skin Face Wash",
        "brand": "Monsoon Beauty Co.",
        "sku_aliases": json.dumps(["MBC-CSFW", "monsoon-face-wash", "Clear Skin Face Wash"]),
        "markets": json.dumps(["IN", "US", "GB"]),
        "description": "[DEMO] Clarifying daily face wash formulated for clear, balanced skin.",
    },
]

GUIDELINES = [
    {
        "product_id_index": 0,          # GlowUp Vitamin-C Serum
        "title": "GlowUp Brand Voice & Claims Policy",
        "source_ref": "Brand Bible v2.1 §3",
        "content": (
            "[DEMO GUIDELINE] "
            "1. Never use the word 'cure' or 'treat' in relation to any skin condition. "
            "2. All efficacy claims must cite the clinical study reference. "
            "3. Percentage figures must be stated to no more than one decimal place. "
            "4. The term 'natural' may only be used for ingredients that appear on the approved natural-ingredients list. "
            "5. SPF values must match the approved product specification exactly. "
            "6. Avoid superlatives (best, greatest, most powerful) without substantiation."
        ),
    },
    {
        "product_id_index": 1,          # PureBalance SPF50
        "title": "PureBalance Sun-Care Claims Policy",
        "source_ref": "Regulatory Annex RX-2024-02",
        "content": (
            "[DEMO GUIDELINE] "
            "1. The word 'sunscreen' is preferred over 'sunblock' in all markets. "
            "2. SPF must always be followed by PA+++ in the IN market. "
            "3. Do not claim 'invisible' finish without supporting consumer study data. "
            "4. Water-resistance claims require specific test method citation."
        ),
    },
    {
        "product_id_index": 0,
        "title": "Approved Terminology List — Vitamin C",
        "source_ref": "Terminology Register 2024",
        "content": (
            "[DEMO GUIDELINE] "
            "Approved terms: 'brightening', 'radiance', 'luminosity', 'even skin tone', "
            "'antioxidant protection', 'collagen-supporting'. "
            "Banned terms: 'whitening', 'lightening', 'skin bleaching', 'depigmenting'."
        ),
    },
    {
        "product_id_index": 3,          # Monsoon Clear Skin Face Wash
        "title": "[V-04] Monsoon Tone & Absolute Claims Policy",
        "source_ref": "Brand Guide v1.0 §V-04",
        "content": (
            "[DEMO GUIDELINE] "
            "Maintain a calm, credible, dermatological tone in all consumer copy. "
            "Never make absolute promises or claim miracle cures (such as '100% acne-free', "
            "'miracle cure', 'completely eliminates', or 'permanent results'). "
            "All copy must use measured, realistic efficacy language."
        ),
    },
    {
        "product_id_index": 3,          # Monsoon Clear Skin Face Wash
        "title": "[T-12] Clear Skin Ingredient Terminology Policy",
        "source_ref": "Terminology Standard §T-12",
        "content": (
            "[DEMO GUIDELINE] "
            "Never use the medical abbreviation 'BPO' in consumer-facing copy. "
            "Always spell out 'Benzoyl Peroxide' in full to ensure consumer clarity and safety."
        ),
    },
    {
        "product_id_index": 3,          # Monsoon Clear Skin Face Wash
        "title": "[F-112] Clinical Efficacy & Results Timeline Policy",
        "source_ref": "Clinical Study Summary F-112",
        "content": (
            "[DEMO GUIDELINE] "
            "Approved clinical trial evidence supports a four-week (28-day) efficacy statement. "
            "Claims promising short-term results (such as 'in 3 days' or 'overnight') are unapproved and prohibited. "
            "All timeline claims must reflect the verified four-week clinical timeframe."
        ),
    },
    {
        "product_id_index": 3,
        "title": "[D-02] Monsoon Offer Date Policy",
        "source_ref": "Fictional Offer Register D-02",
        "content": (
            "[DEMO GUIDELINE] Offer dates must match the approved fictional "
            "Monsoon offer window recorded for this demonstration."
        ),
    },
]

BRAND_RULES = [
    # Rule 0 — banned terms (term_list)
    {
        "product_id_index": 0,
        "guideline_id_index": 0,
        "name": "Banned Whitening/Bleaching Terminology",
        "description": "Flag any use of banned skin-tone-alteration terms.",
        "rule_type": "term_list",
        "scope": json.dumps({"channel": ["all"], "market": ["IN", "US", "GB"]}),
        "parameters": json.dumps({
            "mode": "banned",
            "terms": ["whitening", "lightening", "skin bleaching", "depigmenting", "bleach"],
            "case_sensitive": False,
        }),
        "severity": "HIGH",
        "positive_tests": json.dumps(["This serum supports collagen production."]),
        "negative_tests": json.dumps(["This cream has a whitening effect."]),
        "is_active": 1,
        "owner_approved": 1,
    },
    # Rule 1 — required disclaimer (required_text)
    {
        "product_id_index": 0,
        "guideline_id_index": 0,
        "name": "Clinical Study Disclaimer Required",
        "description": "Any content making an efficacy claim must include a study citation.",
        "rule_type": "required_text",
        "scope": json.dumps({"channel": ["all"], "market": ["IN", "US"]}),
        "parameters": json.dumps({
            "pattern": r"(?i)(study|clinical|tested|proven|BRL-\d{4}-\d{2})",
            "require_match": True,
            "trigger_on": r"(?i)(proven|clinically|shown to|study shows|evidence)",
        }),
        "severity": "HIGH",
        "positive_tests": json.dumps(["Clinically tested (BRL-2024-01): 95% saw improvement."]),
        "negative_tests": json.dumps(["Clinically proven to reduce dark spots."]),
        "is_active": 1,
        "owner_approved": 1,
    },
    # Rule 2 — numeric range on SPF (numeric_range)
    {
        "product_id_index": 1,
        "guideline_id_index": 1,
        "name": "SPF Value Must Be Exactly 50",
        "description": "SPF stated in copy must match approved value of 50.",
        "rule_type": "numeric_range",
        "scope": json.dumps({"channel": ["all"], "market": ["IN", "US"]}),
        "parameters": json.dumps({
            "extract_pattern": r"(?i)SPF\s*(\d+)",
            "min": 50,
            "max": 50,
        }),
        "severity": "CRITICAL",
        "positive_tests": json.dumps(["Provides SPF50 protection every day."]),
        "negative_tests": json.dumps(["Use with SPF30 for best results."]),
        "is_active": 1,
        "owner_approved": 1,
    },
    # Rule 3 — banned word 'cure' (term_list)
    {
        "product_id_index": None,
        "guideline_id_index": 0,
        "name": "No Cure/Treat Claims",
        "description": "The words 'cure', 'treat', 'treatment' in a medical claim context are banned.",
        "rule_type": "term_list",
        "scope": json.dumps({"channel": ["all"], "market": ["IN", "US", "GB"]}),
        "parameters": json.dumps({
            "mode": "banned",
            "terms": ["cure", "cures", "treats acne", "acne treatment", "heals acne"],
            "case_sensitive": False,
        }),
        "severity": "CRITICAL",
        "positive_tests": json.dumps(["Helps reduce the appearance of blemishes."]),
        "negative_tests": json.dumps(["This product cures acne."]),
        "is_active": 1,
        "owner_approved": 1,
    },
    # Rule 4 — SPF must be followed by PA+++ in IN market (regex)
    {
        "product_id_index": 1,
        "guideline_id_index": 1,
        "name": "SPF must include PA+++ in India market",
        "description": "In IN market, every SPF mention must be accompanied by PA+++.",
        "rule_type": "regex",
        "scope": json.dumps({"channel": ["all"], "market": ["IN"]}),
        "parameters": json.dumps({
            "pattern": r"(?i)SPF\s*\d+\s*PA\+{3}",
            "require_match": True,
            "suggested_correction": "SPF mentions in India market must include PA+++ rating (e.g. SPF50 PA+++)",
        }),
        "severity": "HIGH",
        "positive_tests": json.dumps(["Lightweight SPF50 PA+++ moisturiser."]),
        "negative_tests": json.dumps(["Apply SPF50 moisturiser daily."]),
        "is_active": 1,
        "owner_approved": 1,
    },
    # Rule 5 — Percentage precision (regex)
    {
        "product_id_index": 0,
        "guideline_id_index": 0,
        "name": "Percentage figures max one decimal place",
        "description": "Percentage values must have zero or one decimal place only.",
        "rule_type": "regex",
        "scope": json.dumps({"channel": ["all"], "market": ["IN", "US", "GB"]}),
        "parameters": json.dumps({
            "pattern": r"\d+\.\d{2,}%",
            "require_match": False,  # match = violation
        }),
        "severity": "MEDIUM",
        "positive_tests": json.dumps(["95% of users saw improvement."]),
        "negative_tests": json.dumps(["93.456% saw brighter skin."]),
        "is_active": 1,
        "owner_approved": 1,
    },
    # Rule 6 — Unsupported superlatives (term_list) [GlowUp guideline §6]
    {
        "product_id_index": 0,          # GlowUp Vitamin-C Serum
        "guideline_id_index": 0,
        "name": "Unsupported Superlatives Prohibited",
        "description": "Flag unsubstantiated superlative claims (e.g. best, greatest, most powerful, #1) per brand guidelines.",
        "rule_type": "term_list",
        "scope": json.dumps({"channel": ["all"], "market": ["IN", "US", "GB"]}),
        "parameters": json.dumps({
            "mode": "banned",
            "terms": ["best", "greatest", "most powerful", "number 1", "#1", "world's best"],
            "case_sensitive": False,
            "suggested_correction": "high-efficacy brightening serum",
        }),
        "severity": "HIGH",
        "positive_tests": json.dumps(["Clinically tested brightening serum with 15% Vitamin C."]),
        "negative_tests": json.dumps(["The best Vitamin C serum for glowing skin."]),
        "is_active": 1,
        "owner_approved": 1,
    },
    # Rule 7 — V-04 Flag absolute promises (term_list) [Monsoon Clear Skin]
    {
        "product_id_index": 3,          # Monsoon Clear Skin Face Wash
        "guideline_id_index": 3,
        "name": "V-04: Flag Absolute Promises",
        "description": "Flag absolute promises or miracle claims in consumer copy per V-04 calm, credible tone guidance.",
        "rule_type": "term_list",
        "scope": json.dumps({"channel": ["all"], "market": ["IN", "US", "GB"]}),
        "parameters": json.dumps({
            "mode": "banned",
            "terms": [
                "100% acne-free",
                "miracle cure",
                "completely eliminates",
                "permanent results",
                "permanently clears",
                "instant cure",
                "miracle face wash",
                "acne-free forever",
                "acne forever",
                "instant results",
                "guaranteed results",
                "guarantees acne-free",
                "guaranteed acne-free",
                "guarantees clear skin",
                "guaranteed clear skin",
            ],
            "case_sensitive": False,
            "suggested_correction": "Helps clarify and reduce blemishes over time with daily use",
            "explanation": "Absolute promises violate V-04 credible tone guidelines; claims must reflect clinically observed clarifying results.",
        }),
        "severity": "HIGH",
        "positive_tests": json.dumps(["Helps clarify blemish-prone skin over time with daily use."]),
        "negative_tests": json.dumps([
            "Guaranteed 100% acne-free skin in days with this miracle cure.",
            "Say goodbye to acne forever!",
        ]),
        "is_active": 1,
        "owner_approved": 1,
    },
    # Rule 8 — T-12 Prohibit BPO abbreviation (regex) [Monsoon Clear Skin]
    {
        "product_id_index": 3,          # Monsoon Clear Skin Face Wash
        "guideline_id_index": 4,
        "name": "T-12: Prohibit BPO Abbreviation",
        "description": "Prohibit the abbreviation 'BPO' in consumer copy; suggest 'Benzoyl Peroxide'.",
        "rule_type": "regex",
        "scope": json.dumps({"channel": ["all"], "market": ["IN", "US", "GB"]}),
        "parameters": json.dumps({
            "pattern": r"(?i)\bBPO\b",
            "require_match": False,  # match = violation
            "suggested_correction": "Benzoyl Peroxide",
        }),
        "severity": "HIGH",
        "positive_tests": json.dumps(["Formulated with 2.5% Benzoyl Peroxide to gently clarify skin."]),
        "negative_tests": json.dumps(["Contains 2.5% BPO to eliminate acne."]),
        "is_active": 1,
        "owner_approved": 1,
    },
    # Rule 9 — F-112 Four-week timeline required (regex) [Monsoon Clear Skin]
    {
        "product_id_index": 3,          # Monsoon Clear Skin Face Wash
        "guideline_id_index": 5,
        "name": "F-112: Substantiated 4-Week Timeline Required",
        "description": "Flag short-timeline promises (e.g. 3 days, overnight); approved wording supports a 4-week statement.",
        "rule_type": "regex",
        "scope": json.dumps({"channel": ["all"], "market": ["IN", "US", "GB"]}),
        "parameters": json.dumps({
            "pattern": (
                r"(?i)\b(?:\d+|one|two|three|four|five|few)\s*-?\s*"
                r"(?:days?|nights?|hours?)\b|\b(?:overnight|instant results)\b"
            ),
            "require_match": False,  # match = violation
            "suggested_correction": "clinically shown to reduce blemishes in 4 weeks",
        }),
        "severity": "HIGH",
        "positive_tests": json.dumps(["Clinically proven to improve skin clarity in 4 weeks."]),
        "negative_tests": json.dumps(["See clearer skin in 3 days."]),
        "is_active": 1,
        "owner_approved": 1,
    },
    # Rule 10 — Monsoon Unsupported Superlatives Prohibited (term_list) [Monsoon Clear Skin]
    {
        "product_id_index": 3,          # Monsoon Clear Skin Face Wash
        "guideline_id_index": 3,        # Monsoon Tone & Positioning
        "name": "Monsoon: Unsupported Superlatives Prohibited",
        "description": "Flag unsubstantiated superlative claims (e.g. best, greatest, most powerful, number one, #1, world's best) in Monsoon copy. Superlatives require evidentiary substantiation and human review.",
        "rule_type": "term_list",
        "scope": json.dumps({"channel": ["all"], "market": ["IN", "US", "GB"]}),
        "parameters": json.dumps({
            "mode": "banned",
            "terms": ["best", "greatest", "most powerful", "number one", "number 1", "#1", "world's best"],
            "case_sensitive": False,
            "suggested_correction": "effective clarifying face wash",
            "explanation": "Lexical match flags an unverified superlative. Superlatives require evidentiary substantiation and human review, and should not be presented as definitive proof of a regulatory violation.",
        }),
        "severity": "HIGH",
        "positive_tests": json.dumps(["Formulated to clarify skin and reduce blemishes."]),
        "negative_tests": json.dumps(["It's the best solution for everyone."]),
        "is_active": 1,
        "owner_approved": 1,
    },
    # Rule 11 — D-02 Monsoon offer date window
    {
        "product_id_index": 3,
        "guideline_id_index": 6,
        "name": "D-02: Monsoon Offer Date Window",
        "description": "Check that a fictional Monsoon offer date falls within the approved demo date window.",
        "rule_type": "date_window",
        "scope": json.dumps({"channel": ["all"], "market": ["IN", "US", "GB"]}),
        "parameters": json.dumps({
            "extract_pattern": r"(?i)\boffer\s+valid through\s+(\d{4}-\d{2}-\d{2})",
            "date_format": "%Y-%m-%d",
            "earliest": "2099-01-01",
            "latest": "2099-12-31",
            "suggested_correction": "Use the approved fictional Monsoon offer date.",
        }),
        "severity": "MEDIUM",
        "positive_tests": json.dumps([
            "Monsoon offer valid through 2099-12-31."
        ]),
        "negative_tests": json.dumps([
            "Monsoon offer valid through 2000-01-01."
        ]),
        "is_active": 1,
        "owner_approved": 1,
    },
]

FACTS = [
    # Fact 0 — Vitamin C concentration
    {
        "product_id_index": 0,
        "name": "Vitamin C Concentration",
        "category": "ingredient",
        "markets": json.dumps(["IN", "US", "GB"]),
    },
    # Fact 1 — clinical study result
    {
        "product_id_index": 0,
        "name": "Brightening Efficacy Study Result",
        "category": "efficacy_claim",
        "markets": json.dumps(["IN", "US"]),
    },
    # Fact 2 — SPF rating
    {
        "product_id_index": 1,
        "name": "SPF Rating",
        "category": "product_specification",
        "markets": json.dumps(["IN", "US"]),
    },
    # Fact 3 — dermatologist tested stat
    {
        "product_id_index": 0,
        "name": "Dermatologist Tested Statistic",
        "category": "efficacy_claim",
        "markets": json.dumps(["IN", "US", "GB"]),
    },
    # Fact 4 — expiry test fact
    {
        "product_id_index": 2,
        "name": "Activated Charcoal Pore Reduction Claim",
        "category": "efficacy_claim",
        "markets": json.dumps(["IN", "GB"]),
    },
    # Fact 5 — F-112 Monsoon Clear Skin trial timeline
    {
        "product_id_index": 3,
        "name": "F-112: Clear Skin Clinical Trial Timeline",
        "category": "efficacy_claim",
        "markets": json.dumps(["IN", "US", "GB"]),
    },
    {
        "product_id_index": 3,
        "name": "[DEMO] Monsoon Expired Offer Date",
        "category": "promotion",
        "markets": json.dumps(["IN", "US", "GB"]),
    },
    {
        "product_id_index": 3,
        "name": "[DEMO] Monsoon Active Offer Date",
        "category": "promotion",
        "markets": json.dumps(["IN", "US", "GB"]),
    },
]

FACT_VERSIONS = [
    {
        "fact_index": 0,
        "version_number": 1,
        "statement": "GlowUp Vitamin-C Serum contains 15% stabilised L-Ascorbic Acid (Vitamin C).",
        "evidence_ref": "Product Specification Sheet GU-VC-30 Rev. 4",
        "approval_status": "APPROVED",
        "approved_by": "demo_fact_owner",
    },
    {
        "fact_index": 1,
        "version_number": 1,
        "statement": (
            "In a double-blind consumer study (BRL-2024-01, n=120, 8 weeks), "
            "93% of participants reported visibly brighter skin."
        ),
        "evidence_ref": "Clinical Study BRL-2024-01",
        "approval_status": "APPROVED",
        "approved_by": "demo_fact_owner",
    },
    {
        "fact_index": 1,
        "version_number": 2,
        "statement": (
            "In a double-blind consumer study (BRL-2024-01, n=200, 8 weeks), "
            "95% of participants reported visibly brighter skin."
        ),
        "evidence_ref": "Clinical Study BRL-2024-01 (updated cohort)",
        "approval_status": "APPROVED",
        "approved_by": "demo_fact_owner",
    },
    {
        "fact_index": 2,
        "version_number": 1,
        "statement": "PureBalance moisturiser is rated SPF50 PA+++ as tested per ISO 24444:2010.",
        "evidence_ref": "SPF Test Report PB-SPF-2024",
        "approval_status": "APPROVED",
        "approved_by": "demo_fact_owner",
    },
    {
        "fact_index": 3,
        "version_number": 1,
        "statement": "GlowUp Vitamin-C Serum is dermatologist-tested; 9 out of 10 dermatologists recommend it.",
        "evidence_ref": "Dermatologist Survey DS-2023-07",
        "approval_status": "APPROVED",
        "approved_by": "demo_fact_owner",
    },
    {
        "fact_index": 4,
        "version_number": 1,
        "statement": (
            "MintFresh Charcoal Cleanser reduces the visible appearance of pores by up to 30% "
            "after 4 weeks of daily use (consumer perception study, n=80)."
        ),
        "evidence_ref": "Consumer Study MF-2023-11",
        "approval_status": "EXPIRED",
        "approved_by": "demo_fact_owner",
    },
    {
        "fact_index": 5,
        "version_number": 1,
        "statement": (
            "[DEMO] Monsoon Clear Skin Face Wash is clinically shown to reduce blemishes "
            "and excess sebum in 4 weeks of twice-daily use."
        ),
        "evidence_ref": "Clinical Study MS-2024-F112 (n=150, 4 weeks)",
        "approval_status": "APPROVED",
        "approved_by": "demo_fact_owner",
    },
    {
        "fact_index": 6,
        "version_number": 1,
        "statement": (
            "[DEMO] Fictional Monsoon offer expired on 2000-01-01; "
            "this date is fixed for deterministic test coverage."
        ),
        "evidence_ref": "Fictional Offer Register D-02 — expired example",
        "approval_status": "APPROVED",
        "approved_by": "demo_fact_owner",
    },
    {
        "fact_index": 7,
        "version_number": 1,
        "statement": (
            "[DEMO] Fictional Monsoon offer is active through 2099-12-31; "
            "this date is fixed for deterministic test coverage."
        ),
        "evidence_ref": "Fictional Offer Register D-02 — active example",
        "approval_status": "APPROVED",
        "approved_by": "demo_fact_owner",
    },
]

CAMPAIGN_BRIEFS = [
    {
        "product_id_index": 0,
        "name": "GlowUp Summer Radiance Campaign",
        "objectives": "Drive awareness of Vitamin-C Serum brightening benefits for summer skincare routines.",
        "tone_guidance": "Uplifting, science-backed, inclusive. Avoid clinical coldness.",
        "target_audience": "Women and men aged 22–38, urban India and US.",
        "key_messages": json.dumps([
            "Clinically proven brightening with 15% Vitamin C.",
            "Visibly brighter skin in 4 weeks.",
            "Suitable for all skin types.",
        ]),
        "markets": json.dumps(["IN", "US"]),
        "channels": json.dumps(["instagram", "email", "website"]),
    },
    {
        "product_id_index": 1,
        "name": "PureBalance SPF Launch — India",
        "objectives": "Establish PureBalance SPF50 as the go-to daily moisturiser with sun protection.",
        "tone_guidance": "Confident, authoritative, dermatology-informed.",
        "target_audience": "Adults 25–45, India metro cities.",
        "key_messages": json.dumps([
            "SPF50 PA+++ protection in a lightweight moisturiser.",
            "No white cast. Suitable for Indian skin tones.",
            "Dermatologist recommended.",
        ]),
        "markets": json.dumps(["IN"]),
        "channels": json.dumps(["website", "print_ad"]),
    },
    {
        "product_id_index": 3,
        "name": "Monsoon Clear Skin Launch Campaign",
        "objectives": "Launch Monsoon Clear Skin Face Wash with calm, credible, dermatologist-backed messaging.",
        "tone_guidance": "Calm, credible, reassuring, dermatology-informed. Avoid absolute promises or miracle cures.",
        "target_audience": "Adults 18–35 with oily or blemish-prone skin in India, US, and UK.",
        "key_messages": json.dumps([
            "Clinically shown to clarify skin and reduce blemishes in 4 weeks.",
            "Formulated with Benzoyl Peroxide for gentle everyday clarity.",
            "Dermatologist tested, non-stripping cleanser.",
        ]),
        "markets": json.dumps(["IN", "US", "GB"]),
        "channels": json.dumps(["instagram", "website", "email"]),
    },
]


# ---------------------------------------------------------------------------
# Seed function & Targeted idempotent migrations
# ---------------------------------------------------------------------------

def ensure_monsoon_rules(conn) -> None:
    """
    Safely and idempotently ensure existing databases have:
    1. Updated V-04 absolute promise terms (including 'forever', 'acne-free forever', etc.)
    2. Monsoon-specific superlative governance rule ('Monsoon: Unsupported Superlatives Prohibited')
    Preserves all existing user records, claims, and other rules.
    """
    monsoon_product = next(
        (
            product
            for product in PRODUCTS
            if product["brand"] == "Monsoon Beauty Co."
            and product["name"] == "Monsoon Clear Skin Face Wash"
        ),
        None,
    )
    if monsoon_product is None:
        return

    product_row = conn.execute(
        "SELECT id FROM products WHERE name = ? AND brand = ? ORDER BY id LIMIT 1",
        (monsoon_product["name"], monsoon_product["brand"]),
    ).fetchone()
    if product_row is None:
        return
    monsoon_pid = product_row["id"]

    v04_guideline = conn.execute(
        """
        SELECT id FROM guidelines
        WHERE product_id = ? AND title = ?
          AND source_ref = 'Brand Guide v1.0 §V-04'
        ORDER BY id LIMIT 1
        """,
        (monsoon_pid, "[V-04] Monsoon Tone & Absolute Claims Policy"),
    ).fetchone()
    f112_guideline = conn.execute(
        """
        SELECT id FROM guidelines
        WHERE product_id = ? AND title = ?
          AND source_ref = 'Clinical Study Summary F-112'
        ORDER BY id LIMIT 1
        """,
        (monsoon_pid, "[F-112] Clinical Efficacy & Results Timeline Policy"),
    ).fetchone()

    # 1. Update only the seeded V-04 rule linked to the Monsoon guideline.
    v04_row = conn.execute(
        """
        SELECT id, parameters FROM brand_rules
        WHERE product_id = ? AND guideline_id = ?
          AND name = 'V-04: Flag Absolute Promises'
        ORDER BY id LIMIT 1
        """,
        (monsoon_pid, v04_guideline["id"]),
    ).fetchone() if v04_guideline else None
    if v04_row:
        try:
            params = json.loads(v04_row["parameters"]) if isinstance(v04_row["parameters"], str) else dict(v04_row["parameters"])
        except Exception:
            params = {}
        existing_terms = list(params.get("terms", []))
        scoped_terms = [
            term for term in existing_terms
            if not (isinstance(term, str) and term.casefold() == "forever")
        ]
        updated = len(scoped_terms) != len(existing_terms)
        existing_terms = scoped_terms
        existing_lower = {t.lower() for t in existing_terms}
        required_terms = [
            "100% acne-free",
            "miracle cure",
            "completely eliminates",
            "permanent results",
            "permanently clears",
            "instant cure",
            "miracle face wash",
            "acne-free forever",
            "acne forever",
            "instant results",
            "guaranteed results",
            "guarantees acne-free",
            "guaranteed acne-free",
            "guarantees clear skin",
            "guaranteed clear skin",
        ]
        for req in required_terms:
            if req.lower() not in existing_lower:
                existing_terms.append(req)
                existing_lower.add(req.lower())
                updated = True
        if updated:
            params["terms"] = existing_terms
            if "explanation" not in params:
                params["explanation"] = "Absolute promises violate V-04 credible tone guidelines; claims must reflect clinically observed clarifying results."
            conn.execute(
                "UPDATE brand_rules SET parameters = ? WHERE id = ?",
                (json.dumps(params), v04_row["id"]),
            )
            conn.commit()

    # 2. Update the existing F-112 rule without changing its product/market scope.
    f112_row = conn.execute(
        """
        SELECT id, parameters FROM brand_rules
        WHERE product_id = ? AND guideline_id = ?
          AND name = 'F-112: Substantiated 4-Week Timeline Required'
        ORDER BY id LIMIT 1
        """,
        (monsoon_pid, f112_guideline["id"]),
    ).fetchone() if f112_guideline else None
    if f112_row:
        try:
            params = (
                json.loads(f112_row["parameters"])
                if isinstance(f112_row["parameters"], str)
                else dict(f112_row["parameters"])
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            params = {}
        pattern = (
            r"(?i)\b(?:\d+|one|two|three|four|five|few)\s*-?\s*"
            r"(?:days?|nights?|hours?)\b|\b(?:overnight|instant results)\b"
        )
        if params.get("pattern") != pattern:
            params["pattern"] = pattern
            params["require_match"] = False
            params["suggested_correction"] = (
                "clinically shown to reduce blemishes in 4 weeks"
            )
            conn.execute(
                "UPDATE brand_rules SET parameters = ? WHERE id = ?",
                (json.dumps(params), f112_row["id"]),
            )
            conn.commit()

    # 3. Ensure the superlative rule is linked to the Monsoon V-04 guideline.
    monsoon_sup = conn.execute(
        """
        SELECT id FROM brand_rules
        WHERE product_id = ? AND guideline_id = ?
          AND name = 'Monsoon: Unsupported Superlatives Prohibited'
        ORDER BY id LIMIT 1
        """,
        (monsoon_pid, v04_guideline["id"]),
    ).fetchone() if v04_guideline else None

    if not monsoon_sup and v04_guideline:
        sup_params = {
            "mode": "banned",
            "terms": ["best", "greatest", "most powerful", "number one", "number 1", "#1", "world's best"],
            "case_sensitive": False,
            "suggested_correction": "effective clarifying face wash",
            "explanation": "Lexical match flags an unverified superlative. Superlatives require evidentiary substantiation and human review, and should not be presented as definitive proof of a regulatory violation.",
        }
        conn.execute(
            """
            INSERT INTO brand_rules (
                product_id, guideline_id, name, description, rule_type, scope,
                parameters, severity, positive_tests, negative_tests, is_active,
                owner_approved, review_status, readiness_class, checking_method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 'APPROVED', 'CODE_CHECKABLE', ?)
            """,
            (
                monsoon_pid,
                v04_guideline["id"],
                "Monsoon: Unsupported Superlatives Prohibited",
                "Flag unsubstantiated superlative claims (e.g. best, greatest, most powerful, number one, #1, world's best) in Monsoon copy. Superlatives require evidentiary substantiation and human review.",
                "term_list",
                json.dumps({"channel": ["all"], "market": ["IN", "US", "GB"]}),
                json.dumps(sup_params),
                "HIGH",
                json.dumps(["Formulated to clarify skin and reduce blemishes."]),
                json.dumps(["It's the best solution for everyone."]),
                "Deterministic demo rule linked to the approved fictional superlatives policy.",
            ),
        )
        conn.commit()


def _ensure_seed_rules_ready(conn) -> None:
    """Repair legacy seeded rules whose new readiness fields defaulted to blank."""
    names = [rule["name"] for rule in BRAND_RULES]
    names.append("Monsoon: Unsupported Superlatives Prohibited")
    for name in names:
        conn.execute(
            """
            UPDATE brand_rules
            SET review_status = CASE WHEN review_status = 'PROPOSED'
                                     THEN 'APPROVED' ELSE review_status END,
                readiness_class = 'CODE_CHECKABLE',
                checking_method = COALESCE(NULLIF(trim(checking_method), ''), ?)
            WHERE name = ? AND is_active = 1 AND owner_approved = 1
              AND review_status = 'PROPOSED'
            """,
            (
                "Deterministic seeded demo rule; linked source guideline is shown with findings.",
                name,
            ),
        )


def seed(force: bool = False, verbose: bool = True) -> None:
    apply_schema()

    with get_connection() as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        if not force and _already_seeded(conn):
            ensure_monsoon_rules(conn)
            _ensure_seed_rules_ready(conn)
            conn.commit()
            if verbose:
                print("[info] Demo database already seeded. Skipping re-seed.")
            return

        # 1. Ensure products exist (preserving existing IDs)
        for p in PRODUCTS:
            existing = conn.execute(
                "SELECT id FROM products WHERE name = ? AND brand = ?",
                (p["name"], p["brand"]),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO products (name, brand, sku_aliases, markets, description) "
                    "VALUES (:name,:brand,:sku_aliases,:markets,:description)",
                    p,
                )
        conn.commit()

        # Build name -> product_id map & index map based on PRODUCTS list
        product_rows = conn.execute("SELECT id, name FROM products").fetchall()
        prod_map = {r["name"]: r["id"] for r in product_rows}
        prod_id_by_index = {idx: prod_map.get(p["name"]) for idx, p in enumerate(PRODUCTS)}

        # 2. Ensure guidelines exist
        for g in GUIDELINES:
            pid = prod_id_by_index.get(g["product_id_index"])
            existing = conn.execute(
                "SELECT id FROM guidelines WHERE title = ? AND source_ref = ?",
                (g["title"], g["source_ref"]),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO guidelines (product_id, title, content, source_ref) "
                    "VALUES (?,?,?,?)",
                    (pid, g["title"], g["content"], g["source_ref"]),
                )
        conn.commit()

        # Build guideline title -> id map
        gl_rows = conn.execute("SELECT id, title FROM guidelines").fetchall()
        gl_map = {r["title"]: r["id"] for r in gl_rows}
        gl_id_by_index = {idx: gl_map.get(g["title"]) for idx, g in enumerate(GUIDELINES)}

        # 3. Ensure brand rules exist
        for r in BRAND_RULES:
            pid = prod_id_by_index.get(r["product_id_index"]) if r["product_id_index"] is not None else None
            gid = gl_id_by_index.get(r["guideline_id_index"])
            existing = conn.execute(
                "SELECT id FROM brand_rules WHERE name = ?",
                (r["name"],),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO brand_rules "
                    "(product_id, guideline_id, name, description, rule_type, scope, parameters, "
                    " severity, positive_tests, negative_tests, is_active, owner_approved, "
                    "review_status, readiness_class, checking_method) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'APPROVED','CODE_CHECKABLE',?)",
                    (
                        pid, gid, r["name"], r["description"], r["rule_type"],
                        r["scope"], r["parameters"], r["severity"],
                        r["positive_tests"], r["negative_tests"],
                        r["is_active"], r["owner_approved"],
                        "Deterministic seeded demo rule; linked source guideline is shown with findings.",
                    ),
                )
        conn.commit()

        # 4. Ensure facts exist
        for f in FACTS:
            pid = prod_id_by_index.get(f["product_id_index"])
            existing = conn.execute(
                "SELECT id FROM facts WHERE name = ? AND product_id = ?",
                (f["name"], pid),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO facts (product_id, name, category, markets) VALUES (?,?,?,?)",
                    (pid, f["name"], f["category"], f["markets"]),
                )
        conn.commit()

        # Build fact name -> id map
        fact_rows = conn.execute("SELECT id, name FROM facts").fetchall()
        fact_map = {r["name"]: r["id"] for r in fact_rows}
        fact_id_by_index = {idx: fact_map.get(f["name"]) for idx, f in enumerate(FACTS)}

        # 5. Ensure fact versions exist
        for fv in FACT_VERSIONS:
            fid = fact_id_by_index.get(fv["fact_index"])
            if fid is None:
                continue
            existing = conn.execute(
                "SELECT id FROM fact_versions WHERE fact_id = ? AND version_number = ?",
                (fid, fv["version_number"]),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO fact_versions "
                    "(fact_id, version_number, statement, evidence_ref, approval_status, approved_by, approved_at) "
                    "VALUES (?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
                    (
                        fid, fv["version_number"], fv["statement"],
                        fv["evidence_ref"], fv["approval_status"], fv["approved_by"],
                    ),
                )
        conn.commit()

        # 6. Ensure campaign briefs exist
        for cb in CAMPAIGN_BRIEFS:
            pid = prod_id_by_index.get(cb["product_id_index"])
            existing = conn.execute(
                "SELECT id FROM campaign_briefs WHERE name = ?",
                (cb["name"],),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO campaign_briefs "
                    "(product_id, name, objectives, tone_guidance, target_audience, "
                    " key_messages, markets, channels) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        pid, cb["name"], cb["objectives"], cb["tone_guidance"],
                        cb["target_audience"], cb["key_messages"],
                        cb["markets"], cb["channels"],
                    ),
                )
        conn.commit()

        # Targeted check for Monsoon governance rules
        ensure_monsoon_rules(conn)
        _ensure_seed_rules_ready(conn)
        conn.commit()

        total_p = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
        total_g = conn.execute("SELECT COUNT(*) AS n FROM guidelines").fetchone()["n"]
        total_r = conn.execute("SELECT COUNT(*) AS n FROM brand_rules").fetchone()["n"]
        total_f = conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"]
        total_fv = conn.execute("SELECT COUNT(*) AS n FROM fact_versions").fetchone()["n"]
        total_cb = conn.execute("SELECT COUNT(*) AS n FROM campaign_briefs").fetchone()["n"]

        if verbose:
            print("[ok] Demo database synced successfully.")
            print(f"   Products: {total_p}")
            print(f"   Guidelines: {total_g}")
            print(f"   Brand rules: {total_r}")
            print(f"   Facts: {total_f} (versions: {total_fv})")
            print(f"   Campaign briefs: {total_cb}")


if __name__ == "__main__":
    seed(force=False, verbose=True)
