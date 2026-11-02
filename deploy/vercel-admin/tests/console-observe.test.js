import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const read = path => readFile(new URL('../../../src/hermes_zalo/admin_static/' + path, import.meta.url), 'utf8');
const consoleSource = await read('console.js');
const observeSource = await read('console-observe.js');
const boot = consoleSource.lastIndexOf('  api("/api/session").then');
assert.ok(boot > 0);

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; this.hidden = false; this.disabled = false;
    this.open = false; this.dataset = {}; this.attributes = {}; this.style = {}; this._text = ''; this.className = '';
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  set innerHTML(_) { throw new Error('Untrusted content must never use innerHTML'); }
  set outerHTML(_) { throw new Error('Untrusted content must never use outerHTML'); }
  insertAdjacentHTML() { throw new Error('Untrusted content must never be parsed as HTML'); }
  append(...nodes) { for (const n of nodes) { if (n.tag === 'fragment') this.append(...n.children); else { n.parent = this; this.children.push(n); } } }
  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { if (name === 'style') throw new Error('Inline style attributes are blocked by CSP'); this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  get childElementCount() { return this.children.length; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  click() { this.clicked = true; this.listeners.click?.({ preventDefault() {} }); }
  focus() {} reset() {} close() {} showModal() {}
}

function all(root, predicate, found = []) {
  if (predicate(root)) found.push(root);
  for (const child of root.children || []) all(child, predicate, found);
  return found;
}
const byClass = (root, cls) => all(root, n => String(n.className).split(' ').includes(cls));
const byTag = (root, tag) => all(root, n => n.tag === tag);
const response = data => ({ ok: true, status: 200, json: async () => data });

function app(fetchImpl = async () => response({ section: 'x', available: true, items: [] })) {
  const nodes = new Map(), calls = [], tags = [], downloads = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const body = new Element('body');
  const document = {
    body, getElementById: get,
    createElement(tag) { tags.push(tag); return new Element(tag); },
    createElementNS(_, tag) { return new Element(tag); },
    createTextNode(text) { const n = new Element('text'); n.textContent = text; return n; },
    createDocumentFragment() { return new Element('fragment'); },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  class FakeBlob { constructor(parts, options) { this.text = parts.join(''); this.type = options?.type; } }
  const FakeURL = class extends URL {
    static createObjectURL(blob) { downloads.push(blob); return 'blob:observe-test'; }
    static revokeObjectURL() {}
  };
  const window = { addEventListener() {} };
  const context = vm.createContext({ document, window, Node: Element, URL: FakeURL, URLSearchParams, AbortController, Intl, Date,
    location: { hash: '' }, history: { replaceState() {} }, setTimeout: () => 0, clearTimeout() {}, Blob: FakeBlob,
    fetch: async (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); } });
  vm.runInContext(consoleSource.slice(0, boot) + 'globalThis.consoleTest={state,views,load,render};})();', context);
  vm.runInContext(observeSource, context);
  const api = context.consoleTest;
  api.state.user = { username: 'owner', role: 'faculty', permissions: [] };
  api.state.csrf = 'synthetic-csrf';
  const show = (view, data) => { api.state.view = view; api.state.data = data; api.render(); return get('content'); };
  return { ...api, show, content: () => get('content'), calls, tags, downloads, body };
}

const hostile = '<img src=x onerror=alert(1)>';
const turn = (over = {}) => ({ id: 'a7', started_at: '2026-10-31T07:20:00+00:00', platform: 'telegram', session: 'tg-1', session_id: 'tg-1',
  models: ['google/gemini-2.5-flash'], api_calls: 2, tool_calls: 1, input_tokens: 1200, output_tokens: 300, duration_s: 3.4,
  retries: 1, api_errors: 1, tool_failures: 0, blocked: 1, status: 'issues', grouping: 'turn_id', ...over });
const listData = (over = {}) => ({ section: 'activity', available: true, view: 'list', days: 7, total: 1, page: 1, limit: 25,
  items: [turn()], facets: { platforms: ['telegram', 'zalo'], models: ['google/gemini-2.5-flash'] },
  summary: { turns: 1, failed: 0, issues: 1, duration_p50_s: 3.4, duration_p95_s: 3.4 }, gap_seconds: 120, row_limit: 20000, ...over });

test('observe pages register in the observe group', () => {
  const a = app();
  assert.deepEqual([...a.views.activity], ['Agent activity', 'Follow what the agent did, turn by turn and step by step.']);
  assert.equal(a.views.analytics[0], 'Analytics');
});

test('untrusted turn strings render as text, never as markup', () => {
  const a = app();
  const content = a.show('activity', listData({ items: [turn({ models: [hostile], session: hostile, platform: hostile, error_types: [hostile] })],
    facets: { platforms: [hostile], models: [hostile] } }));
  assert.ok(content.textContent.includes(hostile));
  assert.ok(!a.tags.includes('img') && !a.tags.includes('script'));
  assert.match(content.textContent, /2 steps/);
  assert.match(content.textContent, /1 retry/);
  assert.match(content.textContent, /1 blocked/);
});

test('filters and paging build the expected request URLs', async () => {
  const a = app(async () => response(listData()));
  a.state.view = 'activity';
  await a.load();
  assert.equal(a.calls.at(-1).url, '/api/assistant/activity?days=7');
  const form = byTag(a.content(), 'form')[0], [days, platform, model, status] = byTag(form, 'select'), session = byTag(form, 'input')[0];
  days.value = '30'; platform.value = 'zalo'; model.value = 'google/gemini-2.5-flash'; status.value = 'error'; session.value = ' 2026_ab ';
  form.listeners.submit({ preventDefault() {} });
  await new Promise(resolve => setImmediate(resolve));
  const url = new URL(a.calls.at(-1).url, 'http://localhost');
  assert.equal(url.pathname, '/api/assistant/activity');
  assert.deepEqual(Object.fromEntries(url.searchParams), { days: '30', platform: 'zalo', model: 'google/gemini-2.5-flash', status: 'error', session: '2026_ab' });
  byClass(a.content(), 'obs-turn')[0].click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.calls.at(-1).url, '/api/assistant/activity?turn=a7');
});

test('turn detail draws a waterfall with CSSOM widths and a way back', async () => {
  const detail = { section: 'activity', available: true, view: 'detail', days: 7, limit: 400, pricing: { message: null },
    turn: turn({ span_ms: 10000, prompt_tokens: 1500, runs: [{ label: hostile, config_hash: 'abc123' }], cost_usd: 0.0123 }),
    items: [
      { id: 'a1', kind: 'api', offset_ms: 0, duration_ms: 2500, ttfb_ms: 500, model: hostile, retry_count: 1, finish_reason: 'tool_calls', input_tokens: 100, output_tokens: 20, status: 'ok' },
      { id: 't1', kind: 'tool', offset_ms: 2600, duration_ms: 5, tool_name: 'terminal', tool_status: 'blocked', blocked: true, status: 'blocked', requested_by: 0 },
      { id: 'a2', kind: 'api', offset_ms: 2700, duration_ms: 7300, model: 'm', error_type: 'APITimeoutError', status_code: 504, status: 'error' }] };
  const a = app(async () => response(detail));
  a.state.view = 'activity';
  const content = a.show('activity', detail);
  const bars = byClass(content, 'obs-bar');
  assert.equal(bars.length, 3);
  assert.equal(bars[0].style.width, '25.00%');
  assert.equal(bars[2].style.left, '27.00%');
  assert.equal(byClass(bars[0], 'obs-ttfb')[0].style.width, '20.0%');
  assert.ok(content.textContent.includes(hostile));
  assert.match(content.textContent, /retry 1/);
  assert.match(content.textContent, /APITimeoutError \(504\)/);
  assert.match(content.textContent, /from step 1/);
  assert.ok(!a.tags.includes('img'));
  const back = all(content, n => n.tag === 'button' && n.textContent === '← All turns')[0];
  back.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.calls.at(-1).url, '/api/assistant/activity?days=7');
});

test('analytics without pricing never shows $0, and Export JSON downloads the snapshot', async () => {
  const data = { section: 'analytics', available: true, days: 7, timezone: 'UTC+07:00',
    kpis: { turns: 3, api_calls: 5, errors: 1, error_rate: 0.2, latency_p50_s: 1.5, latency_p95_s: 4, ttfb_p50_s: 0.4, total_tokens: 1000, cache_hit_rate: 0.25, cost_usd: null },
    daily: [{ day: '2026-10-30', api_calls: 2, total_tokens: 400 }, { day: '2026-10-31', api_calls: 3, total_tokens: 600 }],
    models: [{ model: hostile, api_calls: 5, priced: false, cost_usd: null }], tools: [{ tool: 'terminal', calls: 1, blocked: 1, failure_rate: 0 }],
    platforms: [{ platform: 'zalo', turns: 3, api_calls: 5 }], runs: [], pricing: { available: false, message: 'Pricing unavailable: add pricing.yaml.' } };
  const a = app(async () => response(data));
  a.state.view = 'analytics';
  await a.load();
  assert.equal(a.calls.at(-1).url, '/api/assistant/analytics?days=7');
  const content = a.content();
  assert.match(content.textContent, /Pricing unavailable/);
  assert.doesNotMatch(content.textContent, /\$0/);
  assert.ok(content.textContent.includes(hostile));
  assert.match(content.textContent, /20\.0%/);
  all(content, n => n.tag === 'button' && n.textContent === 'Export JSON')[0].click();
  assert.equal(a.downloads.length, 1);
  assert.equal(JSON.parse(a.downloads[0].text).kpis.turns, 3);
  assert.equal(a.downloads[0].type, 'application/json');
  const period = all(content, n => n.tag === 'button' && n.textContent === '90 days')[0];
  period.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.calls.at(-1).url, '/api/assistant/analytics?days=90');
});

test('demo snapshots from the server render end to end', async t => {
  const { execFileSync } = await import('node:child_process');
  const root = new URL('../../../', import.meta.url).pathname;
  let snapshots;
  try {
    snapshots = JSON.parse(execFileSync(root + '.runtime/venv/bin/python', ['-c',
      'import json; from pathlib import Path; from hermes_zalo.console_sections import ConsoleContext; from hermes_zalo import admin_observe as o;' +
      'c = ConsoleContext(Path("/nonexistent"), "demo", Path("/nonexistent")); l = o.activity_snapshot(c, {"days": "1"});' +
      'print(json.dumps({"list": l, "detail": o.activity_snapshot(c, {"turn": l["items"][0]["id"]}), "analytics": o.analytics_snapshot(c, {"days": "30"})}))'],
    { env: { ...process.env, PYTHONPATH: root + 'src' }, encoding: 'utf8' }));
  } catch { t.skip('Python runtime unavailable'); return; } // optional for the Vercel package tests
  const a = app();
  assert.match(a.show('activity', snapshots.list).textContent, /Recent turns/);
  assert.ok(byClass(a.content(), 'obs-turn').length > 3);
  assert.ok(byClass(a.show('activity', snapshots.detail), 'obs-bar').length > 0);
  const text = a.show('analytics', snapshots.analytics).textContent;
  assert.match(text, /Runs and settings/);
  assert.match(text, /router-v1/);
  assert.match(text, /\$\d/);
});
