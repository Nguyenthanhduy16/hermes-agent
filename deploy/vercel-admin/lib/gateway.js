/** Fixed Admin gateway; never accepts a destination URL from a browser.
 *
 * Authentication and CSRF remain the Railway application's responsibility.
 * This gateway checks the original browser boundary before replacing Origin
 * for the one trusted upstream. It does not retry writes or log request data.
 */
const COOKIE = 'labcoach_admin';
const MAX_BODY = 32768;
const MAX_RESPONSE = 2 * 1024 * 1024;
const TIMEOUT_MS = 50000;

export const ROUTES = Object.freeze({
  '/api/assistant/models/action': { method: 'POST', query: [] },
  '/api/assistant/config/action': { method: 'POST', query: [] },
  '/api/assistant/keys/action': { method: 'POST', query: [] },
  '/api/assistant/channels/action': { method: 'POST', query: [] },
  '/api/assistant/mcp/action': { method: 'POST', query: [] },
  '/api/assistant/skills/action': { method: 'POST', query: [] },
  '/api/assistant/routines/action': { method: 'POST', query: [] },
  '/api/assistant/plugins/action': { method: 'POST', query: [] },
  '/api/assistant/webhooks/action': { method: 'POST', query: [] },
  '/api/assistant/profiles/action': { method: 'POST', query: [] },
  '/api/assistant/models': { method: 'GET', query: [] },
  '/api/assistant/logs': { method: 'GET', query: ['q', 'level', 'file', 'component', 'lines'] },
  '/api/assistant/plugins': { method: 'GET', query: [] },
  '/api/assistant/mcp': { method: 'GET', query: [] },
  '/api/assistant/channels': { method: 'GET', query: [] },
  '/api/assistant/webhooks': { method: 'GET', query: [] },
  '/api/assistant/pairing': { method: 'GET', query: [] },
  '/api/assistant/profiles': { method: 'GET', query: [] },
  '/api/assistant/config': { method: 'GET', query: [] },
  '/api/assistant/keys': { method: 'GET', query: [] },
  '/api/assistant/documentation': { method: 'GET', query: [] },
  '/api/assistant/files': { method: 'GET', query: ['path'] },
  '/api/assistant/chat': { method: 'GET', methods: ['GET', 'POST'], query: [] },
  '/api/assistant/kanban': { method: 'GET', methods: ['GET', 'POST'], query: [] },
  '/api/assistant/overview': { method: 'GET', query: [] },
  '/api/assistant/runtime': { method: 'GET', query: [] },
  '/api/assistant/skills': { method: 'GET', query: [] },
  '/api/assistant/routines': { method: 'GET', query: [] },
  '/api/assistant/usage': { method: 'GET', query: [] },
  '/api/assistant/users': { method: 'GET', query: [] },
  '/api/assistant/sessions': { method: 'GET', query: ['page'] },
  '/api/assistant/history': { method: 'GET', query: ['page', 'session_id'] },
  // Feature sections registered through src/hermes_zalo/console_sections.py.
  '/api/assistant/activity': { method: 'GET', query: ['days', 'platform', 'model', 'status', 'session', 'page', 'turn'] },
  '/api/assistant/analytics': { method: 'GET', query: ['days'] },
  '/api/assistant/experiments': { method: 'GET', query: ['compare'] },
  '/api/assistant/benchmarks': { method: 'GET', query: ['compare', 'run'] },
  '/api/assistant/benchmarks/action': { method: 'POST', query: [] },
  '/api/assistant/connectors': { method: 'GET', query: [] },
  '/api/assistant/connectors/action': { method: 'POST', query: [] },
  '/api/assistant/reports': { method: 'GET', query: ['job', 'file'] },
  '/api/assistant/reports/action': { method: 'POST', query: [] },
  '/api/assistant/operations': { method: 'GET', query: [] },
  '/api/assistant/operations/action': { method: 'POST', query: [] },
  '/api/assistant/briefing': { method: 'GET', query: [] },
  '/api/assistant/briefing/action': { method: 'POST', query: [] },
  '/api/session': { method: 'GET', query: [] },
  '/api/login': { method: 'POST', query: [] },
  '/api/logout': { method: 'POST', query: [] },
  '/api/password/change': { method: 'POST', query: [] },
  '/api/tickets': { method: 'GET', query: ['q', 'status', 'page'] },
  '/api/ticket': { method: 'GET', query: ['id'] },
  '/api/ticket/action': { method: 'POST', query: [] },
  '/api/issues': { method: 'GET', query: ['q', 'kind', 'status', 'member_id', 'assignee', 'page'] },
  '/api/issue': { method: 'GET', query: ['id'] },
  '/api/issue/operators': { method: 'GET', query: [] },
  '/api/issue/action': { method: 'POST', query: [] },
  '/api/issue/notification/retry': { method: 'POST', query: [] },
  '/api/admin/accounts': { method: 'GET', query: ['q', 'page'] },
  '/api/admin/account': { method: 'GET', query: ['username'] },
  '/api/admin/account/save': { method: 'POST', query: [] },
  '/api/members': { method: 'GET', query: ['q', 'status', 'link', 'page'] },
  '/api/member': { method: 'GET', query: ['id'] },
  '/api/member/lock': { method: 'POST', query: [] },
  '/api/member/link/preview': { method: 'POST', query: [] },
  '/api/member/link/confirm': { method: 'POST', query: [] },
  '/api/member/registration/preview': { method: 'POST', query: [] },
  '/api/member/registration/confirm': { method: 'POST', query: [] },
  '/api/member/registration/reset': { method: 'POST', query: [] },
  '/api/sessions': { method: 'GET', query: ['member_id', 'chat_id', 'page'] },
  '/api/history': { method: 'GET', query: ['session_key', 'member_id', 'chat_id', 'start', 'end', 'page', 'message_id'] },
  '/api/context': { method: 'GET', query: ['session_key'] },
  '/api/context/reset': { method: 'POST', query: [] },
  '/api/context/rebuild': { method: 'POST', query: [] },
  '/api/memories': { method: 'GET', query: ['member_id', 'page'] },
  '/api/memory/save': { method: 'POST', query: [] },
  '/api/memory/stop': { method: 'POST', query: [] },
  '/api/memory/versions': { method: 'GET', query: ['id', 'page'] },
  '/api/audit': { method: 'GET', query: ['kind', 'member_id', 'session_key', 'page'] },
});

class GatewayError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function origin(value) {
  if (typeof value !== 'string' || !value || value.length > 500) {
    throw new GatewayError(503, 'The admin connection is not fully configured.');
  }
  let parsed;
  try { parsed = new URL(value); } catch {
    throw new GatewayError(503, 'The admin connection address is invalid.');
  }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password ||
      parsed.pathname !== '/' || parsed.search || parsed.hash ||
      !parsed.hostname || ![parsed.origin, parsed.origin + '/'].includes(value)) {
    throw new GatewayError(503, 'The admin connection requires an HTTPS origin.');
  }
  return parsed;
}

export function configuration(env) {
  const backend = origin(env.LABCOACH_API_URL);
  const frontend = origin(env.LABCOACH_FRONTEND_URL);
  const secret = env.LABCOACH_PROXY_SECRET;
  if (typeof secret !== 'string' || !/^[\x21-\x7e]{32,256}$/.test(secret) || backend.origin === frontend.origin) {
    throw new GatewayError(503, 'The admin proxy credential is not configured correctly.');
  }
  return { backend, frontend, secret };
}

function header(req, name) {
  const key = name.toLowerCase();
  const value = req.headers?.[key];
  if (value !== undefined && typeof value !== 'string') {
    throw new GatewayError(400, 'Invalid request headers.');
  }
  if (Array.isArray(req.rawHeaders)) {
    let count = 0;
    for (let index = 0; index < req.rawHeaders.length; index += 2) {
      if (String(req.rawHeaders[index]).toLowerCase() === key) count++;
    }
    if (count > 1) throw new GatewayError(400, 'Duplicate request headers.');
  }
  if (value && (value.length > 8192 || /[\r\n\0]/.test(value))) {
    throw new GatewayError(400, 'Invalid request headers.');
  }
  return value;
}

function browserBoundary(req, config) {
  // Never use X-Forwarded-Host or an incoming proxy secret as browser identity.
  if (header(req, 'host') !== config.frontend.host) {
    throw new GatewayError(403, 'Use the configured admin address.');
  }
  const incomingOrigin = header(req, 'origin');
  const site = header(req, 'sec-fetch-site');
  if ((incomingOrigin !== undefined && incomingOrigin !== config.frontend.origin) ||
      site === 'cross-site' || (req.method === 'POST' && incomingOrigin !== config.frontend.origin)) {
    throw new GatewayError(403, 'This request origin is not allowed.');
  }
}

function route(req, config) {
  if (typeof req.url !== 'string' || req.url.length > 2500 ||
      !req.url.startsWith('/') || req.url.startsWith('//') || /[\\\r\n\0#]/.test(req.url)) {
    throw new GatewayError(400, 'Invalid request path.');
  }
  const parsed = new URL(req.url, config.frontend.origin);
  if (parsed.origin !== config.frontend.origin) throw new GatewayError(400, 'Invalid request path.');
  const hints = parsed.searchParams.getAll('__route');
  if (hints.length > 1) throw new GatewayError(400, 'Duplicate route parameters.');
  const helperHint = req.query?.__route;
  if (helperHint !== undefined && typeof helperHint !== 'string') {
    throw new GatewayError(400, 'Invalid route parameter.');
  }
  if (hints.length && helperHint !== undefined && hints[0] !== helperHint) {
    throw new GatewayError(400, 'The route parameter does not match the request path.');
  }
  const hint = hints[0] ?? helperHint;
  let path = parsed.pathname;
  // Supports both original req.url and the rewritten /api/proxy URL.
  if (path === '/api/proxy') path = typeof hint === 'string' ? '/api/' + hint : '';
  else if (hint !== undefined && path !== '/api/' + hint) {
    throw new GatewayError(400, 'The route parameter does not match the request path.');
  }
  const definition = Object.hasOwn(ROUTES, path) ? ROUTES[path] : null;
  if (!definition) throw new GatewayError(404, 'Admin feature not found.');
  if (!(definition.methods || [definition.method]).includes(req.method)) throw new GatewayError(405, 'Method not supported.');
  const target = new URL(path, config.backend.origin);
  const seen = new Set();
  for (const [key, value] of parsed.searchParams) {
    if (key === '__route') continue;
    if (!definition.query.includes(key) || seen.has(key)) {
      throw new GatewayError(400, 'Unsupported or duplicate request parameters.');
    }
    seen.add(key);
    target.searchParams.set(key, value);
  }
  return target;
}

function sessionCookie(req) {
  const raw = header(req, 'cookie');
  if (!raw) return null;
  const found = raw.split(';').map(value => value.trim()).filter(value => value.startsWith(COOKIE + '='));
  if (found.length > 1) throw new GatewayError(400, 'Duplicate session cookies.');
  if (!found.length || found[0] === COOKIE + '=') return null;
  if (!new RegExp('^' + COOKIE + '=[A-Za-z0-9_-]{1,128}$').test(found[0])) {
    throw new GatewayError(400, 'Invalid session cookie.');
  }
  return found[0];
}

async function body(req) {
  const lengthHeader = header(req, 'content-length');
  if (header(req, 'transfer-encoding')) throw new GatewayError(400, 'Unsupported transfer encoding.');
  if (lengthHeader !== undefined && (!/^\d+$/.test(lengthHeader) || Number(lengthHeader) > MAX_BODY)) {
    throw new GatewayError(413, 'The request body is too large.');
  }
  if (req.method !== 'POST') {
    if (Number(lengthHeader || 0) > 0) throw new GatewayError(400, 'Read requests must not contain a body.');
    return undefined;
  }
  if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(header(req, 'content-type') || '')) {
    throw new GatewayError(415, 'A JSON request body is required.');
  }
  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += bytes.length;
    if (total > MAX_BODY) throw new GatewayError(413, 'The request body is too large.');
    chunks.push(bytes);
  }
  if (!total || (lengthHeader !== undefined && total !== Number(lengthHeader))) {
    throw new GatewayError(400, 'Invalid request content length.');
  }
  const bytes = Buffer.concat(chunks, total);
  try {
    const parsed = JSON.parse(bytes.toString('utf8'));
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error();
  } catch {
    throw new GatewayError(400, 'Invalid JSON request body.');
  }
  // Send the original bytes; Railway still rejects duplicate JSON keys.
  return bytes;
}

async function responseBody(response) {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get('content-type') || '')) {
    throw new GatewayError(502, 'The admin server returned an invalid response.');
  }
  const chunks = [];
  let length = 0;
  for await (const chunk of response.body || []) {
    length += chunk.byteLength;
    if (length > MAX_RESPONSE) throw new GatewayError(502, 'The admin response is too large. Narrow your filters.');
    chunks.push(Buffer.from(chunk));
  }
  const bytes = Buffer.concat(chunks, length);
  try { JSON.parse(bytes.toString('utf8')); } catch {
    throw new GatewayError(502, 'The admin server returned an invalid response.');
  }
  return bytes;
}

function responseCookies(response) {
  const cookies = response.headers.getSetCookie();
  if (!cookies.length) return [];
  if (cookies.length !== 1) throw new GatewayError(502, 'Invalid admin session cookie.');
  const cookie = cookies[0];
  if (cookie.length > 2048 || /[\r\n\0]/.test(cookie) ||
      !new RegExp('^' + COOKIE + '=[A-Za-z0-9_-]{0,128};').test(cookie) ||
      !/(?:^|;)\s*HttpOnly(?:;|$)/i.test(cookie) ||
      !/(?:^|;)\s*Secure(?:;|$)/i.test(cookie) ||
      !/(?:^|;)\s*SameSite=Strict(?:;|$)/i.test(cookie) ||
      !/(?:^|;)\s*Path=\/(?:;|$)/i.test(cookie) ||
      /(?:^|;)\s*Domain=/i.test(cookie)) {
    throw new GatewayError(502, 'The admin cookie does not meet the required security settings.');
  }
  // A host-only cookie set here belongs to the Vercel frontend origin.
  return [cookie];
}

function responseHeaders(res) {
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'private, no-store, max-age=0');
  res.setHeader('CDN-Cache-Control', 'no-store');
  res.setHeader('Vercel-CDN-Cache-Control', 'no-store');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'no-referrer');
}

export function createHandler({ env = process.env, fetchImpl = globalThis.fetch, timeoutMs = TIMEOUT_MS } = {}) {
  return async function handler(req, res) {
    responseHeaders(res);
    let controller;
    let timer;
    try {
      const config = configuration(env);
      browserBoundary(req, config);
      const target = route(req, config);
      const requestBody = await body(req);
      const headers = {
        Accept: 'application/json',
        Origin: config.backend.origin,
        'X-LabCoach-Admin-Proxy': config.secret,
      };
      const cookie = sessionCookie(req);
      if (cookie) headers.Cookie = cookie;
      if (req.method === 'POST') {
        const csrf = header(req, 'x-csrf-token');
        if (!csrf || !/^[A-Za-z0-9_-]{16,128}$/.test(csrf)) {
          throw new GatewayError(403, 'Session confirmation is missing. Reload the page before trying again.');
        }
        headers['X-CSRF-Token'] = csrf;
        headers['Content-Type'] = 'application/json';
        headers['Content-Length'] = String(requestBody.length);
      }
      controller = new AbortController();
      const deadline = new Promise((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new GatewayError(504, 'The admin server timed out. Reload the data before retrying a write.'));
        }, timeoutMs);
      });
      const request = async () => {
        const upstream = await fetchImpl(target, {
          method: req.method,
          headers,
          body: requestBody,
          signal: controller.signal,
          redirect: 'manual',
          cache: 'no-store',
        });
        if (upstream.status >= 300 && upstream.status < 400) {
          throw new GatewayError(502, 'The admin server redirected outside the configured origin.');
        }
        const bytes = await responseBody(upstream);
        const cookies = responseCookies(upstream);
        return { status: upstream.status, bytes, cookies };
      };
      const result = await Promise.race([request(), deadline]);
      if (result.cookies.length) res.setHeader('Set-Cookie', result.cookies);
      res.statusCode = result.status;
      res.end(result.bytes);
    } catch (error) {
      if (controller) controller.abort();
      // Deliberately exclude exception messages from network libraries/config.
      res.statusCode = error instanceof GatewayError ? error.status : 502;
      res.end(JSON.stringify({ error: error instanceof GatewayError ? error.message :
        'The admin server is unavailable. Reload the data before retrying a write.' }));
    } finally {
      clearTimeout(timer);
    }
  };
}
