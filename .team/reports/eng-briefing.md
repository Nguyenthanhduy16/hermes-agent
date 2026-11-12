# eng-briefing report: CEO Briefing home (2026-11-01)

## What changed
New files (owned):
- `src/hermes_zalo/admin_briefing.py`: registers `Section("briefing", snapshot, action)`.
  - **Demo:** rich synthetic data with timestamps relative to now (no disk, process or network access).
    It includes 5 attention items (1 urgent, 2 review, 2 new), KPI tiles for Facebook reach, Zalo OA messages,
    Telegram members, X mentions and tasks done in work apps (each with a change % and a 14-point series),
    4 latest reports, 4 "up next" jobs and 5 quick prompts.
  - **Live:** read-only. It builds the page from existing services:
    - `AutomationAdmin.snapshot("routines")` for failed runs, delivery errors, new outputs (the last 24 h), up next and latest reports;
    - the registered `connectors` section for installed connectors that are missing credentials;
    - `admin_operations.gateway_status(home, environ, None)` and `scheduler_status`. It reads status files only and never calls systemctl or docker;
    - `AssistantAdmin` users/usage for the daily quota (warning at 80% or more, urgent at 100%) and a 7-day request series.

    Optional platform KPI tiles come from `<home>/console/kpis.json`, which your own report jobs can write.
    The format is in the `_custom_kpis` docstring: `{items:[{id,platform,label,value,previous|change,series,good_when}]}`.
    The file is validated and bounded. Each block degrades on its own to `available:false` with a message.
  - **Action `dismiss` {id}:** goes through `ControlStore.mutate_json("console/briefing.json")` with a revision check.
    The id must currently be on the briefing, and stored dismissals are capped at 300.
    Action `restore` brings back all dismissed items.
    Item ids include the occurrence (for example, a failed run's time), so a new failure shows up again.
    Demo dismissals stay in memory, per demo home.
- `src/hermes_zalo/admin_static/console-briefing.js`: `register("briefing", {title:"Briefing", group:"main",
  essential:true, icon:"home", searchable:false, actions:true})`.
  - **Layout:** hero (greeting, date, one-sentence summary, tone pill, 3 quick stats) → KPI tiles with an inline SVG
    sparkline (built with createElementNS) → 2-column grid: "Needs your attention" queue and "Latest reports" |
    "Up next" timeline and "Ask your assistant" prompts.
  - **Links:** only `#view` hashes are followed (`kit.navigate`). Other link values are ignored.
  - **Quick prompts:** use `kit.openChat(text)`, so the message is prefilled and never sent.
  - **Dismiss:** one click with no dialog, because it is low-risk and reversible. It POSTs the standard confirmed envelope
    `{action, expected_revision, confirm:true, values:{id}}` through `kit.api` (with CSRF), then does a quiet reload.
    "Show dismissed items" uses the normal descriptor dialog (`kit.actionButton`).
  - **Refresh:** every 60 s (`kit.poll`).
- `src/hermes_zalo/admin_static/console-briefing.css`: uses tokens only, with fallbacks in `var(--x, fallback)`.
  Responsive at 1100 px and 640 px, and honours reduced motion.
- `tests/test_admin_briefing.py` (14 tests) and `deploy/vercel-admin/tests/console-briefing.test.js` (8 tests,
  using the vm harness).

One-line registrations:
- `console_sections.py` EXTENSION_MODULES: added `'admin_briefing'`.
- `admin_web.py` CONSOLE_EXTENSIONS: added `'briefing'`.
- `deploy/vercel-admin/scripts/build.js`: added `'briefing'` to the list.

**For eng-platform:** I also added `'briefing'` to the three copies of the same extension list in
`deploy/vercel-admin/tests/build.test.js`. That test mirrors the build.js list, so it failed as soon as the
assigned registration landed. This was the only change in that file.

## How to see it
`PYTHONPATH=src .runtime/venv/bin/python -m hermes_zalo.admin_cli demo --port 8813`, sign in as admin / LabCoach-Demo-2026.
ux-ia's HOME_PAGE logic already opens `#briefing` as Home.
- **Checked in Chrome (desktop width):** the full page renders; Dismiss works and the "1 dismissed item is hidden" footnote
  shows; a quick prompt opens Chat with the text in the composer.
- **Not checked:** the 375 px layout. The window resize didn't apply, so only the CSS media rules cover it for now.
- The preview server on 8813 has been stopped.

## Tests
- `python -m unittest tests.test_console_sections tests.test_admin_briefing`: 18 OK.
- `node --test tests/console-briefing.test.js`: 8/8 pass. `tests/build.test.js`: 3/3 pass.
- Full JS suite: 129/131 at the time of the run. The 2 failures:
  - `build.test.js`: fixed by the list edit above.
  - `console-integrations.test.js` "reports render the saved report list…": in eng-reports' area, still in progress, unrelated to briefing.
- Full Python suite: 1406 tests, 1 failure, 20 skipped. The failure is not briefing-related:
  `test_setup.SetupTests.test_setup_preserves_provider_and_env_and_disables_tools`. It expects
  `platform_toolsets.zalo == []` but gets the web/vision/… list, from the uncommitted `scripts/configure.py` changes.

## Open issues / requests
- **ux-ia:**
  - kit.note's "info" icon (`#i-info`) is not in the console.html sprite, so the demo note shows an empty icon box.
  - The KPI tile and prompt buttons are plain `<button>`s. It's worth adding them to the command palette's quick
    actions if you want "Ask: …" entries there.
- **ui-visual:** classes are `brf-*` (hero, kpi, mark-<platform>, spark(-line|-area|-dot), item.{critical,warning,info},
  report, slot, prompt). Feel free to restyle them in console.css. console-briefing.css only uses tokens.
- **eng-reports:**
  - Briefing links "Read report" to `#reports`. If the reader supports deep links (job/file query), I can pass the job id.
    The reports items already carry `id` = cron job id.
  - Platform KPIs in live mode need data. The cleanest source is a report job that writes `console/kpis.json`
    (format above), or you could expose a reader function I can call instead.
