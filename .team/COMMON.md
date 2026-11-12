# Team brief — Hermes Agent Console: from demo to production (2026-11-01)

You are one role in a 5-agent team coordinated by a lead (Claude in pane w4:pQ). The CEO (user) wants
the Hermes assistant console to become a real, easy, beautiful, impressive management page: an assistant
that connects to social networks and work platforms, monitors them, automates work and REPORTS to the CEO;
the CEO manages and extends the bot from this UI.

## Lead's evaluation of the current demo (http://127.0.0.1:8799, admin / LabCoach-Demo-2026, demo mode)
1. Home is a technical dashboard (conversations/tokens/cost). A CEO needs a *briefing*: what needs my attention,
   today's KPIs from connected platforms, latest reports, what's scheduled next.
2. Navigation has ~27 items mixing CEO features with research/dev tools (Experiments, Benchmarks, Files, Plugins,
   Webhooks, Logs…). Too much for daily use.
3. Every page opens with long blue/orange disclaimer paragraphs. Noisy; looks like a prototype.
4. Reports page only lists scheduled jobs: no nice report reader, no "create report from template" flow.
   Schedules and Reports overlap. Connectors cards are dense; setup flow is unclear.
5. Production bug: a top-level navigation with `Sec-Fetch-Site: cross-site` (e.g. clicking the console link in an
   email/Zalo) returns raw JSON `{"error": "This request origin is not allowed."}`.
6. Topbar search disappears on some pages; no command palette / quick actions.

## Team & file ownership (do NOT edit files owned by another role; Edit with small string replacements, never rewrite a whole shared file)
| agent | role | owns |
|---|---|---|
| ux-ia | UX / information architecture | console.html, console.js, deploy/vercel-admin/tests/console.test.js |
| ui-visual | UI / visual design | console.css (only) |
| eng-briefing | Engineer: CEO Briefing home | NEW admin_briefing.py, console-briefing.js/.css, tests/test_admin_briefing.py, deploy/vercel-admin/tests/console-briefing.test.js; one-line registrations in console_sections.py EXTENSION_MODULES, admin_web.py CONSOLE_EXTENSIONS tuple, deploy/vercel-admin/scripts/build.js list |
| eng-reports | Engineer: Reports & Connectors | admin_integrations.py, integration_catalog.py, console-integrations.js/.css, their tests |
| eng-platform | Engineer: production readiness & security | admin_web.py (except CONSOLE_EXTENSIONS line), admin_cli.py, admin_auth.py, Dockerfile, compose.yaml, scripts/run.sh, deploy/ (except build.js list line), NEW docs/production.md, tests/test_admin_web.py |

Paths are under src/hermes_zalo/ and src/hermes_zalo/admin_static/ unless shown.

## Shared rules
- Read docs/console-extensions.md first. Follow its conventions: DOM via kit.node/textContent only (no innerHTML),
  CSP forbids inline style attributes, colours from CSS variables in console.css, demo mode returns synthetic data only,
  never return secrets, write actions use action descriptors.
- UI language: English. Keep demo data for every page.
- Several other agents edit this working tree at the same time. Do not run git checkout/reset/stash/commit/push.
  Do not reformat files you don't own.
- Tests: `PYTHONPATH=src:. .runtime/venv/bin/python -m unittest discover -s tests -q` (or targeted modules) and
  `cd deploy/vercel-admin && node --test tests/*.test.js`. Your own area's tests must pass when you finish.
- Do NOT start/stop the servers on ports 8790/8799. If you need a live preview, start your own demo on a free port
  (yours: see your brief) with `PYTHONPATH=src .runtime/venv/bin/python -m hermes_zalo.admin_cli demo --port <port>`,
  and kill it when done.
- When finished, write a short report to .team/reports/<your-agent-name>.md: what changed (files), how to see it,
  tests run + results, open issues / what you need from other roles. Then stop and wait.
