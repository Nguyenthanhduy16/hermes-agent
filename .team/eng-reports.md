# Role: eng-reports (Engineer: Reports & Connectors). Read .team/COMMON.md and docs/console-extensions.md. Preview port: 8814.
You own admin_integrations.py, integration_catalog.py, console-integrations.js/.css and their tests
(tests/test_admin_integrations.py, deploy/vercel-admin/tests/console-integrations.test.js). Mark both pages
`essential: true` in register options.
Reports:
  1. Report reader: list saved reports (newest first, filter by report job) and open one in a reader panel that
     renders the saved Markdown safely (headings, lists, bold/italic, code, tables, links with rel=noopener; build DOM
     nodes, never innerHTML; cap size). Demo: 3-4 realistic synthetic reports (daily social summary, weekly work
     digest, competitor/mention watch).
  2. "New report" flow from templates (Daily social summary, Weekly work digest, Mentions watch, KPI snapshot,
     Custom): choose template → connectors (only enabled ones selectable) → schedule (presets: daily 8:00, weekdays,
     weekly Monday, custom cron) → delivery (save in bot home; send to a configured Zalo/Telegram channel when available)
     → confirm. Implement as action descriptors/fields consistent with the existing action dialog; validate server-side;
     reuse the existing automation/cron services (AutomationAdmin/ControlStore) — single writer rule.
  3. "Run now" action for a report job if the existing services support it safely; otherwise omit.
Connectors:
  4. Status-first layout: summary strip, then "Connected", "Needs setup", "Available" sections; cards compact with
     the credential details behind a disclosure; clear primary CTA per card (Set up / Manage / Enable).
  5. Keep the long demo disclaimer as one compact line.
Keep existing tests passing; add tests for the markdown renderer (XSS: script tags, javascript: links, on* attrs) and
the new-report validation.
