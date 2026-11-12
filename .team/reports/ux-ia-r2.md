# UX / information architecture — Round 2 complete

Codex takeover, 2026-11-01. Existing partial Round 2 work was retained and verified.

- Shared `describeSchedule` and `formatRelative` helpers render automation schedules and relative run dates. Home has one consistent label in navigation, page heading and palette.
- Customize sidebar supports pin/unpin/reset. Fixed persistence to use an encoded username key and reload preferences on sign-in; sign-out clears in-memory pins. Different viewers do not inherit pins.
- Page entrance runs on view changes, including first load, and does not replay on filters, polling or selecting the current page. Toasts support semantic tones.
- Global New report now opens the actual template composer; New automation opens its action dialog in demo and completes synthetic creation.
- README and admin documentation link production operations. Updated the admin control table to describe configured chat delivery.

Files completed in this takeover: `console.js`, `deploy/vercel-admin/tests/console.test.js`, `docs/admin.md`; retained earlier `console.html` customization and README production links.

Validation: regression tests cover account isolation/reset, transitions and palette actions. Browser demo creation succeeds at 375px dark. Full gate and evidence: [codex-r2-handoff.md](codex-r2-handoff.md).

For eng-reports: demo action request and report composer wiring are resolved by this same Codex takeover; no additional Claude work is needed. No open Round 2 UX items.
