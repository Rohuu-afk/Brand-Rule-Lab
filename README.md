# Brand Rule Lab

> **Pre-publication Review with a Claim-to-Fact Ledger**  
> *CodeNexus India 2026 — Round 3 submission*
> Built by Team **rohitlongesha5** (Rohit Longesha)

> **Event timing:** The Round 3 submission form says submissions close at 11:59 pm IST on 13 November 2026, with no late window. Confirm that every required link is publicly accessible and submit before the deadline.

Brand Rule Lab is an intelligent brand-governance platform that reviews content before publication and checks it against approved guidelines, facts, and assets. It catches off-brand tone, wrong terminology, unsupported claims, and inaccessible visuals before publishing, explains every finding, preserves creator intent, and keeps every approved claim cryptographically tied to the exact fact version that supports it.

📄 **[Brand Rule Lab Product Architecture & Walkthrough PDF](Brand_Rule_Lab_Walkthrough.pdf)** — Visual walkthrough styled with the platform's design system explaining the end-to-end workflow, claim ledger, Rule Readiness audit, live checks, and technical architecture.

📑 **[Round 3 Submission Draft](submission/Brand_Rule_Lab_Round3_Submission.pdf)** — Concise product summary, commitment-to-implementation map, validation evidence, and explicit deployment and integration blockers.

🌐 **Previous Round 2 deployment:** [Open the Streamlit app](https://brand-rule-lab-ceyrdcrbd2aaxl6d5ndxs4.streamlit.app/). On 2026-11-13 it displayed Streamlit's sleeping-app screen. It is not verified as the current worktree or a judge-ready Round 3 deployment. It was left asleep because waking it could run startup migrations against its configured database.

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
* **UI Framework**: `streamlit[auth]==1.66.1` (the auth extra provides Authlib for OIDC login)
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

### Local demo accounts

The database seed creates demo domain data only; it does not create user accounts
or prompt for passwords during Streamlit startup. Provision synthetic role
accounts explicitly from the repository root:

```powershell
python -m db.provision_demo_accounts
```

The command securely prompts for each missing password twice. For non-interactive
setup, provide passwords through environment variables:

```powershell
$env:DEMO_CREATOR_PASSWORD = "<choose-a-local-password>"
$env:DEMO_REVIEWER_PASSWORD = "<choose-a-local-password>"
$env:DEMO_FACT_OWNER_PASSWORD = "<choose-a-local-password>"
$env:DEMO_OWNER_PASSWORD = "<choose-a-local-password>"
python -m db.provision_demo_accounts --non-interactive
```

Provisioning never prints passwords. Existing demo accounts are reported as
`existing`; their passwords, account roles, and active status are not changed.

Provisioning also creates the `Brand Rule Lab Demo` workspace and assigns
`demo_creator`, `demo_reviewer`, `demo_fact_owner`, and `demo_owner` the
matching Creator, Reviewer, Fact Owner, and Brand Owner memberships.
Memberships, not the sidebar or a browser value, determine access to the
workspace.

The public **Open demo** role selector is intentionally opt-in. Enable
`BRL_DEMO_ACCESS=1` only on a separate judge-demo app instance whose
`DATABASE_PATH` points to an isolated database with `judge_demo` in its file
name. Configure the four `DEMO_*_PASSWORD` values on that instance; only this
synthetic isolated workspace displays those credentials. Never enable public
demo access on a database containing real workspaces or accounts.

For Streamlit Community Cloud, set the following as top-level app secrets on a
separate judge-demo deployment; the app reads these settings at startup and
creates missing synthetic demo accounts in that isolated database:

```toml
DATABASE_PATH = "data/judge_demo_round3.db"
BRL_DEMO_ACCESS = "1"
BRL_REQUIRE_SSO = "0"
DEMO_CREATOR_PASSWORD = "<unique synthetic password>"
DEMO_REVIEWER_PASSWORD = "<unique synthetic password>"
DEMO_FACT_OWNER_PASSWORD = "<unique synthetic password>"
DEMO_OWNER_PASSWORD = "<unique synthetic password>"
```

The configured demo usernames and passwords are intentionally displayed on
the public landing page, so use unique, disposable credentials and never reuse
them anywhere else. Do not put credentials in the repository or submission
text. For email verification and team invitations, also configure
`RESEND_API_KEY`, `EMAIL_FROM`, and `PUBLIC_APP_URL` as app secrets. Email
sign-in uses the verified address attached to the account; without a working
mail provider, users cannot complete email verification or receive invitations.

### Brand workspaces and invitations

After signing in, a user with no memberships sees only the brand onboarding
screen. Creating a brand creates a Brand Owner membership. The public page
offers a first-owner registration only while there are no accounts or
workspaces; the account is inactive until its 24-hour email verification link
is redeemed. Team members can register only from a one-time, seven-day
invitation link, which verifies the invited email by possession of the link.
Owners can invite an email address and choose Creator, Reviewer, or Fact Owner.
Brand Owners can create additional separate brand workspaces from the **Create
Brand** item in the workspace navigation.
Brand Owners can explicitly transfer ownership to an active, verified existing
member; the current owner remains a member under a chosen non-owner role, and
the transfer is atomic and recorded in that workspace's audit chain.
Configure `RESEND_API_KEY`, `EMAIL_FROM`, and `PUBLIC_APP_URL` to deliver
verification, password-reset, and invitation emails. The app also reads the
`[resend]` section's `api_key` and `from_email` values from Streamlit secrets.
For local Streamlit secrets, run the app from the directory containing the
configured `.streamlit/secrets.toml`, or place the same sections in the
project's ignored `.streamlit/secrets.toml`. Do not commit that file. Without
Resend, no token is shown to visitors; an administrator can enable
`ALLOW_DEV_AUTH_LINKS=1` for local setup or securely share an invitation token.

Google sign-in uses Streamlit's `[auth]` secrets block and configured
`server_metadata_url`. A new verified Google identity can create its own brand
workspace and become its Brand Owner, even after other accounts and demo
workspaces exist. It can also register against a matching active invitation.
Password-based team signup remains invitation-only. An identity cannot gain a
role without a brand membership. Enable `BRL_REQUIRE_SSO=1` to disable password sign-in, password
recovery, and password-based invitations; the default `0` keeps those paths
available for local/demo setups. Configure and verify the identity provider
before enabling SSO-only mode. Enabling it also invalidates existing
password-authenticated sessions; OIDC sessions remain valid. Sessions are
server-side and expire after eight hours. Configured OIDC identity cookies allow a new Streamlit
session after a browser refresh. The separate workspace root contains a
`.streamlit/secrets.toml` outside this nested repository. Streamlit 1.66.1
successfully loaded its `[auth]` and `[resend]` keys when started from that
workspace root; the registered local callback is
`http://localhost:8502/oauth2callback`. The provider redirect was exercised
through to its account-entry page, but no account was selected and authentication
was not completed. Email delivery has not been exercised. Launch from the workspace root
with port 8502, or place a deployment-specific secrets file in the app's
working directory. With no `PUBLIC_APP_URL`, verification and invitation links
use the base URL from the configured OIDC callback.

For OIDC, create `.streamlit/secrets.toml` from your deployment's secret
manager and set the registered redirect URL to `<app URL>/oauth2callback`.
For local development, the configured OAuth client uses
`http://localhost:8502/oauth2callback`. From the workspace root, start the
nested app with `Brand-Rule-Lab\.venv\Scripts\streamlit.exe run
Brand-Rule-Lab\app.py --server.port 8502`. For another environment, use its
registered URL and matching server port:

```toml
[auth]
redirect_uri = "<app URL>/oauth2callback"
cookie_secret = "<generate a long random secret>"
client_id = "<OIDC client ID>"
client_secret = "<OIDC client secret>"
server_metadata_url = "<identity provider's OpenID Connect discovery URL>"
```

After confirming OIDC sign-in and invitation onboarding work, set
`BRL_REQUIRE_SSO=1` in the deployment environment. Password recovery and
password registration remain available only when this flag is off.

### Round 3 submission status

The authenticated workflow now includes Creator, Reviewer, and Fact Owner
accounts, server-side session validation, self-approval prevention, evidence
and `NOT_OBSERVED` approval blockers, claim snapshots, and fact drift tracking.
Review the evidence-backed scope and limitations in
[docs/ROUND3_SUBMISSION.md](docs/ROUND3_SUBMISSION.md), and use the
[participant-entry worksheet](docs/ROUND3_PARTICIPANT_ENTRY_DRAFTS.md) to
record each contributor's own role and work without inventing attribution.

The **Audit & Exports** page provides a tamper-evident JSON chain and a local
PDF report. SQLite blocks ordinary update and delete statements on audit rows;
exports fail closed if the stored chain does not verify. JSON exports can be
verified offline using the service verifier or the **Verify an exported audit
JSON** control on the Audit & Exports page. The PDF report paginates and
includes each record's previous and event hashes. This does not protect against a database-file or schema
administrator who can bypass SQLite triggers, and it is not a trusted digital
signature, external timestamp, or regulatory certification. The **CMS Draft Integrations** page provides
deterministic local mock adapters and optional live draft creation for
WordPress, Shopify, and Contentful. All live adapters reject redirects, verify
the provider confirms an unpublished draft, and expose no publish operation.
Configure credentials in the environment, local `.env`, or deployment's
root-level Streamlit secrets using the `WORDPRESS_*`, `SHOPIFY_*`, and
`CONTENTFUL_*` names in `.env.example`; environment values take precedence.
Shopify requires a store `*.myshopify.com` domain and an Admin API token with
product-write access; it creates a product with status `DRAFT`. Contentful
requires a Management API token, a content type with the configured title and
content field IDs, and the selected locale; entries remain unpublished. The
UI never displays credentials. Provider HTTP behavior is covered by mocked
tests; no live CMS account has been used to verify these connections. A human
must review and publish any created content in its CMS.

Short MP4, MOV, and WebM clips can be reviewed locally using a bounded sample
of up to 12 keyframes and OCR. Uploads are limited by size and duration; sampled
timestamps and extraction warnings are retained, and unsampled moments are
marked not observed. This does not verify every frame, motion, logo placement,
or physical packaging compliance.

The custom username/password flow does not restore authentication after a full
browser refresh because Streamlit does not provide a safe HttpOnly cookie setter
for an application-managed token. Configured OIDC uses Streamlit's identity
cookie. With `BRL_REQUIRE_SSO=1`, password sign-in, recovery, and account
registration are blocked; verify OIDC and invitation onboarding before enabling
the flag.

| Product area | Status | Implementation Details |
|---|---|---|
| **Product & Market Scoped Rules and Facts** | `IMPLEMENTED` | Product/market rules are scoped; fact matching fails closed unless both product and market resolve, fact validity dates are enforced, and new reviews select only the latest effective approved version for each fact. Historical snapshots retain their exact prior version links. |
| **Deterministic Rule Engine** | `IMPLEMENTED` | Pure Python predicates for `term_list`, `regex`, `numeric_range`, `date_window`, `required_text`, `wcag_contrast`, `asset_hash`, and `logo_phash`. |
| **Rule Readiness Audit** | `IMPLEMENTED` | Source-backed guideline rule proposals are classified by a simulated Reviewer as CODE_CHECKABLE, AI_ASSISTED, HUMAN_JUDGEMENT, or UNCLEAR. Only source-verified, human-approved CODE_CHECKABLE rules with whitelisted, self-tested predicates enter `brand_rules` for deterministic review. The read-only Rule Tests page runs stored examples for active approved rules. AI_ASSISTED remains advisory; HUMAN_JUDGEMENT and UNCLEAR are not automatically enforced. |
| **Fictional Demonstration Data** | `IMPLEMENTED` | Full workspace for Monsoon Beauty Co. (*Monsoon Clear Skin Face Wash*), featuring synthetic scenarios: **V-04** (calm tone/absolute promises), **T-12** (prohibit BPO, suggest Benzoyl Peroxide), **F-112** (4-week substantiated trial vs 3 days), **D-02** (fixed-date active and expired offers), and GlowUp unsupported superlatives. |
| **Explainable Spans & Citations** | `IMPLEMENTED` | Python creates source-preserved sentence/clause candidates with IDs and offsets. The model selects IDs and categories only; stored claim text and offsets are sliced from the original draft. Unknown, duplicated, or malformed selections are rejected without suppressing valid selections, deterministic checks, or independent tone checks. Technical citations are available in the collapsed details view. |
| **Human-in-the-Loop Review & Role Gating** | `IMPLEMENTED` | Authenticated Creator, Reviewer, and Fact Owner views; reviewers approve only submitted drafts and claim minting is atomic with review state. Role checks are enforced server-side. |
| **Verifiable Claim-to-Fact Ledger** | `IMPLEMENTED` | Approved claims receive a deterministic 12-character hex `stable_claim_id`, normalized text SHA-256, and append-only approval snapshots with exact `fact_version_id`s. |
| **Claim Drift Propagation** | `IMPLEMENTED` | Updating a fact version flags dependent claims as `REVIEW_REQUIRED`. Expiring a fact version marks dependent claims as `EXPIRED` via database SQL without impacting unaffected claims. |
| **Graceful Offline Fallback & Resilient AI** | `IMPLEMENTED` | NVIDIA NIM is primary with an explicitly logged Google Gemini provider fallback. Requests have bounded timeouts; transient/auth/model errors do not stop deterministic checks; AI-only checks that cannot complete are recorded as `NOT_OBSERVED` (never false `PASS`). |
| **Campaign Messaging Review** | `IMPLEMENTED` | Advisory tone and campaign-message assessment use a selected, matching campaign brief. Without a usable brief or AI consent, campaign alignment is reported as not assessed. |
| **Visual & Accessibility Baseline** | `IMPLEMENTED` | Review-time visual metadata produces WCAG 2.1 AA normal-text contrast findings, image-upload alt-text baseline findings, exact SHA-256 comparison, and pHash comparison for PNG/JPEG submissions against selected active approved same-product assets. Missing metadata is `NOT_OBSERVED`. This is not a full WCAG audit. |
| **Perceptual Asset Comparison** | `IMPLEMENTED` | pHash Hamming distance is compared with a default threshold of 10 for pre-cropped submitted images and selected approved assets. It does not locate a logo inside a full ad. |
| **Guided Review and Results** | `IMPLEMENTED` | New Review uses four steps for content type, brand-scoped context, content preview, and check settings. AI consent defaults off. Results show submitted copy, highlight only verified claim offsets, and surface linked approved evidence and its relationship. |
| **Interface Appearance** | `IMPLEMENTED` | Dark-first glass and neumorphic surfaces are used across the authenticated workspace. These presentation choices do not change review behavior. |
| **Live Database Insights** | `IMPLEMENTED` | Computed dynamically from real findings, review events, and draft records—no fabricated statistics. |
| **Brand Knowledge Onboarding** | `IMPLEMENTED` | Manual brand profile and scoped proposal entry, locally preserved PDF/DOCX/TXT/CSV/image sources, per-page extraction diagnostics, and explicit consent before AI extraction. Bounded page-aware chunks use JSON mode; responses are schema-validated, receive at most one repair attempt, and are saved only when content and quotation resolve to exact source text (including PDF whitespace recovery). Rule proposals remain inactive until source-backed readiness review; fact proposals require Fact Owner approval. |
| **Welcome & First-use Guide** | `IMPLEMENTED` | Overview explains the authenticated workspace, links to permitted pages, and calculates setup progress from saved records. |
| **Database Backup & Recovery** | `IMPLEMENTED` | SQLite's online backup API snapshots committed database state, packages the configured upload tree, verifies SHA-256 hashes plus SQLite integrity and foreign keys, and restores only to new paths without overwriting existing files. |

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

  On Debian-based Streamlit deployments, `packages.txt` installs both
  `tesseract-ocr` and `tesseract-ocr-eng`. Debian places the executable at
  `/usr/bin/tesseract` and versioned language data under
  `/usr/share/tesseract-ocr/<version>/tessdata`. The parser searches these
  package locations, checks that `eng.traineddata` exists, and confirms that
  `tesseract --list-langs` reports `eng`. Do not set `TESSDATA_PREFIX` to
  `/usr/bin/tessdata`; if overriding it on Linux, point it to the directory
  containing `tessdata`. On Windows, the installed Tesseract build may require
  the `tessdata` directory itself; the parser tests both supported forms.
* **Knowledge Onboarding Scope**: AI entries remain proposals until individually approved. Fact proposals create approved versions only after Fact Owner approval; generic evidence proposals remain traceable but do not independently substantiate claims. Rule proposals require source-backed Rule Readiness review: CODE_CHECKABLE proposals are validated against a narrow predicate whitelist and tested before activation; AI_ASSISTED guidance is context for advisory assessment; HUMAN_JUDGEMENT and UNCLEAR items are not automatically enforced. Unclear items need a recorded human clarification. Conflicts are conservative heuristics for human review, not a complete contradiction detector. Workspaces have brand-scoped memberships and queries. Legacy role-string authorization is gated to explicitly opted-in pytest runs; optional-principal service APIs still need a complete audit before enterprise deployment.
* **AI review bounds**: Claim extraction is not sent to an AI provider for drafts over 30,000 characters; deterministic checks still inspect the full draft. Tone analysis is capped at the first 8,000 characters and marked `PARTIAL` with a visible warning. Fact-ranking context is bounded to 4,000 characters. These limits are not a claim that AI reviewed omitted text.
* **Visual Checks**: SHA-256 detects exact image file identity; pHash compares image similarity using a Hamming-distance threshold. Approved packaging references can also be selected for OCR token comparison against uploaded package artwork. OCR differences are reviewer prompts and can be affected by image quality. The checks do not locate a logo inside a full ad or verify geometry, safe zones, layout, colors, or the entire visual design.
* **Format Boundaries**: Brand Knowledge supports PDF, DOCX, TXT, CSV, XLSX, PNG and JPEG sources. New Review supports PDF, DOCX, TXT, PNG, JPEG, MP4, MOV and WebM; video is bounded to 100 MB, 120 seconds and at most 12 sampled OCR frames by default. It does not parse legacy `.xls` workbooks or presentation slides (.pptx), and unsampled video is not verified.

---

## 4. Automated Tests

Run the test suite with `.venv\Scripts\python.exe -m pytest -q` on Windows or `python -m pytest -q` elsewhere. Each test uses a fresh temporary SQLite database and temporary uploads directory. Provider behavior is mocked; automated tests do not make NVIDIA or Gemini API calls.

The captured output in [`submission/evidence/pytest-2026-11-08.txt`](submission/evidence/pytest-2026-11-08.txt) is historical evidence for the 246-test run on 2026-11-08. The PNG in the same folder is a screenshot of the earlier 237-test run; neither artifact represents the current suite. The latest 349-test result is recorded above for the 2026-11-13 worktree.

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

To build and run an isolated judge demo in PowerShell from the workspace root
(the directory containing Brand-Rule-Lab and the configured .streamlit folder):

```powershell
$env:BRL_PROJECT_ROOT = Join-Path $PWD "Brand-Rule-Lab"
$env:PYTHONPATH = $env:BRL_PROJECT_ROOT
$env:JUDGE_DEMO_DATABASE_PATH = Join-Path $env:TEMP "brand_rule_lab_round3_judge_demo.sqlite"
$env:DATABASE_PATH = $env:JUDGE_DEMO_DATABASE_PATH
$env:UPLOADS_PATH = (Join-Path (Split-Path $env:DATABASE_PATH) (([System.IO.Path]::GetFileNameWithoutExtension($env:DATABASE_PATH)) + "_uploads"))
& "$env:BRL_PROJECT_ROOT\.venv\Scripts\python.exe" "$env:BRL_PROJECT_ROOT\db\create_judge_demo.py"
$env:DEMO_CREATOR_PASSWORD = "<synthetic-demo-password>"
$env:DEMO_REVIEWER_PASSWORD = "<synthetic-demo-password>"
$env:DEMO_FACT_OWNER_PASSWORD = "<synthetic-demo-password>"
$env:DEMO_OWNER_PASSWORD = "<synthetic-demo-password>"
& "$env:BRL_PROJECT_ROOT\.venv\Scripts\python.exe" -m db.provision_demo_accounts
$env:BRL_DEMO_ACCESS = "1"
& "$env:BRL_PROJECT_ROOT\.venv\Scripts\streamlit.exe" run "$env:BRL_PROJECT_ROOT\app.py" --server.port 8502
```

The builder refuses to overwrite an existing database. Choose a new temp path
if you have already built this demo. The landing page displays configured demo
credentials and opens the selected role directly. The Creator and Reviewer use
separate memberships for the content and human-approval steps. The app runs on
port 8502 to match the local Google OIDC callback.

Suggested 3–5 minute demonstration:

1. As demo_creator, run deterministic checks on the intentionally flawed Monsoon draft with AI consent off; show V-04, T-12, F-112, and the superlative finding.
2. Correct one finding with a reason and recheck to show the preserved original and new draft version.
3. Submit the flawed draft and show demo_reviewer that unresolved high-priority findings block approval.
4. Review and approve the clean draft prepared during setup, explicitly acknowledging NOT_OBSERVED checks; finish in Audit & Exports to show the recorded decision trail.

**Latest automated validation:** `.venv\Scripts\python.exe -m pytest -q` completed with **369 passed, 0 failed, 6 PyMuPDF/SWIG deprecation warnings** on 2026-11-13. Tests used isolated SQLite databases and mocked provider responses; this run made no NVIDIA, Gemini, Resend, OIDC, or CMS provider calls. Separate disposable migration checks validated migration 14 from both a copied v11 database and a fresh database, preserving all 73 baseline rows with clean SQLite integrity and foreign-key checks. Migrations 15 and 16 are covered for audit immutability and session authentication-method tracking, including invalidation of existing password sessions in SSO-only mode. Backup/restore tests verify preserved records and uploaded bytes, refusal to overwrite, and rejection of tampered or traversal paths. Browser acceptance on a disposable database verified sign-in, product-scoped deterministic findings with AI consent off, correction history, authenticated recheck, submission, reviewer blocking on unresolved high-priority findings, and approval after acknowledging NOT_OBSERVED checks. Public landing, opt-in isolated demo access, role-aware navigation, dashboard metrics, sign-in, and the guided New Review flow have focused UI coverage. A disposable browser pass visually checked the New Review content-type cards and the two-row Results metrics. A previous disposable browser run verified invitation acceptance into the correct brand. The configured database was not used. These checks do not certify live provider behavior or live AI quality.

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
   * Extend visual checks to locate logos inside full creatives, verify layout and safe-zones, and analyze motion and disclaimer size across video frames.
3. **Enterprise Role-Based Access Control (RBAC) & Verifiable Audit Export**:
   * Implemented in part: OIDC SSO, SSO-only mode, brand-scoped Owner/Creator/Reviewer/Fact Owner permissions, membership administration, and tamper-evident JSON/PDF audit exports. SAML, Legal/Regulatory/Marketing role profiles, external signatures/timestamps, independent security review, and regulatory certification remain future work.

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

### 5. Database backup and recovery

Backups contain the configured SQLite database and upload files. They may
contain confidential brand data, so store them in an access-controlled location.
The backup command reads the selected database and writes a new archive; it
refuses to overwrite an existing destination:

```powershell
.\.venv\Scripts\python.exe -m db.backup_database --destination 'D:\Backups\brand-rule-lab-2026-11-13.zip'
```

To verify and restore an archive, choose two paths that do not already exist.
Restore checks the archive manifest and file hashes, then checks SQLite
integrity and foreign keys before publishing the restored files:

```powershell
.\.venv\Scripts\python.exe -m db.restore_database `
  --backup 'D:\Backups\brand-rule-lab-2026-11-13.zip' `
  --database 'D:\BrandRuleLab-Restore\brand_rule_lab.db' `
  --uploads 'D:\BrandRuleLab-Restore\uploads'
$env:DATABASE_PATH = 'D:\BrandRuleLab-Restore\brand_rule_lab.db'
$env:UPLOADS_PATH = 'D:\BrandRuleLab-Restore\uploads'
```

The restored database is not migrated or seeded by the restore command. Point
the application at the new paths only after reviewing the output and intended
recovery procedure. Hash checks detect accidental corruption; the archive is
not digitally signed and must be protected against tampering.

### 6. Run Automated Tests
```bash
python -m pytest -q
```
On Windows, run `.venv\Scripts\python.exe -m pytest -q`.

### 7. Launch Application
```bash
streamlit run app.py
```
On Windows, run `.\.venv\Scripts\streamlit.exe run app.py`. Open `http://localhost:8501` and sign in with a provisioned demo account. Roles come from the authenticated server-side principal; there is no client role selector.

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



# Email for invitation testing 
onboarding@resend.dev 
(we dont have .com domain yet to verify on resend.)

Authentication uses provisioned local accounts with server-side session and
role validation. Brand membership migration 12 is additive and leaves
historical records available; legacy records without a brand association are
not silently assigned to a new tenant. AI analysis is advisory, and a human
must make approval decisions. The included demonstration data is fictional.
