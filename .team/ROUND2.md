# Round 2 — lead QA findings (2026-11-01). Same rules as COMMON.md. Ownership changes are listed per item.
Lead verified: JS 131/131, Python 1405/1406 (test_setup fails only in the full run — env leak, see eng-platform).
Briefing/Reports/Connectors/palette look good. Remaining gaps a CEO would notice:

## eng-reports (now ALSO owns src/hermes_zalo/admin_automation.py + tests/test_admin_automation.py)
1. Delivery to chat is the core promise ("the bot reports to me"). Add an allowlisted `deliver` to AutomationAdmin
   (local | origin | telegram:<configured chat> | zalo:<configured owner/home chat>, only targets that exist in config;
   validate strictly; never free text) and wire the Reports "delivery" field to it. Demo: offer the options with
   synthetic targets.
2. Rename the action "New scheduled job" → "New automation" (and similar jargon in admin_automation.py labels).
3. Humanize: never show raw cron or "Pending scheduler tick" or raw .md filenames as primary text. Use the helper
   `kit.describeSchedule(cron)` / `kit.formatRelative(ts)` that ux-ia adds (fallback to your own until it lands), show
   "Every day at 08:00", "Weekdays at 08:00", "Waiting for the scheduler", and report titles/dates instead of filenames.
   Report jobs subtitle says "edit instructions on Schedules" → "Automations".
4. Deep link: reader opens a specific report via `#reports?job=..&file=..` or equivalent hash state; tell eng-briefing the
   format in your report so Briefing's "Read report" opens that exact report.
5. Add page `commands` for the palette ("Open latest report", "New report").

## ux-ia
1. Add shared helpers in console.js kit: `describeSchedule(cron)` (5-field cron → plain English, fallback to the raw text),
   `formatRelative(ts)` ("in 5 min", "2 h ago"), and use them on Automations cards (no raw cron/"Pending scheduler tick").
2. "Customize sidebar": the owner can pin/unpin any page into Essentials (palette command + a small "Customize" control in
   the sidebar; stored per viewer in localStorage with try/catch; reset to default). This is how the CEO "adds features
   he wants" to his daily view.
3. Hook ui-visual's `page-enter` transition on view change (not on polls/filters); `toast(text, tone)` tones.
4. Home nav label vs page title: make the Briefing page title "Home" with subtitle "Your briefing", or rename nav to
   Briefing — pick one and be consistent in palette.
5. Demo-mode Automations: "New automation" from the palette must open something useful in demo (the dialog with
   synthetic success), not silently navigate. Coordinate with eng-reports who owns admin_automation.py — put the backend
   demo-actions request in your report section "for eng-reports" and message via the file .team/requests.md (append).
6. Link docs/production.md from README.md and docs/admin.md (you now own README.md/docs/admin.md for this).

## eng-briefing
1. Use eng-reports' deep-link format for "Read report" (read .team/reports/eng-reports.md after they update; until then
   keep #reports). Use `kit.note` for the demo disclaimer (already ok) and add `commands` (e.g. "Dismiss all reviewed").
2. Hero decorative circle too heavy in dark mode (use color-mix with ~8% accent). Verify Briefing at 375px and dark.
3. Live KPI path: document in docs/console-extensions.md (Briefing section) how a report job writes console/kpis.json,
   and have eng-reports' "KPI snapshot" template prompt instruct the agent to write that file format (coordinate via
   .team/requests.md; eng-reports implements the prompt text).

## ui-visual
1. Full visual audit of ALL pages (Essentials + Advanced) in light/dark at 1440px and 375px. Fix everything in console.css.
   For issues in other files, append precise requests to .team/requests.md (file, selector, fix).
2. Markdown report tables: override uppercase th inside the reader (`.md-*` or whatever class eng-reports uses).
3. Make the login screen impressive (brand gradient, product tagline) — it's the first impression.

## eng-platform (now ALSO owns tests/test_setup.py, tests/test_assistant_adapter.py, tests/test_hermes_assistant.py)
1. Fix test isolation: `tests.test_setup` passes alone but fails in the full run because ZALO_BOT_MODE (or similar) leaks
   from another test into os.environ (`python -m unittest tests.test_assistant_adapter tests.test_setup` → error).
   Fix the leak (patch.dict / setUpClass cleanup) — don't weaken assertions. Full Python suite must be green.
2. Add `?v=<content-hash>` asset versioning in served console.html (server-side rewrite at serve time, no manual edits),
   then `immutable` long cache for versioned assets. Keep tests green.
3. Final gate at the end of round 2: run full Python + JS suites after everyone finishes (watch .team/reports/*-r2.md),
   report results.

Everyone: when done write .team/reports/<name>-r2.md (short), then stop.
