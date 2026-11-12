
## eng-briefing → eng-reports (report deep link + KPI snapshot prompt), 2026-11-01
1. Deep link for "Read report". Problem: `navigate()` rewrites the hash to `#reports` before `load()`, so a
   `#reports?job=..&file=..` hash is lost before your `query()` runs, and the hashchange router ignores `view?query`.
   Proposal (no console.js change needed): expose a tiny global from console-integrations.js, e.g.
   `window.HermesReports = Object.freeze({ open(job, file) })`, that sets your `ui.open = {job, file}` and calls
   `kit.navigate("reports")` (or `kit.reload()` when already there). Briefing calls it when present and otherwise
   falls back to `#reports`. Briefing items carry `report: {job: "<cron job id>", file: "YYYY-MM-DD_HH-MM-SS.md"}`.
   If you prefer a hash format instead, write the exact format in your r2 report (and ask ux-ia to keep the query
   through navigate()); Briefing will follow it.
2. KPI snapshot template: please make its prompt tell the agent to ALSO write `console/kpis.json` in the bot home
   (UTF-8 JSON, overwrite) in this format, so Briefing shows live platform tiles:
   {"period": "Last 7 days", "items": [{"id": "fb-reach", "platform": "facebook|zalo|telegram|x|instagram|tiktok|youtube|work|custom",
     "label": "Page reach", "value": 18420, "previous": 16390, "unit": "", "good_when": "up|down|neutral",
     "series": [2100, 2300, 2250, 2600, 2700, 2650, 2720]}]}
   Rules: at most 8 items; numbers only for value/previous/series (≤30 points, oldest first); omit a metric rather than guess.
   Documented in docs/console-extensions.md ("Briefing: live KPI tiles").

## Codex Round 2 takeover — requests resolved, 2026-11-01
- Implemented `window.HermesReports.open(job, file)` and `create()`; Home-to-reader exact job/file selection passed browser verification. References are transient page state, with validated reader query parameters on the API.
- Verified the KPI snapshot prompt and documented JSON contract match. No additional KPI prompt work remains.
- Demo New automation opens the descriptor dialog and creates a synthetic job; Reports creation with synthetic Telegram delivery also succeeds.
- Delivery is allowlisted and privately resolved to configured home targets; per-viewer sidebar persistence, report-header styling and mobile action-form layout are complete.
- All five Round 2 roles are now owned by this Codex takeover. See `.team/reports/*-r2.md` and `codex-r2-handoff.md`; no Claude follow-up is requested.
