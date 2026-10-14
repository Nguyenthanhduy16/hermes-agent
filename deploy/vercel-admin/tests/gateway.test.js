import test from 'node:test';
import assert from 'node:assert/strict';
import { Readable } from 'node:stream';
import { createHandler, ROUTES, configuration } from '../lib/gateway.js';

const ENV = {
  LABCOACH_API_URL: 'https://backend.example.test',
  LABCOACH_FRONTEND_URL: 'https://admin.example.test',
  LABCOACH_PROXY_SECRET: 'synthetic-gateway-secret-12345678901234567890',
};
const CSRF = 'synthetic_csrf_token_01234567890';
const COOKIE = 'labcoach_admin=synthetic_session_token';
const SAFE_COOKIE = COOKIE + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800; Secure';

function incoming(path = '/api/session', { method = 'GET', data, headers = {}, rawHeaders, query } = {}) {
  const bytes = data === undefined ? null : Buffer.isBuffer(data) ? data : Buffer.from(typeof data === 'string' ? data : JSON.stringify(data));
  const req = Readable.from(bytes ? [bytes] : []);
  req.method = method;
  req.url = path;
  req.headers = {
    host: 'admin.example.test',
    'sec-fetch-site': 'same-origin',
    ...(method === 'POST' ? {
      origin: ENV.LABCOACH_FRONTEND_URL,
      'content-type': 'application/json',
      'x-csrf-token': CSRF,
      ...(bytes ? { 'content-length': String(bytes.length) } : {}),
    } : {}),
    ...headers,
  };
  req.rawHeaders = rawHeaders;
  if (query) req.query = query;
  return req;
}

function outgoing() {
  return {
    headers: {},
    statusCode: 200,
    setHeader(name, value) { this.headers[name.toLowerCase()] = value; },
    end(bytes) {
      this.raw = Buffer.isBuffer(bytes) ? bytes.toString('utf8') : bytes;
      this.data = JSON.parse(this.raw);
    },
  };
}

function json(value = { ok: true }, { status = 200, headers = {} } = {}) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json', ...headers } });
}

async function call(req, { env = ENV, response, fetchImpl, timeoutMs } = {}) {
  const requests = [];
  const handler = createHandler({
    env,
    timeoutMs,
    fetchImpl: async (url, options) => {
      requests.push({ url: String(url), options });
      return fetchImpl ? fetchImpl(url, options) : response || json();
    },
  });
  const res = outgoing();
  await handler(req, res);
  return { res, requests };
}

test('fixed upstream receives only the permitted browser headers and server credential', async () => {
  const { res, requests } = await call(incoming('/api/member/lock', {
    method: 'POST', data: { member_id: 'member-demo', locked: true, confirm: true },
    headers: {
      cookie: 'tracking=private; ' + COOKIE + '; other=private',
      authorization: 'Bearer client-must-never-reach-upstream',
      'x-labcoach-admin-proxy': 'attacker-supplied',
      'x-forwarded-host': 'evil.example.test',
      referer: 'https://evil.example.test/private',
    },
  }));
  assert.equal(res.statusCode, 200);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'https://backend.example.test/api/member/lock');
  assert.deepEqual(Object.keys(requests[0].options.headers).sort(),
    ['Accept', 'Content-Length', 'Content-Type', 'Cookie', 'Origin', 'X-CSRF-Token', 'X-LabCoach-Admin-Proxy'].sort());
  assert.equal(requests[0].options.headers.Cookie, COOKIE);
  assert.equal(requests[0].options.headers.Origin, ENV.LABCOACH_API_URL);
  assert.equal(requests[0].options.headers['X-LabCoach-Admin-Proxy'], ENV.LABCOACH_PROXY_SECRET);
  assert.equal(requests[0].options.headers['X-CSRF-Token'], CSRF);
  assert.equal(requests[0].options.redirect, 'manual');
  assert.equal(requests[0].options.cache, 'no-store');
});

test('rewritten and original request URL contracts preserve allowed filters', async () => {
  for (const [url, query] of [
    ['/api/proxy?__route=members&q=Th%C3%A0nh+vi%C3%AAn&page=2', undefined],
    ['/api/members?q=Th%C3%A0nh+vi%C3%AAn&page=2', { __route: 'members' }],
    ['/api/members?__route=members&q=Th%C3%A0nh+vi%C3%AAn&page=2', undefined],
  ]) {
    const { res, requests } = await call(incoming(url, { query }));
    assert.equal(res.statusCode, 200);
    const target = new URL(requests[0].url);
    assert.equal(target.pathname, '/api/members');
    assert.equal(target.searchParams.get('q'), 'Thành viên');
    assert.equal(target.searchParams.get('page'), '2');
    assert.equal(target.searchParams.has('__route'), false);
  }
});

test('route/method/query allowlist rejects open proxy, unknown endpoints and duplicates', async () => {
  for (const url of [
    '/api/secrets', '/api/backup', '/api/proxy?__route=backup', '/api/proxy?__route=https://evil.example.test/', '/api/proxy?__route=../api/session',
    '/api/session?url=https://evil.example.test/', '/api/session?__route=members',
    '/api/members?page=1&page=2', '/api/proxy?__route=session&__route=members',
    '//evil.example.test/api/session', 'https://evil.example.test/api/session',
    '/api/%2e%2e/secrets', '/api/session#fragment', '/api/session\\evil',
  ]) {
    const { res, requests } = await call(incoming(url));
    assert.ok(res.statusCode >= 400, url);
    assert.equal(requests.length, 0, url);
  }
  const { res, requests } = await call(incoming('/api/member/lock'));
  assert.equal(res.statusCode, 405);
  assert.equal(requests.length, 0);
  assert.equal(Object.keys(ROUTES).length, 33);
});

test('registration repair stays on fixed POST routes with CSRF and no phone query', async () => {
  for (const [path, data] of [
    ['/api/member/registration/preview', { member_id: 'member-synthetic', phone: '0900000001', reason: 'Synthetic test' }],
    ['/api/member/registration/confirm', { preview_token: 'synthetic-preview', confirm: true }],
    ['/api/member/registration/reset', { member_id: 'member-synthetic', request_id: 'DK-synthetic', expected_revision: 3, reason: 'Synthetic test', confirm: true }],
  ]) {
    const { res, requests } = await call(incoming(path, { method: 'POST', data, headers: { cookie: COOKIE } }));
    assert.equal(res.statusCode, 200);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, ENV.LABCOACH_API_URL + path);
    assert.equal(requests[0].options.headers.Cookie, COOKIE);
    assert.equal(requests[0].options.headers['X-CSRF-Token'], CSRF);
    assert.deepEqual(JSON.parse(requests[0].options.body.toString('utf8')), data);
    for (const req of [incoming(path), incoming(path + '?phone=0900000001', { method: 'POST', data }),
      incoming(path, { method: 'POST', data, headers: { origin: 'https://evil.example.test' } })]) {
      const blocked = await call(req);
      assert.ok([400, 403, 405].includes(blocked.res.statusCode));
      assert.equal(blocked.requests.length, 0);
    }
  }
});

test('self password change preserves CSRF, clears session cookie and forbids target query', async () => {
  const payload = { current_password: 'synthetic-current-password', new_password: 'synthetic-new-password', confirm_password: 'synthetic-new-password' };
  const { res, requests } = await call(incoming('/api/password/change', {
    method: 'POST', data: payload, headers: { cookie: COOKIE },
  }), { fetchImpl: async (_url, options) => {
    assert.equal(options.headers.Cookie, COOKIE);
    assert.equal(options.headers['X-CSRF-Token'], CSRF);
    assert.deepEqual(JSON.parse(options.body.toString('utf8')), payload);
    return new Response(JSON.stringify({ ok: true }), { headers: {
      'Content-Type': 'application/json', 'Set-Cookie': 'labcoach_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0; Secure',
    } });
  } });
  assert.equal(res.statusCode, 200);
  assert.equal(requests.length, 1);
  assert.match(res.headers['set-cookie'][0], /Max-Age=0/);
  for (const options of [{ headers: { 'x-csrf-token': undefined } }, { headers: { origin: 'https://other.example.test' } }]) {
    const denied = await call(incoming('/api/password/change', { method: 'POST', data: payload, ...options }));
    assert.equal(denied.res.statusCode, 403);
    assert.equal(denied.requests.length, 0);
  }
  const denied = await call(incoming('/api/password/change?username=other', { method: 'POST', data: payload }));
  assert.equal(denied.res.statusCode, 400);
  assert.equal(denied.requests.length, 0);
});

test('ticket list and detail preserve only the supported filters', async () => {
  for (const path of ['/api/tickets?q=Th%C3%A0nh&status=pending_manager&page=2', '/api/ticket?id=DC-DEMO']) {
    const { res, requests } = await call(incoming(path, { headers: { cookie: COOKIE } }));
    assert.equal(res.statusCode, 200);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, ENV.LABCOACH_API_URL + path);
    assert.equal(requests[0].options.headers.Cookie, COOKIE);
  }
  for (const path of ['/api/tickets?role=manager', '/api/ticket?id=DC-DEMO&id=DC-OTHER', '/api/ticket/action?action=approve']) {
    const { res, requests } = await call(incoming(path));
    assert.ok(res.statusCode >= 400);
    assert.equal(requests.length, 0);
  }
});

test('ticket approval preserves CSRF and expected version and never retries a failed write', async () => {
  const payload = { id: 'DC-DEMO', action: 'approve', expected_version: 4, reason: '', confirm: true };
  const { res, requests } = await call(incoming('/api/ticket/action', {
    method: 'POST', data: payload, headers: { cookie: COOKIE },
  }), { fetchImpl: async (_url, options) => {
    assert.equal(options.headers.Cookie, COOKIE);
    assert.equal(options.headers['X-CSRF-Token'], CSRF);
    assert.deepEqual(JSON.parse(options.body.toString('utf8')), payload);
    throw new Error('synthetic uncertain result');
  } });
  assert.equal(res.statusCode, 502);
  assert.equal(requests.length, 1);
  const denied = await call(incoming('/api/ticket/action', {
    method: 'POST', data: payload, headers: { 'x-csrf-token': undefined },
  }));
  assert.equal(denied.res.statusCode, 403);
  assert.equal(denied.requests.length, 0);
});

test('canonical browser Host and Origin are checked before any upstream call', async () => {
  for (const headers of [
    { host: 'evil.example.test', 'x-forwarded-host': 'admin.example.test' },
    { origin: 'https://evil.example.test' },
    { origin: 'null' },
    { origin: ENV.LABCOACH_FRONTEND_URL + '/' },
    { 'sec-fetch-site': 'cross-site' },
    { host: ['admin.example.test', 'evil.example.test'] },
  ]) {
    const { res, requests } = await call(incoming('/api/session', { headers }));
    assert.ok([400, 403].includes(res.statusCode));
    assert.equal(requests.length, 0);
  }
  const { res, requests } = await call(incoming('/api/login', { method: 'POST', data: {}, headers: { origin: undefined } }));
  assert.equal(res.statusCode, 403);
  assert.equal(requests.length, 0);
});

test('duplicate security headers and session cookies are rejected', async () => {
  for (const [headers, rawHeaders] of [
    [{}, ['Host', 'admin.example.test', 'Host', 'evil.example.test']],
    [{ cookie: COOKIE + '; ' + COOKIE }, undefined],
    [{ cookie: 'labcoach_admin=bad value' }, undefined],
    [{ origin: ENV.LABCOACH_FRONTEND_URL }, ['Origin', ENV.LABCOACH_FRONTEND_URL, 'Origin', ENV.LABCOACH_FRONTEND_URL]],
  ]) {
    const { res, requests } = await call(incoming('/api/session', { headers, rawHeaders }));
    assert.equal(res.statusCode, 400);
    assert.equal(requests.length, 0);
  }
});

test('POST requires a syntactically valid CSRF token and JSON body', async () => {
  for (const options of [
    { headers: { 'x-csrf-token': undefined }, data: {} },
    { headers: { 'x-csrf-token': 'tiny' }, data: {} },
    { headers: { 'content-type': 'text/plain' }, data: '{}' },
    { data: '[1,2]' }, { data: 'null' }, { data: '{bad}' }, { data: '' },
  ]) {
    const { res, requests } = await call(incoming('/api/login', { method: 'POST', ...options }));
    assert.ok([400, 403, 415].includes(res.statusCode));
    assert.equal(requests.length, 0);
  }
});

test('original raw JSON bytes reach backend so duplicate keys are not silently collapsed', async () => {
  const payload = '{"locked":false,"locked":true,"note":"Tiếng Việt"}';
  const { requests } = await call(incoming('/api/member/lock', { method: 'POST', data: payload }));
  assert.equal(requests[0].options.body.toString('utf8'), payload);
  assert.equal(Number(requests[0].options.headers['Content-Length']), Buffer.byteLength(payload));
});

test('body limit applies before forwarding and cannot be bypassed by missing/false length', async () => {
  for (const options of [
    { data: { text: 'a'.repeat(32768) } },
    { data: { text: 'a'.repeat(32768) }, headers: { 'content-length': undefined } },
    { data: {}, headers: { 'content-length': '99' } },
    { data: {}, headers: { 'transfer-encoding': 'chunked' } },
  ]) {
    const { res, requests } = await call(incoming('/api/login', { method: 'POST', ...options }));
    assert.ok([400, 413].includes(res.statusCode));
    assert.equal(requests.length, 0);
  }
});

test('cookie security flags, logout cookie and response status survive gateway', async () => {
  for (const cookie of [SAFE_COOKIE, 'labcoach_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0; Secure']) {
    const { res } = await call(incoming(), { response: json({ user: null }, { headers: { 'Set-Cookie': cookie } }) });
    assert.equal(res.statusCode, 200);
    assert.deepEqual(res.headers['set-cookie'], [cookie]);
    assert.equal(res.headers['cache-control'], 'private, no-store, max-age=0');
    assert.equal(res.headers['cdn-cache-control'], 'no-store');
    assert.equal(res.headers['vercel-cdn-cache-control'], 'no-store');
    assert.equal(res.headers['access-control-allow-origin'], undefined);
  }
  const { res } = await call(incoming(), { response: json({ error: 'Chưa đăng nhập' }, { status: 401 }) });
  assert.equal(res.statusCode, 401);
  assert.equal(res.data.error, 'Chưa đăng nhập');
});

test('insecure, foreign or domain-scoped upstream cookies are not issued to browser', async () => {
  for (const cookie of [SAFE_COOKIE.replace('; Secure', ''), SAFE_COOKIE.replace('; HttpOnly', ''),
    SAFE_COOKIE + '; Domain=backend.example.test', 'other=cookie; HttpOnly; SameSite=Strict; Path=/; Secure']) {
    const { res } = await call(incoming(), { response: json({}, { headers: { 'Set-Cookie': cookie } }) });
    assert.equal(res.statusCode, 502);
    assert.equal(res.headers['set-cookie'], undefined);
  }
});

test('upstream redirects are never followed or exposed', async () => {
  const { res, requests } = await call(incoming(), {
    response: new Response(null, { status: 302, headers: { Location: 'https://evil.example.test/' } }),
  });
  assert.equal(res.statusCode, 502);
  assert.equal(requests.length, 1);
  assert.equal(res.headers.location, undefined);
  assert.equal(res.raw.includes('evil.example.test'), false);
});

test('timeout aborts exactly one request; no write retries or sensitive exception data', async () => {
  let signal;
  const { res, requests } = await call(incoming('/api/member/lock', { method: 'POST', data: {} }), {
    timeoutMs: 10,
    fetchImpl: async (_url, options) => { signal = options.signal; return new Promise(() => {}); },
  });
  assert.equal(res.statusCode, 504);
  assert.equal(signal.aborted, true);
  assert.equal(requests.length, 1);
  assert.equal(res.headers['vercel-cdn-cache-control'], 'no-store');
  const failed = await call(incoming(), { fetchImpl: async () => { throw new Error('secret=' + ENV.LABCOACH_PROXY_SECRET); } });
  assert.equal(failed.res.statusCode, 502);
  assert.equal(failed.res.raw.includes(ENV.LABCOACH_PROXY_SECRET), false);
});

test('non-JSON and oversized upstream responses are bounded and hidden', async () => {
  for (const response of [new Response('<h1>upstream detail</h1>'), json({ data: 'x'.repeat(2 * 1024 * 1024) })]) {
    const { res } = await call(incoming(), { response });
    assert.equal(res.statusCode, 502);
    assert.ok(res.raw.length < 400);
  }
});

test('missing/invalid secret or origins fail closed without exposing configured values', async () => {
  for (const changed of [
    { LABCOACH_PROXY_SECRET: '' }, { LABCOACH_PROXY_SECRET: 'short' },
    { LABCOACH_PROXY_SECRET: 'a'.repeat(256) + 'b' }, { LABCOACH_PROXY_SECRET: 'a'.repeat(31) + '\n' },
    { LABCOACH_API_URL: 'http://backend.example.test' },
    { LABCOACH_API_URL: 'https://backend.example.test/private?secret=do-not-show' },
    { LABCOACH_API_URL: 'https://user:password@backend.example.test' },
    { LABCOACH_FRONTEND_URL: ENV.LABCOACH_API_URL },
  ]) {
    const { res, requests } = await call(incoming(), { env: { ...ENV, ...changed } });
    assert.equal(res.statusCode, 503);
    assert.equal(requests.length, 0);
    assert.equal(res.raw.includes('do-not-show'), false);
    assert.equal(res.raw.includes(ENV.LABCOACH_PROXY_SECRET), false);
  }
  assert.equal(configuration(ENV).backend.origin, ENV.LABCOACH_API_URL);
});
