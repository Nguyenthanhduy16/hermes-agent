# Reports and Automations — Round 2 complete

Codex takeover, 2026-11-01. Supersedes Round 1 delivery/deep-link open issues.

- Delivery choices are server-allowlisted: local, configured Telegram home chat, configured Zalo home chat, and an existing origin only when it matches a currently configured supported home target with a credential. Browser descriptors use opaque aliases; chat IDs/tokens are not returned. Arbitrary recipients and raw explicit targets are refused.
- Server resolves selection to an exact `platform:chat` before saving, revalidates inside the write transaction, and rejects Run now when the target is removed. Existing externally managed targets may be preserved during unrelated edits; they are not exposed or made triggerable by the console.
- Reports passes its selected delivery to the same AutomationAdmin service, including the scoped context environment. Demo targets and creation are synthetic/in-memory.
- Humanized report/automation schedules, run dates and latest-output wording; no filenames as the primary report title. Template schedule preset says Template recommendation. Jobs refer to Automations.
- Implemented `window.HermesReports.open(job, file)` with bounded identifiers, and `create()`. It sets selection before navigating/reloading, so Home opens the exact reader. This is session state, not a reload-shareable URL; the server reader uses `?job=…&file=…`.
- Added Reports palette commands Open latest report / New report from template, and connected the global New report command.
- Verified the existing KPI snapshot prompt writes the documented KPI JSON contract.

Files: `admin_automation.py`, `admin_integrations.py`, `admin_static/console-integrations.js`, `console.js`, `docs/console-extensions.md`, `tests/test_admin_automation.py`, `tests/test_admin_integrations.py`, `deploy/vercel-admin/tests/console-integrations.test.js`.

Validation covers unknown/foreign origins, target removal races, credentials/IDs absent from snapshots, configured target persistence, scoped environment, exact reader query and palette composition. Browser creation of both automation and report with synthetic Telegram delivery passed. [Full gate](codex-r2-handoff.md).

No open Round 2 implementation items. Actual chat delivery was intentionally not invoked; production delivery requires its configured gateway/scheduler.
