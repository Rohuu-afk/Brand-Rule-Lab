"""
Generate a visually stunning, UI-matching product walkthrough and architecture PDF
for Brand Rule Lab, formatted with the exact design system, tokens, and aesthetic of the app.
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
    margin: 22pt 30pt 22pt 30pt;
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
    font-size: 8.2pt;
    line-height: 1.32;
}

.page {
    page-break-after: always;
    break-after: page;
    position: relative;
    padding-bottom: 16pt;
}

.page:last-child {
    page-break-after: avoid;
    break-after: avoid;
}

/* Header Banner */
.hero {
    background: linear-gradient(135deg, #155B61 0%, #1D7077 50%, #2A838B 100%);
    color: #FFFFFF;
    border-radius: 12pt;
    padding: 16pt 20pt 14pt;
    margin-bottom: 12pt;
    box-shadow: 0 4pt 12pt rgba(21, 91, 97, 0.16);
}

.hero-tag {
    display: inline-block;
    background: rgba(255, 255, 255, 0.2);
    border: 0.5pt solid rgba(255, 255, 255, 0.4);
    color: #E6F7F6;
    font-size: 7pt;
    font-weight: 600;
    letter-spacing: 0.8pt;
    text-transform: uppercase;
    padding: 2pt 8pt;
    border-radius: 20pt;
    margin-bottom: 6pt;
}

.hero-title {
    font-size: 21pt;
    font-weight: 750;
    line-height: 1.15;
    margin: 0 0 3pt;
    letter-spacing: -0.3pt;
}

.hero-subtitle {
    font-size: 10.5pt;
    font-style: italic;
    color: #C3E9E5;
    margin: 0 0 8pt;
}

.hero-desc {
    font-size: 8.2pt;
    color: #E7F6F5;
    line-height: 1.38;
    margin: 0 0 10pt;
    max-width: 98%;
}

.hero-meta {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-top: 0.5pt solid rgba(255, 255, 255, 0.25);
    padding-top: 7pt;
    font-size: 7.3pt;
    color: #D3EFEA;
}

/* Pipeline Flow */
.pipeline-bar {
    display: flex;
    justify-content: space-between;
    background: #FFFFFF;
    border: 1pt solid #D6E7E5;
    border-radius: 9pt;
    padding: 7pt 12pt;
    margin-bottom: 12pt;
    box-shadow: 0 2pt 5pt rgba(21, 91, 97, 0.04);
}

.pipe-step {
    display: flex;
    align-items: center;
    font-size: 7.6pt;
    font-weight: 700;
    color: #155B61;
    letter-spacing: 0.3pt;
}

.pipe-step span.num {
    display: inline-block;
    width: 14pt;
    height: 14pt;
    background: #E4F3F1;
    color: #155B61;
    border-radius: 50%;
    text-align: center;
    line-height: 14pt;
    font-size: 7pt;
    font-weight: 800;
    margin-right: 5pt;
}

.pipe-arrow {
    color: #9EC6C3;
    font-size: 8.5pt;
    align-self: center;
}

/* Grid & Cards */
.grid-2 {
    display: flex;
    gap: 10pt;
    margin-bottom: 10pt;
}

.grid-3 {
    display: flex;
    gap: 8pt;
    margin-bottom: 10pt;
}

.col-1 { flex: 1; }
.col-2 { flex: 2; }

.card {
    background: #FFFFFF;
    border: 1pt solid #D9EAE7;
    border-radius: 9pt;
    padding: 10pt 12pt;
    box-shadow: 0 2pt 5pt rgba(21, 91, 97, 0.04);
}

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6pt;
    border-bottom: 0.75pt solid #EAF2F1;
    padding-bottom: 4pt;
}

.card-title {
    font-size: 10pt;
    font-weight: 700;
    color: #155B61;
    margin: 0;
}

/* Badges & Status */
.badge {
    display: inline-block;
    padding: 1.5pt 5pt;
    border-radius: 4pt;
    font-size: 6.8pt;
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
    font-size: 7.6pt;
    margin-top: 3pt;
}

th {
    background: #EBF4F3;
    color: #155B61;
    font-weight: 700;
    text-align: left;
    padding: 4.5pt 6pt;
    border-bottom: 1pt solid #D2E5E3;
}

td {
    padding: 4.5pt 6pt;
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
    padding: 1pt 3pt;
    border-radius: 3pt;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace;
    font-size: 7.5pt;
}

.highlight-box {
    background: #F0F7F6;
    border-left: 2.5pt solid #155B61;
    border-radius: 0 5pt 5pt 0;
    padding: 6pt 8pt;
    margin: 6pt 0;
    font-size: 7.8pt;
}

.stat-pill {
    display: flex;
    flex-direction: column;
    background: #F7FBFA;
    border: 1pt solid #DFECEB;
    border-radius: 6pt;
    padding: 5pt 8pt;
    text-align: center;
}

.stat-num {
    font-size: 14pt;
    font-weight: 800;
    color: #155B61;
    line-height: 1.1;
}

.stat-label {
    font-size: 6.5pt;
    font-weight: 600;
    color: #4C6F72;
    text-transform: uppercase;
}

.footer-bar {
    margin-top: 10pt;
    border-top: 0.75pt solid #D6E7E5;
    padding-top: 4pt;
    display: flex;
    justify-content: space-between;
    font-size: 7pt;
    color: #6C8D90;
}
</style>
</head>
<body>

<!-- =================================================== -->
<!-- PAGE 1: OVERVIEW, THE PROBLEM, AND THE CLAIM LEDGER -->
<!-- =================================================== -->
<div class="page">

    <div class="hero">
        <div class="hero-tag">Inkloom AI Hackathon • CodeNexus India 2026 • Round 2: The Build</div>
        <h1 class="hero-title">Brand Rule Lab</h1>
        <div class="hero-subtitle">Pre-publication review with a verifiable claim-to-fact ledger</div>
        <div class="hero-desc">
            An intelligent brand governance platform built for high-scrutiny marketing. It catches off-brand tone, wrong terminology, unsupported claims, and inaccessible visuals before publication. Every approved claim receives an immutable hash and stable identifier directly tied to the exact approved fact version that substantiates it.
        </div>
        <div class="hero-meta">
            <span><strong>Team:</strong> rohitlongesha5 (Rohit Longesha)</span>
            <span><strong>Focus Market:</strong> India D2C Personal Care & Beauty</span>
            <span><strong>Status:</strong> 216 Tests Passed (100% Green) • Live NVIDIA NIM & Gemini AI</span>
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

    <!-- GRID: PROBLEM VS SOLUTION WEDGE -->
    <div class="grid-2">
        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">The Real Industry Problem</h2>
                <span class="badge badge-fail">ASCI Scrutiny</span>
            </div>
            <p style="margin-top: 0;">In fast-moving digital marketing, rapid-result claims and absolute promises face strict scrutiny from regulatory bodies and consumers:</p>
            <div class="grid-3" style="margin-bottom: 6pt;">
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
                    <span class="stat-label">Peer turnaround delay</span>
                </div>
            </div>
            <div class="highlight-box">
                <em>"Annoying part design ka approval ni milta... senior deny krdete late factual edit pe."</em> — Peer interview evidence.
            </div>
            <p style="font-size: 7.8pt; color: #39585B; margin: 0;">
                Sequential sign-offs, subjective comments ("make it pop"), and unrecorded guideline preferences lead to costly re-works and delayed campaigns.
            </p>
        </div>

        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">Our Wedge: The Claim Ledger</h2>
                <span class="badge badge-pass">Core Wedge</span>
            </div>
            <p style="margin-top: 0;">Generic brand assistants offer text review without historical lineage. <strong>Brand Rule Lab</strong> links every approved claim to a verifiable evidence version:</p>
            <ul style="padding-left: 12pt; margin: 3pt 0 5pt;">
                <li><strong>Stable Identifier:</strong> Each approved claim receives a unique 12-char hex <code>stable_claim_id</code> and SHA-256 normalized text hash.</li>
                <li><strong>Fact-Version Linkage:</strong> Bound to a specific <code>fact_version_id</code> (e.g. Clinical Study F-112 v3).</li>
                <li><strong>Instant Drift Propagation:</strong> Fact updates or expiries flag dependent claims in <strong>1 SQL update with 0 LLM calls</strong>.</li>
            </ul>
            <div class="highlight-box">
                <strong>Strict Boundary:</strong> <code>UNLINKED</code>, <code>NOT_OBSERVED</code>, and <code>REVIEW_REQUIRED</code> remain distinct states. A missing match is never quietly treated as compliant.
            </div>
            <p style="font-size: 7.8pt; color: #39585B; margin: 0;">
                External live edits require a pasted-text hash check or manual import to verify coverage.
            </p>
        </div>
    </div>

    <!-- SQL UPDATE DIAGRAM CARD -->
    <div class="card">
        <div class="card-header">
            <h2 class="card-title">Deterministic Drift Cascades (Zero-LLM Overhead)</h2>
            <span class="badge badge-info">SQL Cascade</span>
        </div>
        <div class="grid-2" style="margin-bottom: 0;">
            <div style="flex: 1; background: #20383B; color: #E7F6F5; border-radius: 5pt; padding: 7pt; font-family: monospace; font-size: 6.8pt; line-height: 1.35;">
                <span style="color: #6EBAB1;">-- Fact Owner approves version 4 of F-112</span><br>
                <span style="color: #F8B471;">UPDATE</span> approved_claims<br>
                <span style="color: #F8B471;">SET</span> drift_status = <span style="color: #92E897;">'REVIEW_REQUIRED'</span>,<br>
                &nbsp;&nbsp;&nbsp;&nbsp;flagged_at = CURRENT_TIMESTAMP<br>
                <span style="color: #F8B471;">WHERE</span> fact_id = <span style="color: #92E897;">'F-112'</span> <span style="color: #F8B471;">AND</span> fact_version_id &lt; 4;<br>
                <span style="color: #A9DCD2;">-- Expiry sweep flags expired versions instantly</span>
            </div>
            <div style="flex: 1; padding: 4pt 0 0 4pt; font-size: 7.8pt;">
                <p style="margin: 0 0 4pt;"><strong>Why this matters:</strong></p>
                <ul style="padding-left: 12pt; margin: 0; color: #39585B;">
                    <li><strong>0 LLM API calls:</strong> Drift tracking runs entirely in the database engine.</li>
                    <li><strong>0 Web crawls:</strong> Immediate notification without scraping external sites.</li>
                    <li><strong>1 Atomic SQL transaction:</strong> Updates all registered dependencies without race conditions.</li>
                </ul>
            </div>
        </div>
    </div>

    <div class="footer-bar">
        <span>Brand Rule Lab • CodeNexus India 2026 Round 2 Submission • Team rohitlongesha5</span>
        <span>Page 1 of 3</span>
    </div>

</div>

<!-- =================================================== -->
<!-- PAGE 2: ROLES, RULE READINESS, AND PYTHON VS. AI    -->
<!-- =================================================== -->
<div class="page">

    <!-- ROLE-BASED GOVERNANCE & DIVISION OF LABOR -->
    <div class="grid-2">
        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">Three Role-Gated Views</h2>
                <span class="badge badge-info">Simulated Roles</span>
            </div>
            <table style="margin-top: 0;">
                <thead>
                    <tr><th>Role</th><th>Primary Workflow</th><th>Key Permissions</th></tr>
                </thead>
                <tbody>
                    <tr>
                        <td><strong>Creator</strong></td>
                        <td>Author & revise marketing copy</td>
                        <td>Uploads drafts (PDF, DOCX, TXT, images); accepts/edits suggestions.</td>
                    </tr>
                    <tr>
                        <td><strong>Reviewer</strong></td>
                        <td>Approval queue & guideline audits</td>
                        <td>Audits findings; classifies rules; approves/rejects submitted claims.</td>
                    </tr>
                    <tr>
                        <td><strong>Fact Owner</strong></td>
                        <td>Evidence & regulatory specifications</td>
                        <td>Approves facts, sets validity periods & markets; bumps versions.</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">Python Enforces. LLM Language.</h2>
                <span class="badge badge-pass">Deterministic Core</span>
            </div>
            <table style="margin-top: 0;">
                <thead>
                    <tr><th>Component</th><th>Responsible Agent</th><th>Principle</th></tr>
                </thead>
                <tbody>
                    <tr>
                        <td><strong>Exact Rules</strong></td>
                        <td>Python Predicates</td>
                        <td>100% reproducible exact pass/fail; zero hallucinations.</td>
                    </tr>
                    <tr>
                        <td><strong>Claim Extraction</strong></td>
                        <td>Python + NIM / Gemini</td>
                        <td>Python slices spans & offsets; AI selects IDs only.</td>
                    </tr>
                    <tr>
                        <td><strong>Fact Matching</strong></td>
                        <td>SQLite Filter + AI</td>
                        <td>SQL filters by product/market; AI ranks relationship.</td>
                    </tr>
                    <tr>
                        <td><strong>Tone & Advice</strong></td>
                        <td>Advisory AI (Nemotron)</td>
                        <td>Contextual suggestions only. AI never auto-publishes.</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- RULE READINESS AUDIT SYSTEM -->
    <div class="card" style="margin-bottom: 10pt;">
        <div class="card-header">
            <h2 class="card-title">Rule Readiness Audit System</h2>
            <span class="badge badge-info">Source-Verified Governance</span>
        </div>
        <p style="margin-top: 0; font-size: 8pt; color: #20383B;">
            When brand guideline documents (PDF/DOCX/TXT/XLSX) are onboarded, extracted proposals undergo an audit gate before any rule enters the enforcement engine. Every active rule retains source quote and page-level provenance:
        </p>
        <div class="grid-2" style="margin-bottom: 6pt;">
            <div style="flex: 1; background: #F6FBFA; border: 1pt solid #D6E7E5; border-radius: 6pt; padding: 6pt;">
                <div style="font-weight: 700; color: #236B4D; font-size: 7.8pt; margin-bottom: 2pt;">1. CODE_CHECKABLE (Active Rules)</div>
                <div style="font-size: 7.2pt; color: #39585B;">
                    Strictly mapped to supported Python predicates (<code>term_list</code>, <code>regex</code>, <code>numeric_range</code>, <code>date_window</code>, <code>required_text</code>, <code>wcag_contrast</code>, <code>asset_hash</code>). Must pass automated positive/negative self-tests and human reviewer sign-off.
                </div>
            </div>
            <div style="flex: 1; background: #F6FBFA; border: 1pt solid #D6E7E5; border-radius: 6pt; padding: 6pt;">
                <div style="font-weight: 700; color: #155B61; font-size: 7.8pt; margin-bottom: 2pt;">2. AI_ASSISTED (Advisory Guidance)</div>
                <div style="font-size: 7.2pt; color: #39585B;">
                    Contextual guidance such as brand voice nuances and narrative style. Surfaces advisory suggestions during review; never compiled into blocking deterministic predicates.
                </div>
            </div>
        </div>
        <div class="grid-2" style="margin-bottom: 0;">
            <div style="flex: 1; background: #F6FBFA; border: 1pt solid #D6E7E5; border-radius: 6pt; padding: 6pt;">
                <div style="font-weight: 700; color: #8A5F0B; font-size: 7.8pt; margin-bottom: 2pt;">3. HUMAN_JUDGEMENT (Manual Routing)</div>
                <div style="font-size: 7.2pt; color: #39585B;">
                    Subjective visual balance, aesthetics, and cultural nuances that cannot be decided algorithmically. Routed straight to human reviewer queues.
                </div>
            </div>
            <div style="flex: 1; background: #F6FBFA; border: 1pt solid #D6E7E5; border-radius: 6pt; padding: 6pt;">
                <div style="font-weight: 700; color: #9A2C23; font-size: 7.8pt; margin-bottom: 2pt;">4. UNCLEAR (Clarification Required)</div>
                <div style="font-size: 7.2pt; color: #39585B;">
                    Ambiguous or contradictory source guideline wording. Explicitly held and blocked from enforcement until the brand owner logs a written clarification.
                </div>
            </div>
        </div>
    </div>

    <!-- SECURITY, PRIVACY & PROMPT SAFETY -->
    <div class="card">
        <div class="card-header">
            <h2 class="card-title">Responsible Design, Privacy & Prompt Safety</h2>
            <span class="badge badge-pass">Safety by Design</span>
        </div>
        <div class="grid-2" style="margin-bottom: 0;">
            <div style="flex: 1;">
                <ul style="padding-left: 11pt; margin: 0; font-size: 7.6pt; color: #39585B;">
                    <li><strong>Human Authority:</strong> AI never approves claims or auto-publishes content. Reviewers and Fact Owners retain exclusive authority.</li>
                    <li><strong>Prompt Injection Defense:</strong> Draft text is treated as untrusted data. AI has zero tool/database-write capabilities. Deterministic Python rules run independently and cannot be suppressed by draft instructions.</li>
                </ul>
            </div>
            <div style="flex: 1;">
                <ul style="padding-left: 11pt; margin: 0; font-size: 7.6pt; color: #39585B;">
                    <li><strong>Privacy & Data Minimization:</strong> Uploaded binary files (PDF/DOCX/PNG) are parsed locally on the host. Raw files are never transmitted to external AI.</li>
                    <li><strong>Dual AI Provider Resiliency:</strong> Primary NVIDIA NIM (Nemotron-3-Nano-Omni) with automatic graceful fallback to Google Gemini (3.8-Flash).</li>
                </ul>
            </div>
        </div>
    </div>

    <div class="footer-bar">
        <span>Brand Rule Lab • CodeNexus India 2026 Round 2 Submission • Team rohitlongesha5</span>
        <span>Page 2 of 3</span>
    </div>

</div>

<!-- =================================================== -->
<!-- PAGE 3: CHECKS IN ACTION, TECH STACK & ROADMAP      -->
<!-- =================================================== -->
<div class="page">

    <!-- CHECKS IN ACTION (MONSOON BEAUTY CO. SCENARIOS) -->
    <div class="card" style="margin-bottom: 10pt;">
        <div class="card-header">
            <h2 class="card-title">Live Checks in Action (Synthetic Demonstration Suite)</h2>
            <span class="badge badge-pass">Monsoon Beauty Co.</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Check Domain</th>
                    <th>Draft Signal / Violation</th>
                    <th>System Enforcement & Finding</th>
                    <th>Intent-Preserving Suggestion</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><strong>Tone (V-04)</strong></td>
                    <td><em>"Say goodbye to acne forever!"</em></td>
                    <td><span class="badge badge-fail">FAIL</span> Absolute promise conflicts with approved calm voice rule V-04.</td>
                    <td>Suggest qualified wording: <em>"Helps clear blemishes"</em>; reviewer confirms.</td>
                </tr>
                <tr>
                    <td><strong>Terminology (T-12)</strong></td>
                    <td><em>"Our 2.5% BPO face wash"</em></td>
                    <td><span class="badge badge-fail">FAIL</span> Rule T-12 disallows abbreviation "BPO" in consumer-facing copy.</td>
                    <td>One-click intent-preserving replacement with approved INCI term: <em>"Benzoyl Peroxide"</em>.</td>
                </tr>
                <tr>
                    <td><strong>Claim vs. Fact (F-112)</strong></td>
                    <td><em>"Clears breakouts in 3 days"</em></td>
                    <td><span class="badge badge-warn">UNLINKED</span> Approved clinical trial MS-2024-F112 observes 4-week timeline, not 3 days.</td>
                    <td>Suggest: <em>"Within 4 weeks of twice-daily use"</em> citing Study MS-2024-F112.</td>
                </tr>
                <tr>
                    <td><strong>Offer Date Window</strong></td>
                    <td><em>"Diwali sale ends 15 Nov"</em></td>
                    <td><span class="badge badge-fail">FAIL</span> <code>date_window</code> predicate check fails; approved brief campaign expires 12 Nov.</td>
                    <td>Flag for campaign owner: renew approved fact or update copy date.</td>
                </tr>
                <tr>
                    <td><strong>Visual & Contrast</strong></td>
                    <td>Banner graphic contrast ratio 2.5:1</td>
                    <td><span class="badge badge-fail">FAIL</span> Fails WCAG AA 4.5:1 threshold for normal text legibility.</td>
                    <td>Change text foreground/background color pair to achieve &ge; 4.5:1.</td>
                </tr>
                <tr>
                    <td><strong>Asset Integrity</strong></td>
                    <td>Uploaded product image altered</td>
                    <td><span class="badge badge-fail">FAIL</span> Exact SHA-256 identity does not match approved product asset record.</td>
                    <td>Re-select approved asset from registry or submit for Fact Owner review.</td>
                </tr>
            </tbody>
        </table>
    </div>

    <!-- TECH STACK & VERIFICATION RESULTS -->
    <div class="grid-2">
        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">Technology & Architecture Disclosures</h2>
                <span class="badge badge-pass">Production Ready</span>
            </div>
            <ul style="padding-left: 11pt; margin: 2pt 0; font-size: 7.5pt; color: #39585B;">
                <li><strong>UI:</strong> Streamlit 1.66.1 (multi-role views, reactive diff & audit inspectors).</li>
                <li><strong>Primary AI:</strong> NVIDIA NIM HTTPS API (<code>nemotron-3-nano-omni-30b-a3b-reasoning</code>).</li>
                <li><strong>Fallback AI:</strong> Google Gemini API (<code>gemini-3.8-flash</code>) with auto-switchover.</li>
                <li><strong>Local Parsing:</strong> PyMuPDF (PDF), python-docx (DOCX), openpyxl (XLSX), Tesseract OCR (images).</li>
                <li><strong>Database:</strong> SQLite 3 with WAL mode, foreign keys, and additive schema migrations.</li>
                <li><strong>Contracts:</strong> jsonschema 4.23.0 enforcement on all LLM responses.</li>
                <li><strong>Testing:</strong> 216 pytest unit/integration tests (<strong>216 passed, 0 failed</strong>).</li>
            </ul>
        </div>

        <div class="card col-1">
            <div class="card-header">
                <h2 class="card-title">Round 3 Future Roadmap (Next Steps)</h2>
                <span class="badge badge-warn">Future Scope</span>
            </div>
            <div style="font-size: 7.4pt; line-height: 1.32; color: #39585B;">
                <p style="margin: 0 0 4pt;">
                    <strong>1. Pre-Flight CMS & Social Gatekeeper:</strong> Direct webhooks for Shopify, WordPress, and Hootsuite that intercept publication if <code>stable_claim_id</code> is missing or drifted.
                </p>
                <p style="margin: 0 0 4pt;">
                    <strong>2. Multimodal Packaging & Video Frame Inspection:</strong> Temporal analysis using Whisper for audio disclaimers and frame extraction for video overlays and packaging renders.
                </p>
                <p style="margin: 0;">
                    <strong>3. Enterprise RBAC & Cryptographic Merkle Export:</strong> Single Sign-On (OIDC/SAML) and mathematically tamper-evident Merkle-tree claim exports for ASCI/FTC legal compliance audits.
                </p>
            </div>
        </div>
    </div>

    <!-- REPO SUMMARY CARD -->
    <div class="card" style="margin-top: 10pt;">
        <div class="card-header">
            <h2 class="card-title">Deployment & GitHub Repository Structure</h2>
            <span class="badge badge-info">Ready to Deploy</span>
        </div>
        <div style="font-size: 7.5pt; color: #39585B; line-height: 1.35;">
            The project repository includes a production Dockerfile with Tesseract OCR dependencies, automated database migrations, and healthcheck probe. All 66 source files are tracked in Git on branch <code>main</code>, with local credentials (<code>.env</code>) and virtual environments (<code>.venv/</code>) completely excluded.
        </div>
    </div>

    <div class="footer-bar">
        <span>Brand Rule Lab • CodeNexus India 2026 Round 2 Submission • Team rohitlongesha5</span>
        <span>Page 3 of 3</span>
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
            "title": "Brand Rule Lab — Pre-publication review with a claim-to-fact ledger",
            "author": "Rohit Longesha (Team rohitlongesha5)",
            "subject": "CodeNexus India 2026 - Inkloom AI Hackathon Round 2 Build Walkthrough",
            "keywords": "Brand Governance, Claim Ledger, Deterministic Rules, Streamlit, NVIDIA NIM, Gemini, Python",
        }
        doc.save(str(destination))
        num_pages = len(doc)
        doc.close()
        print(f"Successfully generated: {destination} ({num_pages} pages)")


if __name__ == "__main__":
    output_path = Path("Brand_Rule_Lab_Walkthrough.pdf")
    generate_pdf(output_path)
