# Round 3 judge demonstration (4 minutes)

## Setup before the session

1. Follow the README's isolated judge-demo commands from the workspace root.
   They build a new database under %TEMP%, provision three accounts with
   password prompts, and launch on port 8502. The builder refuses overwrites;
   choose a new filename for each rebuild.
2. In the demo workspace, sign in as demo_creator, create and submit one short,
   clean draft with AI consent off, then sign out. This gives the Reviewer a
   pending version to approve during the walkthrough.
3. Keep separate browser profiles ready for demo_creator and demo_reviewer.
   Confirm the fictional data labels are visible. Do not select a live AI or CMS
   integration for this script.

## Walkthrough

| Time | Action | Explain |
| --- | --- | --- |
| 0:00–0:30 | Open the intentionally flawed Monsoon draft and run deterministic checks with AI consent off. Show V-04, T-12, F-112, and the superlative finding. | Rules produce explainable findings without a model call. Clearly label the fictional seeded scenario. |
| 0:30–1:20 | Correct one finding with a reason, then recheck. Show the new version and preserved prior finding. | Remediation preserves the original draft, version history, and evidence links. A correction is not an approval. |
| 1:20–2:00 | Submit the flawed draft and open the Reviewer queue in the second profile. Show approval blocked by unresolved high-priority findings. | A separate Reviewer is required; unsafe copy cannot be approved just because a Creator submitted it. |
| 2:00–2:50 | Open the prepared clean draft, review its checks, explicitly acknowledge `NOT_OBSERVED` items, and approve it as Reviewer. | The Reviewer decision is explicit and recorded on the submitted version. |
| 2:50–3:30 | Open Audit & Exports and inspect the review, correction, and approval events. | The audit chain is tamper-evident, not a third-party digital signature. |
| 3:30–4:00 | If time allows, show the synthetic packaging or sampled-video findings. | Video samples selected frames only. OCR does not certify package layout, logo placement, or disclaimer size. |

## Truthful demo boundaries

- The supplied synthetic scenarios and synthetic provider are fictional test
  fixtures, not customer or live-model results.
- Google OIDC and Resend settings are in the separate workspace-root
  `.streamlit/secrets.toml`; Streamlit 1.66.1 loads them when launched from that
  root. The configured local callback is `http://localhost:8502/oauth2callback`.
  The Google redirect reached the account-entry page; no account was selected
  and sign-in was not completed. Email delivery remains untested; do not promise
  these paths in a live demonstration until an approved account and test
  recipient have been used.
- Live WordPress, Shopify, and Contentful draft creation requires configured
  test accounts and has not been verified against real CMS instances in this
  environment. Use local mock mode for this walkthrough.
- Use a dedicated disposable database and local demo passwords. Do not enter
  production brand documents or confidential campaign content.
