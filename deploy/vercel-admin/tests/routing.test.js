import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Readable } from 'node:stream';
import routingUtils from '@vercel/routing-utils';
import { createHandler, ROUTES } from '../lib/gateway.js';

const config = JSON.parse(await readFile(new URL('../vercel.json', import.meta.url), 'utf8'));
const transformed = routingUtils.getTransformedRoutes(config);
const ENV = {
  LABCOACH_API_URL: 'https://backend.example.test',
  LABCOACH_FRONTEND_URL: 'https://admin.example.test',
  LABCOACH_PROXY_SECRET: 'synthetic-routing-secret-12345678901234567890',
};
const CSRF = 'synthetic_csrf_token_01234567890';
const COOKIE = 'labcoach_admin=synthetic_session_token';
const SET_COOKIE = COOKIE + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800; Secure';

// Compile the actual deployment configuration using Vercel's own transformer.
// Handwritten gateway URLs miss query parameters injected by this transformer.
function rewrittenUrl(url) {
  const { routes, error } = transformed;
  assert.equal(error, null, 'Vercel must accept the checked-in routing configuration');
  const incoming = new URL(url, ENV.LABCOACH_FRONTEND_URL);
  const matches = routes.filter(route => typeof route.dest === 'string' &&
    new RegExp(route.src).test(incoming.pathname));
  assert.equal(matches.length, 1, 'The API URL must select exactly one rewrite');
  const route = matches[0];
  const rewritten = new URL(incoming.pathname.replace(new RegExp(route.src), route.dest),
    ENV.LABCOACH_FRONTEND_URL);
  for (const [key, value] of incoming.searchParams) rewritten.searchParams.append(key, value);
  return rewritten.pathname + rewritten.search;
}

function request(url, { method = 'GET', payload, cookie, csrf = CSRF } = {}) {
  const bytes = payload === undefined ? null : Buffer.from(JSON.stringify(payload));
  const req = Readable.from(bytes ? [bytes] : []);
  req.method = method;
  req.url = rewrittenUrl(url);
  req.headers = {
    host: 'admin.example.test',
    'sec-fetch-site': 'same-origin',
    ...(cookie ? { cookie } : {}),
    ...(method === 'POST' ? {
      origin: ENV.LABCOACH_FRONTEND_URL,
      'content-type': 'application/json',
      'content-length': String(bytes?.length ?? 0),
      'x-csrf-token': csrf,
    } : {}),
  };
  return req;
}

function response() {
  return {
    statusCode: 200,
    headers: {},
    setHeader(key, value) { this.headers[key.toLowerCase()] = value; },
    end(bytes) { this.data = JSON.parse(Buffer.isBuffer(bytes) ? bytes.toString('utf8') : bytes); },
  };
}

function json(data, headers = {}) {
  return new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json', ...headers } });
}

test('deployed Vercel rewrite passes every Admin route and preserves only its permitted filters', async () => {
  for (const [path, definition] of Object.entries(ROUTES)) {
    const client = new URL(path, ENV.LABCOACH_FRONTEND_URL);
    for (const key of definition.query) client.searchParams.set(key, key === 'page' ? '2' : 'Thành viên thử');
    const calls = [];
    const handler = createHandler({ env: ENV, fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return json({ ok: true });
    } });
    const res = response();
    await handler(request(client.pathname + client.search, {
      method: definition.method,
      ...(definition.method === 'POST' ? { payload: { synthetic: true } } : {}),
    }), res);
    assert.equal(res.statusCode, 200, `${path}: ${JSON.stringify(res.data)}`);
    assert.equal(calls.length, 1, path);
    const upstream = new URL(calls[0].url);
    assert.equal(upstream.origin, ENV.LABCOACH_API_URL);
    assert.equal(upstream.pathname, path);
    assert.deepEqual([...upstream.searchParams], [...client.searchParams], path);
    assert.equal(calls[0].options.method, definition.method);
  }
});

test('session then login works through compiled deployment rewrites with cookie and CSRF intact', async () => {
  const login = { username: 'synthetic-admin', password: 'synthetic-password' };
  const calls = [];
  const handler = createHandler({ env: ENV, fetchImpl: async (url, options) => {
    const target = new URL(url);
    calls.push(target.pathname);
    assert.equal(target.search, '');
    if (target.pathname === '/api/session') {
      assert.equal(options.method, 'GET');
      return json({ user: null, csrf: CSRF }, { 'Set-Cookie': SET_COOKIE });
    }
    assert.equal(target.pathname, '/api/login');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers.Cookie, COOKIE);
    assert.equal(options.headers['X-CSRF-Token'], CSRF);
    assert.deepEqual(JSON.parse(options.body.toString('utf8')), login);
    return json({ user: { username: login.username } }, { 'Set-Cookie': SET_COOKIE });
  } });
  const session = response();
  await handler(request('/api/session'), session);
  assert.equal(session.statusCode, 200);
  assert.equal(session.data.csrf, CSRF);
  const loggedIn = response();
  await handler(request('/api/login', {
    method: 'POST', payload: login,
    cookie: session.headers['set-cookie'][0].split(';')[0], csrf: session.data.csrf,
  }), loggedIn);
  assert.equal(loggedIn.statusCode, 200);
  assert.deepEqual(loggedIn.data, { user: { username: 'synthetic-admin' } });
  assert.deepEqual(calls, ['/api/session', '/api/login']);
});

test('compiled rewrites still reject unknown endpoints, unexpected parameters and duplicate filters', async () => {
  let calls = 0;
  const handler = createHandler({ env: ENV, fetchImpl: async () => { calls++; return json({ ok: true }); } });
  for (const path of [
    '/api/not-allowed', '/api/session?destination=https://untrusted.example.test',
    '/api/session?path=session', '/api/members?page=1&page=2',
  ]) {
    const res = response();
    await handler(request(path), res);
    assert.ok([400, 404].includes(res.statusCode), `${path}: ${JSON.stringify(res.data)}`);
  }
  assert.equal(calls, 0);
});
