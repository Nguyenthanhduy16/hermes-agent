# ux-ia report — navigation, Home, notes, command palette (2026-11-01)

## What changed
Files (all mine, plus the Frontend section of the doc):
- `src/hermes_zalo/admin_static/console.html`
  - Sidebar rebuilt as **Essentials** (Home, Chat, Reports*, Automations, Connectors*, Channels, Memory) plus a collapsible
    **Advanced** area with labelled subgroups: Agent (Models, Skills, Tools MCP), Integrations (non-essential integration pages),
    Observe (System overview, Conversations, Agent activity, Analytics, Experiments, Benchmarks), People & usage (Users, Usage & cost),
    System (API keys, Settings, Logs, Files, Plugins, Webhooks, Admin accounts, Help, System/operations).
    *Reports and Connectors are inserted by their extension in the documented order.
  - "Schedules" is now **Automations** in the UI (route `#routines` unchanged; `#automations` and `#schedules` are aliases).
  - `<link>`/`<script defer>` for `console-briefing.css/js`.
  - Topbar: a **"Search or jump to… Ctrl K"** trigger (`#palette-open`) that is always visible. The per-page filter (`#search`)
    moved into the page heading (`#search-box.search.page-filter`, "Filter this page", `/` shortcut) and shows on every page that has
    records to filter.
  - New `<dialog id="palette">` command palette, plus an `i-info` sprite symbol.
- `src/hermes_zalo/admin_static/console.js`
  - IA model: `ESSENTIALS`, `essential(view)` (built-in list, `essential: true` or `group: "main"`), `NAV_GROUPS`, aliases, and
    insertion of essential extension buttons in the fixed order. The Advanced open/closed state is remembered in localStorage
    (`hermes-console-advanced`, try/catch). Opening an Advanced page expands the area without overwriting the saved preference.
  - **Briefing as Home**: when `briefing` registers, `#nav-home` points to it, and `#overview`, `#home` and the default landing
    resolve to it, including when the script registers after sign-in. The old dashboard is `#system-overview` ("System overview" under
    Advanced → Observe). It reads `/api/assistant/overview`, needs owner access, and is hidden from the nav and palette until the
    briefing exists. If briefing is absent, Home stays the old overview.
  - **`kit.note(title, text, tone)`** gives a compact one-line notice with an accessible "Details" disclosure (button with
    aria-expanded/aria-controls). **`kit.noteFrom(message, tone)`** splits a server message into a first-sentence title and details.
    All built-in paragraph disclaimers (users, memory, keys, runtime, access, files, skills, automations, scheduler warning,
    section messages, "change did not complete", unavailable data) now use notes. Per-job error notices stay as they are.
  - **Command palette** (Ctrl/Cmd+K, or the topbar trigger):
    - Contents: quick actions (Open chat, New report, New automation, Connect an app, Add memory), commands contributed by pages
      (`commands: [{label, keywords?, icon?, run(kit)}]`, up to 8 per page, invalid ones ignored), the current page's untargeted
      descriptor actions ("On this page"), every allowed page (Essentials first, then Advanced), and preferences (theme, change password).
      Typing anything also offers "Ask Hermes: "…"", which opens Chat with the text prefilled. It never sends.
    - "New automation" and "New report" navigate and then open the page's single untargeted `create*`/`new*` action once the snapshot
      loads. If there isn't exactly one, they just open the page. Demo snapshots have no actions, so in demo they only navigate.
    - Accessibility: modal `<dialog>` (focus trap and Esc are native), combobox/listbox with `aria-activedescendant`. ↑/↓/PgUp/PgDn/Tab
      move with wrap-around, Enter runs. A backdrop click closes the palette, and focus returns to the opener or moves to `#main` after
      navigating. Results are ranked: label prefix > word prefix > substring > keywords/description.
  - `kit.openChat(text)` prefills Chat; eng-briefing can use it for quick prompts. `kit.openPalette()` is also exported.
  - Mobile drawer: opening it focuses the current nav item, and Esc closes it and returns focus to the menu button.
- `deploy/vercel-admin/tests/console.test.js`: 11 new tests (IA/HTML structure, aliases and Advanced persistence with blocked storage,
  briefing takeover and System overview API, note a11y/XSS, notes replacing paragraphs, palette ranking/keyboard/sections,
  Ask Hermes doesn't send, pending "New automation" action, admin-role palette filtering, sprite icon coverage). The harness exposes
  `register` and accepts extra globals.
- `docs/console-extensions.md`: Frontend section documents `essential`, `commands`, the Essentials/Advanced groups, briefing-as-Home,
  aliases, `kit.note`/`noteFrom`, the palette, `kit.openChat`, and the page filter.

## How to see it
`PYTHONPATH=src .runtime/venv/bin/python -m hermes_zalo.admin_cli demo --port 8811` → admin / LabCoach-Demo-2026.
- Lands on `#briefing` with Home highlighted.
- Press Ctrl+K, type "rep", ↓, Enter.
- Open Advanced → System overview.
- `#automations` and `#schedules` open Automations.
- The API keys page shows compact notes.

Verified live (Chrome at 1920px, plus headless Chrome over CDP at 375×740 with mobile emulation):
- No horizontal scroll at 375px (scrollWidth 375).
- Drawer opens with focus on Home; Esc closes it and returns focus.
- Palette is 351px wide and keeps focus in its input; Enter runs a command, and choosing an Advanced page auto-expands Advanced.

## Tests
- `node --test tests/console.test.js tests/console-observe.test.js tests/console-bench.test.js tests/console-operations.test.js tests/console-briefing.test.js`
  → **61 pass, 0 fail**.
- Full `node --test tests/*.test.js`: my files pass. Failures belong to other roles' in-progress work:
  - `build.test.js`: eng-briefing added `briefing` to the build.js list, but build.test.js (deploy/, eng-platform) still expects
    four extensions. It needs `'briefing'` added to its two lists.
  - `console-integrations.test.js` (3 tests): console-integrations.js was being rewritten by eng-reports at 10:42, and their tests
    weren't updated yet. Unrelated to console.js.
- Python unittest suite: see the bottom of this file.

## New class names for ui-visual (structure only; console.css already styles most of them)
- Nav: `.nav-essentials`, `.nav-advanced-area`, `.nav-subgroup` (inside `#advanced-nav.nav-advanced`), `#advanced-toggle.nav-toggle`
  (now contains an icon, a `<span>` label and a `.chevron`).
- Topbar and heading: `.palette-trigger`, `.palette-trigger-label` (hide on mobile), `#search-shortcut` kbd, `.search.page-filter`
  in `.heading-actions`, `.page-filter-key`.
- Palette: `dialog.palette`, `.palette-box`, `.palette-search`, `.palette-close`, `.palette-list`, `.palette-section`,
  `.palette-item(.active)`, `.palette-icon`, `.palette-label`, `.palette-hint`, `.palette-empty`, `.palette-footer kbd`.
- Notes: `.note.{info|warning|error|good}`, `.note-icon`, `.note-body`, `.note-line`, `.note-title`, `.note-toggle`, `.note-text`.
  Already styled; they look good in the demo.

## Open items / requests
- eng-reports / eng-briefing / others: replace long paragraph disclaimers in your own console-*.js with `kit.note()` or
  `kit.noteFrom()`. Expose a `commands` list (for example Reports → "Open latest report"). Give the new-report flow a single
  untargeted `create_report`-style action so the palette's "New report" opens it directly.
- eng-briefing: use `kit.openChat(prompt)` for quick prompts.
- eng-platform: update build.test.js for `briefing`.
- The "New scheduled job" action label comes from admin_automation.py. Consider renaming it "New automation" (it isn't owned by any
  role in this brief).

## Python suite
`PYTHONPATH=src:. .runtime/venv/bin/python -m unittest discover -s tests -q` ran 1392 tests: 1 failure, 20 skipped.
- The failure, `test_setup.SetupTests.test_setup_preserves_provider_and_env_and_disables_tools`, expects the `zalo` toolsets to be `[]`
  but they are now web/vision/…. It is in setup/configure (pre-existing working-tree change for the assistant pivot), not in any
  console file. Owner: whoever owns `scripts/configure.py` and `tests/test_setup.py` (not assigned in this brief).
