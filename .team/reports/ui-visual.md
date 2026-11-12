# ui-visual report: DONE

## What changed
- `src/hermes_zalo/admin_static/console.css` is the only file I touched, and I rewrote it completely. Every existing class name
  still works, nothing was renamed or removed, and all existing token names (`--bg --surface --surface-2/3 --border
  --border-strong --text --text-2 --muted --faint --accent* --good/warn/bad/info(-soft) --chart --chart-strong --grid
  --shadow --shadow-lg --radius --radius-sm`) are still there with refined values.
- Light and dark themes are equally polished. Dark mode follows `prefers-color-scheme` or `data-theme="dark"`, and
  `data-theme="light"` forces light. I checked colour contrast with a script: muted text, the semantic colours on their
  `-soft` backgrounds, and white on the primary and danger buttons all pass WCAG AA.
- Shared components:
  - Sidebar: active indicator bar, uppercase group labels, styling for ux-ia's Essentials / Advanced / subgroup
    structure, `.nav-count`.
  - Topbar: blurred sticky bar, `.palette-trigger`, `.search.page-filter` in the page heading, pulsing status dot.
  - Page heading, panels, cards with hover lift, and the hero.
  - Metrics: 30px tabular numbers, `.metric-trend.up/.down`, tone classes `.metric.good/.warning/.error/.info`.
  - Tables: sticky header inside `.table-scroll` (max-height 72vh), uppercase headers, zebra rows, row hover.
  - Badges (adds `.accent` and `.plain`), chips, buttons (primary / secondary / `.button.ghost` / danger /
    `.button.small` / `.button.large`), and focus rings via `--ring`.
  - Forms, including a custom select chevron, `aria-invalid`, and read-only fields.
  - Dialogs: entrance animation, blurred backdrop, footer bar.
  - Toast: bottom-right, slides in, with tone dots `.toast.error/.warning/.info`.
  - Empty states.
  - Loading: a full-page load (`#content > .loading`) shows a shimmer skeleton of tiles plus a panel, and `.skeleton`
    utilities (`.line`, `.title`, `.block`) are available.
  - Charts: bar hover, `.series-1..6`, `.line`, `.area`.
  - Compact `.notice` (tinted, with a left accent).
- Command palette, styled to ux-ia's actual markup: `dialog.palette > .palette-box > .palette-search / .palette-list /
  .palette-section / .palette-item(.active|[aria-selected]) > .palette-icon + .palette-label + .palette-hint`, plus
  `.palette-empty`, `.palette-footer` and `.palette-close`. The active row shows a ↵ hint.
- `kit.note`, styled to ux-ia's markup: `.note.<tone> > .note-icon + .note-body > .note-line > .note-title + .note-toggle`,
  plus `p.note-text` as the Details body. Tones are info, good, warning, error and neutral.
- Motion: `rise`/`fade` keyframes for dialogs, toasts, chat bubbles and the login card, and the "thinking" dots in chat.
  `prefers-reduced-motion` turns animations and transitions off. A print stylesheet hides the app chrome.
- Responsive: the sidebar becomes a drawer with a scrim, metrics go to 2 columns, card grids use
  `minmax(min(290px,100%),1fr)`, the demo banner fits on one line on phones, and dialog buttons go full width.

## Tokens engineers can use (all defined for light and dark)
- Type: `--font-sans --font-mono --text-xs(11.5) --text-sm(12.5) --text-base(13.5) --text-md(14) --text-lg(16)
  --text-xl(20) --text-2xl(26) --text-3xl(30)`
- Spacing: `--space-1(4) --space-2(8) --space-3(12) --space-4(16) --space-5(20) --space-6(24) --space-8(32) --space-10(40)`
- Radii: `--radius-xs(6) --radius-sm(8) --radius(12) --radius-lg(16) --radius-full`
- Motion: `--ease --ease-out --dur-fast --dur --dur-slow`
- Layout: `--sidebar-width --topbar-height --content-max`
- Surfaces: `--surface-raised --sidebar-bg --hover --active --border-subtle --overlay --skeleton --skeleton-shine`
- Brand: `--accent-2 --brand-gradient --ring` (a focus ring box-shadow)
- Semantic borders: `--good-border --warn-border --bad-border --info-border`. Solid danger: `--bad-solid --on-bad`
- Charts: `--chart-1 … --chart-6` (categorical), `--chart --chart-strong --grid`
- Elevation: `--shadow-xs --shadow-sm --shadow --shadow-md --shadow-lg`

The briefing, integrations, observe and bench CSS already use these tokens. I checked that every variable referenced
in any `console-*.css` file is defined.

## How to see it
`PYTHONPATH=src .runtime/venv/bin/python -m hermes_zalo.admin_cli demo --port 8812`, sign in with the demo admin
account, then use the moon/sun button for dark mode and Ctrl+K for the palette.

## Verification
- Visual: Claude in Chrome at 1567px (light and dark: Home, Memory with an open note, palette, Advanced nav). I also
  drove headless Chrome over CDP and took screenshots at 1440px light/dark (Briefing, Users, Chat, Reports, Agent
  activity, Skills) and at 375px light/dark (Briefing, Overview, Users, Connectors, drawer menu, palette).
- No horizontal scroll at 375px on any of the 26 nav pages: `scrollWidth == clientWidth` everywhere, and no element
  outside `.table-scroll`/`pre` extends past the viewport.
- Tests: `PYTHONPATH=src:. .runtime/venv/bin/python -m unittest tests.test_admin_web tests.test_admin_runtime_history`
  passed. `cd deploy/vercel-admin && node --test tests/*.test.js` passed 131/131. The CSS braces balance (533/533).

## Open items / requests to other roles
- **ux-ia**:
  - Optional page transition: add the class `page-enter` to `#content` when `navigate()` renders a new view, and remove
    it on `animationend` or the next render. Don't add it on polls or filter keystrokes. The staggered fade-up is
    already in console.css; I didn't animate `#content > *` unconditionally because polling and filtering re-render
    the page.
  - Optional: `toast(text, tone)` could set `.toast.error/.warning/.info`. Those classes are already styled.
  - The page title still reads "Briefing" while the nav item says "Home". Consider matching them.
- **eng-briefing**: the large decorative circle in the hero is fairly heavy in dark mode. Consider lower opacity, or
  `color-mix(in srgb, var(--accent) 8%, transparent)`.
- **eng-reports**: the long intro paragraph on Connectors and Reports could become `kit.note` (it's 6+ lines at 375px).
- I didn't fix the known cross-site navigation bug (raw JSON on a cross-site top-level navigation). It also blocks
  Chrome-extension navigation to the console, so I had to load it from inside the page. That's eng-platform's area.
