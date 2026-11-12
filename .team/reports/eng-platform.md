# eng-platform report — production readiness & security (2026-11-01)

## What changed

**1. Cross-site navigation bug (lead item 5) — fixed** · `src/hermes_zalo/admin_web.py`
- New `Handler.is_navigation()`: GET/HEAD to a non-`/api/` path with `Sec-Fetch-Mode: navigate` + `Sec-Fetch-Dest: document`
  (or no fetch metadata and `Accept: text/html`).
- Only such page loads are exempt from the `Sec-Fetch-Site: cross-site` rejection. Still rejected: every `/api/*` request,
  every POST, a foreign `Origin`, a wrong `Host`, cross-site iframes and cross-site subresource loads (script/style).
  The session cookie stays `SameSite=Strict`.
- Errors on browser navigations (404, wrong host, …) now show a small HTML page with no scripts and an "Open the Hermes
  Agent Console" link, not raw JSON. API errors stay JSON.

**2. Health endpoint** · `admin_web.py`, `admin_cli.py`, `Dockerfile`, `compose.yaml`, `railway.toml`
- `GET`/`HEAD /healthz` needs no login and skips the Host and proxy-secret checks, so Docker, Railway and systemd probes
  work. It returns only `{"status":"ok"}` (200), or `{"status":"unavailable"}` (503) when the bot event loop or the
  account store does not respond. It never includes data, a version number or configuration.
- New command `python -m hermes_zalo.admin_cli healthcheck`, used by the Dockerfile `HEALTHCHECK`. It passes while the
  bot is in standby (`BOT_START_ENABLED=false`) or the console is off; otherwise it probes `127.0.0.1:<port>/healthz`.
- `compose.yaml`: commented example that publishes the console port to loopback only. `railway.toml`: commented
  `healthcheckPath`. It is not enabled, because a required health path would fail standby deployments.

**3. Static caching and security headers** · `admin_web.py`, `deploy/vercel-admin/vercel.json`
- JS/CSS: `Cache-Control: no-cache` + strong `ETag` (sha256), `If-None-Match` → 304, gzip when the browser accepts it
  (memoised by content hash, `Vary: Accept-Encoding`).
- HTML stays `no-store`, so the Back button after logout cannot restore a page that showed data. `/api/*` responses
  stay `no-store` and are never compressed.
- Added to every response: `Permissions-Policy` (camera, microphone, geolocation, payment, usb all off),
  `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`, `X-Robots-Tag: noindex`, and
  `object-src 'none'` in the CSP. The CSP is otherwise unchanged and still strict.
- HSTS is unchanged: it is sent when `ZALO_ADMIN_PUBLIC_URL` (HTTPS) is set. Vercel static headers now match the backend.

**4. Real (non-demo) start path** · `admin_cli.py`, `admin_auth.py`, `scripts/run.sh`
- One command: `scripts/run.sh Console` (or `admin_cli start`). On first run it asks for the owner username and a
  hidden password, creates the owner account with full permissions, then serves. Later runs just start the console.
  Without a terminal it stops with instructions instead of hanging.
- Demo-password guard: `AdminAuth(allow_default_passwords=False)` is the default outside demo mode. Outside the demo:
  - the demo password `LabCoach-Demo-2026` cannot be set via the CLI, the Accounts page or password change;
  - it cannot be used to sign in (403 with instructions), even if the account store was copied from a demo;
  - `serve`/`start` refuse to start when any active account still uses it, or when no account exists.

  `AdminRuntime` turns the guard off only in `mode='demo'`, so the demo is unchanged. The gateway console is also
  covered, through the login block.
- `run.sh`: new `Console` and `ConsoleAccount` actions; extra arguments are passed through and data lives in
  `.runtime/hermes-home`.

**5. `docs/production.md`** (Vietnamese) covers: requirements, first run, environment variables, Caddy and nginx
HTTPS (the doc warns that `proxy_set_header Host` is required), a hardened systemd unit, Docker/Compose/Railway with
the health check, SQLite backup and restore, upgrades, a summary of built-in security, and a troubleshooting table.

**6. Vercel gateway allowlist** (`deploy/vercel-admin/lib/gateway.js`, `tests/gateway.test.js`)
- Added `/api/assistant/briefing` (GET) and `/api/assistant/briefing/action` (POST).
- `/api/assistant/reports` now forwards the `job` and `file` query parameters that eng-reports added. Without this,
  both features would have returned 404/400 behind Vercel.
- New Python test `GatewayRouteSyncTests` fails if any registered console section is missing from the gateway
  allowlist or its query keys differ, so this drift can't happen silently again.

## Tests
- `tests/test_admin_web.py` gained 10 tests:
  - cross-site link opens the page while the API, frames, subresources and POSTs stay strict;
  - HTML error pages on navigation versus JSON on the API;
  - `/healthz` with a foreign Host, with a proxy secret, with HEAD/POST, and a 503 path that leaks nothing;
  - ETag/304/gzip/`q=0` and the security headers, plus HEAD on the API returning 405;
  - HSTS;
  - the demo password refused for set, login and change;
  - CLI preflight refusal and first-run owner creation;
  - `healthcheck` skip/probe;
  - gateway↔sections sync.
- Admin modules: `test_admin_web`, `test_admin_password`, `test_admin_roles`, `test_admin_runtime_history`,
  `test_admin_tickets` → **86 tests, OK**.
- Full Python suite: **1406 tests, 1 failure, 20 skipped**. The failure is outside my area (below).
- Full JS suite (`node --test tests/*.test.js`): **131/131 pass**.
- Live check on port 8815 (temporary home, server killed afterwards):
  - `start` with no terminal → refused with a clear message;
  - with the demo password → "Refusing to start…";
  - after a reset → starts;
  - cross-site navigation to `/` → 200 HTML; cross-site `/api/session` → 403;
  - `/healthz` → `{"status": "ok"}`;
  - console.css served gzip with ETag and `no-cache`.

## Failures in other areas (not fixed — not my files)
- `tests/test_setup.py::test_setup_preserves_provider_and_env_and_disables_tools` expects `platform_toolsets.zalo == []`
  but gets `['web','vision','code_execution','file','skills','todo']`. This comes from the uncommitted
  `scripts/configure.py` change (assistant pivot enables tools). The owner of configure.py should either update the test
  or restore the old behaviour.

## Notes / requests for other roles
- **ux-ia / README owner:** please link `docs/production.md` from `README.md` and `docs/admin.md`, and mention
  `scripts/run.sh Console` as the start command for real use.
- **Anyone adding a console section:** add its route(s) to `deploy/vercel-admin/lib/gateway.js` ROUTES and bump the
  count in `gateway.test.js`. `GatewayRouteSyncTests` now enforces this.
- Unversioned asset URLs are revalidated on every load (cheap 304s). If ux-ia later adds `?v=<hash>` to asset URLs in
  `console.html`, the server could serve `immutable` long cache; this isn't needed now.
- On first start after this change, existing gateway accounts whose password is the demo password can no longer sign in
  outside demo mode. This is intended; reset them with `admin_cli create-account --username NAME --replace`.
