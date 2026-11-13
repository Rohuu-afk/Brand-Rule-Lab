# Brand Rule Lab — CodeNexus India 2026 Round 3

**Concise submission handoff:** [Brand Rule Lab Round 3 PDF](../submission/Brand_Rule_Lab_Round3_Submission.pdf). It summarizes the product, known-commitment mapping, validation evidence, and release blockers. It is explicitly a local draft because the current checkout has not been deployed and the previous hosted URL was asleep.

**Official timing:** The CodeNexus India event page sets the Round 3 window to end at 10:00 IST on 13 November 2026. That deadline has elapsed. This checkout and this document are post-window work; they must not be presented as an on-time Round 3 submission.

## Project overview

Brand Rule Lab is a pre-publication brand-governance workspace. It checks campaign
content against approved terminology, rules, facts, evidence, assets, and claim
lineage before a human reviewer approves it.

## Problem and target users

Marketing teams need to move quickly without publishing unsupported claims or
off-brand content. Creators submit drafts, Reviewers make final compliance
decisions, and Fact Owners govern the evidence and fact versions used by checks.

## Implemented features

- SQLite-backed products, guidelines, rules, facts, evidence, assets, drafts,
  findings, reviews, claims, snapshots, and drift tracking.
- Deterministic rule checks plus optional AI-assisted extraction and ranking.
- PDF, DOCX, XLSX, CSV, text, and image parsing with size/type safeguards.
- Authenticated Creator, Reviewer, and Fact Owner accounts using scrypt password
  hashes and server-side SHA-256 session-token hashes.
- Additive brand workspaces, memberships, Brand Owner administration, and
  expiring single-use invitation tokens.
- Explicit, atomic Brand Owner transfer to an active, verified existing member;
  the outgoing owner remains a member and the transfer is tenant-audited.
- Role checks in protected workflow services, latest-version approval checks,
  self-approval prevention, evidence blockers, and NOT_OBSERVED acknowledgement.
- Finding-dismissal overrides require a live Reviewer/Owner session and
  selected-brand membership; the authenticated decision is also recorded in the
  tenant audit chain. Lower-privilege members cannot create another workspace.
- Brand membership checks prevent an authenticated account from listing or
  administering a workspace where it has no membership.
- Immutable claim-to-fact snapshots and fact-drift detection.
- Creator remediation proposals create preserved new draft versions with a
  required reason, finding traceability, and Reviewer decision through the
  existing approval workflow.
- Append-only review history and a separate tamper-evident audit chain.
- SQLite online-backup snapshots and hash-checked restores of the database and
  uploads to new, non-overwriting paths.
- Verifiable JSON audit export and local PDF audit report.
- Public landing page with a direct sign-in path before authenticated workspace
  navigation.
- Username or email login when the identifier resolves to one account; ambiguous
  identifiers fail closed.
- Local mock CMS drafts for WordPress, Shopify, and Contentful, plus live
  WordPress draft creation over HTTPS.

## Three Round 2 improvements

### Human-authorised claim remediation

The Review Results workflow is implemented for human-authorised remediation. A
Creator selects a finding, previews a phrase or full-draft correction, supplies
a required reason, and saves a new checked draft version. Original versions,
findings, evidence links, and correction metadata remain preserved. A different
authenticated Reviewer uses the existing approval or request-changes workflow;
AI never approves.

The Creator entry flow is a four-step guide for content type, brand-scoped
context, source content and preview, then review settings. External AI consent
is explicit and defaults off. Results keep the submitted copy visible, highlight
only claim spans whose stored offsets match the exact source text, and show
linked approved facts and their evidence relationship in the finding itself.
Reviewers enter through the pending-draft queue; decisions remain on the
authenticated Review Results workflow and never publish content.

### CMS integrations

The `services.cms_adapters` contract supports deterministic local mock draft
creation and optional live draft creation for WordPress, Shopify, and
Contentful. WordPress uses an Application Password; Shopify creates a product
with status `DRAFT` through the Admin GraphQL API; Contentful creates an
unpublished entry through the Content Management API. All adapters reject
redirects, verify provider draft identifiers/status where available, and
provide no publish operation. Their requests are covered with mocked HTTP
responses, but no live CMS account has been used to verify provider behavior.

### Multimodal packaging and video

Image parsing, OCR, exact image hashes, perceptual hashes, brand asset approval,
and visual metadata are implemented. Approved packaging references can be
selected for OCR token comparison against uploaded package artwork. Short MP4,
MOV, and WebM uploads can be sampled locally using a bounded pipeline: at most
12 keyframes from clips up to 120 seconds, with OCR text, timestamps, and
extraction warnings retained in review metadata. Unsampled moments are marked
not observed. OCR comparison does not verify layout, colors, visual placement,
logo safe zones, motion, or every video frame.

## Architecture

Streamlit provides the authenticated UI. Services contain authentication,
review, approval, fact, asset, parser, ledger, audit, and adapter logic.
SQLite uses ordered additive migrations, foreign keys, WAL mode, and isolated
test databases. AI calls are optional and schema-validated; deterministic
checks remain available when providers are unavailable.

## Authentication and RBAC

Passwords are never stored in plaintext. Login creates an opaque token and
stores only its SHA-256 hash. Session resolution rechecks expiry, revocation,
account activity, username, and current role. Creator, Reviewer, and Fact Owner
permissions are enforced in the core authenticated workflow services.

OIDC is supported through Streamlit's `st.login` and `[auth]` configuration;
verified identities can onboard as the first owner or accept a matching brand
invitation. Set `BRL_REQUIRE_SSO=1` to disable password sign-in, password
recovery, and password registration in deployments that require SSO. Its
default remains `0` for local/demo compatibility. Password sessions do not
survive a browser refresh; configured OIDC uses Streamlit's identity cookie.
Live identity-provider authentication remains unverified in this environment.

## Audit and exports

Audit events use canonical JSON and SHA-256 hashes. Each event stores the
previous event hash, and SQLite triggers reject ordinary updates and deletions.
Exports fail closed if the stored chain does not verify. JSON packages can be
verified without database access. PDF reports reference the verification
result and head hash. This is tamper-evident integrity, not protection against
an operator who can alter the database file or schema, an external timestamp,
an externally trusted digital signature, or regulatory certification.
The Audit & Exports page can verify a previously downloaded JSON package
locally. PDF exports paginate and include per-event previous and current hashes.

## Testing

The repository uses the nested `.venv` and isolated temporary SQLite databases.
The latest full regression command is:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The latest full worktree run completed on 2026-11-13 with **369 passed, 0 failed** and six
PyMuPDF/SWIG deprecation warnings. Streamlit `AppTest` covers the public
landing, sign-in reveal, reviewer overview, and the complete confirmed owner
handoff. An isolated local browser session rendered the landing page, signed in
to a disposable workspace, created a tenant-scoped product and fact, approved
its synthetic evidence, created and checked a draft with AI consent off, then
completed a disposable one-time Reviewer invitation registration and acceptance
into the correct brand in the browser, Creator submission, and separate
Reviewer approval. A second disposable browser run exercised product-scoped
deterministic findings with AI consent off, Creator correction history,
authenticated recheck, submission, Reviewer blocking on unresolved high-priority
findings, and approval after explicit acknowledgement of NOT_OBSERVED checks.
The latest browser database passed SQLite integrity and foreign-key checks. A
cold-start check launched the Streamlit server with a new disposable database
path and returned HTTP 200 from `/_stcore/health`; the process was stopped
after the check. The browser workflows above verified the application itself.
A disposable copy of the version-11 database and a separate fresh
database each initialized repeatedly through migration 14. Row counts for all
30 pre-existing tables matched exactly in the v11 copy; both databases had
SQLite integrity `ok` and zero foreign-key violations. The configured database
was not used for migration tests. A separate backup/restore round-trip
preserved three demo accounts and one brand and returned clean integrity and
foreign-key checks.

Focused audit/CMS tests cover empty and multi-event chains, payload tampering,
reordering, export verification, immutable database rows, fail-closed exports,
PDF generation, role restrictions, provider validation, and draft-only
behavior. A migration test confirms version 15 installs the audit immutability
triggers when upgrading a version-14 database.

## Demo

Build and run an isolated demo from the workspace root (the directory
containing Brand-Rule-Lab and the configured .streamlit folder):

```powershell
$env:BRL_PROJECT_ROOT = Join-Path $PWD "Brand-Rule-Lab"
$env:PYTHONPATH = $env:BRL_PROJECT_ROOT
$env:JUDGE_DEMO_DATABASE_PATH = Join-Path $env:TEMP "brand_rule_lab_round3_judge_demo.sqlite"
$env:DATABASE_PATH = $env:JUDGE_DEMO_DATABASE_PATH
$env:UPLOADS_PATH = (Join-Path (Split-Path $env:DATABASE_PATH) (([System.IO.Path]::GetFileNameWithoutExtension($env:DATABASE_PATH)) + "_uploads"))
& "$env:BRL_PROJECT_ROOT\.venv\Scripts\python.exe" "$env:BRL_PROJECT_ROOT\db\create_judge_demo.py"
& "$env:BRL_PROJECT_ROOT\.venv\Scripts\python.exe" -m db.provision_demo_accounts
& "$env:BRL_PROJECT_ROOT\.venv\Scripts\streamlit.exe" run "$env:BRL_PROJECT_ROOT\app.py" --server.port 8502
```

The database builder refuses to overwrite a file; choose a new temp filename
for each rebuild. Account provisioning prompts securely for the demo passwords
and does not overwrite existing credentials. Sign in as demo_creator for the
Brand Owner journey, then as demo_reviewer to show separate human approval.
Port 8502 matches the configured local Google OIDC callback.

## Known limitations and roadmap

1. First-owner signup, email verification, invitation-based email/password
   registration, and password reset are implemented with expiring one-time
   tokens. A separate workspace-root Streamlit secrets file contains the
   configured `[auth]` and `[resend]` keys, and Streamlit 1.66.1 loaded them
   from that root. The provider redirect reached Google's account-entry page,
   but no account was selected and authentication was not completed. Resend
   delivery remains untested because no recipient was selected.
2. OIDC can create the first owner or register an invitee only when the
   verified identity email matches a live invitation. Existing verified
   accounts can link by matching email. Workspace membership still grants
   authorization. `BRL_REQUIRE_SSO=1` disables password-based access and
   recovery. The workspace OAuth redirect is
   `http://localhost:8502/oauth2callback`. Configuration is loaded when
   launched from the workspace root. No live identity-provider login was
   performed. Password login does not persist through browser refresh;
   configured OIDC uses Streamlit's identity cookie.
3. Live CMS draft creation is configuration-dependent and has not been tested
   against real WordPress, Shopify, or Contentful accounts. No publishing
   action exists; a human must review and publish content in the CMS.
4. Video OCR checks only sampled frames. Packaging OCR comparisons can be
   affected by image quality and do not verify visual layout or correctness.
5. Browser workflows use validated sessions and selected-brand membership.
   The static service/UI helper review found that legacy `actor_role` and
   unscoped compatibility paths in review, facts, guidelines, knowledge,
   approvals, assets, ledger, drift maintenance, and UI analytics are limited
   to explicitly opted-in pytest fixtures. An independent security review or
   penetration test remains outstanding before enterprise deployment.
6. Migration 12 establishes brand ownership and membership. Existing
   historical rows remain unscoped (`brand_id IS NULL`) until deliberately
   assigned by an owner migration; they are not exposed as a new member's
   private tenant data.
7. Backup creation and restore are operator-run. They do not schedule retention,
   remote replication, or key-backed archive signatures; protect backup archives
   as confidential production data.

## Requirement source and judging alignment

The local checkout and searched OneDrive / download folders contain the Round 1
submission deck and Round 2 product guide, but no original Round 3 project deck
or scoring sheet. The Round 2 PDF names
the three commitments tracked below: human-authorised claim remediation and
CMS integrations; multimodal packaging and video verification; and enterprise
RBAC with verifiable audit exports. The official [CodeNexus India event page](https://wecodecoders.in/events/codenexus-india)
says Round 3 must deliver those named improvements, complete core features,
resolve major issues, and provide a stable product, final PDF, working link,
and updated repository. The event page states that its Round 3 submission
window ended at **10:00 IST on 13 November 2026**, and that missing the deadline
has no late window; current work is post-window and cannot be submitted as
timely. The page says rounds are scored out of 100 and that score sheets are
published, but the accessible content contains a detailed Round 1 rubric only
and separately says judging sheets and comparative breakdowns are not shared.
No Round 3 point breakdown or original Round 3 presentation was found, so the
Round 2 roadmap below remains the only product-specific commitment source.
The official page also requires one individual entry per participant, naming
that person's role and the part they built in their own words. See the
[participant-entry worksheet](ROUND3_PARTICIPANT_ENTRY_DRAFTS.md); it is a
prompt, not a claim about anyone's personal contribution.
OIDC and Resend credentials are present in the separate workspace
secrets file, outside this repository. The previously deployed Streamlit URL displayed a
sleeping-app page; it was not woken because app startup could run migrations
against its deployed database. A current judge-ready deployment is therefore
still outstanding.

### Evidence against the available Round 2 roadmap

| Roadmap area | Current implementation evidence | Status and boundary |
| --- | --- | --- |
| Human-authorised claim remediation and CMS | Creator remediation preserves a new draft version and reason; Reviewer approval remains separate. Live WordPress, Shopify, and Contentful draft adapters verify unpublished responses and reject redirects; no publish operation exists. | Implemented with provider behavior tested through mocked HTTP responses. Live accounts and any provider-specific content model still require configuration and verification; human review/publishing remains in the CMS. |
| Multimodal packaging and video verification | Image ingestion, OCR, exact hashes, perceptual hashes, approved packaging references with OCR token comparison, image review metadata, and bounded video keyframe OCR with timestamped coverage limits exist. | Partially implemented. OCR can vary by image quality; video sampling does not inspect unsampled frames or motion; image matching does not find a logo inside a full creative; geometry, safe-zones, and layout checks remain incomplete. |
| Enterprise RBAC and verifiable audit export | Configurable OIDC and SSO-only mode; per-brand Owner, Creator, Reviewer, and Fact Owner membership; invitation and atomic owner-transfer workflow; backend membership checks; immutable tenant audit rows, fail-closed hash-chain exports, and JSON/PDF reports. | Core workflow implemented and tested. Live IdP sign-in, SAML, Legal/Regulatory role profiles, independent security review, external signatures/timestamps, and regulatory certification remain unverified or out of scope. |

### Remaining submission evidence

- The disposable browser pass verified the public landing, Creator sign-in,
  protected workspace navigation, one-time Reviewer invitation acceptance,
  Creator submission, and separate Reviewer approval. The flow ran against an
  isolated SQLite database and a local Streamlit server; all three tenant audit
  events verified as a valid hash chain, SQLite integrity was `ok`, and the
  foreign-key check was empty. No real email was sent.
- Still run a full Owner-to-invitation-to-Reviewer/Fact Owner journey in the
  browser from a newly provisioned demonstration database before a live demo.
- Configure and verify the draft integrations against disposable WordPress,
  Shopify, and Contentful accounts.
- The local video-sampling/OCR and packaging-reference comparison flows are
  implemented and covered by fixture tests, but still need representative
  real-media validation and human review of OCR accuracy.
- Obtain the original Round 3 deck/rubric and reconcile any differences from
  this Round 2 roadmap.
- Each team member must prepare and file their own participant entry, with a
  role and first-person account of work they personally completed; do not
  reuse team-level wording as an individual claim.
- Google OIDC and Resend credentials are present in the separate workspace-root
  Streamlit secrets file, outside the repository. Streamlit loaded the expected
  key names from that root; live OIDC still needs an approved account and live
  email delivery needs a selected test recipient before either can be claimed
  operational.
