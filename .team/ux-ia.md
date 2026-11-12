# Role: ux-ia (UX / information architecture). Read .team/COMMON.md first. Preview port: 8811.
Goal: make the console feel like a product a CEO uses daily, not a research tool.
1. Navigation for two audiences: an "Essentials" set shown by default — Home, Chat, Reports, Automations
   (the current Schedules view, renamed in UI), Connectors, Channels, Memory — and everything else
   (Models, Skills, MCP, Conversations, Activity, Analytics, Experiments, Benchmarks, Users, Usage, keys, config,
   logs, files, plugins, webhooks, access, System/operations, help) under a clearly labelled "Advanced" area
   (collapsible group, remembered in localStorage wrapped in try/catch). Extension pages register with a `group`;
   map groups sensibly and let a page opt into essentials (e.g. `essential: true` in register options) — the
   connectors/reports/briefing pages will set it. Keep all existing routes/hashes working.
2. Home: eng-briefing is building an extension page named "briefing" (console-briefing.js/.css). Add its
   <link>/<script defer> tags in console.html now, and make "#overview"/default landing show Briefing when that
   page is registered (fall back to the current overview otherwise). Keep the old overview reachable as
   "System overview" in Advanced.
3. Long notices: add a kit helper (e.g. `kit.note(title, text, tone)`) that renders a compact one-line notice with a
   "Details" disclosure; use it in console.js pages instead of full paragraphs. Document it in
   docs/console-extensions.md (you may edit that doc's Frontend section).
4. Command palette: Ctrl/Cmd+K opens a palette to jump to any page and run quick actions exposed by pages
   (e.g. "New report", "New automation", "Open chat"). Accessible (focus trap, arrows, Esc). Keep the per-page
   search filter usable and consistent on every page where it applies.
5. Mobile: sidebar drawer + palette must work at 375px width.
Add/adjust tests in deploy/vercel-admin/tests/console.test.js. Coordinate class names with ui-visual by writing
new classes you introduce into your report (ui-visual styles them; add only minimal structural CSS inside console.js
via classes, never inline styles).
