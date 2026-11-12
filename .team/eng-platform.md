# Role: eng-platform (Engineer: production readiness & security). Read .team/COMMON.md. Preview port: 8815.
Goal: the console can go live safely and is easy to deploy/operate.
1. Fix: top-level navigations (GET of the HTML pages / static assets, `Sec-Fetch-Mode: navigate` or no fetch metadata)
   arriving with `Sec-Fetch-Site: cross-site` must render the page (or redirect to login), not JSON 403. Keep the
   strict Origin/Sec-Fetch checks for /api/* and all POSTs. Add regression tests in tests/test_admin_web.py.
2. Health/readiness endpoint (e.g. GET /healthz, no auth, no secrets, minimal body) suitable for Docker/Railway
   health checks; wire it into Dockerfile HEALTHCHECK / compose / railway.toml if appropriate.
3. Static assets: correct cache headers (versioned or short max-age + ETag), security headers review (CSP still strict,
   HSTS when behind HTTPS via config, Referrer-Policy, Permissions-Policy), gzip only if simple and safe.
4. A real (non-demo) start path that is easy: review scripts/run.sh and admin_cli; make sure a first-time owner can
   create the admin account and start the console with one documented command; refuse to start in live mode with
   default/demo password.
5. Write docs/production.md (Vietnamese is fine for docs, UI stays English): requirements, env vars, first run,
   reverse proxy with HTTPS (Caddy/nginx example), systemd and Docker options, backups, upgrade, troubleshooting.
6. Run the FULL Python and JS test suites at the end; fix failures in your area, and list failures in other areas in your
   report (don't fix files you don't own).
