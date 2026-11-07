# Project Cleanup Report

Status as of 2026-11-07: migration 6, the focused provenance checks, the document-warning path, and the final verification pass are complete. The private evaluation documents were not re-uploaded or re-OCR'd for this pass, and no live AI calls were made.

## Verified results

- Migration 6 is idempotent and the schema migration sequence remains `[1, 2, 3, 4, 5, 6]`.
- The focused migration checks passed: `2 passed, 0 failed`.
- The page-level provenance and degraded-document warning checks passed: `4 passed, 0 failed`.
- The latest full suite passed: `192 passed, 0 failed` in `43.78s`.
- The false numeric-conflict regression is fixed and retained in the suite.

## Document evidence retained in the verified workspace

- Document 1: five manually verified facts approved and one source-backed `CODE_CHECKABLE` rule approved in the isolated reference workspace.
- Document 2: deterministic review caught the `overnight` issue; the stored finding evidence is page `1` with character offsets `383` through `392`. AI extraction did not run because external-AI consent was disabled.
- Document 3: `NEEDS_ATTENTION`; nine page warnings; four incomplete/unreadable pages; zero extracted claims; no automatic approvals. The New Review page still surfaces the page-level warning text.

## Release archive and security exclusions

The source ZIP was rebuilt from the verified workspace. It includes the application source, tests, fixtures, and documentation required for the release, while excluding:

- `.env` and local environment secrets
- `.venv/`
- SQLite databases and sidecars
- uploaded documents and private scenario stores
- temporary outputs and generated archives
- the three private evaluation PDFs and any local stress corpus outputs

## Validation evidence

- Pytest migration-focused verification: passed.
- Pytest provenance/warning verification: passed.
- Latest full project pytest run: `192 passed, 0 failed`.
- The refreshed source ZIP contains 63 files and passed integrity, required-source, and exclusion checks. It excludes `.env`, `.venv/`, SQLite databases and sidecars, uploads, and the private document-stress corpus. The synthetic PDF fixture is retained because the onboarding tests require it.

## Scope note
This report reflects the current verified source and test evidence in the workspace. No fresh OCR, re-upload, or proposal regeneration was performed on the private evaluation corpus during this release pass.