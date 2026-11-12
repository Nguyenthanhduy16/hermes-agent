# eng-reports: Reports & Connectors (done)

## What changed
- `src/hermes_zalo/integration_catalog.py`
  - New report templates: **Weekly work digest**, **KPI snapshot**, **Custom report** (free-form; "What should the report cover?" is required).
  - `mentions-to-reply` is now named **Mentions watch**. Its id and job-name prefix did not change, so existing jobs still match.
  - The 5 templates in the brief are marked `featured`; the other 4 sit under "More templates".
- `src/hermes_zalo/admin_integrations.py`
  - **Report reader.** The `reports` snapshot now returns:
    - `saved`: every saved report file, newest first, up to 60;
    - `reader`: one report's full text, already redacted by `AutomationAdmin.read_output` and capped at 64k characters. The newest report is opened by default, or the one named by the `?job=&file=` query;
    - `reader_error`, plus `summary` counts (scheduled, paused, saved, failing).
    - The query keys are registered as `query_keys={"job","file"}`. The job id and the file name are checked against regexes and against the job's listed files, so a file path can never be passed in.
  - **New report flow.** Each template has a `create_report` descriptor whose fields follow the steps:
    - account;
    - one `use.<connector>` checkbox per connector that is **enabled** (no others are offered);
    - `schedule_preset`: suggested, daily 8:00, weekdays 8:00, Monday 8:00, or custom cron/phrase;
    - `delivery`;
    - language, focus, notes, create paused.
  - **Server-side validation.** Every value is checked again on submit:
    - a `use.*` value must be a boolean, used by the template, and enabled when submitted;
    - a running report needs at least one connector, unless the template can rely on web search;
    - the preset must be from the list, and "custom" needs a schedule;
    - delivery is limited to the allowlist;
    - unknown fields are refused.
  - Jobs are still created only through `AutomationAdmin.create_routine` (single writer). The prompt names only the connectors the owner ticked.
  - **Run now** uses the existing `run_routine`. It only appears while a Hermes scheduler heartbeat is detected, so it is hidden in the demo.
  - **Demo:** 4 synthetic report jobs and 4 saved reports: two daily social summaries, a weekly work digest and a mentions watch. One more job is paused with a failed last run.
  - **Connectors:** each item gets a `group` (connected / needs_setup / available) and the summary counts per group. The page note is now one short line.
  - New helper `saved_reports(ctx, limit)` returns the newest saved reports without their text. eng-briefing can use it for "latest reports".
- `src/hermes_zalo/admin_static/console-integrations.js` (rewritten) and `console-integrations.css`
  - **Safe Markdown renderer** built from DOM nodes only:
    - supports headings, paragraphs, lists (incl. nested), block quotes, code fences, inline code, bold/italic, tables with alignment, rules and links;
    - only http(s) links are made clickable, with no embedded user:password, `target=_blank` and `rel="noopener noreferrer"`;
    - raw HTML is shown as text;
    - input is capped at 64k characters and output at 1,500 blocks.
  - **Reports page:**
    - one-line note and a status strip, plus a **New report** button;
    - the composer shows a 5-step indicator (Template, Connectors, Schedule, Delivery, Confirm) and the featured template cards. "Use template" opens the existing action dialog, and its confirmation is the Confirm step;
    - **Saved reports** library: a filter by report job, a newest-first list and a reader panel. A leading heading that repeats the reader title is dropped;
    - report job cards: Run now / Pause / Delete, and "Read reports".
  - **Connectors page:**
    - status strip (Connected / Needs setup / Available), then category chips, then the three sections in that order;
    - compact cards: description, risk badge, server count and test result;
    - one primary button per card: **Manage**, **Enable** or **Set up**. Credentials, capabilities, servers, snippet, docs and the other actions are behind a "Details" disclosure.
  - Both pages are registered with `essential: true`.
- Tests: `tests/test_admin_integrations.py` (8 new report-flow, reader and grouping tests; demo test updated) and `deploy/vercel-admin/tests/console-integrations.test.js`:
  - XSS cases for the renderer: script/img/iframe/svg tags, `javascript:`/`data:`/`vbscript:` links, links with credentials, `on*` attributes, HTML inside code and tables;
  - size bounds, the reader query, the new-report submission payload and the status-first layout.

## How to see it
`PYTHONPATH=src .runtime/venv/bin/python -m hermes_zalo.admin_cli demo --port 8814`, then open `http://localhost:8814/#reports` or `#connectors` and sign in as admin / LabCoach-Demo-2026.

Use **localhost**, not 127.0.0.1: all demo ports on 127.0.0.1 share one session cookie, so the other agents' demos keep logging you out.

I checked it live in Chrome: the reader renders tables and lists, and the New report dialog creates a job in the demo.

## Tests
- `python -m unittest tests.test_admin_integrations tests.test_console_sections`: 34 OK.
- `node --test tests/*.test.js` (deploy/vercel-admin): **131/131 pass**.
- Full Python suite: 1406 tests, 1 failure in `test_setup.test_setup_preserves_provider_and_env_and_disables_tools`. It is not my area and **passes when run alone**, so it looks like interference from parallel edits.

## Open issues / needs from other roles
- **Chat delivery (Zalo/Telegram):** not offered. `AutomationAdmin` always creates `deliver: "local"` jobs and accepts no delivery target, and I did not edit that unowned service. Adding it means accepting an allowlisted `deliver` (`origin`/`telegram:<chat>`) in `admin_automation.py`. The `delivery` field and its validation are ready for it.
- **ux-ia:** `essential: true` is set on both pages; console.js has to read that flag.
- **ui-visual:** the generic `th` style in console.css uppercases headers; overriding it in Markdown report tables would read better.
- **eng-briefing:** `admin_integrations.saved_reports(ctx, limit)` is available for the briefing's "latest reports"; link to `#reports`.
