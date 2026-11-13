# Brand Rule Lab

## Pre-publication governance for trustworthy marketing content

Brand Rule Lab checks content against approved brand rules, terminology, facts,
evidence, assets, and claim lineage before publication. Creators retain their
original content; Reviewers make the final decision.

### Main workflow

1. A Creator signs in and creates a draft.
2. The engine runs deterministic and optional AI-assisted checks.
3. Findings explain the issue, evidence, and suggested correction.
4. The Creator submits the latest checked version.
5. A different authenticated Reviewer inspects findings and evidence.
6. Approval mints immutable claim snapshots linked to exact fact versions.
7. Fact changes automatically create drift warnings for re-verification.

### Implemented

- Dark-first public landing page explains the product and review journey before
  sign-in; the authenticated dashboard presents role-specific next steps and
  workspace-backed activity metrics.
- Optional public demo role selector for an isolated judge database, with
  separate Creator, Reviewer, Fact Owner, and Brand Owner memberships and
  clearly labelled synthetic credentials.
- Authenticated Creator, Reviewer, and Fact Owner roles.
- SQLite migrations, foreign keys, WAL mode, and isolated test databases.
- SQLite-consistent backup and integrity-checked restore for database plus
  uploaded files, with non-overwriting destinations.
- Password hashing with standard-library scrypt.
- Opaque server sessions with hashed tokens, expiry, revocation, and live role checks.
- Approval safeguards for latest versions, high/critical findings, evidence,
  NOT_OBSERVED acknowledgement, concurrency, rollback, and self-approval.
- Session-backed Reviewer/Owner authorization for dismissal overrides, with
  tenant audit records; non-owners cannot create additional workspaces.
- PDF/DOCX/XLSX/CSV/text/image parsing and image identity checks.
- Claim-to-fact ledger, immutable snapshots, and drift tracking.
- Tamper-evident audit-chain JSON and PDF exports.
- Draft-only mock and optional live adapters for WordPress, Shopify, and
  Contentful; no adapter can publish.
- Authenticated Creator remediation proposals with preserved revisions and
  Reviewer decisions.
- Brand workspaces with owner-created membership, invitation, and role
  administration.
- Explicit atomic owner handoff to an active verified member, with tenant audit
  history and retained membership for the outgoing owner.

### Three improvements

Human decisions remain required for claim remediation and approval. Creator
remediation creates a new version with a required reason and retains the
original finding/evidence lineage. CMS draft adapters are available in local
mock mode or can create unpublished entries/posts/products using configured
credentials. Publishing is not exposed and remains a human action in the CMS.
Image
verification is local and honest about measurable limits. Short clips support
bounded keyframe OCR with visible coverage warnings, and uploaded package
artwork can be checked for OCR text differences against an approved reference.

### Security and reliability

No passwords, session tokens, API keys, or credentials are stored in source or
exported audit details. The audit chain detects modification, removal, and
reordering but is tamper-evident rather than a digital signature. Browser
refresh persistence for the custom login is unresolved because Streamlit does
not expose a safe HttpOnly cookie API for application-managed session tokens.
Configured OIDC supports first-owner and invitation registration using a
verified identity email, as well as linking existing accounts.
`BRL_REQUIRE_SSO=1` disables password sign-in, recovery, and registration for
SSO-only deployments and invalidates existing password-authenticated sessions;
it should be enabled only after verifying the configured identity provider and
redirect. OIDC and Resend settings are configured in Streamlit secrets. Live
sign-in and email delivery remain unverified.

### Testing evidence

Run the suite from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The latest full suite completed with 366 passing tests on 2026-11-13. Focused coverage includes
authentication, SSO-only enforcement, email token lifecycle, workflow
authorization, approval safeguards, immutable audit rows, audit-chain tampering,
fail-closed JSON/PDF exports, mock CMS behavior,
timestamped video OCR, approved packaging-reference OCR comparisons, and
database/upload backup recovery.

### Roadmap

- Configure OIDC or a reviewed proxy cookie layer for refresh persistence.
- Extend audit instrumentation to remaining legacy compatibility paths.
- Verify provider-backed CMS draft calls against disposable test accounts.
- Expand image and video checks to inspect layout, logo placement, and safe zones.
- Migrate remaining legacy role-string compatibility paths to principals.
