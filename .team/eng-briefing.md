# Role: eng-briefing (Engineer: CEO Briefing home). Read .team/COMMON.md and docs/console-extensions.md. Preview port: 8813.
Build a new extension page "briefing" (title "Briefing", group "main", essential: true, icon "home") that becomes the
CEO's landing page. Backend src/hermes_zalo/admin_briefing.py registers Section("briefing", snapshot, action).
Snapshot (demo: rich synthetic data; live: derive from what already exists — schedules/cron jobs and their last
status, reports saved in the bot home, connectors state from admin_integrations/integration_catalog, usage/quota,
gateway status — reading only, never network calls, tolerate missing data with `available: false` per block):
  - greeting + date + one-sentence "state of things" summary;
  - "Needs your attention" queue: failed automations, connectors needing credentials, quota near limit,
    gateway down, unread/new reports — each with severity and a link (hash) to the page that fixes it;
  - KPI tiles per connected platform (demo: Facebook page reach, Zalo OA messages, Telegram members, X mentions,
    tasks done in work apps) with change vs previous period and a tiny sparkline series;
  - "Latest reports" (title, source, time, 2-line excerpt, link to #reports);
  - "Up next" — next scheduled automations;
  - quick prompts that open #chat with a prefilled message (e.g. "Summarize today's social activity").
Action: "dismiss" an attention item (stored in the console control store via existing services, with revision check)
— keep it simple and safe; if no fitting store exists, make the page read-only and note it.
Frontend console-briefing.js/.css: visually striking but token-based (use console.css variables), sparkline as inline
SVG built with createElementNS, no innerHTML, CSP-safe. Register in EXTENSION_MODULES, CONSOLE_EXTENSIONS and the
build.js list (ux-ia adds the console.html tags). Tests: tests/test_admin_briefing.py and
deploy/vercel-admin/tests/console-briefing.test.js (copy the vm harness pattern from console-observe.test.js).
