# Visual design — Round 2 complete

Codex takeover, 2026-11-01.

- Audited all 27 authenticated pages at 1440×1000 and 375×900, both light and dark: 108 screenshots, plus four login screenshots. Checked contact sheets and individual Home/report-dialog images. No document overflow, page errors or browser runtime exceptions.
- Markdown reader headers preserve original capitalization with `.int-reader .int-md th`.
- Login uses the brand gradient, a restrained accent stripe and product-oriented tagline in English.
- Fixed the long report action dialog on mobile: fields scroll within the form and Cancel / Apply change remain visible. Verified a settled screenshot after animation; the dialog surface is opaque.
- Existing dark hero decoration is low-alpha, and mobile Home uses readable stacked sections/two-column KPI cards.

Files: `src/hermes_zalo/admin_static/console.css`, `console.html` (login copy).

Evidence: `/tmp/hermes-r2-visual/{1440,375}-{light,dark}-*.png`, four `contact-*.jpg` sheets, `audit.json`, `flow-audit.json`, `375-dark-report-dialog.png`. The modal correction was rechecked separately after the full page audit; it changes only action-form layout.

Browser flows: exact Home report selection, New automation creation and New report creation with synthetic Telegram delivery all passed. Full suite results: [codex-r2-handoff.md](codex-r2-handoff.md). No open visual findings from this matrix.
