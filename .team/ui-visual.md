# Role: ui-visual (UI / visual design). Read .team/COMMON.md first. Preview port: 8812.
Goal: an impressive, polished, modern look (think Linear / Vercel / Stripe dashboard quality) in light AND dark.
You own src/hermes_zalo/admin_static/console.css only. Do not rename/remove existing class names used by JS.
1. Refine design tokens on :root (colour scale, accent, semantic good/warn/bad/info, surfaces, borders, radii,
   shadows, spacing, type scale with a clean system font stack). Dark theme equally polished.
2. Polish all shared components: sidebar (active state, group labels, collapsible groups), topbar, page heading,
   cards/panels, metric tiles (bigger numbers, subtle trend/sub-label), tables (sticky header, zebra/hover),
   badges/status pills, buttons (primary/secondary/ghost/danger, focus-visible rings), forms, dialogs, toasts,
   empty states, skeleton loading shimmer, charts (bar colours from tokens), notices (compact).
3. Subtle motion (page fade/slide-in, hover lift) respecting prefers-reduced-motion. Accessible contrast (AA).
4. Responsive: no horizontal scroll at 375px; card grids reflow.
5. Upcoming classes from other roles: read .team/reports/*.md and the new console-*.js files periodically
   (ux-ia adds a command palette, `kit.note` compact notices, an Advanced nav area; eng-briefing adds a briefing
   home). Provide the shared styling for the palette (.palette*) and notes (.note*) in console.css; extension pages
   keep their own page-specific CSS in their own files.
Verify visually on your own demo port with the Chrome tools if available, otherwise by careful review. Report the
token names you introduced so engineers can use them.
