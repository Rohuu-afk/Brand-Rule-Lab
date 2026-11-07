# Brand Rule Lab

> **Pre-publication Review with a Claim-to-Fact Ledger**  
> *CodeNexus India 2026 — Inkloom AI Hackathon (Round 2: The Build)*  
> Built by Team **rohitlongesha5** (Rohit Longesha)

Brand Rule Lab is an intelligent brand-governance platform that reviews content before publication and checks it against approved guidelines, facts, and assets. It catches off-brand tone, wrong terminology, unsupported claims, and inaccessible visuals before publishing, explains every finding, preserves creator intent, and keeps every approved claim cryptographically tied to the exact fact version that supports it.

📄 **[Brand Rule Lab Product Architecture & Walkthrough PDF](Brand_Rule_Lab_Walkthrough.pdf)** — Visual walkthrough styled with the platform's design system explaining the end-to-end workflow, claim ledger, Rule Readiness audit, live checks, and technical architecture.

🌐 **Live Deployed Application:** **[https://brand-rule-lab-ceyrdcrbd2aaxl6d5ndxs4.streamlit.app/](https://brand-rule-lab-ceyrdcrbd2aaxl6d5ndxs4.streamlit.app/)**  
*(Open in any browser without sign-in to test the live pre-publication review and claim ledger)*

📂 **Source Code Repository:** **[https://github.com/Rohuu-afk/Brand-Rule-Lab](https://github.com/Rohuu-afk/Brand-Rule-Lab)**

---

## 1. Architecture & Technology Disclosures

### System Architecture
The application is structured into layered, decoupled components:

```
┌────────────────────────────────────────────────────────┐
│                   Streamlit UI Layer                   │
│   (Creator, Reviewer, and Fact Owner Workspace Views)  │
└──────────────────────────┬─────────────────────────────┘
                           │
       ┌───────────────────┴───────────────────┐
       ▼                                       ▼
┌─────────────────────────────┐   ┌──────────────────────────────┐
│  Deterministic Rule Engine  │   │     AI Provider Service      │
│  - term_list (banned/req)   │   │  - NVIDIA NIM primary       │
│  - regex & patterns         │   │  - Gemini fallback          │
│  - numeric_range / dates    │   │  - JSON schema-validated     │
│  - wcag_contrast / asset_hash│  │  - Offline NOT_OBSERVED fallback│
└──────────────┬──────────────┘   └──────────────┬───────────────┘
               │                                 │
               └───────────────────┬─────────────┘
                                   ▼
┌────────────────────────────────────────────────────────┐
│              SQLite Storage & Audit Ledger             │
│   (WAL mode, foreign-key enforced, immutable versions) │
└────────────────────────────────────────────────────────┘
```

### Technology Stack & Libraries
* **UI Framework**: `streamlit==1.66.1`
* **AI Provider SDK**: NVIDIA NIM's OpenAI-compatible HTTPS API (stdlib HTTP client); Google Gemini uses `google-genai==2.34.0` as fallback.
* **Document Parsing**: `PyMuPDF==1.25.5` (PDF), `python-docx==1.1.2` (DOCX), `openpyxl==3.1.5` (XLSX), `pytesseract==0.3.13` & `Pillow==13.0.0` (OCR)
* **Deterministic Predicates**: WCAG 2.1 contrast is calculated locally in Python. `imagehash==4.3.2` computes pHash values at registration and compares submitted PNG/JPEG images with a selected approved asset. This comparison is for pre-cropped images only; it does not locate a logo inside a full ad. Exact SHA-256 identity comparison remains available for supported image reviews.
* **Schema Validation**: `jsonschema==4.23.0`
* **Testing & Quality**: `pytest==8.3.5`, `pytest-mock==3.14.0`
* **Database**: SQLite 3 with WAL journal mode and foreign-key enforcement
* **Retrieval and storage**: SQLite is canonical for facts, rules, versions, approvals, and review history. Eligible fact retrieval uses scoped SQLite queries; Chroma is not implemented or required.

### Security & Privacy by Design
* **Privacy Disclosure**: Before each draft review or knowledge extraction, the user must affirmatively consent. For drafts within the 30,000-character claim-extraction limit, claim extraction receives the draft text and Python-generated source candidates; each fact-ranking request receives one validated source claim and only eligible approved fact candidates (statements and evidence references); tone/messaging receives at most 8,000 draft characters plus bounded guideline and campaign context. Brand Knowledge uploads are parsed locally and their raw files are stored in SQLite. Extracted document text is sent to NVIDIA NIM or its configured Google Gemini fallback only after explicit consent and the user starts extraction. Without consent, deterministic checks still run and AI-only checks are marked `NOT_OBSERVED`. Do not submit confidential production material unless your organisation has reviewed both services' data handling and configured safeguards. AI suggestions are advisory and require human review.
* **No AI Tool Access**: The AI model is strictly restricted to text analysis; it is given no tool/function calling access and no direct database access.
* **Schema Validation**: Every AI response must conform to rigid `jsonschema` definitions (`CLAIM_EXTRACTION_SCHEMA`, `KNOWLEDGE_EXTRACTION_SCHEMA`, `FACT_RANKING_SCHEMA`, `TONE_FINDINGS_SCHEMA`) before entering the application pipeline. NVIDIA exposes response format and usage metadata where available.
* **Credential Isolation**: Provider configuration prefers environment variables and falls back to root-level Streamlit Community Cloud secrets. Credential values are never exposed in logs, UI components, or error messages.
* **Model and external services**: NVIDIA NIM is called through its OpenAI-compatible API at `https://integrate.api.nvidia.com/v1`; the safe default and tested configuration are `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`. `NVIDIA_MODEL` remains configurable alongside `NVIDIA_API_KEY` and `NVIDIA_TIMEOUT_SECONDS`. If NIM is unavailable, the adapter records the provider switch and uses Google Gemini (`GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL`, and `GEMINI_TIMEOUT_SECONDS`). Gemini retains a bounded model fallback. A provider fallback does not guarantee service availability.

---

## 2. Implemented Capabilities

| Product area | Status | Implementation Details |
|---|---|---|
| **Product & Market Scoped Rules and Facts** | `IMPLEMENTED` | Product/market rules are scoped; fact matching fails closed unless both product and market resolve, fact validity dates are enforced, and new reviews select only the latest effective approved version for each fact. Historical snapshots retain their exact prior version links. |
| **Deterministic Rule Engine** | `IMPLEMENTED` | Pure Python predicates for `term_list`, `regex`, `numeric_range`, `date_window`, `required_text`, `wcag_contrast`, `asset_hash`, and `logo_phash`. |
| **Rule Readiness Audit** | `IMPLEMENTED` | Source-backed guideline rule proposals are classified by a simulated Reviewer as CODE_CHECKABLE, AI_ASSISTED, HUMAN_JUDGEMENT, or UNCLEAR. Only source-verified, human-approved CODE_CHECKABLE rules with whitelisted, self-tested predicates enter `brand_rules` for deterministic review. The read-only Rule Tests page runs stored examples for active approved rules. AI_ASSISTED remains advisory; HUMAN_JUDGEMENT and UNCLEAR are not automatically enforced. |
| **Fictional Demonstration Data** | `IMPLEMENTED` | Full workspace for Monsoon Beauty Co. (*Monsoon Clear Skin Face Wash*), featuring synthetic scenarios: **V-04** (calm tone/absolute promises), **T-12** (prohibit BPO, suggest Benzoyl Peroxide), **F-112** (4-week substantiated trial vs 3 days), **D-02** (fixed-date active and expired offers), and GlowUp unsupported superlatives. |
| **Explainable Spans & Citations** | `IMPLEMENTED` | Python creates source-preserved sentence/clause candidates with IDs and offsets. The model selects IDs and categories only; stored claim text and offsets are sliced from the original draft. Unknown, duplicated, or malformed selections are rejected without suppressing valid selections, deterministic checks, or independent tone checks. Technical citations are available in the collapsed details view. |
| **Human-in-the-Loop Review & Role Gating** | `IMPLEMENTED` | Simulated Creator, Reviewer, and Fact Owner views; reviewers approve only submitted drafts and claim minting is atomic with review state. Role switching is not authentication. |
| **Verifiable Claim-to-Fact Ledger** | `IMPLEMENTED` | Approved claims receive a deterministic 12-character hex `stable_claim_id`, normalized text SHA-256, and append-only approval snapshots with exact `fact_version_id`s. |
| **Claim Drift Propagation** | `IMPLEMENTED` | Updating a fact version flags dependent claims as `REVIEW_REQUIRED`. Expiring a fact version marks dependent claims as `EXPIRED` via database SQL without impacting unaffected claims. |
| **Graceful Offline Fallback & Resilient AI** | `IMPLEMENTED` | NVIDIA NIM is primary with an explicitly logged Google Gemini provider fallback. Requests have bounded timeouts; transient/auth/model errors do not stop deterministic checks; AI-only checks that cannot complete are recorded as `NOT_OBSERVED` (never false `PASS`). |
| **Campaign Messaging Review** | `IMPLEMENTED` | Advisory tone and campaign-message assessment use a selected, matching campaign brief. Without a usable brief or AI consent, campaign alignment is reported as not assessed. |
| **Visual & Accessibility Baseline** | `IMPLEMENTED` | Review-time visual metadata produces WCAG 2.1 AA normal-text contrast findings, image-upload alt-text baseline findings, exact SHA-256 comparison, and pHash comparison for PNG/JPEG submissions against selected active approved same-product assets. Missing metadata is `NOT_OBSERVED`. This is not a full WCAG audit. |
| **Perceptual Asset Comparison** | `IMPLEMENTED` | pHash Hamming distance is compared with a default threshold of 10 for pre-cropped submitted images and selected approved assets. It does not locate a logo inside a full ad. |
| **Interface Appearance** | `IMPLEMENTED` | The sidebar uses the Brand Rule Lab logo and offers an optional dark theme with a restrained glass-like highlight. These presentation options do not change review behavior. |
| **Live Database Insights** | `IMPLEMENTED` | Computed dynamically from real findings, review events, and draft records—no fabricated statistics. |
| **Brand Knowledge Onboarding** | `IMPLEMENTED` | Manual brand profile and scoped proposal entry, locally preserved PDF/DOCX/TXT/CSV/image sources, per-page extraction diagnostics, and explicit consent before AI extraction. Bounded page-aware chunks use JSON mode; responses are schema-validated, receive at most one repair attempt, and are saved only when content and quotation resolve to exact source text (including PDF whitespace recovery). Rule proposals remain inactive until source-backed readiness review; fact proposals require Fact Owner approval. |
| **Welcome & First-use Guide** | `IMPLEMENTED` | Overview explains the no-login demo, links to existing pages, and calculates setup progress from saved records. Demo role switching is not authentication. |

---

## 3. Privacy & Known Limitations

* **Offline / Non-LLM Behavior**: When NVIDIA NIM is not configured or unavailable, the adapter records a switch to Google Gemini. If neither provider is available, deterministic rules run locally and AI-only checks are explicitly logged as `NOT_OBSERVED`. A missing or mismatched campaign brief is also shown as not assessed, not as successful alignment.
* **WCAG Scope**: Color contrast checking is a baseline mathematical check of supplied foreground/background hex ratios against 4.5:1 for normal text. Missing color metadata is `NOT_OBSERVED`, never `PASS`. Image alt-text is checked for non-empty minimum-length text only; neither check is a full WCAG audit or screen-reader validation.
* **OCR Dependencies**: Scanned-PDF OCR relies on `pytesseract` and an installed Tesseract binary. Selectable PDF text is preferred; pages with too little usable text are rendered for OCR. The onboarding parser attempts page orientation correction, contrast enhancement, and optional small-angle deskewing where NumPy is available. OCR confidence is an uncertainty signal, not factual verification, and OCR-derived knowledge remains a proposal for human approval. Tesseract is not bundled with the Python dependency. On Windows, install the trusted Mannheim University Library build from [its Tesseract project](https://github.com/UB-Mannheim/tesseract) using Winget:

  ```powershell
  winget install --id UB-Mannheim.TesseractOCR --exact --source winget
  ```

  Allow the normal installer/UAC and Windows Security checks; do not disable Defender or Smart App Control. The parser honors `TESSERACT_CMD` and `TESSDATA_PREFIX`; safe Windows defaults are `C:\Program Files\Tesseract-OCR\tesseract.exe` and its sibling `tessdata` directory. If installing elsewhere, set both in PowerShell before starting Streamlit:

  ```powershell
  $env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
  $env:TESSDATA_PREFIX = 'C:\Program Files\Tesseract-OCR\tessdata'
  ```

  Open a new PowerShell window and verify `where.exe tesseract`, `tesseract --version`, `tesseract --list-langs` (including `eng`), and the presence of `eng.traineddata`. Missing executable or English data produces a per-page attention warning; a Python wrapper alone does not enable OCR. Unreadable and low-confidence pages are flagged for inspection/correction. Visual-asset uploads are preserved without text OCR. OCR tests are local fixture checks, not a general OCR accuracy guarantee.
* **Knowledge Onboarding Scope**: AI entries remain proposals until individually approved. Fact proposals create approved versions only after Fact Owner approval; generic evidence proposals remain traceable but do not independently substantiate claims. Rule proposals require source-backed Rule Readiness review: CODE_CHECKABLE proposals are validated against a narrow predicate whitelist and tested before activation; AI_ASSISTED guidance is context for advisory assessment; HUMAN_JUDGEMENT and UNCLEAR items are not automatically enforced. Unclear items need a recorded human clarification. Conflicts are conservative heuristics for human review, not a complete contradiction detector. The demo roles are simulated, not secure authorisation; there is no production-grade tenant isolation.
* **AI review bounds**: Claim extraction is not sent to an AI provider for drafts over 30,000 characters; deterministic checks still inspect the full draft. Tone analysis is capped at the first 8,000 characters and marked `PARTIAL` with a visible warning. Fact-ranking context is bounded to 4,000 characters. These limits are not a claim that AI reviewed omitted text.
* **Visual Checks**: SHA-256 detects exact image file identity; pHash compares image similarity using a Hamming-distance threshold. Both compare image-to-image for a selected approved asset, and pHash is limited to pre-cropped images; it does not locate a logo inside a full ad. No geometric, safe-zone, or full visual-forensics verification is claimed.
* **Format Boundaries**: Brand Knowledge supports PDF, DOCX, TXT, CSV, XLSX, PNG and JPEG sources. New Review uploads support PDF, DOCX, TXT, PNG and JPEG. Does not parse video streams, legacy `.xls` workbooks or presentation slides (.pptx).

---

## 4. Automated Tests

Run the test suite with `.venv\Scripts\python.exe -m pytest -q` on Windows or `python -m pytest -q` elsewhere. Each test uses a fresh temporary SQLite database and temporary uploads directory. Provider behavior is mocked; automated tests do not make NVIDIA or Gemini API calls.

The captured full-suite output in [`submission/evidence/pytest-2026-11-08.txt`](submission/evidence/pytest-2026-11-08.txt) records the latest 246-test run. The PNG in the same folder is a historical screenshot of the earlier 237-test run and is retained as such; it is not evidence for the latest count.

The synthetic, offline evaluation runner is `.venv\Scripts\python.exe evaluation\run_synthetic_evaluation.py`. It exercises 60 synthetic deterministic relationship cases, 10 generated asset registrations, and 30 prompt-injection drafts. Its results are internal fixture checks only: they are not independent validation, AI top-3 accuracy, production timings, or proof of real-model prompt-injection robustness.

AI top-3 accuracy remains unmeasured (`null`). Synthetic fixture outcomes are not independent validation, real-user testing, live-model results, or production performance measurements.

The automated provider tests mock NVIDIA request/authentication handling, NVIDIA-to-Gemini fallback, and Gemini model fallback. They do not validate live connectivity, model access, or real-model response quality.

**Optional manual live NVIDIA NIM connection test (one request; requires an NVIDIA key and may consume service quota):** configure `NVIDIA_API_KEY` in the local environment or `.env` after reviewing your organisation's data-handling requirements. This calls the primary directly and does not print the key or response:

```powershell
& .\.venv\Scripts\python.exe -c "from ai.nvidia_provider import NvidiaNimProvider; p=NvidiaNimProvider(); p.test_connection(); print('NVIDIA NIM connection succeeded; model=' + str(p.last_model_used))"
```

Do not run this as part of automated tests. A successful connection only verifies authentication and a response from the configured model; it does not establish claim-extraction quality or review accuracy.

**Optional manual live Gemini connection test (one request; requires a Gemini key and may consume service quota):**

```powershell
& .\.venv\Scripts\python.exe -c "from ai.gemini_provider import GeminiProvider; p=GeminiProvider(); p.test_connection(); print('Google Gemini connection succeeded; model=' + str(p.last_model_used))"
```

On Streamlit Community Cloud, configure `AI_PROVIDER`, `NVIDIA_API_KEY`, `NVIDIA_MODEL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, and optionally `GEMINI_FALLBACK_MODEL` in the app's **Settings → Secrets** as root-level TOML entries. Environment variables take precedence; Streamlit secrets are the fallback. No committed `.env` or `secrets.toml` is required. The Overview page's **AI provider connection diagnostics** panel tests NVIDIA and Gemini independently with a fixed, non-draft prompt. Do not copy secret values into the repository, screenshots, logs, or issue reports.

## 5. Synthetic Judge Database and Walkthrough

The source archive excludes operational databases and uploads. In a clean checkout, `.venv\Scripts\python.exe db\create_judge_demo.py` creates the default `data\judge_demo_round2_20261106.db`; the builder refuses to overwrite an existing file and refuses protected working/demo database names. For another fresh location, set `JUDGE_DEMO_DATABASE_PATH` to a new path; the builder creates a sibling `<database-stem>_uploads` directory and prints both resulting paths. The builder creates fictional seed content, two synthetic approved claim snapshots, one clearly labelled synthetic approved asset, and an unapproved flawed draft with deterministic V-04, T-12, F-112, and superlative findings. `SyntheticDemoProvider` is explicitly labelled `SYNTHETIC MOCK`; its output is not NVIDIA or Gemini output. No supplied database is used or modified.

To build and run the demo in PowerShell from the project root (choose a new path if this database already exists):

```powershell
$env:JUDGE_DEMO_DATABASE_PATH = (Join-Path $PWD "data\judge_demo_round2_release.db")
& .\.venv\Scripts\python.exe db\create_judge_demo.py
$env:DATABASE_PATH = $env:JUDGE_DEMO_DATABASE_PATH
$env:UPLOADS_PATH = (Join-Path (Split-Path $env:DATABASE_PATH) (([System.IO.Path]::GetFileNameWithoutExtension($env:DATABASE_PATH)) + "_uploads"))
.\.venv\Scripts\streamlit.exe run app.py
```

Suggested 3–5 minute demonstration:

1. Select the synthetic Monsoon draft and inspect its four-week claim, cited F-112 fact version, and synthetic approval history.
2. Open the claim ledger and compare the Monsoon claim marked `REVIEW_REQUIRED` with the unrelated GlowUp claim still `CURRENT`.
3. Review the fact history to see the new version that triggered deterministic propagation; no model call or web crawl was used for this status change.
4. Inspect the deliberately flawed synthetic draft and its V-04, T-12, F-112, and superlative findings. Inspect the generated synthetic approved image and its exact SHA-256 identity result; it is not a real company logo.

**Latest automated validation:** `.venv\Scripts\python.exe -m pytest -q` completed with **246 passed, 0 failed, 6 PyMuPDF/SWIG deprecation warnings**. Tests use isolated SQLite databases and mocked provider responses; this run made no NVIDIA or Gemini API calls. An image-review regression verifies pHash still runs while raw image bytes are excluded from saved JSON metadata. An OCR-banner integration regression verifies that image-extracted text enters claim extraction and deterministic review using mocked OCR and AI responses. A separate disposable check with local Tesseract and mocked AI verified real banner OCR text was submitted to claim extraction and saved as the reviewed claim; it used no external AI or durable database. In a separate disposable browser workflow, a Reviewer classified and approved a source-backed synthetic CODE_CHECKABLE rule with a whitelisted `term_list` predicate and passing/failing examples. The active approved rule generated a deterministic `FAIL` in content review without external-AI consent. After restarting the app against the same disposable database, its active state and history row remained present and the health endpoint returned HTTP 200. This verifies the Rule Readiness UI-to-review path, not secure authentication or live AI quality. The application is deployed and publicly accessible on Streamlit Community Cloud at https://brand-rule-lab-ceyrdcrbd2aaxl6d5ndxs4.streamlit.app/.

### Privacy wording for the Round 1 deck (slides 8 and 12)

The deck is not present in this workspace, so its slide text could not be edited or verified. When updating slides 8 and 12, use these statements and remove any wording that says the model receives “claim text and approved fact fields only”:

> **AI provider data flow:** NVIDIA NIM is the primary provider and Google Gemini is used when NIM is unavailable. Claim extraction and tone assessment receive the full extracted draft text; fact ranking receives an individual claim and eligible approved fact candidates (statements and evidence references). Uploaded PDF, DOCX, TXT, PNG, and JPEG file bytes are parsed locally and are not sent to either provider. The judge demo uses synthetic material. Do not submit confidential production material unless your organisation has reviewed both services' data handling and configured appropriate safeguards.

> **Human control and evidence:** AI output is schema-validated and advisory; deterministic brand rules and human review remain in control. Synthetic mock-provider output is not live model output. New reviews use only the latest effective approved version of each fact; immutable historical claim snapshots retain the exact older fact versions that supported approval. Subsequent fact changes flag only claims linked to those versions.

---

## 6. User-Testing Evidence

No structured 3–5 participant usability study is documented in this repository. Developer walkthroughs and automated tests are not user-testing evidence. Record anonymized participant feedback, observed friction, and changes made after testing before claiming this study is complete.

---

## 7. Three Named Round 3 Improvements

1. **Human-Authorized Claim Remediation & CMS Integrations**:
   * Add reviewer-authorized claim remediation and controlled CMS integrations (Shopify, WordPress, Contentful). Human approval remains required before changes are published.
2. **Multimodal Packaging & Video Frame Verification**:
   * Extend visual checks to video assets and physical packaging artwork, decomposing video into keyframes to detect ephemeral claims, disclaimer font-size compliance, and brand logo safe-zones.
3. **Enterprise Role-Based Access Control (RBAC) & Verifiable Audit Export**:
   * Replace simulated demo roles with enterprise SAML/OAuth SSO, granular role permissions (Legal, Regulatory, Marketing, Brand Lead), and cryptographic tamper-evident PDF audit exports for regulatory submissions.

---

## 8. Running the Application

### 1. Prerequisites
* Python 3.10+
* Tesseract OCR is an optional system dependency for scanned-PDF and image text extraction.

### 2. Windows (PowerShell)
```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```
Edit `.env` locally to add API keys only if you intend to use AI. The app creates and migrates the configured SQLite database and seeds fictional demo data on first startup. Default paths are `data/brand_rule_lab.db` and `data/uploads`; override them using `DATABASE_PATH` and `UPLOADS_PATH`.

### 3. Linux/macOS
```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```
Edit `.env` locally if needed. Install Tesseract and its English language data with the operating system's package manager to enable OCR.

### 4. Database Setup & Seed
`DATABASE_PATH` selects the SQLite database; `UPLOADS_PATH` selects the upload directory. The app applies schema migrations and seeds demo data only when the selected database is empty. To populate the configured database manually:
```bash
python db/seed.py
```
On Windows, run `.venv\Scripts\python.exe db\seed.py`. Never point the seed command at a database containing user data with `force=True`.

### 5. Run Automated Tests
```bash
python -m pytest -q
```
On Windows, run `.venv\Scripts\python.exe -m pytest -q`.

### 6. Launch Application
```bash
streamlit run app.py
```
On Windows, run `.\.venv\Scripts\streamlit.exe run app.py`. Open `http://localhost:8501`. The sidebar role selector switches between Creator, Reviewer, and Fact Owner demo roles; it is not authentication.

---

## 9. Deployment: Docker & Render Configuration

### Docker Deployment
The Dockerfile installs the Python dependencies and Tesseract OCR, binds Streamlit to `0.0.0.0`, honors the platform-provided `PORT`, and exposes Streamlit's `/_stcore/health` endpoint as a Docker health check. It includes no credentials. Build and run locally:
```bash
docker build -t brand-rule-lab .
docker run --rm -p 8501:8501 -e DATABASE_PATH=/app/data/brand_rule_lab.db -e UPLOADS_PATH=/app/data/uploads -v brl-data:/app/data -e NVIDIA_API_KEY -e GEMINI_API_KEY brand-rule-lab
```
Set the provider keys in the host environment before running this command; never put real keys in the Dockerfile or image. Omitting either AI key leaves deterministic checks usable; AI-only operations will not complete.

### Render Deployment Instructions
To deploy Brand Rule Lab on Render as a Web Service:
1. Connect this repository to Render.
2. Select **Docker** as the environment (Render detects the root `Dockerfile`).
3. Set environment variables in the Render Dashboard:
   - `NVIDIA_API_KEY`: NVIDIA NIM API key.
   - `NVIDIA_MODEL`: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` or another model enabled for your NIM account.
   - `GEMINI_API_KEY`: Google Gemini fallback API key.
   - `GEMINI_MODEL`: `gemini-3.8-flash` (or alternative Gemini model name).
   - `DATABASE_PATH`: `/app/data/brand_rule_lab.db`
   - `UPLOADS_PATH`: `/app/data/uploads`
   - `PORT`: supplied by Render; the Dockerfile uses it automatically.
   - Add `NVIDIA_API_KEY` and `GEMINI_API_KEY` only through Render's secret environment-variable settings if using AI. `GEMINI_FALLBACK_MODEL` is optional.
4. **SQLite and uploads persistence**: Render's container filesystem is ephemeral unless a persistent disk is attached. Without a disk, both the database and uploaded files may be lost on redeploy or restart. For a persistent single-instance demonstration, attach a disk mounted at `/app/data` and keep both paths under that mount. This SQLite MVP is not configured for multi-instance/horizontal scaling.
5. **Safe startup**: The container runs the idempotent `seed(force=False)` path before Streamlit starts. It creates demo data only when the configured database is empty and never scans or seeds sibling database files. Database files and SQLite WAL sidecars are excluded from the Docker build context.

The role selector is a demonstration-only permission simulation, not production authentication. AI analysis is advisory, and a human must make approval decisions. The included demonstration data is fictional.
