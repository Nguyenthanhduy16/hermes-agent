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
  '/api/session': { method: 'GET', query: [] },
  '/api/login': { method: 'POST', query: [] },
  '/api/logout': { method: 'POST', query: [] },
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
    throw new GatewayError(503, 'Trang Admin chưa được cấu hình kết nối đầy đủ.');
  }
  let parsed;
  try { parsed = new URL(value); } catch {
    throw new GatewayError(503, 'Địa chỉ kết nối Admin chưa hợp lệ.');
  }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password ||
      parsed.pathname !== '/' || parsed.search || parsed.hash ||
      !parsed.hostname || ![parsed.origin, parsed.origin + '/'].includes(value)) {
    throw new GatewayError(503, 'Địa chỉ kết nối Admin cần là địa chỉ gốc HTTPS.');
  }
  return parsed;
}

export function configuration(env) {
  const backend = origin(env.LABCOACH_API_URL);
  const frontend = origin(env.LABCOACH_FRONTEND_URL);
  const secret = env.LABCOACH_PROXY_SECRET;
  if (typeof secret !== 'string' || !/^[\x21-\x7e]{32,256}$/.test(secret) || backend.origin === frontend.origin) {
    throw new GatewayError(503, 'Khóa kết nối Admin chưa được cấu hình hợp lệ.');
  }
  return { backend, frontend, secret };
}

function header(req, name) {
  const key = name.toLowerCase();
  const value = req.headers?.[key];
  if (value !== undefined && typeof value !== 'string') {
    throw new GatewayError(400, 'Tiêu đề yêu cầu không hợp lệ.');
  }
  if (Array.isArray(req.rawHeaders)) {
    let count = 0;
    for (let index = 0; index < req.rawHeaders.length; index += 2) {
      if (String(req.rawHeaders[index]).toLowerCase() === key) count++;
    }
    if (count > 1) throw new GatewayError(400, 'Tiêu đề yêu cầu bị lặp.');
  }
  if (value && (value.length > 8192 || /[\r\n\0]/.test(value))) {
    throw new GatewayError(400, 'Tiêu đề yêu cầu không hợp lệ.');
  }
  return value;
}

function browserBoundary(req, config) {
  // Never use X-Forwarded-Host or an incoming proxy secret as browser identity.
  if (header(req, 'host') !== config.frontend.host) {
    throw new GatewayError(403, 'Hãy sử dụng địa chỉ Admin đã được cấu hình.');
  }
  const incomingOrigin = header(req, 'origin');
  const site = header(req, 'sec-fetch-site');
  if ((incomingOrigin !== undefined && incomingOrigin !== config.frontend.origin) ||
      site === 'cross-site' || (req.method === 'POST' && incomingOrigin !== config.frontend.origin)) {
    throw new GatewayError(403, 'Nguồn yêu cầu không được phép.');
  }
}

function route(req, config) {
  if (typeof req.url !== 'string' || req.url.length > 2500 ||
      !req.url.startsWith('/') || req.url.startsWith('//') || /[\\\r\n\0#]/.test(req.url)) {
    throw new GatewayError(400, 'Đường dẫn không hợp lệ.');
  }
  const parsed = new URL(req.url, config.frontend.origin);
  if (parsed.origin !== config.frontend.origin) throw new GatewayError(400, 'Đường dẫn không hợp lệ.');
  const hints = parsed.searchParams.getAll('__route');
  if (hints.length > 1) throw new GatewayError(400, 'Tham số định tuyến bị lặp.');
  const helperHint = req.query?.__route;
  if (helperHint !== undefined && typeof helperHint !== 'string') {
    throw new GatewayError(400, 'Tham số định tuyến không hợp lệ.');
  }
  if (hints.length && helperHint !== undefined && hints[0] !== helperHint) {
    throw new GatewayError(400, 'Tham số định tuyến không khớp.');
  }
  const hint = hints[0] ?? helperHint;
  let path = parsed.pathname;
  // Supports both original req.url and the rewritten /api/proxy URL.
  if (path === '/api/proxy') path = typeof hint === 'string' ? '/api/' + hint : '';
  else if (hint !== undefined && path !== '/api/' + hint) {
    throw new GatewayError(400, 'Tham số định tuyến không khớp.');
  }
  const definition = Object.hasOwn(ROUTES, path) ? ROUTES[path] : null;
  if (!definition) throw new GatewayError(404, 'Không tìm thấy chức năng Admin.');
  if (req.method !== definition.method) throw new GatewayError(405, 'Phương thức không được hỗ trợ.');
  const target = new URL(path, config.backend.origin);
  const seen = new Set();
  for (const [key, value] of parsed.searchParams) {
    if (key === '__route') continue;
    if (!definition.query.includes(key) || seen.has(key)) {
      throw new GatewayError(400, 'Tham số không được hỗ trợ hoặc bị lặp.');
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
  if (found.length > 1) throw new GatewayError(400, 'Cookie phiên bị lặp.');
  if (!found.length || found[0] === COOKIE + '=') return null;
  if (!new RegExp('^' + COOKIE + '=[A-Za-z0-9_-]{1,128}$').test(found[0])) {
    throw new GatewayError(400, 'Cookie phiên không hợp lệ.');
  }
  return found[0];
}

async function body(req) {
  const lengthHeader = header(req, 'content-length');
  if (header(req, 'transfer-encoding')) throw new GatewayError(400, 'Kiểu truyền không hỗ trợ.');
  if (lengthHeader !== undefined && (!/^\d+$/.test(lengthHeader) || Number(lengthHeader) > MAX_BODY)) {
    throw new GatewayError(413, 'Nội dung yêu cầu quá dài.');
  }
  if (req.method !== 'POST') {
    if (Number(lengthHeader || 0) > 0) throw new GatewayError(400, 'Yêu cầu đọc không được kèm nội dung.');
    return undefined;
  }
  if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(header(req, 'content-type') || '')) {
    throw new GatewayError(415, 'Cần gửi nội dung JSON.');
  }
  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += bytes.length;
    if (total > MAX_BODY) throw new GatewayError(413, 'Nội dung yêu cầu quá dài.');
    chunks.push(bytes);
  }
  if (!total || (lengthHeader !== undefined && total !== Number(lengthHeader))) {
    throw new GatewayError(400, 'Độ dài nội dung không hợp lệ.');
  }
  const bytes = Buffer.concat(chunks, total);
  try {
    const parsed = JSON.parse(bytes.toString('utf8'));
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error();
  } catch {
    throw new GatewayError(400, 'Nội dung JSON không hợp lệ.');
  }
  // Send the original bytes; Railway still rejects duplicate JSON keys.
  return bytes;
}

async function responseBody(response) {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get('content-type') || '')) {
    throw new GatewayError(502, 'Máy chủ Admin chưa trả về dữ liệu hợp lệ.');
  }
  const chunks = [];
  let length = 0;
  for await (const chunk of response.body || []) {
    length += chunk.byteLength;
    if (length > MAX_RESPONSE) throw new GatewayError(502, 'Phản hồi Admin quá dài; hãy thu hẹp bộ lọc.');
    chunks.push(Buffer.from(chunk));
  }
  const bytes = Buffer.concat(chunks, length);
  try { JSON.parse(bytes.toString('utf8')); } catch {
    throw new GatewayError(502, 'Máy chủ Admin chưa trả về dữ liệu hợp lệ.');
  }
  return bytes;
}

function responseCookies(response) {
  const cookies = response.headers.getSetCookie();
  if (!cookies.length) return [];
  if (cookies.length !== 1) throw new GatewayError(502, 'Cookie Admin chưa hợp lệ.');
  const cookie = cookies[0];
  if (cookie.length > 2048 || /[\r\n\0]/.test(cookie) ||
      !new RegExp('^' + COOKIE + '=[A-Za-z0-9_-]{0,128};').test(cookie) ||
      !/(?:^|;)\s*HttpOnly(?:;|$)/i.test(cookie) ||
      !/(?:^|;)\s*Secure(?:;|$)/i.test(cookie) ||
      !/(?:^|;)\s*SameSite=Strict(?:;|$)/i.test(cookie) ||
      !/(?:^|;)\s*Path=\/(?:;|$)/i.test(cookie) ||
      /(?:^|;)\s*Domain=/i.test(cookie)) {
    throw new GatewayError(502, 'Cookie Admin chưa đáp ứng cấu hình bảo mật.');
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
          throw new GatewayError(403, 'Thiếu xác nhận phiên; tải lại trang rồi thử lại.');
        }
        headers['X-CSRF-Token'] = csrf;
        headers['Content-Type'] = 'application/json';
        headers['Content-Length'] = String(requestBody.length);
      }
      controller = new AbortController();
      const deadline = new Promise((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new GatewayError(504, 'Máy chủ Admin phản hồi chậm. Tải lại dữ liệu trước khi thử lại thao tác ghi.'));
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
          throw new GatewayError(502, 'Máy chủ Admin chuyển hướng ngoài cấu hình.');
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
        'Chưa kết nối được máy chủ Admin. Tải lại dữ liệu trước khi thử lại thao tác ghi.' }));
    } finally {
      clearTimeout(timer);
    }
  };
}
