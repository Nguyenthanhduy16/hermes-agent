import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const base = new URL('../../../src/hermes_zalo/admin_static/', import.meta.url);
const source = await readFile(new URL('console.js', base), 'utf8');
const extension = await readFile(new URL('console-operations.js', base), 'utf8');
const boot = source.lastIndexOf('  api("/api/session").then');
assert.ok(boot > 0);

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; this.hidden = false; this.disabled = false;
    this.open = false; this.dataset = {}; this.attributes = {}; this._text = ''; this.className = '';
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  set id(value) { this._id = value; this.onId?.(value); }
  get id() { return this._id; }
  set innerHTML(_) { throw new Error('Untrusted content must never use innerHTML'); }
  append(...nodes) { for (const n of nodes) { if (n.tag === 'fragment') this.append(...n.children); else { n.parent = this; this.children.push(n); } } }
  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { if (/^on/i.test(name) || name === 'style') throw new Error('No inline handlers or styles: ' + name); this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  get childElementCount() { return this.children.length; }
  querySelectorAll() { return []; }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
  find(predicate) { if (predicate(this)) return this; for (const c of this.children) { const hit = c.find?.(predicate); if (hit) return hit; } return null; }
  findAll(predicate, out = []) { if (predicate(this)) out.push(this); for (const c of this.children) c.findAll?.(predicate, out); return out; }
}

const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data });
function app(fetchImpl = async () => response({ ok: true, message: 'Done.' })) {
  const nodes = new Map(), calls = [], tags = [], timers = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const document = {
    getElementById: get,
    createElement(tag) { tags.push(tag); const e = new Element(tag); e.onId = id => nodes.set(id, e); return e; },
    createElementNS(_, tag) { return new Element(tag); },
    createTextNode(text) { const n = new Element('text'); n.textContent = text; return n; },
    createDocumentFragment() { return new Element('fragment'); },
    querySelectorAll(selector) { return selector === 'dialog[open]' ? [...nodes.values()].filter(n => n.open) : []; },
    addEventListener() {},
  };
  const window = { addEventListener() {} };
  const context = vm.createContext({ document, Node: Element, URL, URLSearchParams, AbortController, Intl, Date, window,
    location: { hash: '' }, history: { replaceState() {} },
    setTimeout: (fn, ms) => { timers.push(ms); return 0; }, clearTimeout() {},
    fetch: async (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); } });
  vm.runInContext(source.slice(0, boot) + 'globalThis.consoleTest={state,views,render,allowed,openAction,submitAction,sectionActions};})();', context);
  vm.runInContext(extension, context);
  const api = context.consoleTest;
  api.state.user = { username: 'owner', role: 'faculty', permissions: [] };
  api.state.csrf = 'synthetic-csrf';
  api.state.view = 'operations';
  return { ...api, node: get, calls, tags, timers };
}

const now = new Date().toISOString();
function snapshot(overrides = {}) {
  return {
    section: 'operations', available: true, source: 'system', revision: 'rev-1',
    gateway: { state: 'running', pid: 4321, uptime_seconds: 93784, detected_by: 'systemd', detail: 'systemd: active (running)',
      platforms: [{ name: 'telegram', state: 'connected', needs_attention: false }], active_agents: 1 },
    scheduler: { status: 'fresh', age_seconds: 20 }, api_server: { status: 'ok', detail: 'Responding on port 8642' },
    versions: { hermes_version: '0.21.6', hermes_running_commit: 'b'.repeat(40), hermes_source_commit: 'c'.repeat(40), console_commit: 'd'.repeat(40), bot_mode: 'assistant', sandbox_backend: 'docker' },
    resources: { cpu_count: 2, load_1m: 0.5, load_5m: 0.4, load_15m: 0.3, memory_total_bytes: 4e9, memory_used_bytes: 3.8e9, memory_available_bytes: 2e8,
      memory_percent: 95, disk_total_bytes: 8e10, disk_used_bytes: 3e10, disk_free_bytes: 5e10, disk_percent: 37.5, host_uptime_seconds: 86400 },
    pressure: [{ id: 'memory', label: 'Memory is almost full', detail: '95% in use.' }],
    history: { points: [{ t: now, load_1m: 0.4, memory_percent: 60 }, { t: now, load_1m: 0.6, memory_percent: 70 }] },
    control: { enabled: true, kind: 'systemd-user', target: 'hermes-gateway.service', commands: [] },
    checks: { ran_at: null, items: [{ id: 'env', label: '.env', status: 'warn', detail: '<img src=x onerror=alert(1)>', hint: 'chmod 600 /srv/.env' }], summary: { warn: 1 } },
    backups: { items: [{ id: 'hermes-home-20261031-020000.tar.gz', name: 'hermes-home-20261031-020000.tar.gz', size_bytes: 1048576, created_at: now, includes_secrets: true }],
      total_bytes: 1048576, restore_command: 'tar -xzpf /h/backups/console/hermes-home-20261031-020000.tar.gz -C /h --strip-components=1 hermes-home', stop_first: 'systemctl --user stop hermes-gateway.service', folder: '/h/backups/console' },
    job: null,
    updates: { results: [{ id: 'hermes', label: 'Hermes Agent', status: 'available', message: 'Newer commit.' }], commands: [{ label: 'Update Hermes', command: 'hermes update' }] },
    actions: [
      { id: 'run_checks', label: 'Run checks', revision: 'rev-1' },
      { id: 'create_backup', label: 'Create backup', revision: 'rev-1', fields: [{ name: 'include_secrets', label: 'Include secrets', type: 'checkbox', value: false }, { name: 'keep', label: 'Keep', type: 'number', value: 5 }] },
      { id: 'delete_backup', label: 'Delete', revision: 'rev-1', target: 'hermes-home-20261031-020000.tar.gz', fields: [{ name: 'backup', type: 'hidden', value: 'hermes-home-20261031-020000.tar.gz' }] },
      { id: 'check_updates', label: 'Check for updates', revision: 'rev-1' },
      { id: 'gateway_restart', label: 'Restart gateway', revision: 'rev-1' },
      { id: 'gateway_stop', label: 'Stop gateway', revision: 'rev-1', fields: [{ name: 'confirm_name', label: 'Type hermes-gateway.service to confirm', type: 'text', required: true }] }],
    ...overrides,
  };
}
const buttonLabels = a => a.node('content').findAll(n => n.tag === 'button').map(b => b.textContent);

test('the System page registers as an owner-only advanced view', () => {
  const a = app();
  assert.equal(a.views.operations[0], 'System');
  assert.equal(a.allowed('operations'), true);
  a.state.user.role = 'admin';
  assert.equal(a.allowed('operations'), false);
});

test('status, pressure, checks, backups and updates render as text with descriptor buttons', () => {
  const a = app(); a.state.data = snapshot(); a.render();
  const text = a.node('content').textContent;
  for (const expected of ['Running', 'Memory is almost full', '1d 2h', 'telegram (connected)', 'Fix: chmod 600 /srv/.env',
    'hermes-home-20261031-020000.tar.gz', 'Included', '--strip-components=1', 'Update available', 'Restore (on the server only)',
    'The running gateway is older than the installed code']) assert.ok(text.includes(expected), expected);
  assert.ok(text.includes('<img src=x onerror=alert(1)>'), 'hostile text stays text');
  assert.ok(!a.tags.includes('img') && !a.tags.includes('script'));
  const labels = buttonLabels(a);
  for (const label of ['Run checks', 'Create backup', 'Delete', 'Check for updates', 'Restart gateway', 'Stop gateway']) assert.ok(labels.includes(label), label);
  assert.equal(a.timers.at(-1), 30000, 'idle pages refresh slowly');
});

test('disabled control explains how to enable it and offers no control buttons', () => {
  const a = app();
  a.state.data = snapshot({ control: { enabled: false, error: 'HERMES_CONSOLE_GATEWAY_CONTROL must start with one of: systemd-user:',
    examples: [{ label: 'systemd user service', value: 'HERMES_CONSOLE_GATEWAY_CONTROL=systemd-user:hermes-gateway.service' }],
    commands: [{ label: 'Restart', command: 'systemctl --user restart hermes-gateway.service' }] },
    actions: snapshot().actions.filter(x => !x.id.startsWith('gateway_')) });
  a.render();
  const text = a.node('content').textContent;
  assert.match(text, /Gateway control is off/);
  assert.match(text, /HERMES_CONSOLE_GATEWAY_CONTROL=systemd-user:hermes-gateway.service/);
  assert.match(text, /must start with/);
  assert.ok(!buttonLabels(a).some(l => /gateway/.test(l)));
});

test('a running backup shows progress and polls quickly', () => {
  const a = app();
  a.state.data = snapshot({ job: { kind: 'backup', label: 'Creating backup', status: 'running', progress: { files: 1234, bytes: 2097152 } } });
  a.render();
  assert.match(a.node('content').textContent, /Creating backup….*1,234 files, 2\.0 MB read/);
  assert.equal(a.timers.at(-1), 3000);
});

test('stop posts the typed name and delete posts only the hidden backup name', async () => {
  const a = app(); a.state.data = snapshot(); a.render();
  const stop = a.state.data.actions.find(x => x.id === 'gateway_stop');
  a.openAction(stop);
  a.state.control.controls[0].control.value = 'hermes-gateway.service';
  a.node('confirm-dialog').showModal = function () { this.open = true; this.returnValue = 'accept'; queueMicrotask(() => this.close()); };
  await a.submitAction();
  const post = a.calls.find(c => c.url === '/api/assistant/operations/action');
  assert.deepEqual(JSON.parse(post.options.body), { action: 'gateway_stop', expected_revision: 'rev-1', confirm: true, values: { confirm_name: 'hermes-gateway.service' } });
  a.state.data = snapshot(); a.render();
  a.openAction(a.state.data.actions.find(x => x.id === 'delete_backup'));
  await a.submitAction();
  const last = a.calls.filter(c => c.url === '/api/assistant/operations/action').at(-1);
  assert.deepEqual(JSON.parse(last.options.body).values, { backup: 'hermes-home-20261031-020000.tar.gz' });
});

test('missing or malformed data degrades to unknown values', () => {
  const a = app();
  a.state.data = { section: 'operations', available: true, items: [], gateway: { state: '<b>bad</b>' }, resources: { load_1m: 'NaN' }, history: { points: 'x' } };
  a.render();
  const text = a.node('content').textContent;
  assert.match(text, /Unknown/);
  assert.match(text, /Collecting readings/);
  assert.match(text, /No backups yet/);
});
