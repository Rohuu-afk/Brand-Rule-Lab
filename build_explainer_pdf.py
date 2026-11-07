"""
Generate an exhaustive, visually stunning, UI-matching Product Master Guide & Walkthrough PDF
for Brand Rule Lab. Explains every single page, feature, check, role, workflow, and technical detail.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import fitz


def get_html_content() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
@page {
    size: A4 portrait;
    margin: 22pt 28pt 22pt 28pt;
}

* {
    box-sizing: border-box;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: #20383B;
    background-color: #F4F8F7;
    margin: 0;
    padding: 0;
    font-size: 8pt;
    line-height: 1.32;
}

.page {
    page-break-after: always;
    break-after: page;
    position: relative;
    padding-bottom: 14pt;
}

.page:last-child {
    page-break-after: avoid;
    break-after: avoid;
}

/* Header Banner */
.hero {
    background: linear-gradient(135deg, #155B61 0%, #1D7077 50%, #2A838B 100%);
    color: #FFFFFF;
    border-radius: 11pt;
    padding: 14pt 18pt 12pt;
    margin-bottom: 10pt;
    box-shadow: 0 4pt 12pt rgba(21, 91, 97, 0.16);
}

.hero-tag {
    display: inline-block;
    background: rgba(255, 255, 255, 0.2);
    border: 0.5pt solid rgba(255, 255, 255, 0.4);
    color: #E6F7F6;
    font-size: 6.8pt;
    font-weight: 600;
    letter-spacing: 0.8pt;
    text-transform: uppercase;
    padding: 1.5pt 7pt;
    border-radius: 20pt;
    margin-bottom: 4pt;
}

.hero-title {
    font-size: 20pt;
    font-weight: 750;
    line-height: 1.15;
    margin: 0 0 2pt;
    letter-spacing: -0.3pt;
}

.hero-subtitle {
    font-size: 10pt;
    font-style: italic;
    color: #C3E9E5;
    margin: 0 0 6pt;
}

.hero-desc {
    font-size: 8pt;
    color: #E7F6F5;
    line-height: 1.35;
    margin: 0 0 8pt;
    max-width: 98%;
}

.hero-meta {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-top: 0.5pt solid rgba(255, 255, 255, 0.25);
    padding-top: 6pt;
    font-size: 7.2pt;
    color: #D3EFEA;
}

/* Page Section Headers */
.section-header {
    background: #E8F3F1;
    border-left: 3.5pt solid #155B61;
    border-radius: 0 6pt 6pt 0;
    padding: 5pt 10pt;
    margin-bottom: 9pt;
}

.section-title {
    font-size: 11pt;
    font-weight: 750;
    color: #155B61;
    margin: 0;
}

.section-subtitle {
    font-size: 7.2pt;
    color: #4C6F72;
    margin: 1pt 0 0;
}

/* Pipeline Flow */
.pipeline-bar {
    display: flex;
    justify-content: space-between;
    background: #FFFFFF;
    border: 1pt solid #D6E7E5;
    border-radius: 8pt;
    padding: 6pt 10pt;
    margin-bottom: 10pt;
    box-shadow: 0 2pt 5pt rgba(21, 91, 97, 0.04);
}

.pipe-step {
    display: flex;
    align-items: center;
    font-size: 7.4pt;
    font-weight: 700;
    color: #155B61;
    letter-spacing: 0.3pt;
}

.pipe-step span.num {
    display: inline-block;
    width: 13pt;
    height: 13pt;
    background: #E4F3F1;
    color: #155B61;
    border-radius: 50%;
    text-align: center;
    line-height: 13pt;
    font-size: 6.8pt;
    font-weight: 800;
    margin-right: 4pt;
}

.pipe-arrow {
    color: #9EC6C3;
    font-size: 8pt;
    align-self: center;
}

/* Grid & Cards */
.grid-2 {
    display: flex;
    gap: 9pt;
    margin-bottom: 9pt;
}

.grid-3 {
    display: flex;
    gap: 7pt;
    margin-bottom: 9pt;
}

.col-1 { flex: 1; }
.col-2 { flex: 2; }

.card {
    background: #FFFFFF;
    border: 1pt solid #D9EAE7;
    border-radius: 8pt;
    padding: 8pt 10pt;
    box-shadow: 0 2pt 5pt rgba(21, 91, 97, 0.04);
}

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 5pt;
    border-bottom: 0.75pt solid #EAF2F1;
    padding-bottom: 3pt;
}

.card-title {
    font-size: 9.5pt;
    font-weight: 700;
    color: #155B61;
    margin: 0;
}

/* Badges & Status */
.badge {
    display: inline-block;
    padding: 1pt 4.5pt;
    border-radius: 3pt;
    font-size: 6.5pt;
    font-weight: 700;
    letter-spacing: 0.3pt;
    text-transform: uppercase;
}

.badge-pass { background: #DDF2E6; color: #236B4D; border: 0.5pt solid #BCE5CE; }
.badge-fail { background: #FDE8E5; color: #9A2C23; border: 0.5pt solid #F8CCC6; }
.badge-warn { background: #FEF4DC; color: #8A5F0B; border: 0.5pt solid #FBE6B3; }
.badge-info { background: #E1F2F4; color: #155B61; border: 0.5pt solid #C6E6E9; }

/* Tables */
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 7.4pt;
    margin-top: 2pt;
}

th {
    background: #EBF4F3;
    color: #155B61;
    font-weight: 700;
    text-align: left;
    padding: 4pt 5.5pt;
    border-bottom: 1pt solid #D2E5E3;
}

td {
    padding: 4pt 5.5pt;
    border-bottom: 0.5pt solid #EDF4F3;
    vertical-align: top;
}

tr:last-child td {
    border-bottom: none;
}

/* Highlights & Code */
code {
    background: #EAF3F2;
    color: #105055;
    padding: 1pt 2.5pt;
    border-radius: 2.5pt;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace;
    font-size: 7.2pt;
}

.highlight-box {
    background: #F0F7F6;
    border-left: 2.5pt solid #155B61;
    border-radius: 0 4pt 4pt 0;
    padding: 5pt 7pt;
    margin: 5pt 0;
    font-size: 7.5pt;
}

.stat-pill {
    display: flex;
    flex-direction: column;
    background: #F7FBFA;
    border: 1pt solid #DFECEB;
    border-radius: 5pt;
    padding: 4pt 6pt;
    text-align: center;
}

.stat-num {
    font-size: 13pt;
    font-weight: 800;
    color: #155B61;
    line-height: 1.1;
}

.stat-label {
    font-size: 6.2pt;
    font-weight: 600;
    color: #4C6F72;
    text-transform: uppercase;
}

.footer-bar {
    margin-top: 8pt;
    border-top: 0.75pt solid #D6E7E5;
    padding-top: 4pt;
    display: flex;
    justify-content: space-between;
    font-size: 6.8pt;
    color: #6C8D90;
}
</style>
</head>
<body>

<!-- ========================================================== -->
<!-- PAGE 1: TITLE, EXECUTIVE SUMMARY & THE CLAIM LEDGER WEDGE -->
<!-- ========================================================== -->
<div class="page">

    <div class="hero">
        <div class="hero-tag">Inkloom AI Hackathon • CodeNexus India 2026 • Round 2: The Build</div>
        <h1 class="hero-title">Brand Rule Lab — Master Product Guide</h1>
        <div class="hero-subtitle">Comprehensive feature walkthrough, UI manual & architectural specification</div>
        <div class="hero-desc">
            Brand Rule Lab is an intelligent pre-publication brand governance platform designed for consumer-facing brands. It pairs deterministic Python rules with NVIDIA NIM claim extraction and fact ranking, Google Gemini fallback, and contextual tone analysis, backed by an immutable fact-version ledger and an append-only audit trail.
        </div>
        <div class="hero-meta">
            <span><strong>Team:</strong> rohitlongesha5 (Rohit Longesha)</span>
            <span><strong>Focus Domain:</strong> India D2C Personal Care & Beauty</span>
            <span><strong>Verified Status:</strong> 216 Tests Passed (100% Green) • Live NVIDIA NIM & Gemini AI</span>
        </div>
    </div>

    <!-- PIPELINE STEPS -->
    <div class="pipeline-bar">
        <div class="pipe-step"><span class="num">1</span> DRAFT</div>
        <div class="pipe-arrow">➔</div>
        <div class="pipe-step"><span class="num">2</span> CHECK</div>
        <div class="pipe-arrow">➔</div>
        <div class="pipe-step"><span class="num">3</span> SUGGEST</div>
        <div class="pipe-arrow">➔</div>
        <div class="pipe-step"><span class="num">4</span> APPROVE</div>
        <div class="pipe-arrow">➔</div>
        <div class="pipe-step"><span class="num">5</span> LEDGER</div>
        <div class="pipe-arrow">➔</div>
        <div class="pipe-step"><span class="num">6</span> MONITOR</div>
    </div>

    <!-- GRID 1: THE PROBLEM & OUR WEDGE -->
    <div class="grid-2">
        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">The Real Problem (ASCI & Peer Data)</h2>
                <span class="badge badge-fail">Regulatory Risk</span>
            </div>
            <p style="margin-top: 0; font-size: 7.7pt;">In high-growth consumer marketing, brands publish rapidly across agencies and generative AI tools. Unsubstantiated claims and off-brand visuals lead to severe scrutiny:</p>
            <div class="grid-3" style="margin-bottom: 5pt;">
                <div class="stat-pill">
                    <span class="stat-num">90%</span>
                    <span class="stat-label">ASCI cases needed edits</span>
                </div>
                <div class="stat-pill">
                    <span class="stat-num">82%</span>
                    <span class="stat-label">Scrutinised in digital ads</span>
                </div>
                <div class="stat-pill">
                    <span class="stat-num">1–4 d</span>
                    <span class="stat-label">Peer approval delay</span>
                </div>
            </div>
            <div class="highlight-box">
                <em>"Annoying part design ka approval ni milta... senior deny krdete late factual edit pe."</em> — Primary peer research.
            </div>
            <p style="font-size: 7.5pt; color: #39585B; margin: 0;">
                Subjective feedback ("make it pop"), late factual edits, and unrecorded verbal preferences cause massive rework cycles.
            </p>
        </div>

        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">Our Innovation: The Claim-to-Fact Ledger</h2>
                <span class="badge badge-pass">Core Wedge</span>
            </div>
            <p style="margin-top: 0; font-size: 7.7pt;">Conventional tools offer subjective chatbot reviews without evidence lineage. <strong>Brand Rule Lab</strong> ties every approved claim to a verifiable evidence record:</p>
            <ul style="padding-left: 11pt; margin: 2pt 0 4pt; font-size: 7.5pt;">
                <li><strong>Stable Identifier (<code>stable_claim_id</code>):</strong> A deterministic 12-character hex ID minted upon approval.</li>
                <li><strong>Normalized SHA-256 Hash:</strong> Immutable fingerprint of the exact approved wording.</li>
                <li><strong>Fact-Version Lineage (<code>fact_version_id</code>):</strong> Bound to the exact approved fact version (e.g. Clinical Study F-112 v3).</li>
                <li><strong>Deterministic Drift Cascade:</strong> When evidence changes or expires, dependent claims are flagged in <strong>1 SQL update with 0 LLM calls</strong>.</li>
            </ul>
            <div class="highlight-box">
                <strong>Strict Operational Boundary:</strong> <code>UNLINKED</code>, <code>NOT_OBSERVED</code>, and <code>REVIEW_REQUIRED</code> remain separate states. A missing match is never quietly treated as compliant.
            </div>
        </div>
    </div>

    <!-- SYSTEM ARCHITECTURE DIAGRAM CARD -->
    <div class="card">
        <div class="card-header">
            <h2 class="card-title">Layered System Architecture</h2>
            <span class="badge badge-info">Zero Single Point of Failure</span>
        </div>
        <div class="grid-3" style="margin-bottom: 0;">
            <div style="background: #F6FBFA; border: 1pt solid #D6E7E5; border-radius: 5pt; padding: 6pt;">
                <div style="font-weight: 700; color: #155B61; font-size: 7.8pt; margin-bottom: 2pt;">1. Presentation Layer</div>
                <div style="font-size: 7.2pt; color: #39585B;">Streamlit 1.66.1 multi-role dashboard. Real-time sidebar role switcher (Creator, Reviewer, Fact Owner). Interactive highlighted text spans, side-by-side diff editor, and live governance metrics.</div>
            </div>
            <div style="background: #F6FBFA; border: 1pt solid #D6E7E5; border-radius: 5pt; padding: 6pt;">
                <div style="font-weight: 700; color: #155B61; font-size: 7.8pt; margin-bottom: 2pt;">2. Hybrid Enforcement Layer</div>
                <div style="font-size: 7.2pt; color: #39585B;"><strong>Python Rules:</strong> Deterministic evaluation of terms, regex, dates, ranges, contrast.<br><strong>AI Engine:</strong> NVIDIA NIM primary (Nemotron-3-Nano) with Gemini fallback for claim extraction and semantic ranking.</div>
            </div>
            <div style="background: #F6FBFA; border: 1pt solid #D6E7E5; border-radius: 5pt; padding: 6pt;">
                <div style="font-weight: 700; color: #155B61; font-size: 7.8pt; margin-bottom: 2pt;">3. Persistence & Ledger Layer</div>
                <div style="font-size: 7.2pt; color: #39585B;">SQLite 3 in WAL mode with strict foreign keys. Immutable <code>fact_versions</code>, append-only <code>approved_claims</code> snapshots, and tamper-evident <code>review_events</code> audit logs.</div>
            </div>
        </div>
    </div>

    <div class="footer-bar">
        <span>Brand Rule Lab • Product Master Guide & Architecture Manual • Team rohitlongesha5</span>
        <span>Page 1 of 5</span>
    </div>

</div>

<!-- ========================================================== -->
<!-- PAGE 2: FULL UI PAGE-BY-PAGE GUIDE (WHAT EVERY PAGE DOES) -->
<!-- ========================================================== -->
<div class="page">

    <div class="section-header">
        <h2 class="section-title">The Complete UI Walkthrough: What Every Page Does & How to Use It</h2>
        <div class="section-subtitle">A detailed breakdown of all 10 views accessible via the sidebar role switcher</div>
    </div>

    <table>
        <thead>
            <tr>
                <th style="width: 18%;">Page Name</th>
                <th style="width: 14%;">Allowed Roles</th>
                <th style="width: 38%;">What This Page Does</th>
                <th style="width: 30%;">How to Use It Step-by-Step</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><strong>1. Overview</strong></td>
                <td><span class="badge badge-info">All Roles</span></td>
                <td>First-use welcome hub, setup progress checklist, database health status, and quick-start scenario links.</td>
                <td>Select your demo role from the sidebar. Check the system readiness checklist and click into synthetic demo scenarios.</td>
            </tr>
            <tr>
                <td><strong>2. Brand Knowledge</strong></td>
                <td><span class="badge badge-info">Creator / Reviewer</span></td>
                <td>Onboards raw brand assets (PDF, DOCX, XLSX, TXT, images). Performs local text extraction and OCR. Extracts source-quoted facts & guidelines with explicit AI consent.</td>
                <td>Upload a brand manual or clinical study. Enable AI consent checkbox. Click <em>"Extract Proposals"</em>. Inspect extracted source quotes and page numbers.</td>
            </tr>
            <tr>
                <td><strong>3. Guidelines & Rule Readiness</strong></td>
                <td><span class="badge badge-warn">Reviewer Only</span></td>
                <td>Audits guideline proposals into 4 readiness classes (`CODE_CHECKABLE`, `AI_ASSISTED`, `HUMAN_JUDGEMENT`, `UNCLEAR`). Compiles predicates and runs automated self-tests.</td>
                <td>Review pending proposals. Assign readiness class. For `CODE_CHECKABLE`, pick predicate (`term_list`, etc.), run positive/negative tests, and click <em>"Approve Rule"</em>.</td>
            </tr>
            <tr>
                <td><strong>4. Facts & Claims</strong></td>
                <td><span class="badge badge-pass">Fact Owner / Reviewer</span></td>
                <td>Central registry of approved product facts. Shows statement, evidence reference, valid date window, market scope, and version history.</td>
                <td>Filter by product. Review fact versions. Click <em>"New Fact Version"</em> to update clinical evidence, or expire outdated facts to trigger drift.</td>
            </tr>
            <tr>
                <td><strong>5. Assets</strong></td>
                <td><span class="badge badge-info">Creator / Fact Owner</span></td>
                <td>Registers and manages approved product images, logos, and packaging renders. Computes exact SHA-256 and pHash fingerprints.</td>
                <td>Upload approved image assets. View SHA-256 identity hashes. Set approval status so drafts can be verified against canonical creative.</td>
            </tr>
            <tr>
                <td><strong>6. New Review</strong></td>
                <td><span class="badge badge-pass">Creator Only</span></td>
                <td>Draft intake console. Select product, target market (e.g. IN), channel (e.g. instagram), and optional campaign brief. Accepts pasted copy or document upload.</td>
                <td>Select "Monsoon Clear Skin Face Wash". Paste marketing copy or upload PDF/image. Toggle AI consent. Click <em>"Run Brand & Claim Review"</em>.</td>
            </tr>
            <tr>
                <td><strong>7. Review Results</strong></td>
                <td><span class="badge badge-info">Creator / Reviewer</span></td>
                <td>Displays cited findings (Tone V-04, Terminology T-12, Claim F-112, Contrast, Date). Highlights exact text spans. Side-by-side diff editor with one-click corrections.</td>
                <td>Inspect red/amber violation tags. Click <em>"Apply Suggestion"</em> to automatically replace off-brand phrasing. Save new draft version and submit for approval.</td>
            </tr>
            <tr>
                <td><strong>8. Approval Queue</strong></td>
                <td><span class="badge badge-warn">Reviewer Only</span></td>
                <td>Reviewer decision inbox. Lists submitted drafts awaiting sign-off. Shows blocking rule violations, unlinked claims, and reviewer action buttons.</td>
                <td>Open submitted draft. Review findings and fact linkages. Click <em>"Approve"</em> (mints stable claim ID), <em>"Request Changes"</em>, or <em>"Escalate"</em>.</td>
            </tr>
            <tr>
                <td><strong>9. Claim Ledger</strong></td>
                <td><span class="badge badge-pass">Fact Owner / Reviewer</span></td>
                <td>Immutable cryptographic ledger of all approved claims. Displays `stable_claim_id`, normalized text hash, linked `fact_version_id`, and real-time drift state.</td>
                <td>Inspect registered claims. Filter by status: `CURRENT`, `REVIEW_REQUIRED`, or `EXPIRED`. View full audit event history and reviewer signatures.</td>
            </tr>
            <tr>
                <td><strong>10. Insights & Analytics</strong></td>
                <td><span class="badge badge-info">All Roles</span></td>
                <td>Live governance analytics computed directly from the database. Shows rule failure frequencies, turnaround times, and audit activity. Zero fabricated stats.</td>
                <td>Analyze recurring brand mistakes (e.g., T-12 BPO infractions) over time to identify areas where brand guidelines or creator training need updating.</td>
            </tr>
        </tbody>
    </table>

    <div class="footer-bar">
        <span>Brand Rule Lab • Product Master Guide & Architecture Manual • Team rohitlongesha5</span>
        <span>Page 2 of 5</span>
    </div>

</div>

<!-- ========================================================== -->
<!-- PAGE 3: ENGINE ARCHITECTURE & ALL PREDICATE CHECKS        -->
<!-- ========================================================== -->
<div class="page">

    <div class="section-header">
        <h2 class="section-title">Engine Architecture: How Every Check Operates Under the Hood</h2>
        <div class="section-subtitle">Deep dive into deterministic predicates, claim extraction, evidence ranking, and tone evaluation</div>
    </div>

    <!-- GRID: DETERMINISTIC PREDICATES TABLE -->
    <div class="card" style="margin-bottom: 9pt;">
        <div class="card-header">
            <h2 class="card-title">1. The Deterministic Rule Engine (Pure Python)</h2>
            <span class="badge badge-pass">100% Reproducible</span>
        </div>
        <p style="margin-top: 0; font-size: 7.5pt; color: #20383B;">
            Pure Python predicates execute without calling external AI models. They provide microsecond-level pass/fail evaluation, guarantee zero hallucinations, and cannot be bypassed by prompt injection:
        </p>
        <table>
            <thead>
                <tr>
                    <th style="width: 18%;">Predicate Type</th>
                    <th style="width: 28%;">Parameter Configuration</th>
                    <th style="width: 27%;">Evaluation Mechanism</th>
                    <th style="width: 27%;">Production Use Case</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><code>term_list</code></td>
                    <td><code>banned_terms</code>, <code>allowed_terms</code>, <code>case_sensitive</code></td>
                    <td>Regex boundary search against prohibited words or acronyms.</td>
                    <td>Disallow "BPO" in consumer copy; require "Benzoyl Peroxide".</td>
                </tr>
                <tr>
                    <td><code>regex</code></td>
                    <td><code>pattern</code>, <code>must_match</code> (True/False)</td>
                    <td>Standardized regex evaluation with bounded execution timeouts.</td>
                    <td>Enforce mandatory registration numbers or disclaimer formatting.</td>
                </tr>
                <tr>
                    <td><code>numeric_range</code></td>
                    <td><code>field_name</code>, <code>min_val</code>, <code>max_val</code></td>
                    <td>Extracts numeric percentages or dosages and validates bounds.</td>
                    <td>Validate active ingredient concentrations (e.g. Salicylic Acid &le; 2.0%).</td>
                </tr>
                <tr>
                    <td><code>date_window</code></td>
                    <td><code>valid_from</code>, <code>expires_at</code></td>
                    <td>Compares published or scheduled dates against approved windows.</td>
                    <td>Verify festive promo offers do not outlive approved campaign dates.</td>
                </tr>
                <tr>
                    <td><code>required_text</code></td>
                    <td><code>substrings</code>, <code>all_required</code></td>
                    <td>Verifies exact presence of mandatory legal / statutory warnings.</td>
                    <td>Mandate "Patch test recommended" on exfoliating skincare acids.</td>
                </tr>
                <tr>
                    <td><code>wcag_contrast</code></td>
                    <td><code>fg_color</code>, <code>bg_color</code>, <code>min_ratio</code></td>
                    <td>Calculates relative luminance and contrast ratio per WCAG 2.1.</td>
                    <td>Flag text overlays on social graphics with contrast &lt; 4.5:1.</td>
                </tr>
                <tr>
                    <td><code>asset_hash</code></td>
                    <td><code>expected_sha256</code></td>
                    <td>Computes cryptographic SHA-256 hash of submitted creative file.</td>
                    <td>Verify social ad visual matches the exact approved product packshot.</td>
                </tr>
            </tbody>
        </table>
    </div>

    <!-- GRID: AI ENGINES -->
    <div class="grid-2">
        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">2. AI Claim Extraction Pipeline</h2>
                <span class="badge badge-info">Offset Preserved</span>
            </div>
            <p style="margin-top: 0; font-size: 7.4pt;">To prevent LLM hallucination of quotes or positions, extraction is strictly bounded:</p>
            <ol style="padding-left: 11pt; margin: 2pt 0; font-size: 7.2pt; color: #39585B;">
                <li><strong>Source Segmentation:</strong> Python segments the text into sentence and clause candidates with exact character start/end offsets.</li>
                <li><strong>Candidate Selection:</strong> External AI receives candidate IDs (e.g. <code>C0001</code>) and labels the claim type hint.</li>
                <li><strong>Deterministic Slicing:</strong> Python slices the draft using verified offsets. The model is physically incapable of inventing non-existent spans.</li>
            </ol>
        </div>

        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">3. Evidence Ranking & Tone Assessment</h2>
                <span class="badge badge-info">Schema Validated</span>
            </div>
            <p style="margin-top: 0; font-size: 7.4pt;">Once claims are extracted, evidence ranking matches approved facts:</p>
            <ul style="padding-left: 11pt; margin: 2pt 0; font-size: 7.2pt; color: #39585B;">
                <li><strong>SQL Scoped Filter:</strong> SQLite filters approved facts strictly by product ID and market. Claims never see facts from other brands.</li>
                <li><strong>Four Evidence States:</strong> AI ranks relationship into <code>SUPPORTS</code>, <code>CONTRADICTS</code>, <code>RELATED_NOT_EVIDENCE</code>, or <code>NO_MATCH</code>.</li>
                <li><strong>Tone & Brief:</strong> Advisory tone analysis inspects brand voice rules (V-04) and campaign briefs. AI suggestions require human approval.</li>
            </ul>
        </div>
    </div>

    <div class="footer-bar">
        <span>Brand Rule Lab • Product Master Guide & Architecture Manual • Team rohitlongesha5</span>
        <span>Page 3 of 5</span>
    </div>

</div>

<!-- ========================================================== -->
<!-- PAGE 4: RULE READINESS AUDIT & LIFE OF A CLAIM / FACT     -->
<!-- ========================================================== -->
<div class="page">

    <div class="section-header">
        <h2 class="section-title">Governance Lifecycle: Rule Readiness & The Life of a Claim</h2>
        <div class="section-subtitle">How human reviewers gate automated rules and how claims travel from draft to cryptographic ledger</div>
    </div>

    <!-- RULE READINESS AUDIT SYSTEM CARDS -->
    <div class="card" style="margin-bottom: 9pt;">
        <div class="card-header">
            <h2 class="card-title">The Four Rule Readiness Classes</h2>
            <span class="badge badge-info">Audit Gate</span>
        </div>
        <p style="margin-top: 0; font-size: 7.6pt; color: #20383B;">
            Extracted brand rules are never automatically enforced. A human reviewer audits each proposal against its source document quotation and assigns one of four strict classifications:
        </p>
        <div class="grid-2" style="margin-bottom: 5pt;">
            <div style="flex: 1; background: #F6FBFA; border: 1pt solid #D6E7E5; border-radius: 5pt; padding: 5pt 7pt;">
                <div style="font-weight: 700; color: #236B4D; font-size: 7.8pt; margin-bottom: 1.5pt;">1. CODE_CHECKABLE (Deterministic Active Rule)</div>
                <div style="font-size: 7.2pt; color: #39585B;">
                    Rule can be cleanly converted into a whitelisted Python predicate. Reviewer specifies parameters (e.g. banned term "BPO") and must run automated positive and negative self-tests before activation.
                </div>
            </div>
            <div style="flex: 1; background: #F6FBFA; border: 1pt solid #D6E7E5; border-radius: 5pt; padding: 5pt 7pt;">
                <div style="font-weight: 700; color: #155B61; font-size: 7.8pt; margin-bottom: 1.5pt;">2. AI_ASSISTED (Advisory Contextual Guidance)</div>
                <div style="font-size: 7.2pt; color: #39585B;">
                    Nuanced guidelines such as brand voice, storytelling style, and emotional resonance. The rule provides advisory suggestions during review but is never compiled into a blocking rule.
                </div>
            </div>
        </div>
        <div class="grid-2" style="margin-bottom: 0;">
            <div style="flex: 1; background: #F6FBFA; border: 1pt solid #D6E7E5; border-radius: 5pt; padding: 5pt 7pt;">
                <div style="font-weight: 700; color: #8A5F0B; font-size: 7.8pt; margin-bottom: 1.5pt;">3. HUMAN_JUDGEMENT (Manual Decision Gate)</div>
                <div style="font-size: 7.2pt; color: #39585B;">
                    Aesthetic balance, cultural appropriateness, or subjective brand fit. The system bypasses automated evaluation and routes the content directly to the reviewer queue.
                </div>
            </div>
            <div style="flex: 1; background: #F6FBFA; border: 1pt solid #D6E7E5; border-radius: 5pt; padding: 5pt 7pt;">
                <div style="font-weight: 700; color: #9A2C23; font-size: 7.8pt; margin-bottom: 1.5pt;">4. UNCLEAR (Clarification Required)</div>
                <div style="font-size: 7.2pt; color: #39585B;">
                    Contradictory or poorly phrased guidelines. Flagged as needing clarification; strictly blocked from enforcement until the brand owner documents a written resolution.
                </div>
            </div>
        </div>
    </div>

    <!-- LIFE OF A CLAIM & DRIFT CASCADE -->
    <div class="grid-2">
        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">The Life of an Approved Claim</h2>
                <span class="badge badge-pass">Ledger Traceability</span>
            </div>
            <ol style="padding-left: 11pt; margin: 2pt 0; font-size: 7.3pt; color: #39585B; line-height: 1.35;">
                <li><strong>Draft Submission:</strong> Creator drafts copy and submits for review.</li>
                <li><strong>Pre-Publication Check:</strong> Python predicates and AI extraction evaluate the copy.</li>
                <li><strong>Human Approval:</strong> Reviewer inspects findings and signs off. AI cannot approve.</li>
                <li><strong>Minting Stable ID:</strong> System generates a 12-char hex <code>stable_claim_id</code> and SHA-256 hash.</li>
                <li><strong>Snapshot Commitment:</strong> Stores approved wording, product ID, market, reviewer name, timestamp, and linked <code>fact_version_id</code>.</li>
            </ol>
        </div>

        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">Drift Propagation & Expiry Sweeps</h2>
                <span class="badge badge-warn">Zero-LLM Maintenance</span>
            </div>
            <div style="font-size: 7.3pt; color: #39585B; line-height: 1.35;">
                <p style="margin: 0 0 3pt;">When clinical facts evolve, the claim ledger updates automatically:</p>
                <ul style="padding-left: 11pt; margin: 0 0 4pt;">
                    <li><strong>Fact Version Bump:</strong> If Fact Owner updates Fact F-112 to version 4, all claims linked to versions &lt; 4 are flagged as <code>REVIEW_REQUIRED</code>.</li>
                    <li><strong>Fact Expiration:</strong> When a fact exceeds its <code>expires_at</code> date, an atomic sweep transitions dependent claims to <code>EXPIRED</code>.</li>
                    <li><strong>Targeted Isolation:</strong> Unrelated claims remain <code>CURRENT</code>. Zero web crawling or manual re-auditing required.</li>
                </ul>
            </div>
        </div>
    </div>

    <div class="footer-bar">
        <span>Brand Rule Lab • Product Master Guide & Architecture Manual • Team rohitlongesha5</span>
        <span>Page 4 of 5</span>
    </div>

</div>

<!-- ========================================================== -->
<!-- PAGE 5: CONCRETE SCENARIOS, DEPLOYMENT & ROUND 3 ROADMAP  -->
<!-- ========================================================== -->
<div class="page">

    <div class="section-header">
        <h2 class="section-title">Demonstration Scenarios, Deployment & Future Roadmap</h2>
        <div class="section-subtitle">Concrete execution walkthrough on Monsoon Beauty Co. and next-phase additions</div>
    </div>

    <!-- SYNTHETIC DEMONSTRATION TABLE -->
    <div class="card" style="margin-bottom: 9pt;">
        <div class="card-header">
            <h2 class="card-title">Live Demonstration Scenarios (Monsoon Beauty Co.)</h2>
            <span class="badge badge-pass">Pre-Loaded Fixtures</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th style="width: 14%;">Domain / Rule</th>
                    <th style="width: 25%;">Offending Draft Copy</th>
                    <th style="width: 31%;">System Finding & Enforcement</th>
                    <th style="width: 30%;">Preserved Intent Suggestion</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><strong>Tone (V-04)</strong></td>
                    <td><em>"Say goodbye to acne forever!"</em></td>
                    <td><span class="badge badge-fail">FAIL</span> Absolute promise conflicts with approved calm/credible voice rule V-04.</td>
                    <td>Suggest qualified phrasing: <em>"Helps clear blemishes"</em>; reviewer confirms.</td>
                </tr>
                <tr>
                    <td><strong>Terminology (T-12)</strong></td>
                    <td><em>"Our 2.5% BPO face wash"</em></td>
                    <td><span class="badge badge-fail">FAIL</span> Predicate T-12 disallows "BPO" in consumer-facing copy.</td>
                    <td>One-click intent replacement with approved INCI term: <em>"Benzoyl Peroxide"</em>.</td>
                </tr>
                <tr>
                    <td><strong>Claim vs. Fact (F-112)</strong></td>
                    <td><em>"Clears breakouts in 3 days"</em></td>
                    <td><span class="badge badge-warn">UNLINKED</span> Approved clinical trial MS-2024-F112 observes 4-week timeline, not 3 days.</td>
                    <td>Suggest: <em>"Within 4 weeks of twice-daily use"</em> citing Clinical Study MS-2024-F112.</td>
                </tr>
                <tr>
                    <td><strong>Campaign Dates</strong></td>
                    <td><em>"Diwali sale ends 15 Nov"</em></td>
                    <td><span class="badge badge-fail">FAIL</span> <code>date_window</code> predicate check fails; approved brief campaign expires 12 Nov.</td>
                    <td>Flag for campaign owner: renew approved fact or update copy date.</td>
                </tr>
                <tr>
                    <td><strong>Visual Contrast</strong></td>
                    <td>Banner graphic contrast ratio 2.5:1</td>
                    <td><span class="badge badge-fail">FAIL</span> Fails WCAG AA 4.5:1 threshold for normal text legibility.</td>
                    <td>Adjust background or font color to achieve contrast &ge; 4.5:1.</td>
                </tr>
            </tbody>
        </table>
    </div>

    <!-- TECH STACK & DEPLOYMENT INSTRUCTIONS -->
    <div class="grid-2">
        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">Technical Stack & Deployment</h2>
                <span class="badge badge-pass">Docker & Cloud Ready</span>
            </div>
            <ul style="padding-left: 11pt; margin: 2pt 0; font-size: 7.3pt; color: #39585B;">
                <li><strong>UI:</strong> Streamlit 1.66.1 (multi-role views, reactive diff & audit inspectors).</li>
                <li><strong>Primary AI:</strong> NVIDIA NIM HTTPS API (<code>nemotron-3-nano-omni-30b-a3b-reasoning</code>).</li>
                <li><strong>Fallback AI:</strong> Google Gemini API (<code>gemini-3.8-flash</code>) with auto-switchover.</li>
                <li><strong>Local Parsing:</strong> PyMuPDF (PDF), python-docx (DOCX), openpyxl (XLSX), Tesseract OCR (images).</li>
                <li><strong>Testing & Quality:</strong> 216 pytest unit/integration tests (<strong>216 passed, 0 failed</strong>).</li>
                <li><strong>Deployment:</strong> Multi-stage Dockerfile with Debian Tesseract OCR and automated database seeding on boot.</li>
            </ul>
        </div>

        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">Three Major Round 3 Improvements</h2>
                <span class="badge badge-warn">Future Scope</span>
            </div>
            <div style="font-size: 7.2pt; line-height: 1.32; color: #39585B;">
                <p style="margin: 0 0 3pt;">
                    <strong>1. Pre-Flight CMS & Social Gatekeeper:</strong> Direct bi-directional webhooks for Shopify, WordPress, and Hootsuite that intercept publication if a valid <code>stable_claim_id</code> is missing or drifted.
                </p>
                <p style="margin: 0 0 3pt;">
                    <strong>2. Multimodal Packaging & Video Frame Inspection:</strong> Temporal analysis using Whisper for audio disclaimers and frame extraction for video overlays and 3D packaging renders.
                </p>
                <p style="margin: 0;">
                    <strong>3. Enterprise RBAC & Cryptographic Merkle Export:</strong> Single Sign-On (OIDC/SAML) and mathematically tamper-evident Merkle-tree claim exports for ASCI/FTC legal compliance audits.
                </p>
            </div>
        </div>
    </div>

    <div class="footer-bar">
        <span>Brand Rule Lab • Product Master Guide & Architecture Manual • Team rohitlongesha5</span>
        <span>Page 5 of 5</span>
    </div>

</div>

</body>
</html>"""


def _find_edge() -> Path:
    candidates = [
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("Microsoft Edge was not found; install Edge or add msedge to PATH.")


def generate_pdf(destination: Path) -> None:
    edge = _find_edge()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="brl-explainer-") as temp_dir:
        temp = Path(temp_dir)
        html_file = temp / "explainer.html"
        raw_pdf = temp / "rendered.pdf"

        html_file.write_text(get_html_content(), encoding="utf-8")

        command = [
            str(edge),
            "--headless",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={temp / 'edge-profile'}",
            f"--print-to-pdf={raw_pdf}",
            "--no-pdf-header-footer",
            html_file.as_uri(),
        ]

        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode or not raw_pdf.is_file():
            raise RuntimeError(f"Edge PDF generation failed: {result.stderr or result.stdout}")

        # Post-process with PyMuPDF to add exact metadata
        doc = fitz.open(raw_pdf)
        doc.metadata = {
            "title": "Brand Rule Lab — Product Master Guide & Architectural Manual",
            "author": "Rohit Longesha (Team rohitlongesha5)",
            "subject": "CodeNexus India 2026 - Inkloom AI Hackathon Round 2 Master Guide",
            "keywords": "Brand Governance, Claim Ledger, Deterministic Rules, Streamlit, NVIDIA NIM, Gemini, Python",
        }
        doc.save(str(destination))
        num_pages = len(doc)
        doc.close()
        print(f"Successfully generated: {destination} ({num_pages} pages)")


if __name__ == "__main__":
    output_path = Path("Brand_Rule_Lab_Walkthrough.pdf")
    generate_pdf(output_path)
