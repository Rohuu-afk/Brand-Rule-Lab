# Brand Rule Lab
## CodeNexus India 2026 — Inkloom AI | Round 2 Submission

**Team:** rohitlongesha5  
**Team Leader:** Rohit Longesha  
**Project:** Brand Rule Lab — Pre-publication review with a claim-to-fact ledger

---

## 1. Project Overview & Problem Alignment

### The Real Problem (Inkloom AI Brief & Round 1 Evidence)
Growing consumer-facing brands publish content rapidly across distributed marketing teams, creative agencies, and generative AI tools. In high-scrutiny sectors such as Indian D2C Personal Care & Beauty, unsubstantiated claims, outdated facts, inconsistent terminology, and off-brand visuals lead to major credibility damage and regulatory friction (e.g., ASCI reports 90% of reviewed Beauty & Personal Care cases required modification; 82% of scrutinised digital ads were sponsored social media ads).

Primary research conducted in Round 1 revealed recurring approval friction:
- **Peer 1:** "Annoying part toh design ka hi rehta... approval ni milta sabse... senior deny krdete" (turnaround: 1–2 days; rejections due to late factual edits).
- **Peer 2:** "Last-minute changes — caption is good but date, venue, sponsor logo is wrong and whole post goes back for redo" (turnaround: 1–4 days).
- **Peer 3:** "Can we make it pop?" or "This doesn’t feel right" without actionable specifics (turnaround: 24–48h social, 3–7 days high-stakes).

### Brand Rule Lab Wedge
Instead of simply rejecting content or relying on vague chatbot reviews, **Brand Rule Lab links every approved promotional claim to a stable identifier (`stable_claim_id`) and an exact versioned evidence record (`fact_version_id`) in an immutable claim ledger.** When facts change or expire, dependent claims are automatically flagged for review (`REVIEW_REQUIRED` / `EXPIRED`).

---

## 2. Target Users & Workflow Roles

Brand Rule Lab establishes role-based governance:
- **Creators:** Upload drafts (PDF, DOCX, TXT, images), run pre-publication checks, inspect cited findings with exact character offsets, and accept, edit, or dismiss suggestions with intent-preserving corrections.
- **Reviewers:** Inspect the approval queue, review deterministic and AI findings, demand revisions, and make authoritative approval decisions. Reviewers also classify source-extracted guidelines through the **Rule Readiness Audit**.
- **Product / Quality Fact Owners:** Onboard source materials, manage approved facts, increment fact versions, set validity dates/markets, and expire evidence.

---

## 3. End-to-End Workflow (Draft → Check → Suggest → Approve → Ledger → Monitor)

1. **Brand Knowledge Onboarding:** Upload brand manuals, clinical studies, and product specification sheets (PDF, DOCX, TXT, XLSX, PNG, JPEG). Files are parsed locally (with Tesseract OCR when needed). AI extraction proposes source-quoted facts and guidelines with strict page/document provenance.
2. **Rule Readiness Gate:** Reviewers audit rule proposals into four distinct readiness classes:
   - `CODE_CHECKABLE`: strictly verified against a whitelisted predicate (`term_list`, `regex`, `numeric_range`, `date_window`, `required_text`, `wcag_contrast`, `asset_hash`) with mandatory positive/negative self-tests. Only passing, human-approved rules enter the active `brand_rules` engine.
   - `AI_ASSISTED`: advisory contextual guidance; never silently converted into blocking code predicates.
   - `HUMAN_JUDGEMENT`: subjective matters (e.g., brand aesthetics) routed directly to human reviewers.
   - `UNCLEAR`: marked for clarification; blocked from enforcement until resolved.
3. **Draft Intake:** Creator submits copy or uploads documents/creatives, selecting product, target market (e.g., `IN`), channel (e.g., `instagram`), and optional campaign brief.
4. **Hybrid Check Pipeline:**
   - **Python Deterministic Predicates:** evaluate terminology, regex constraints, date windows, required statements, image SHA-256 match, and WCAG AA 4.5:1 text contrast.
   - **Source-Preserved Claim Extraction:** Python parses sentences and clause spans with exact character offsets. External AI selects candidate IDs without inventing spans or offsets.
   - **Scoped Fact Retrieval & Evidence Ranking:** SQLite filters approved facts strictly by product and market. The AI ranks claims against candidates, classifying relationships into `SUPPORTS`, `CONTRADICTS`, `RELATED_NOT_EVIDENCE`, or `NO_MATCH`.
   - **Advisory Tone & Campaign Alignment:** evaluates tone against voice guidelines (e.g., V-04 calm/credible tone vs. absolute promises) and campaign briefs.
5. **Intent-Preserving Suggestions:** Suggestions provide qualified rewrites (e.g., replacing "clears breakouts in 3 days" with "within 4 weeks" citing clinical study MS-2024-F112).
6. **Role-Gated Human Approval:** Reviewers approve, request changes, or escalate. Unlinked or contradictory claims cannot be approved without recorded overrides.
7. **Claim-to-Fact Ledger:** Approvals mint an immutable record with a 12-character hex `stable_claim_id`, normalized text SHA-256 hash, reviewer signature, timestamp, and linked `fact_version_id`.
8. **Drift Monitoring:** When a Fact Owner updates a fact to a newer version or a fact expires, an atomic database update immediately flags linked claims as `REVIEW_REQUIRED` or `EXPIRED`, while unrelated claims remain `CURRENT`.

---

## 4. Completed Features (Round 2 Deliverables)

- [x] **Deterministic Python Rule Engine:** Pure Python evaluation for `term_list` (banned/allowed terms), `regex`, `numeric_range`, `date_window`, `required_text`, `asset_hash`, and `wcag_contrast`.
- [x] **Rule Readiness Audit System:** Classification, source quote extraction, predicate compilation, automated positive/negative self-test harness, and SQLite history tracking.
- [x] **AI-Assisted Claim Extraction & Ranking:** Candidate offset preservation, product/market SQL isolation, JSON-schema validation, and explicit `NOT_OBSERVED` handling.
- [x] **Dual AI Provider Architecture:** Primary NVIDIA NIM (`nemotron-3-nano-omni-30b-a3b-reasoning`) with automatic graceful fallback to Google Gemini (`gemini-3.8-flash`) and offline resilience.
- [x] **Verifiable Claim Ledger:** `stable_claim_id` minting, immutable text hashes, fact-version lineage tracking, and atomic SQL drift cascade.
- [x] **Role-Based Workflows:** Separate Creator, Reviewer, and Fact Owner interfaces in Streamlit with immutable audit logging.
- [x] **Multi-format Ingestion:** Local parsing of PDF (PyMuPDF), DOCX (python-docx), XLSX (openpyxl), TXT, and image OCR (pytesseract & Pillow).
- [x] **Visual & Accessibility Checks:** WCAG AA 4.5:1 contrast calculation, alt-text presence verification, and exact SHA-256 asset verification.
- [x] **Synthetic Demonstration Suite:** Fully populated reference data for *Monsoon Beauty Co.* and *GlowUp* demonstrating V-04 (tone), T-12 (terminology), F-112 (clinical timeline), and offer expiration.
- [x] **Automated Test Suite:** 216 passing automated unit and integration tests (`216 passed, 0 failed`).
- [x] **Synthetic Evaluation Harness:** Reproducible test runner measuring turnaround, 60-claim matching, drift propagation, and prompt-injection safety.
- [x] **Production Packaging:** Dockerfile with multi-stage OCR dependencies and container health check; clean `.gitignore` and `.env.example`.

---

## 5. Technology & Implementation Details

| Component | Technology | Rationale & Implementation Details |
|---|---|---|
| **UI Framework** | Streamlit 1.66.1 | Fast, reactive multi-role interface with sidebar navigation, diff highlighting, and audit viewers. |
| **Primary AI Provider** | NVIDIA NIM API | OpenAI-compatible HTTPS API calling `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` with bounded timeouts. |
| **Fallback AI Provider** | Google Gemini (google-genai 2.34.0) | Automated fallback on network/quota failure to `gemini-3.8-flash` / `gemini-3.1-flash-lite`. |
| **Local Parsing & OCR** | PyMuPDF, python-docx, openpyxl, pytesseract | Local extraction of documents and image OCR without transmitting binary files to cloud LLMs. |
| **Database & Ledger** | SQLite 3 (WAL mode) | Strict foreign keys, ACID compliance, zero external infrastructure dependency, and deterministic SQL cascades. |
| **Rule Engine** | Custom Python Engine | High-throughput, deterministic execution of allowlisted predicates with regex caching and contrast calculations. |
| **Contract Validation** | jsonschema 4.23.0 | Strict schema validation for all structured model outputs before ingestion. |
| **Testing Harness** | pytest 8.3.5 | 216 comprehensive unit and integration tests covering security, drift, and edge cases. |

---

## 6. Progress Against the Round 1 Plan & Explanation of Changes

| Plan Item (Round 1 Deck) | Status in Round 2 | Details & Changes Made |
|---|---|---|
| **End-to-End Workflow (Slides 1, 5, 8)** | `COMPLETED` | Fully operational Draft → Check → Suggest → Approve → Ledger → Monitor lifecycle across Creator, Reviewer, and Fact Owner roles. |
| **Checks in Action (Slide 6)** | `COMPLETED` | Implemented V-04 (tone), T-12 (terminology), F-112 (claim vs. evidence duration), date windows, OCR text extraction, and WCAG AA contrast. |
| **Fact Ledger & Drift (Slide 7)** | `COMPLETED` | SQL drift cascade flags superseded fact versions (`REVIEW_REQUIRED`) and expired facts (`EXPIRED`) in 1 SQL update with 0 LLM calls, matching the slide specification. |
| **Technical Stack (Slide 9)** | `COMPLETED` | Python, Streamlit, SQLite, PyMuPDF, python-docx, Tesseract OCR. |
| **Evaluation Plan (Slide 10)** | `COMPLETED` | Reproducible evaluation suite verifying claim matching, drift cascade, and adversarial prompt-injection resistance. |
| **Retrieval Architecture** | `REFINED` | **Change from Plan:** Round 1 mentioned exploring Chroma vector index. We chose **canonical SQLite scoped queries** for eligible fact retrieval. This avoids dual-store sync anomalies, eliminates external persistence risks, and guarantees strict product/market isolation. |
| **AI Provider Resiliency** | `ENHANCED` | **Addition to Plan:** Integrated NVIDIA NIM as primary inference with seamless Google Gemini fallback and complete offline resilience (`NOT_OBSERVED` state instead of failure). |

---

## 7. Responsible Design, Privacy & Prompt Safety (Slide 12 Alignment)

- **Human Authority:** AI never approves claims or auto-publishes content. Reviewers and Fact Owners retain exclusive approval authority.
- **Privacy & Data Minimization:** Raw binary documents and images are parsed locally and never sent to external LLMs. LLMs receive only segmented candidate text spans and product-scoped approved facts.
- **Prompt Safety & Injection Defense:** Draft copy is treated as untrusted data. External AI has no database-write tools or execution permissions. Rule predicates run independently in Python; adversarial injection attempts cannot suppress deterministic rule failures.
- **Clear Operational Boundaries:** Coverage boundaries are explicit: the ledger tracks claims approved inside Brand Rule Lab; external untracked changes require manual hash verification. Internal states (`UNLINKED`, `NOT_OBSERVED`, `REVIEW_REQUIRED`) are strictly separated and never masked as compliant passes.

---

## 8. Verification, Test Results & Live Performance

- **Automated Test Suite:**
  ```text
  ====================== 216 passed, 6 warnings in 44.95s =======================
  ```
  100% test pass rate across 216 comprehensive test cases covering schema migrations, deterministic predicates, claim extraction, role gating, drift cascades, and security constraints.
- **Live Model Verification:**
  Verified with live calls to NVIDIA NIM (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`) and Google Gemini (`gemini-3.8-flash`), demonstrating exact offset preservation, fact ranking against approved clinical evidence (F-112), and tone analysis.
- **Synthetic Evaluation Results:**
  - Claim-to-fact matching: 60/60 deterministic test cases passed.
  - Fact-drift propagation: 100% of affected linked claims flagged upon fact version bump or expiration.
  - Adversarial safety test: 30/30 prompt-injection attacks failed to suppress deterministic rule violations.

---

## 9. Team Contributions

- **Team Name:** `rohitlongesha5`
- **Team Leader:** Rohit Longesha
- **Member A (Backend & Logic — Rohit Longesha):**
  - Architecture and SQLite schema with WAL mode and migration sequence.
  - Deterministic rule engine (`term_list`, `regex`, `numeric_range`, `date_window`, `wcag_contrast`, `asset_hash`).
  - Scoped fact retrieval, AI provider integrations (NVIDIA NIM & Google Gemini fallback), and JSON-schema validation.
  - Claim ledger, stable ID hashing, and SQL drift cascade propagation.
  - Automated test suite (216 test cases) and synthetic evaluation harness.
- **Member B (UI, Workflows & QA):**
  - Streamlit user interface across Creator, Reviewer, and Fact Owner workflows.
  - Rule Readiness Audit interface, proposal review screens, and self-test runner UI.
  - Visual metadata inspection, contrast previews, and diff visualization.
  - Synthetic test corpus creation, manual walkthrough verification, and demo script preparation.

---

## 10. Three Major Improvements for Round 3 (Future Roadmap)

1. **Pre-Flight CMS & Social Scheduler Gatekeeper (Direct Publishing Integrations):**
   Build bi-directional webhooks and API connectors for Shopify, WordPress, Webflow, and social schedulers (Buffer, Hootsuite). Before any scheduled post goes live, a pre-flight webhook queries Brand Rule Lab with the content's hash. If the associated `stable_claim_id` is missing, expired, or marked `REVIEW_REQUIRED` due to an upstream clinical fact update, the release is automatically intercepted and held for re-approval.
2. **Multimodal Packaging & Video Frame Inspection:**
   Extend local ingestion beyond static images and documents to packaging renders and video creative. Integrate a lightweight local pipeline using Whisper for audio transcription and PyMuPDF/OpenCV for keyframe extraction, verifying spoken disclaimers, on-screen supers, and packaging ingredient claims against approved regulatory facts.
3. **Enterprise RBAC, Single Sign-On & Cryptographic Merkle Ledger Export:**
   Upgrade from simulated demonstration roles to enterprise-grade authentication (OIDC/SAML, Okta, Google Workspace) with strict organization-level multi-tenancy. Implement a cryptographically verifiable Merkle-tree export of the claim ledger, allowing brands to provide auditors (such as ASCI or FTC regulatory bodies) with mathematically provable timestamps and fact-lineage evidence for any published asset.
