import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const read = path => readFile(new URL('../../../src/hermes_zalo/admin_static/' + path, import.meta.url), 'utf8');
const consoleSource = await read('console.js');
const briefingSource = await read('console-briefing.js');
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
  prepend(...nodes) { for (const n of nodes.reverse()) { n.parent = this; this.children.unshift(n); } }
  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { if (name === 'style') throw new Error('Inline style attributes are blocked by CSP'); if (/^on/i.test(name)) throw new Error('Event handler attributes are not allowed'); this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  get childElementCount() { return this.children.length; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  click() { this.clicked = true; this.listeners.click?.({ preventDefault() {}, currentTarget: this, target: this }); }
  focus() { this.focused = true; } reset() {} close() {} showModal() {}
}

function all(root, predicate, found = []) {
  if (predicate(root)) found.push(root);
  for (const child of root.children || []) all(child, predicate, found);
  return found;
}
const classes = n => String(n.className || n.attributes?.class || '').split(' ');
const byClass = (root, cls) => all(root, n => classes(n).includes(cls));
const byTag = (root, tag) => all(root, n => n.tag === tag);
const buttonByText = (root, text) => all(root, n => n.tag === 'button' && n.textContent === text)[0];
const response = data => ({ ok: true, status: 200, json: async () => data });
const tick = () => new Promise(resolve => setImmediate(resolve));

function app(fetchImpl = async () => response({ section: 'x', available: true, items: [] })) {
  const nodes = new Map(), calls = [], tags = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const body = new Element('body');
  // Like a real page, the chat composer exists only once Chat has rendered (it is not tracked here).
  const document = {
    body, getElementById: id => id === 'chat-message' ? null : get(id),
    createElement(tag) { tags.push(tag); return new Element(tag); },
    createElementNS(_, tag) { tags.push(tag); return new Element(tag); },
    createTextNode(text) { const n = new Element('text'); n.textContent = text; return n; },
    createDocumentFragment() { return new Element('fragment'); },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const window = { addEventListener() {} };
  // Short timers run on the next turn; long ones (polling, toasts) never fire in tests.
  const setTimeoutShort = (fn, ms) => { if ((Number(ms) || 0) < 1000) setImmediate(fn); return 0; };
  const context = vm.createContext({ document, window, Node: Element, URL, URLSearchParams, AbortController, Intl, Date,
    location: { hash: '' }, history: { replaceState() {} }, setTimeout: setTimeoutShort, clearTimeout() {},
    fetch: async (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); } });
  vm.runInContext(consoleSource.slice(0, boot) + 'globalThis.consoleTest={state,views,load,render,extensions};})();', context);
  vm.runInContext(briefingSource, context);
  const api = context.consoleTest;
  api.state.user = { username: 'owner', role: 'faculty', permissions: [] };
  api.state.csrf = 'synthetic-csrf';
  const show = (view, data) => { api.state.view = view; api.state.data = data; api.render(); return get('content'); };
  return { ...api, show, get, content: () => get('content'), calls, tags, body, window };
}

const hostile = '<img src=x onerror=alert(1)>';
const REV = 'a'.repeat(64);
const item = (over = {}) => ({ id: 'a-0123456789abcdef', severity: 'critical', kind: 'automation', title: 'Digest failed',
  detail: 'Connector disabled', link: '#routines', link_label: 'Open Automations', at: new Date(Date.now() - 3600e3).toISOString(), ...over });
const briefing = (over = {}) => ({
  section: 'briefing', available: true, source: 'hermes-home', summary: '1 urgent item needs your attention.', tone: 'bad',
  connected: 2, revision: REV,
  attention: { available: true, items: [item()], dismissed: 0, can_dismiss: true },
  kpis: { available: true, period: 'Last 7 days', items: [
    { id: 'fb', platform: 'facebook', label: 'Facebook page reach', value: 18420, change: 0.124, change_label: 'vs previous 7 days', good_when: 'up', series: [1, 3, 2, 5, 4, 6, 8], link: '#routines' },
    { id: 'tasks', platform: 'work', label: 'Tasks done', value: 37, change: -0.05, change_label: 'vs previous 7 days', good_when: 'up', series: [6, 5, 7, 4], link: null }] },
  reports: { available: true, total: 3, items: [{ id: 'r1', title: 'Daily social summary', source: 'Report', at: new Date().toISOString(), excerpt: 'Reach up 12%', status: 'ok', link: '#routines' }] },
  up_next: { available: true, total: 1, items: [{ id: 'j1', name: 'Reply digest', at: new Date(Date.now() + 7200e3).toISOString(), schedule: 'every 2h', kind: 'automation', link: '#routines' }] },
  prompts: [{ id: 'social', label: "Summarize today's social activity", prompt: 'Summarize today across my social channels.' }],
  items: [item()], actions: [{ id: 'dismiss', label: 'Dismiss', revision: REV, target: 'a-0123456789abcdef', fields: [{ name: 'id', label: 'Item', type: 'hidden', value: 'a-0123456789abcdef' }] }],
  ...over });

test('briefing registers as an essential page in the main group', () => {
  const a = app();
  assert.equal(a.views.briefing[0], 'Briefing');
  assert.match(a.views.briefing[1], /needs you/);
});

test('renders hero, KPI tiles with sparklines, queue, reports, schedule and prompts', () => {
  const a = app();
  const content = a.show('briefing', briefing());
  const text = content.textContent;
  for (const part of ['Needs you', '1 urgent item needs your attention.', 'Facebook page reach', '18.4K', '▲ 12.4%', '▼ 5.0%',
    'Needs your attention', 'Digest failed', 'Latest reports', 'Daily social summary', 'Reach up 12%', 'Up next', 'Reply digest',
    'Ask your assistant', "Summarize today's social activity"]) assert.ok(text.includes(part), 'missing ' + part);
  assert.equal(byClass(content, 'brf-kpi').length, 2);
  const sparks = byClass(content, 'brf-spark');
  assert.equal(sparks.length, 2);
  assert.match(sparks[0].attributes.class, /good/);
  assert.match(sparks[1].attributes.class, /bad/); // down while good_when is up
  assert.ok(byTag(sparks[0], 'path').length === 2 && byTag(sparks[0], 'circle').length === 1);
  assert.match(byTag(sparks[0], 'path')[1].attributes.d, /^M0\.00 /);
  assert.equal(byClass(content, 'brf-change')[1].className, 'brf-change bad');
  assert.ok(byClass(content, 'brf-item').some(n => n.className.includes('critical')));
});

test('untrusted strings render as text and only console hashes become links', async () => {
  const a = app(async () => response({ section: 'routines', available: true, items: [] }));
  const data = briefing({
    summary: hostile,
    attention: { available: true, can_dismiss: true, dismissed: 0, items: [
      item({ title: hostile, detail: hostile, link: 'javascript:alert(1)', link_label: 'Evil' }),
      item({ id: 'a-1111111111111111', title: 'Safe', link: '#routines', link_label: 'Open Automations' })] },
    kpis: { available: true, items: [{ id: 'k', platform: hostile, label: hostile, value: 'NaN', series: ['x', null, 3], link: 'https://evil.example/' }] },
    reports: { available: true, items: [{ id: 'r', title: hostile, source: hostile, excerpt: hostile, at: 'not a date', link: '//evil.example' }] },
    prompts: [{ id: 'p', label: hostile, prompt: hostile }] });
  a.state.view = 'briefing';
  const content = a.show('briefing', data);
  assert.ok(content.textContent.includes(hostile));
  assert.ok(!a.tags.includes('img') && !a.tags.includes('script') && !a.tags.includes('a'));
  assert.equal(buttonByText(content, 'Evil'), undefined);
  assert.equal(byClass(content, 'brf-kpi')[0].tag, 'div'); // external link ignored, tile not clickable
  assert.equal(byClass(content, 'brf-report')[0].tag, 'div');
  assert.equal(byClass(content, 'brf-kpi')[0].textContent.includes('—'), true);
  assert.equal(byClass(content, 'brf-spark')[0].attributes.class, 'brf-spark empty');
  buttonByText(content, 'Open Automations').click();
  await tick();
  assert.equal(a.state.view, 'routines');
  assert.equal(a.calls.at(-1).url, '/api/assistant/routines');
});

test('Dismiss posts the confirmed descriptor envelope with CSRF, then reloads quietly', async () => {
  const posted = [];
  const a = app(async (url, options) => {
    if (options?.method === 'POST') { posted.push({ url, options }); return response({ ok: true, message: 'Dismissed.' }); }
    return response(briefing({ attention: { available: true, items: [], dismissed: 1, can_dismiss: true }, items: [], actions: [] }));
  });
  a.state.view = 'briefing';
  const content = a.show('briefing', briefing());
  const dismiss = buttonByText(content, 'Dismiss');
  dismiss.click();
  assert.equal(dismiss.disabled, true);
  await tick(); await tick(); await tick();
  assert.equal(posted.length, 1);
  assert.equal(posted[0].url, '/api/assistant/briefing/action');
  assert.equal(posted[0].options.headers['X-CSRF-Token'], 'synthetic-csrf');
  assert.deepEqual(JSON.parse(posted[0].options.body), { action: 'dismiss', expected_revision: REV, confirm: true, values: { id: 'a-0123456789abcdef' } });
  assert.equal(a.calls.at(-1).url, '/api/assistant/briefing');
  assert.match(a.content().textContent, /all caught up/);
  assert.match(a.get('toast').textContent, /Dismissed/);
});

test('no Dismiss button when the server says the store is not writable', () => {
  const a = app();
  const content = a.show('briefing', briefing({ attention: { available: true, items: [item()], dismissed: 0, can_dismiss: false }, revision: null, actions: [] }));
  assert.equal(buttonByText(content, 'Dismiss'), undefined);
});

test('a quick prompt opens Chat with the message prefilled', async () => {
  const a = app(async () => response({ available: true, items: [] }));
  a.state.view = 'briefing';
  const content = a.show('briefing', briefing());
  byClass(content, 'brf-prompt')[0].click();
  for (let i = 0; i < 6; i++) await tick();
  assert.equal(a.state.view, 'chat');
  assert.equal(a.state.chatDraft, 'Summarize today across my social channels.');
  const composer = byTag(a.content(), 'textarea')[0];
  assert.equal(composer?.value, 'Summarize today across my social channels.');
  assert.equal(a.calls.filter(c => c.options?.method === 'POST').length, 0); // prefilled, never sent
});

test('unavailable blocks show their own empty states', () => {
  const a = app();
  const content = a.show('briefing', briefing({
    tone: 'good', summary: 'All clear.',
    attention: { available: true, items: [], dismissed: 2, can_dismiss: true },
    kpis: { available: false, message: 'No metrics are recorded yet.', items: [] },
    reports: { available: false, message: 'Automations could not be read.', items: [] },
    up_next: { available: true, items: [], total: 0 }, prompts: [], actions: [{ id: 'restore', label: 'Show dismissed items', revision: REV, fields: [] }] }));
  const text = content.textContent;
  for (const part of ['All clear', "You're all caught up", '2 dismissed item(s) are hidden.', 'No metrics are recorded yet.',
    'Automations could not be read.', 'Nothing scheduled', 'Show dismissed items']) assert.ok(text.includes(part), 'missing ' + part);
});

test('demo snapshot from the server renders end to end', async t => {
  const { execFileSync } = await import('node:child_process');
  const root = new URL('../../../', import.meta.url).pathname;
  let snapshot;
  try {
    snapshot = JSON.parse(execFileSync(root + '.runtime/venv/bin/python', ['-c',
      'import json; from pathlib import Path; from hermes_zalo.console_sections import ConsoleContext; from hermes_zalo import admin_briefing as b;' +
      'print(json.dumps(b.snapshot(ConsoleContext(Path("/nonexistent/js-test"), "demo", Path("/nonexistent")), {})))'],
    { env: { ...process.env, PYTHONPATH: root + 'src' }, encoding: 'utf8' }));
  } catch { t.skip('Python runtime unavailable'); return; } // optional for the Vercel package tests
  const a = app();
  const content = a.show('briefing', snapshot);
  assert.ok(byClass(content, 'brf-kpi').length >= 5);
  assert.ok(byClass(content, 'brf-item').length >= 3);
  assert.equal(byClass(content, 'brf-report').length, 4);
  assert.ok(byClass(content, 'brf-slot').length >= 3);
  assert.match(content.textContent, /Facebook page reach/);
  assert.equal(all(content, n => n.tag === 'button' && n.textContent === 'Dismiss').length, snapshot.attention.items.length);
});

const reportItem = (over = {}) => ({ id: 'r1', title: 'Daily social summary', source: 'Report', at: new Date().toISOString(), excerpt: 'Reach up 12%',
  status: 'ok', link: '#reports', report: { job: 'job_1', file: '2026-11-01_08-00-00.md' }, ...over });

test('Read report opens the exact report through HermesReports.open, with safe ids only', () => {
  const a = app(), opened = [];
  a.window.HermesReports = { open: (job, file) => opened.push([job, file]) };
  const data = briefing({
    attention: { available: true, can_dismiss: true, dismissed: 0, items: [item({ kind: 'report', severity: 'info', link: '#reports', link_label: 'Read report',
      report: { job: 'job_1', file: '2026-11-01_08-00-00.md' } })] },
    reports: { available: true, items: [reportItem(), reportItem({ id: 'r2', title: 'Bad ref', report: { job: '../x', file: '../../.env' } })] } });
  const content = a.show('briefing', data);
  buttonByText(content, 'Read report').click();
  byClass(content, 'brf-report')[0].click();
  assert.deepEqual(opened, [['job_1', '2026-11-01_08-00-00.md'], ['job_1', '2026-11-01_08-00-00.md']]);
  byClass(content, 'brf-report')[1].click(); // unsafe reference: never handed to the reader
  assert.equal(opened.length, 2);
});

test('without HermesReports, Read report falls back to the Reports page link', () => {
  const a = app(async () => response({ available: true, items: [] }));
  a.state.view = 'briefing';
  const content = a.show('briefing', briefing({ reports: { available: true, items: [reportItem({ link: '#routines' })] } }));
  byClass(content, 'brf-report')[0].click();
  assert.equal(a.state.view, 'routines');
});

test('Dismiss all appears in the queue header only with several items', () => {
  const a = app();
  const all = { id: 'dismiss_all', label: 'Dismiss all', revision: REV, fields: [] };
  const two = briefing({ attention: { available: true, can_dismiss: true, dismissed: 0, items: [item(), item({ id: 'a-1111111111111111' })] }, actions: [all] });
  assert.ok(buttonByText(a.show('briefing', two), 'Dismiss all'));
  assert.equal(buttonByText(a.show('briefing', briefing({ actions: [all] })), 'Dismiss all'), undefined);
});

test('palette commands: dismiss all goes through the briefing descriptor, prompts open Chat prefilled', async () => {
  const all = { id: 'dismiss_all', label: 'Dismiss all', revision: REV, fields: [] };
  const a = app(async url => response(url.startsWith('/api/assistant/briefing')
    ? briefing({ attention: { available: true, can_dismiss: true, dismissed: 0, items: [item(), item({ id: 'a-1111111111111111' })] }, actions: [all] })
    : { available: true, items: [] }));
  const spec = a.extensions.get('briefing');
  const kit = a.window.HermesConsole.kit;
  const list = spec.commands(kit);
  assert.ok(list.length >= 3 && list.every(c => typeof c.label === 'string' && typeof c.run === 'function'));
  a.state.view = 'routines'; a.state.data = { available: true, items: [] };
  list.find(c => c.label === 'Dismiss all attention items').run(kit);
  for (let i = 0; i < 6; i++) await tick();
  assert.equal(a.state.view, 'briefing');
  assert.equal(a.state.control?.action?.id, 'dismiss_all'); // descriptor dialog opened, nothing posted yet
  assert.equal(a.calls.filter(c => c.options?.method === 'POST').length, 0);
  list.find(c => /what needs my reply/i.test(c.label)).run(kit);
  for (let i = 0; i < 6; i++) await tick();
  assert.equal(a.state.view, 'chat');
  assert.match(a.state.chatDraft, /waiting for my reply/);
});
