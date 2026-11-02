import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../../../src/hermes_zalo/admin_static/console.js', import.meta.url), 'utf8');
const boot = source.lastIndexOf('  api("/api/session").then');
assert.ok(boot > 0);

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; this.hidden = false;
    this.disabled = false; this.open = false; this.dataset = {}; this.attributes = {}; this._text = '';
    const classes = new Set();
    this.classList = { add: c => classes.add(c), remove: c => classes.delete(c), toggle: (c, on = !classes.has(c)) => { if (on) classes.add(c); else classes.delete(c); return on; }, contains: c => classes.has(c) };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  set id(value) { this._id = value; this.onId?.(value); }
  get id() { return this._id; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  set innerHTML(_) { throw new Error('Untrusted content must never use innerHTML'); }
  append(...nodes) { for (const n of nodes) { if (n.tag === 'fragment') this.append(...n.children); else { n.parent = this; this.children.push(n); } } }
  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  get lastChild() { return this.children.at(-1); }
  get childElementCount() { return this.children.length; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); }
  querySelectorAll() { return []; }
  focus() {}
  reset() {}
  scrollIntoView() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
}

const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data });
function app(fetchImpl = async () => response({ items: [] }), extra = {}) {
  const nodes = new Map(), calls = [], tags = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const document = {
    getElementById: get,
    createElement(tag) { tags.push(tag); const element = new Element(tag); element.onId = id => nodes.set(id, element); return element; },
    createElementNS(_, tag) { return new Element(tag); },
    createTextNode(text) { const n = new Element('text'); n.textContent = text; return n; },
    createDocumentFragment() { return new Element('fragment'); },
    querySelectorAll(selector) { return selector === 'dialog[open]' ? [...nodes.values()].filter(n => n.open) : []; },
    addEventListener() {},
  };
  const window = { addEventListener() {} };
  const context = vm.createContext({ document, Node: Element, URL, URLSearchParams, AbortController, Intl, Date,
    location: { hash: '' }, history: { replaceState() {} }, window, ...extra,
    setTimeout: () => 0, clearTimeout() {},
    fetch: async (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); },
  });
  vm.runInContext(source.slice(0, boot) + 'globalThis.consoleTest={state,views,showApp,showLogin,load,openSession,render,allowed,busy,sendChat,safeDocumentationUrl,openAction,submitAction,sectionActions,navigate,resolveView,essential,note,noteFrom,openChat,openPalette,renderPalette,movePalette,runPaletteItem,paletteEntries,refreshNav,describeSchedule,formatRelative,setPinned,resetPins,openCustomize,toast,heading};})();', context);
  const api = context.consoleTest;
  api.state.user = { username: 'owner', role: 'faculty', permissions: ['conversations', 'memories'] };
  api.state.csrf = 'synthetic-csrf';
  return { ...api, node: get, calls, tags, register: window.HermesConsole.register };
}

test('owner status does not bypass conversation or memory permission', () => {
  const a = app(); a.state.user.permissions = [];
  assert.equal(a.allowed('overview'), true);
  assert.equal(a.allowed('sessions'), false);
  assert.equal(a.allowed('memories'), false);
  a.state.user.permissions = ['memories'];
  assert.equal(a.allowed('memories'), false);
  a.state.user.permissions = ['conversations', 'memories'];
  assert.equal(a.allowed('memories'), true);
  a.state.runtimeHistory = true;
  assert.equal(a.allowed('memories'), false, 'standalone runtime does not claim access to legacy managed memory');
  a.state.user.role = 'admin';
  assert.equal(a.allowed('runtime'), false);
});

test('late sensitive reads cannot repaint the console after logout', async () => {
  let finish;
  const a = app(() => new Promise(resolve => { finish = resolve; }));
  a.state.view = 'memories';
  const pending = a.load();
  a.showLogin();
  finish(response({ items: [{ content: 'private memory must stay hidden', active: true }] }));
  await pending;
  assert.equal(a.state.user, null);
  assert.equal(a.state.csrf, '');
  assert.equal(a.state.data, null);
  assert.equal(a.node('content').textContent, '');
});

test('session expiry clears the workspace and pending metadata', async () => {
  const a = app(async () => response({ error: 'Session expired' }, 401));
  await a.load();
  assert.equal(a.state.user, null);
  assert.equal(a.state.csrf, '');
  assert.equal(a.node('app').hidden, true);
  assert.equal(a.node('content').textContent, '');
});

test('runtime mode reads its explicit session API and never falls back to Lab history', async () => {
  const a = app(); a.state.runtimeHistory = true; a.state.view = 'sessions'; a.state.page = 3;
  await a.load();
  assert.equal(a.calls[0].url, '/api/assistant/sessions?page=3');
  await a.openSession({ runtime_session_id: 'tele-opaque', title: 'Telegram' }, 2);
  assert.equal(a.calls[1].url, '/api/assistant/history?session_id=tele-opaque&page=2');
});

test('untrusted skill text is rendered as text and unknown prices stay unknown', () => {
  const a = app(); a.state.view = 'skills';
  const hostile = '<img src=x onerror=alert(1)>';
  a.state.data = { items: [{ name: hostile, description: '<script>private</script>' }] };
  a.render();
  assert.ok(a.node('content').textContent.includes(hostile));
  assert.ok(!a.tags.includes('script') && !a.tags.includes('img'));
  a.state.view = 'usage'; a.state.data = { calls: null, cost_usd: null, items: [] };
  a.render();
  assert.match(a.node('content').textContent, /Pricing unavailable/);
  assert.doesNotMatch(a.node('content').textContent, /\$0/);
});

test('duplicate form submissions cannot repeat an in-flight write', async () => {
  const a = app(), form = new Element('form');
  let finish, writes = 0;
  const pending = a.busy(form, async () => { writes++; await new Promise(resolve => { finish = resolve; }); });
  await a.busy(form, async () => { writes++; });
  assert.equal(writes, 1);
  finish(); await pending;
  await a.busy(form, async () => { writes++; });
  assert.equal(writes, 2);
});

test('users show channel allowlists without inventing quota usage or granting permissions', () => {
  const a = app(); a.state.view = 'users';
  a.state.data = { total: 1, items: [{ user_id: 'synthetic-telegram-user', platform: 'telegram', used_today: null }],
    user_daily_limit: 30, total_daily_limit: 500, total_used_today: null, day: '2026-10-31' };
  a.render();
  assert.match(a.node('content').textContent, /synthetic-telegram-user/);
  assert.match(a.node('content').textContent, /Telegram/);
  assert.match(a.node('content').textContent, /—/);
  assert.equal(a.calls.length, 0, 'viewing the roster does not change user permissions');
});

test('English management navigation keeps the useful sections and preserves restricted views', async () => {
  const html = await readFile(new URL('../../../src/hermes_zalo/admin_static/console.html', import.meta.url), 'utf8');
  assert.match(html, /<html lang="en">/);
  for (const view of ['kanban', 'pairing', 'profiles']) {
    assert.doesNotMatch(html, new RegExp('data-view="' + view + '"'), view + ' was removed from the console');
    assert.equal(app().views[view], undefined, view);
  }
  for (const view of ['chat', 'files', 'models', 'plugins', 'mcp', 'channels', 'webhooks', 'logs', 'config', 'keys', 'documentation']) {
    assert.match(html, new RegExp('data-view="' + view + '"'));
    const a = app(); a.state.user.role = 'admin';
    assert.equal(a.allowed(view), false, view + ' requires owner access');
  }
});

test('metadata and file previews render untrusted text without creating HTML', async () => {
  const a = app(); a.state.view = 'plugins';
  a.state.data = { items: [{ name: '<img onerror=alert(1)>', status: '<script>bad</script>', tools: ['web', 'vision'] }] };
  a.render();
  assert.match(a.node('content').textContent, /<img onerror=alert\(1\)>/);
  assert.ok(!a.tags.includes('img') && !a.tags.includes('script'));
  a.state.view = 'files'; a.state.data = { path: 'report.txt', content: '<script>private</script>', truncated: true };
  a.render();
  assert.match(a.node('content').textContent, /<script>private<\/script>/);
  assert.match(a.node('content').textContent, /truncated/);
  assert.equal(a.safeDocumentationUrl('javascript:alert(1)'), null);
  assert.equal(a.safeDocumentationUrl('https://evil.example/docs'), null);
  assert.equal(a.safeDocumentationUrl('https://github.com/NousResearch/hermes-agent'), 'https://github.com/NousResearch/hermes-agent');
});

test('file browsing sends only a relative encoded workspace path', async () => {
  const a = app(); a.state.view = 'files'; a.state.filePath = 'reports/Q4 sales.csv';
  await a.load();
  assert.equal(a.calls[0].url, '/api/assistant/files?path=reports%2FQ4+sales.csv');
});

test('chat submits one CSRF-protected request with bounded prior history and preserves original language', async () => {
  let finish;
  const a = app(() => new Promise(resolve => { finish = resolve; }));
  a.state.view = 'chat'; a.state.data = { available: true };
  a.state.chatMessages = [
    { role: 'system', content: 'not a browser instruction' },
    ...Array.from({ length: 12 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', content: 'x'.repeat(3000) }))
  ];
  a.render(); a.node('chat-message').value = 'Giúp tôi phân tích dữ liệu';
  const pending = a.sendChat({ preventDefault() {} });
  await a.sendChat({ preventDefault() {} });
  assert.equal(a.calls.length, 1);
  assert.equal(a.calls[0].url, '/api/assistant/chat');
  assert.equal(a.calls[0].options.headers['X-CSRF-Token'], 'synthetic-csrf');
  const payload = JSON.parse(a.calls[0].options.body);
  assert.equal(payload.message, 'Giúp tôi phân tích dữ liệu');
  assert.ok(payload.history.length <= 12);
  assert.ok(payload.history.reduce((size, item) => size + item.content.length, 0) <= 24000);
  assert.ok(payload.history.every(item => ['user', 'assistant'].includes(item.role)));
  finish(response({ reply: 'Tôi có thể giúp bạn.', source: 'synthetic' }));
  await pending;
  assert.match(a.node('content').textContent, /Tôi có thể giúp bạn/);
});

test('failed chat never retries or invents a reply; logout clears the local conversation', async () => {
  const a = app(async () => response({ error: 'Gateway unavailable' }, 503));
  a.state.view = 'chat'; a.state.data = { available: true }; a.render();
  a.node('chat-message').value = 'Test message';
  await a.sendChat({ preventDefault() {} });
  assert.equal(a.calls.length, 1);
  assert.equal(a.state.chatMessages.length, 0);
  assert.match(a.node('chat-error').textContent, /Gateway unavailable/);
  a.state.chatMessages.push({ role: 'user', content: 'private content' });
  a.showLogin();
  assert.equal(a.state.chatMessages.length, 0);
});

test('late chat replies cannot enter a new signed-in user conversation', async () => {
  let finish;
  const a = app(() => new Promise(resolve => { finish = resolve; }));
  a.state.view = 'chat'; a.state.data = { available: true }; a.render();
  a.node('chat-message').value = 'Private request';
  const pending = a.sendChat({ preventDefault() {} });
  a.showLogin();
  a.state.user = { username: 'different-owner', role: 'faculty', permissions: [] };
  a.state.data = { available: true }; a.state.view = 'chat'; a.render();
  finish(response({ reply: 'Private reply from the previous user' }));
  await pending;
  assert.equal(a.state.chatMessages.length, 0);
  assert.doesNotMatch(a.node('content').textContent, /Private reply/);
});

test('a draft typed while a reply is pending survives the re-render; the sent text is cleared', async () => {
  let finish;
  const a = app(() => new Promise(resolve => { finish = resolve; }));
  a.state.view = 'chat'; a.state.data = { available: true }; a.render();
  a.node('chat-message').value = 'First question';
  const pending = a.sendChat({ preventDefault() {} });
  a.node('chat-message').value = 'Follow-up draft';
  finish(response({ reply: 'First answer' })); await pending;
  assert.equal(a.node('chat-message').value, 'Follow-up draft');
  assert.equal(a.calls.length, 1);
  a.node('chat-message').value = 'Second question';
  const second = a.sendChat({ preventDefault() {} });
  finish(response({ reply: 'Second answer' })); await second;
  assert.equal(a.node('chat-message').value, '');
  a.showLogin();
  assert.equal(a.state.chatDraft, '');
});

test('long earlier replies stay whole on screen but each transmitted history entry fits the 4000 limit', async () => {
  const long = 'a'.repeat(3999) + '😀' + 'tail'.repeat(500);
  const a = app(async () => response({ reply: 'ok' }));
  a.state.view = 'chat'; a.state.data = { available: true };
  a.state.chatMessages = [{ role: 'user', content: 'q' }, { role: 'assistant', content: long }];
  a.render();
  assert.ok(a.node('content').textContent.includes(long), 'displayed reply is not truncated');
  a.node('chat-message').value = 'Next question';
  await a.sendChat({ preventDefault() {} });
  const { history } = JSON.parse(a.calls[0].options.body);
  assert.ok(history.every(item => Array.from(item.content).length <= 4000));
  assert.equal(history[1].content, 'a'.repeat(3999) + '😀', 'truncation never splits a character');
  assert.ok(history.reduce((size, item) => size + item.content.length, 0) <= 24000);
  assert.equal(a.state.chatMessages[1].content, long, 'stored conversation keeps the full reply');
});


// ---- Management action descriptors -----------------------------------------
const walk = n => [n, ...n.children.flatMap(walk)];
const buttons = a => walk(a.node('content')).filter(n => n.tag === 'button');
const press = (a, label) => { const b = buttons(a).find(n => n.textContent === label); assert.ok(b, 'button ' + label); b.listeners.click(); return b; };
const tick = () => new Promise(resolve => setImmediate(resolve));
const accept = a => { a.node('confirm-dialog').returnValue = 'accept'; a.node('confirm-dialog').close(); };
const modelSnapshot = () => ({
  section: 'models', available: true, revision: 'r1',
  items: [{ id: 'main', role: 'Main', model: 'synthetic/model-a', provider: 'openrouter' }, { id: 'vision', role: 'vision', model: 'synthetic/vision' }],
  actions: [
    { id: 'save_main', label: 'Change main model', revision: 'rev-1', target: 'main', fields: [
      { name: 'model', label: 'Model', type: 'text', value: 'synthetic/model-a', required: true, max_length: 20 },
      { name: 'provider', label: 'Provider', type: 'select', value: 'openrouter', options: [{ value: 'openrouter', label: 'OpenRouter' }, { value: 'auto', label: 'Automatic' }] },
      { name: 'api_key', label: 'API key', type: 'password', value: 'must-never-prefill' },
      { name: 'context_length', label: 'Context', type: 'number' },
      { name: 'stream', label: 'Stream', type: 'checkbox', value: true },
      { name: 'role', type: 'hidden', value: 'main' }] },
    { id: 'add_aux', label: '<img src=x onerror=alert(1)>', revision: 'rev-1', fields: [] },
    { id: 'bad_type', label: 'Run shell', revision: 'rev-1', fields: [{ name: 'cmd', type: 'script' }] },
    { id: 'bad_value', label: 'Object prefill', revision: 'rev-1', fields: [{ name: 'x', type: 'text', value: { a: 1 } }] },
    { id: 'dupe', label: 'Duplicate', revision: 'rev-1', fields: [{ name: 'x', type: 'text' }, { name: 'x', type: 'text' }] },
    { id: 'no_revision', label: 'No revision', fields: [] }]
});
function managed(fetchImpl) {
  const a = app(fetchImpl); a.state.view = 'models'; a.state.data = modelSnapshot(); a.render(); return a;
}

test('descriptors render as text; only valid ones become controls, row actions sit with their row', () => {
  const a = managed();
  const labels = buttons(a).map(b => b.textContent);
  assert.ok(labels.includes('Change main model'));
  assert.ok(labels.includes('<img src=x onerror=alert(1)>'), 'hostile label rendered as text');
  assert.ok(!a.tags.includes('img') && !a.tags.includes('script'));
  for (const rejected of ['Run shell', 'Object prefill', 'Duplicate', 'No revision']) assert.ok(!labels.includes(rejected), rejected);
  assert.deepEqual(a.sectionActions(a.state.data).map(x => x.id), ['save_main', 'add_aux']);
  a.state.data = { ...modelSnapshot(), actions: undefined }; a.render();
  assert.equal(buttons(a).length, 0, 'no controls when the snapshot has no actions');
});

test('action dialog never prefills secrets and posts the exact contract once with CSRF', async () => {
  let finish;
  const a = managed((url, options) => options.method === 'POST' ? new Promise(resolve => { finish = resolve; }) : response(modelSnapshot()));
  press(a, 'Change main model');
  assert.equal(a.node('action-dialog').open, true);
  assert.equal(a.node('action-title').textContent, 'Change main model');
  const fields = walk(a.node('action-fields')).filter(n => ['input', 'select', 'textarea'].includes(n.tag));
  const password = fields.find(n => n.type === 'password');
  assert.equal(password.value, '', 'existing secret is never prefilled');
  assert.equal(fields.find(n => n.type === 'text').value, 'synthetic/model-a');
  password.value = 'sk-synthetic'; fields.find(n => n.type === 'number').value = '128000';
  fields.find(n => n.tag === 'select').value = 'auto';
  const pending = a.submitAction({ preventDefault() {} });
  await a.submitAction({ preventDefault() {} });
  assert.equal(a.calls.length, 0, 'confirmation precedes the write');
  accept(a); await tick();
  await a.submitAction({ preventDefault() {} });
  assert.equal(a.calls.length, 1, 'competing submit is ignored');
  const call = a.calls[0];
  assert.equal(call.url, '/api/assistant/models/action');
  assert.equal(call.options.headers['X-CSRF-Token'], 'synthetic-csrf');
  assert.deepEqual(JSON.parse(call.options.body), { action: 'save_main', expected_revision: 'rev-1', confirm: true,
    values: { model: 'synthetic/model-a', provider: 'auto', api_key: 'sk-synthetic', context_length: 128000, stream: true, role: 'main' } });
  finish(response({ ok: true, message: 'Main model saved for new sessions.' })); await pending; await tick();
  assert.equal(a.node('action-dialog').open, false);
  assert.equal(a.node('action-fields').children.length, 0, 'fields, including the secret, are discarded');
  assert.equal(a.state.control, null);
  assert.match(a.node('toast').textContent, /Main model saved/);
  assert.equal(a.calls.filter(c => c.options.method === 'POST').length, 1);
  assert.equal(a.calls.at(-1).url, '/api/assistant/models', 'success reloads the snapshot');
});

test('a conflict clears the secret, never retries and blocks writes until a successful refresh', async () => {
  const a = managed((url, options) => options.method === 'POST' ? response({ error: 'Configuration changed elsewhere.' }, 409) : response(modelSnapshot()));
  press(a, 'Change main model');
  const password = walk(a.node('action-fields')).find(n => n.type === 'password'); password.value = 'sk-synthetic';
  const pending = a.submitAction({ preventDefault() {} }); accept(a); await pending;
  assert.equal(a.calls.length, 1);
  assert.equal(password.value, '', 'secret cleared after an error');
  assert.match(a.node('action-error').textContent, /changed elsewhere.*Refresh this section/);
  assert.equal(a.state.controlRefresh, 'models');
  await a.submitAction({ preventDefault() {} });
  assert.equal(a.calls.length, 1, 'no automatic or repeated write');
  a.node('action-dialog').close();
  assert.ok(buttons(a).find(b => b.textContent === 'Change main model').disabled, 'actions disabled until refresh');
  await a.load();
  assert.equal(a.state.controlRefresh, '');
  assert.equal(buttons(a).find(b => b.textContent === 'Change main model').disabled, false);
});

test('local validation blocks the write without locking the section', async () => {
  const a = managed();
  press(a, 'Change main model');
  const text = walk(a.node('action-fields')).find(n => n.type === 'text');
  text.value = '   ';
  await a.submitAction({ preventDefault() {} });
  assert.match(a.node('action-error').textContent, /Model is required/);
  text.value = 'x'.repeat(21);
  await a.submitAction({ preventDefault() {} });
  assert.match(a.node('action-error').textContent, /20 characters or fewer/);
  assert.equal(a.calls.length, 0);
  assert.equal(a.state.controlRefresh, '');
});

test('logout during an in-flight action discards the dialog and the late reply cannot repaint', async () => {
  let finish;
  const a = managed((url, options) => options.method === 'POST' ? new Promise(resolve => { finish = resolve; }) : response(modelSnapshot()));
  press(a, 'Change main model');
  walk(a.node('action-fields')).find(n => n.type === 'password').value = 'sk-synthetic';
  const pending = a.submitAction({ preventDefault() {} }); accept(a); await tick();
  a.showLogin();
  assert.equal(a.state.control, null);
  assert.equal(a.node('action-fields').children.length, 0);
  finish(response({ ok: true, message: 'late' })); await pending;
  assert.notEqual(a.node('toast').textContent, 'late');
  assert.equal(a.calls.length, 1);
});

test('admin role cannot open owner management actions', () => {
  const a = managed(); a.state.user.role = 'admin';
  press(a, 'Change main model');
  assert.equal(a.node('action-dialog').open, false);
});

test('a response without ok:true is not treated as success', async () => {
  const a = managed((url, options) => options.method === 'POST' ? response({ message: 'maybe' }) : response(modelSnapshot()));
  press(a, 'Change main model');
  const pending = a.submitAction({ preventDefault() {} }); accept(a); await pending;
  assert.match(a.node('action-error').textContent, /did not confirm/);
  assert.equal(a.state.controlRefresh, 'models');
});

test('skills filter by category and search; row toggles stay with their skill', () => {
  const a = app(); a.state.view = 'skills';
  a.state.data = { items: [
    { id: 'research:arxiv', name: 'arxiv', category: 'research', enabled: true, tags: ['papers'] },
    { id: 'productivity:notes', name: 'notes', category: 'productivity', enabled: false }],
    actions: [{ id: 'toggle', label: 'Disable', revision: 'r', target: 'research:arxiv', fields: [{ name: 'skill', type: 'hidden', value: 'research:arxiv' }] },
      { id: 'create', label: 'Create skill', revision: 'r', fields: [{ name: 'name', type: 'text', required: true }] }] };
  a.render();
  assert.match(a.node('content').textContent, /1 enabled · 2 installed/);
  press(a, 'Productivity · 1');
  assert.equal(a.state.skillCategory, 'productivity');
  assert.doesNotMatch(a.node('content').textContent, /arxiv/);
  assert.ok(!buttons(a).some(b => b.textContent === 'Disable'), 'row action hidden with its filtered row');
  assert.ok(buttons(a).some(b => b.textContent === 'Create skill'), 'global action stays in toolbar');
  press(a, 'All · 2'); a.node('search').value = 'papers'; a.render();
  assert.match(a.node('content').textContent, /arxiv/);
  assert.doesNotMatch(a.node('content').textContent, /notes/);
});

test('cron uses the AutomationAdmin schema in plain English: waiting state, redacted latest excerpt, run errors as text', () => {
  const a = app(); a.state.view = 'routines';
  a.state.data = { total: 2, scheduler_active: false, items: [
    { id: 'job-1', name: 'Digest', prompt_preview: 'Summarize <b>news</b>', schedule: '0 9 * * 5', enabled: true, state: 'scheduled',
      next_run_at: null, next_run_pending: true, last_status: 'error', last_error: '<script>x</script>', deliver: 'local',
      repeat: { times: 5, completed: 2 }, failure_streak: 2, has_script: true,
      runs: [{ status: 'failed', claimed_at: '2026-10-30T02:00:00Z', started_at: null, finished_at: null, error: 'Timeout <img>' }],
      outputs: [{ name: '2026-10-31_07-00-04.md', at: '2026-10-31T07:00:04' }, { name: '2026-10-30_07-00-03.md', at: '2026-10-30T07:00:03' }],
      latest_output: { name: '2026-10-31_07-00-04.md', excerpt: 'Headline [REDACTED] <script>y</script>', truncated: true } },
    { id: 'job-2', name: 'Weekly', schedule: 'every 7d', enabled: false, state: 'paused', next_run_pending: false,
      latest_output: { name: '2026-10-29_09-00-00.md', excerpt: null, excerpt_omitted: true }, outputs: [{ name: '2026-10-29_09-00-00.md' }] }],
    actions: [{ id: 'resume_routine', label: 'Resume', revision: 'r', target: 'job-2', fields: [{ name: 'id', type: 'hidden', value: 'job-2' }] }] };
  a.render();
  const text = a.node('content').textContent;
  assert.match(text, /Waiting for the scheduler/);
  assert.doesNotMatch(text, /Pending scheduler tick/);
  assert.match(text, /Every Friday at 09:00/);
  assert.match(text, /Every week/);
  assert.doesNotMatch(text, /0 9 \* \* 5/, 'raw cron is never the primary text');
  assert.match(text, /No running Hermes scheduler/);
  assert.match(text, /Summarize <b>news<\/b>/);
  assert.match(text, /Last error: <script>x<\/script>/);
  assert.match(text, /Latest output · Run of 10\/31, 07:00 AM · 2 saved/);
  assert.doesNotMatch(text, /Latest output · 2026-10-31_07-00-04\.md/);
  assert.match(text, /Headline \[REDACTED\] <script>y<\/script>/);
  assert.match(text, /Excerpt shortened/);
  assert.match(text, /Excerpt omitted/);
  assert.match(text, /Timeout <img>/);
  assert.match(text, /2 of 5/);
  assert.match(text, /Paused/);
  assert.ok(!a.tags.includes('script') && !a.tags.includes('img') && !a.tags.includes('b'));
  const cards = walk(a.node('content')).filter(n => n.tag === 'article');
  assert.ok(walk(cards[1]).some(n => n.tag === 'button' && n.textContent === 'Resume'), 'resume sits with its job');
  assert.ok(!walk(cards[0]).some(n => n.tag === 'button' && n.textContent === 'Resume'));
});

test('config uses ConnectionsAdmin rows: labels, help, source and locked settings never show Undefined', () => {
  const a = app(); a.state.view = 'config';
  a.state.data = { items: [
    { id: 'timezone', label: 'Timezone', value: 'Asia/Ho_Chi_Minh', editable: true, source: 'config.yaml', help: 'IANA timezone for schedules.' },
    { id: 'agent.max_turns', label: 'Max turns', value: null, editable: true, source: 'Hermes default', help: 'Tool-calling turns.' },
    { id: 'memory', label: 'Memory providers', value: 'holographic', editable: false, source: 'config.yaml', help: 'Locked for isolation.' }],
    actions: [{ id: 'save_setting', label: 'Change timezone', revision: 'c1', target: 'timezone', fields: [{ name: 'setting', type: 'hidden', value: 'timezone' }, { name: 'value', label: 'Timezone', type: 'text', value: 'Asia/Ho_Chi_Minh' }] }] };
  a.render();
  const text = a.node('content').textContent;
  assert.doesNotMatch(text, /Undefined/);
  for (const expected of ['Timezone', 'IANA timezone for schedules.', 'config.yaml', 'Hermes default', 'Max turns', 'Unavailable', 'Editable settings', 'Locked settings', 'Memory providers', 'Locked']) assert.ok(text.includes(expected), expected);
  const rows = walk(a.node('content')).filter(n => n.tag === 'tr');
  const tz = rows.find(r => r.textContent.includes('IANA'));
  assert.ok(walk(tz).some(n => n.tag === 'button' && n.textContent === 'Change timezone'));
  const locked = rows.find(r => r.textContent.includes('Memory providers'));
  assert.ok(!walk(locked).some(n => n.tag === 'button'));
});

test('channels, keys, MCP and models show ConnectionsAdmin fields without inventing connectivity', () => {
  const a = app(); a.state.view = 'channels';
  a.state.data = { items: [
    { id: 'telegram', channel: 'Telegram', enabled: true, mode: 'auto', credential: 'TELEGRAM_BOT_TOKEN', credential_configured: true, credential_source: 'bot home .env', reply_to_mode: 'first', typing_indicator: false, connection: 'not verified', editable: true },
    { id: 'slack', channel: 'Slack', enabled: false, mode: 'off', credential_configured: false, connection: 'not verified', editable: false, note: 'Read-only here.' }] };
  a.render();
  let text = a.node('content').textContent;
  for (const expected of ['Automatic (when a token exists)', 'Configured · bot home .env', 'First message only', 'Typing indicatorOff', 'Not verified', 'Read-only here.']) assert.ok(text.includes(expected), expected);
  a.state.view = 'keys';
  a.state.data = { items: [
    { id: 'OPENROUTER_API_KEY', variable: 'OPENROUTER_API_KEY', provider: 'OpenRouter', configured: true, source: 'bot home .env', editable: true, notice: 'Process environment overrides this file.' },
    { id: 'ZALO_TOKEN', variable: 'ZALO_TOKEN', provider: 'Zalo', configured: false, source: null, editable: false, note: 'Managed in the repository .env by the Zalo setup.' }] };
  a.render(); text = a.node('content').textContent;
  for (const expected of ['Process environment overrides this file.', 'Managed in the repository .env', 'Managed elsewhere', 'Not set']) assert.ok(text.includes(expected), expected);
  a.state.view = 'mcp';
  a.state.data = { items: [{ id: 'docs', name: 'docs', transport: 'http', host: 'mcp.example.com', enabled: true, credentials_configured: false, exposed_to: ['cli', 'telegram'], connection: 'not tested' }] };
  a.render(); text = a.node('content').textContent;
  for (const expected of ['mcp.example.com', 'cli, telegram', 'Not tested']) assert.ok(text.includes(expected), expected);
  a.state.view = 'models';
  a.state.data = { items: [{ id: 'main', role: 'Main', model: 'm', provider: 'custom', base_url: 'https://llm.example.com', base_url_configured: true, credential_in_config: true },
    { id: 'vision', role: 'Vision', model: null, provider: 'auto', uses_main: true }, { id: 'compression', role: 'Compression', model: null, provider: 'openrouter', uses_main: false }] };
  a.render(); text = a.node('content').textContent;
  for (const expected of ['Endpoint: https://llm.example.com', 'Key stored in config.yaml', 'Uses main model', 'Provider default']) assert.ok(text.includes(expected), expected);
});

test('log filters offer only the server allowlist, send only on Apply and keep unapplied edits across re-renders', async () => {
  const a = app(); a.state.view = 'logs';
  a.state.data = { items: [{ file: 'gateway.log', level: 'INFO', line: 'ok' }], files: ['../etc/passwd'] };
  a.render();
  const options = id => walk(a.node(id)).filter(n => n.tag === 'option').map(n => n.value);
  assert.deepEqual(options('log-file'), ['', 'agent', 'gateway', 'errors', 'hermes'], 'snapshot data cannot add file choices');
  assert.deepEqual(options('log-component'), ['', 'gateway', 'agent', 'tools', 'cli', 'cron']);
  assert.deepEqual(options('log-lines'), ['', '50', '100', '200', '500']);
  assert.deepEqual(options('log-level'), ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']);
  // Edit without applying, then re-render (as page search does): the draft survives and nothing is fetched.
  a.node('log-q').value = '  timeout '; a.node('log-q').listeners.input();
  a.node('log-component').value = 'cron'; a.node('log-component').listeners.change();
  a.render();
  assert.equal(a.node('log-q').value, '  timeout ');
  assert.equal(a.node('log-component').value, 'cron');
  assert.equal(a.calls.length, 0, 'no request until Apply; no auto-refresh');
  a.node('log-level').value = 'ERROR'; a.node('log-file').value = 'gateway'; a.node('log-lines').value = '500';
  walk(a.node('content')).find(n => n.tag === 'form').listeners.submit({ preventDefault() {} }); await tick();
  assert.equal(a.calls[0].url, '/api/assistant/logs?q=timeout&level=ERROR&file=gateway&component=cron&lines=500');
  assert.equal(a.state.logDraft, null);
  a.node('log-file').value = '../etc/passwd'; a.node('log-component').value = 'shell'; a.node('log-level').value = 'TRACE'; a.node('log-lines').value = '5000';
  walk(a.node('content')).find(n => n.tag === 'form').listeners.submit({ preventDefault() {} }); await tick();
  assert.equal(a.calls[1].url, '/api/assistant/logs?q=timeout', 'values outside the allowlist are dropped, not sent');
  a.state.logDraft = { q: 'pending' }; a.showLogin();
  assert.deepEqual({ ...a.state.logFilter }, {});
  assert.equal(a.state.logDraft, null);
});

test('descriptor actions render for any assistant section; name targets attach to metadata rows', async () => {
  const a = app((url, options) => response(options.method === 'POST' ? { ok: true, message: 'Plugin disabled.' } : { items: [] }));
  a.state.view = 'plugins';
  a.state.data = { items: [{ name: 'web-tools', version: '1.0' }], actions: [
    { id: 'toggle', label: 'Disable plugin', revision: 'p1', target: 'web-tools', fields: [{ name: 'name', type: 'hidden', value: 'web-tools' }] },
    { id: 'toggle', label: 'Disable gone', revision: 'p1', target: 'removed-plugin', fields: [] }] };
  a.render();
  const card = walk(a.node('content')).find(n => n.tag === 'article');
  assert.ok(walk(card).some(n => n.tag === 'button' && n.textContent === 'Disable plugin'), 'row action placed in its card');
  assert.ok(buttons(a).some(b => b.textContent === 'Disable gone · removed-plugin'), 'unmatched target is still reachable and labelled');
  press(a, 'Disable plugin');
  const pending = a.submitAction({ preventDefault() {} }); accept(a); await pending;
  assert.equal(a.calls[0].url, '/api/assistant/plugins/action');
  assert.deepEqual(JSON.parse(a.calls[0].options.body).values, { name: 'web-tools' });
  for (const view of ['webhooks']) {
    const b = app(); b.state.view = view; b.state.data = { items: [{ name: 'local' }], actions: [{ id: 'x', label: 'Generic ' + view, revision: 'r', target: 'local', fields: [] }] }; b.render();
    assert.ok(buttons(b).some(n => n.textContent === 'Generic ' + view), view);
  }
  for (const view of ['memories', 'access', 'usage', 'runtime', 'users', 'overview', 'documentation', 'logs', 'chat', 'files']) {
    const b = app(); b.state.view = view; b.state.data = { items: [], actions: [{ id: 'x', label: 'Should not render', revision: 'r', fields: [] }] }; b.render();
    b.openAction(b.state.data.actions[0]);
    assert.equal(b.node('action-dialog').open, false, view);
    assert.ok(!buttons(b).some(n => n.textContent === 'Should not render'), view + ' has no /api/assistant action route');
  }
});

test('keys never display secret values and channels show unverified connections', () => {
  const a = app(); a.state.view = 'keys';
  a.state.data = { items: [{ provider: 'OpenRouter', variable: 'OPENROUTER_API_KEY', configured: true, value: 'sk-should-not-render' }] };
  a.render();
  assert.match(a.node('content').textContent, /OPENROUTER_API_KEY/);
  assert.doesNotMatch(a.node('content').textContent, /sk-should-not-render/);
  a.state.view = 'channels'; a.state.data = { items: [{ channel: 'telegram', enabled: true, credential_configured: true }] }; a.render();
  assert.match(a.node('content').textContent, /Not verified/);
});

test('skill category actions appear only for the selected category, never as an unfiltered pile', () => {
  const a = app(); a.state.view = 'skills';
  a.state.data = { items: [
    { id: 'news:briefing', name: 'briefing', category: 'news', enabled: true },
    { id: 'core:hermes-agent', name: 'hermes-agent', category: 'core', enabled: true, essential: true }],
    categories: [{ id: 'category:news', name: 'news', total: 1, enabled: 1 }, { id: 'category:core', name: 'core', total: 1, enabled: 1 }],
    actions: [{ id: 'disable_category', label: 'Disable category', revision: 's1', target: 'category:news', fields: [{ name: 'category', type: 'hidden', value: 'news' }] }] };
  a.render();
  assert.ok(!buttons(a).some(b => b.textContent.startsWith('Disable category')), 'hidden until a category is chosen');
  assert.match(a.node('content').textContent, /Essential/);
  press(a, 'News · 1');
  assert.ok(buttons(a).some(b => b.textContent === 'Disable category'));
  assert.match(a.node('content').textContent, /News category · 1 of 1 enabled/);
  press(a, 'Core · 1');
  assert.ok(!buttons(a).some(b => b.textContent.startsWith('Disable category')));
});

// ---- Information architecture, notes and command palette -------------------
const tree = (root, found = []) => { found.push(root); for (const child of root.children || []) tree(child, found); return found; };
const htmlSource = () => readFile(new URL('../../../src/hermes_zalo/admin_static/console.html', import.meta.url), 'utf8');

test('sidebar shows a short Essentials set and keeps every other page under a collapsed Advanced area', async () => {
  const html = await htmlSource();
  const essentials = html.slice(html.indexOf('data-nav-group="main"'), html.indexOf('id="advanced-toggle"'));
  const advanced = html.slice(html.indexOf('id="advanced-nav"'), html.indexOf('</nav>'));
  assert.deepEqual([...essentials.matchAll(/data-view="([a-z-]+)"/g)].map(m => m[1]), ['overview', 'chat', 'routines', 'channels', 'memories']);
  assert.match(essentials, />Automations</);
  assert.doesNotMatch(html, />Schedules</);
  for (const view of ['models', 'skills', 'mcp', 'sessions', 'system-overview', 'users', 'usage', 'keys', 'config', 'logs', 'files', 'plugins', 'webhooks', 'access', 'documentation'])
    assert.match(advanced, new RegExp('data-view="' + view + '"'), view + ' lives under Advanced');
  assert.match(html, /id="advanced-nav" class="nav-advanced" hidden/);
  assert.match(html, /<span>Advanced<\/span>/);
  assert.match(html, /<link rel="stylesheet" href="\/static\/console-briefing.css">/);
  assert.match(html, /<script src="\/static\/console-briefing.js" defer><\/script>/);
  assert.doesNotMatch(html, /\sstyle=/, 'CSP forbids inline styles');
  const a = app();
  for (const view of ['overview', 'chat', 'reports', 'routines', 'connectors', 'channels', 'memories']) assert.equal(a.essential(view), true, view);
  for (const view of ['models', 'sessions', 'usage', 'logs', 'system-overview']) assert.equal(a.essential(view), false, view);
  a.register('opt-in', { title: 'Opt in', group: 'observe', essential: true, render() {} });
  a.register('research', { title: 'Research', group: 'observe', render() {} });
  assert.equal(a.essential('opt-in'), true);
  assert.equal(a.essential('research'), false);
});

test('old and friendly hashes resolve; opening an Advanced page expands the area without saving the choice', async () => {
  const stored = [];
  const a = app(async () => response({ items: [] }), { localStorage: { getItem() { throw new Error('blocked'); }, setItem(k, v) { stored.push([k, v]); } } });
  assert.equal(a.resolveView('automations'), 'routines');
  assert.equal(a.resolveView('schedules'), 'routines');
  assert.equal(a.resolveView('routines'), 'routines');
  assert.equal(a.resolveView('overview'), 'overview', 'without the briefing page Home stays the overview');
  a.navigate('models');
  assert.equal(a.state.view, 'models');
  assert.equal(a.node('advanced-nav').hidden, false);
  assert.equal(a.node('advanced-toggle').attributes['aria-expanded'], 'true');
  assert.deepEqual(stored, [], 'auto-expanding never overwrites the remembered preference');
  a.node('advanced-toggle').listeners.click();
  assert.deepEqual(stored, [['hermes-console-advanced', 'closed']]);
  a.navigate('automations');
  assert.equal(a.state.view, 'routines');
  assert.equal(a.views.routines[0], 'Automations');
});

test('blocked storage never breaks the Advanced toggle', () => {
  const a = app(async () => response({ items: [] }), { localStorage: { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } } });
  assert.doesNotThrow(() => a.node('advanced-toggle').listeners.click());
  assert.equal(a.node('advanced-nav').hidden, false);
});

test('the briefing page becomes Home; the old overview stays reachable as System overview', async () => {
  const a = app(async () => response({ items: [], counts: { sessions: 3 } }));
  a.state.view = 'overview';
  a.register('briefing', { title: 'Briefing', group: 'main', essential: true, icon: 'home', render(data, root, kit) { root.append(kit.node('p', '', 'CEO briefing')); } });
  assert.equal(a.node('nav-home').dataset.view, 'briefing', 'the Home button now opens the briefing');
  assert.equal(a.resolveView('overview'), 'briefing');
  assert.equal(a.resolveView('home'), 'briefing');
  a.navigate('overview');
  assert.equal(a.state.view, 'briefing');
  assert.equal(a.calls.at(-1).url, '/api/assistant/briefing');
  a.navigate('system-overview');
  assert.equal(a.state.view, 'system-overview');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.calls.at(-1).url, '/api/assistant/overview', 'System overview reads the existing overview API');
  assert.match(a.node('content').textContent, /Hermes assistant/);
  assert.equal(a.views['system-overview'][0], 'System overview');
  a.state.user.role = 'admin';
  assert.equal(a.allowed('system-overview'), false, 'System overview keeps the owner-only rule');
});

test('kit.note renders a one-line notice with an accessible Details disclosure and text-only content', () => {
  const a = app(), hostile = '<img src=x onerror=alert(1)>';
  const n = a.note('Short title', 'Long explanation ' + hostile, 'warning');
  assert.equal(n.className, 'note warning');
  assert.equal(n.attributes.role, 'note');
  const toggle = tree(n).find(x => x.className === 'note-toggle'), text = tree(n).find(x => x.className === 'note-text');
  assert.equal(text.hidden, true);
  assert.equal(toggle.attributes['aria-expanded'], 'false');
  assert.equal(toggle.attributes['aria-controls'], text.id);
  toggle.listeners.click();
  assert.equal(text.hidden, false);
  assert.equal(toggle.attributes['aria-expanded'], 'true');
  assert.ok(n.textContent.includes(hostile));
  assert.ok(!a.tags.includes('img'));
  assert.equal(a.note('Failed', '', 'error').attributes.role, 'alert');
  assert.equal(tree(a.note('Only a title')).some(x => x.className === 'note-toggle'), false, 'no disclosure without details');
  assert.equal(a.note('x', 'y', 'bogus').className, 'note info');
  const split = a.noteFrom('Reports are saved jobs. They run while the gateway is running.');
  assert.equal(tree(split).find(x => x.className === 'note-title').textContent, 'Reports are saved jobs.');
  assert.equal(tree(split).find(x => x.className === 'note-text').textContent, 'They run while the gateway is running.');
});

test('built-in pages use compact notes instead of paragraph disclaimers', () => {
  const a = app(); a.state.view = 'keys';
  a.state.data = { items: [], message: 'Credentials are read from .env. Changes apply after a restart of the gateway process.' };
  a.render();
  const notes = tree(a.node('content')).filter(x => String(x.className).startsWith('note '));
  assert.ok(notes.length >= 1);
  assert.equal(tree(a.node('content')).filter(x => x.className === 'notice').length, 0);
  a.state.view = 'memories'; a.state.data = { total: 0, items: [] }; a.render();
  assert.match(tree(a.node('content')).find(x => x.className === 'note-title').textContent, /may not be in use/);
});

test('command palette lists pages and quick actions, ranks matches, wraps with arrows and runs with Enter', () => {
  const a = app();
  a.register('reports', { title: 'Reports', group: 'integrations', essential: true, icon: 'file', render() {},
    commands: [{ label: 'Open latest report', keywords: 'read', run(kit) { kit.navigate('reports'); } }, { label: 42, run() {} }, { label: 'No run' }] });
  a.state.view = 'models'; a.state.data = { items: [], actions: [{ id: 'set_main', label: 'Change main model', revision: 'r1', fields: [] }] };
  a.openPalette();
  assert.equal(a.node('palette').open, true);
  const labels = () => [...a.state.palette.items.map(i => i.label)];
  for (const expected of ['Open chat', 'New report', 'New automation', 'Add memory', 'Open latest report', 'Change main model', 'Home', 'Automations', 'Reports', 'Models', 'Change password'])
    assert.ok(labels().includes(expected), expected);
  assert.ok(!labels().includes('System'), 'the legacy runtime view is not listed twice');
  assert.ok(!labels().includes('System overview'), 'System overview only appears once the briefing replaces Home');
  assert.equal(labels().filter(l => l === 42).length, 0, 'invalid commands are ignored');
  const sections = tree(a.node('palette-list')).filter(x => x.className === 'palette-section').map(x => x.textContent);
  assert.deepEqual(sections, ['Quick actions', 'On this page', 'Pages', 'Advanced', 'Sidebar', 'Preferences']);
  a.node('palette-input').value = 'auto'; a.node('palette-input').listeners.input();
  assert.equal(labels()[0], 'Automations', 'a label prefix match ranks first');
  assert.equal(labels().at(-1), 'Ask Hermes: “auto”');
  assert.equal(a.node('palette-input').attributes['aria-activedescendant'], 'palette-opt-0');
  const keys = a.node('palette-input').listeners.keydown, press = key => keys({ key, preventDefault() {} });
  press('ArrowUp');
  assert.equal(a.state.palette.index, a.state.palette.items.length - 1, 'ArrowUp wraps to the last result');
  press('ArrowDown');
  assert.equal(a.state.palette.index, 0);
  assert.equal(a.node('palette-input').attributes['aria-activedescendant'], 'palette-opt-0');
  press('Enter');
  assert.equal(a.node('palette').open, false);
  assert.equal(a.state.view, 'routines');
  a.openPalette(); a.node('palette-input').value = 'zzzz-nothing'; a.node('palette-input').listeners.input();
  assert.deepEqual(labels(), ['Ask Hermes: “zzzz-nothing”']);
});

test('"Ask Hermes" in the palette prefills chat but never sends a message', () => {
  const a = app(); a.state.view = 'models';
  a.openPalette();
  a.node('palette-input').value = 'Summarize <b>today</b>'; a.node('palette-input').listeners.input();
  const index = a.state.palette.items.findIndex(i => i.label.startsWith('Ask Hermes'));
  a.runPaletteItem(index);
  assert.equal(a.state.view, 'chat');
  assert.equal(a.state.chatDraft, 'Summarize <b>today</b>');
  assert.ok(a.calls.every(c => c.options.method === 'GET'), 'no chat POST was made');
  a.openChat('x'.repeat(5000));
  assert.equal(a.state.chatDraft.length, 4000);
});

test('"New automation" opens the create dialog only once its page loads, and only for one matching action', async () => {
  const create = { id: 'create_routine', label: 'New scheduled job', revision: 'r', fields: [{ name: 'name', type: 'text', label: 'Name' }] };
  const a = app(async () => response({ items: [], actions: [create, { id: 'pause_routine', label: 'Pause', revision: 'r', target: 'job-1', fields: [] }] }));
  a.state.view = 'chat';
  a.openPalette();
  a.runPaletteItem(a.state.palette.items.findIndex(i => i.label === 'New automation'));
  assert.equal(a.state.view, 'routines');
  assert.equal(a.node('action-dialog').open, false, 'nothing opens before the snapshot arrives');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.node('action-dialog').open, true);
  assert.equal(a.node('action-title').textContent, 'New scheduled job');
  assert.equal(a.state.pendingAction, null);
  a.showLogin();
  assert.equal(a.state.pendingAction, null);
});

test('admins without owner access only see the pages they may open in the palette', () => {
  const a = app(); a.state.user.role = 'admin'; a.state.user.permissions = ['conversations'];
  a.openPalette();
  const labels = a.state.palette.items.map(i => i.label);
  assert.ok(labels.includes('Conversations'));
  for (const hidden of ['Open chat', 'New automation', 'Models', 'API keys', 'Automations']) assert.ok(!labels.includes(hidden), hidden);
});

test('every icon used by notes and the palette exists in the sprite', async () => {
  const html = await htmlSource(), js = await readFile(new URL('../../../src/hermes_zalo/admin_static/console.js', import.meta.url), 'utf8');
  const sprite = new Set([...html.matchAll(/<symbol id="i-([a-z]+)"/g)].map(m => m[1]));
  const noteIcons = /const NOTE_ICON = \{([^}]+)\}/.exec(js)[1], pageIcons = /const PAGE_ICONS = \{([^}]+)\}/.exec(js)[1];
  for (const name of [...noteIcons.matchAll(/"([a-z]+)"/g), ...pageIcons.matchAll(/: "([a-z]+)"/g)].map(m => m[1]).filter(n => !['info', 'warning', 'error', 'good'].includes(n) || noteIcons.includes(': "' + n + '"')))
    assert.ok(sprite.has(name), 'missing sprite symbol i-' + name);
  assert.ok(sprite.has('info'));
});

// ---- Round 2: human time, sidebar customization, motion, toasts -------------
test('describeSchedule turns cron and Hermes intervals into plain English and falls back to the raw text', () => {
  const a = app(), d = a.describeSchedule;
  const cases = {
    '0 8 * * *': 'Every day at 08:00', '0 8 * * 1-5': 'Weekdays at 08:00', '30 7 * * 1': 'Every Monday at 07:30',
    '0 9 * * 1,3,5': 'Every Monday, Wednesday and Friday at 09:00', '0 10 * * 0,6': 'Weekends at 10:00', '0 9 * * 7': 'Every Sunday at 09:00',
    '0 8,17 * * *': 'Every day at 08:00 and 17:00', '0 6 1 * *': 'Monthly on the 1st at 06:00', '0 6 1,15 * *': 'Monthly on the 1st and 15th at 06:00',
    '*/15 * * * *': 'Every 15 minutes', '0 * * * *': 'Every hour', '15 * * * *': 'Every hour at 15 past', '0 */2 * * *': 'Every 2 hours',
    '0 */2 * * 1-5': 'Weekdays, every 2 hours', '0 8 * 1 *': 'Every day in January at 08:00', '@daily': 'Every day at 00:00',
    'every 30m': 'Every 30 minutes', 'every 10080m': 'Every week', 'every 2h': 'Every 2 hours', 'every 1d': 'Every day',
  };
  for (const [cron, text] of Object.entries(cases)) assert.equal(d(cron), text, cron);
  for (const raw of ['0 8 * * MON', '61 8 * * *', '0 8 1 * 1', 'not a schedule', '0 25 * * *', '<b>x</b>']) assert.equal(d(raw), raw, raw);
  assert.equal(d(''), '—'); assert.equal(d(null), '—');
});

test('formatRelative speaks in short relative times and never invents a value', () => {
  const a = app(), now = Date.parse('2026-11-01T08:00:00Z'), f = v => a.formatRelative(v, now);
  assert.equal(f('2026-11-01T08:05:00Z'), 'in 5 min');
  assert.equal(f('2026-11-01T06:00:00Z'), '2 h ago');
  assert.equal(f('2026-11-01T07:59:40Z'), 'just now');
  assert.equal(f('2026-11-01T08:00:30Z'), 'in under a minute');
  assert.equal(f('2026-11-02T08:00:00Z'), 'tomorrow');
  assert.equal(f('2026-10-31T08:00:00Z'), 'yesterday');
  assert.equal(f('2026-10-29T08:00:00Z'), '3 days ago');
  assert.equal(f(now / 1000 + 600), 'in 10 min', 'epoch seconds');
  assert.equal(f(now - 120000), '2 min ago', 'epoch milliseconds');
  assert.equal(f('2026-09-01T08:00:00Z'), a.formatRelative('2026-09-01T08:00:00Z', now));
  assert.notEqual(f('2026-09-01T08:00:00Z'), '—', 'older dates show the date');
  for (const bad of [null, undefined, '', 'soon']) assert.equal(f(bad), '—');
});

test('automation cards show plain schedules, relative times and delivery in words', () => {
  const a = app(); a.state.view = 'routines';
  a.state.data = { total: 1, items: [{ id: 'j', name: 'Daily social', schedule: '0 8 * * 1-5', enabled: true, state: 'scheduled',
    next_run_at: new Date(Date.now() + 3 * 3600e3).toISOString(), last_run_at: new Date(Date.now() - 20 * 60e3).toISOString(), last_status: 'ok', deliver: 'telegram:CEO chat' }] };
  a.render();
  const text = a.node('content').textContent;
  for (const expected of ['Weekdays at 08:00', 'in 3 h', '20 min ago', 'Succeeded', 'Telegram · CEO chat']) assert.ok(text.includes(expected), expected);
  assert.ok(!text.includes('0 8 * * 1-5'));
});

test('the viewer can pin and unpin pages; choices persist, survive blocked storage and reset to default', () => {
  const store = new Map();
  const localStorage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  const a = app(async () => response({ items: [] }), { localStorage });
  assert.equal(a.essential('usage'), false);
  a.setPinned('usage', true);
  assert.equal(a.essential('usage'), true);
  a.setPinned('channels', false);
  assert.equal(a.essential('channels'), false);
  a.setPinned('overview', false);
  assert.equal(a.essential('overview'), true, 'Home cannot be removed');
  assert.deepEqual(JSON.parse(store.get('hermes-console-sidebar:owner')), { add: ['usage'], remove: ['channels'] });
  const b = app(async () => response({ items: [] }), { localStorage });
  b.showApp({ user: b.state.user });
  assert.equal(b.essential('usage'), true, 'pins are remembered for this viewer');
  assert.equal(b.essential('channels'), false);
  b.state.view = 'usage'; b.openPalette();
  const labels = [...b.state.palette.items.map(i => i.label)];
  assert.ok(labels.includes('Remove Usage & cost from the sidebar'));
  assert.ok(labels.includes('Reset sidebar to default'));
  b.runPaletteItem(labels.indexOf('Reset sidebar to default'));
  assert.equal(b.essential('usage'), false);
  assert.equal(b.essential('channels'), true);
  b.state.view = 'logs'; b.openPalette();
  assert.ok([...b.state.palette.items.map(i => i.label)].includes('Pin Logs to the sidebar'));
  store.set('hermes-console-sidebar:owner', '{"add":["<img>", 5, "logs"],"remove":"bad"}');
  const c = app(async () => response({ items: [] }), { localStorage });
  c.showApp({ user: c.state.user });
  assert.equal(c.essential('logs'), true, 'only valid page names are read back');
  const blocked = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  const d = app(async () => response({ items: [] }), { localStorage: blocked });
  assert.doesNotThrow(() => d.setPinned('logs', true));
  assert.equal(d.essential('logs'), true, 'still applies for this page view');
});

test('live session labels describe the data source without claiming gateway connectivity', async () => {
  for (const runtime_history of [true, false]) {
    const a = app();
    a.showApp({ user: a.state.user, mode: 'standalone', runtime_history });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(a.node('environment').textContent, 'Live data');
    assert.equal(a.node('workspace-mode').textContent, runtime_history ? 'Hermes workspace' : 'Personal assistant');
    assert.equal(a.state.mode, 'standalone', 'session architecture is unchanged');
    a.state.view = 'overview'; a.state.data = { available: true, counts: {}, usage: {} }; a.render();
    assert.ok(a.node('content').textContent.includes('Live data'));
    assert.ok(!/Standalone|Connected/.test(a.node('content').textContent));
  }
  const demo = app(); demo.showApp({ user: demo.state.user, mode: 'demo', runtime_history: false });
  assert.equal(demo.node('environment').textContent, 'Demo mode');
  assert.equal(demo.node('workspace-mode').textContent, 'Demo workspace');
});

test('sidebar pins belong to the signed-in viewer and are cleared on sign-out', () => {
  const store = new Map(), localStorage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  const a = app(async () => response({ items: [] }), { localStorage });
  a.showApp({ user: a.state.user }); a.setPinned('logs', true);
  assert.equal(a.essential('logs'), true);
  a.showLogin();
  assert.equal(a.essential('logs'), false);
  a.showApp({ user: { username: 'second-owner', role: 'faculty', permissions: [] } });
  assert.equal(a.essential('logs'), false, 'another account does not inherit pins');
  a.setPinned('usage', true);
  a.showApp({ user: { username: 'owner', role: 'faculty', permissions: [] } });
  assert.equal(a.essential('logs'), true);
  assert.equal(a.essential('usage'), false, 'returning account restores its own choices');
});

test('the Customize dialog lists allowed pages as checkboxes that pin and unpin', () => {
  const a = app();
  a.openCustomize();
  assert.equal(a.node('customize-dialog').open, true);
  const boxes = tree(a.node('customize-list')).filter(n => n.tag === 'input');
  const byPage = Object.fromEntries(boxes.map(b => [b.dataset.page, b]));
  assert.equal(byPage.overview, undefined, 'Home is not optional');
  assert.equal(byPage.chat.checked, true);
  assert.equal(byPage.models.checked, false);
  assert.equal(a.node('customize-reset').disabled, true);
  byPage.models.checked = true; byPage.models.listeners.change();
  assert.equal(a.essential('models'), true);
  assert.equal(a.node('customize-reset').disabled, false);
  a.node('customize-reset').listeners.click();
  assert.equal(a.essential('models'), false);
  a.state.user.role = 'admin'; a.state.user.permissions = ['conversations'];
  a.openCustomize();
  assert.deepEqual([...tree(a.node('customize-list')).filter(n => n.tag === 'input').map(b => b.dataset.page)], ['sessions']);
});

test('page-enter motion runs after a view change only, not on filtering or polling', async () => {
  const a = app(async () => response({ items: [{ name: 'alpha' }] }));
  a.navigate('plugins');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.node('content').classList.contains('page-enter'), true);
  a.render();
  assert.equal(a.node('content').classList.contains('page-enter'), false, 'a filter re-render does not replay the entrance');
  await a.load({ quiet: true });
  assert.equal(a.node('content').classList.contains('page-enter'), false, 'polling does not replay it');
  a.navigate('plugins');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.node('content').classList.contains('page-enter'), false, 'selecting the current page does not replay it');
});

test('toasts take a tone; errors are announced as alerts', () => {
  const a = app();
  a.toast('Saved.');
  assert.equal(a.node('toast').className, 'toast');
  a.toast('Could not save.', 'error');
  assert.equal(a.node('toast').className, 'toast error');
  assert.equal(a.node('toast').attributes.role, 'alert');
  a.toast('Heads up', 'bogus');
  assert.equal(a.node('toast').className, 'toast');
});

test('the briefing is called Home in the heading and the palette', () => {
  const a = app();
  a.register('briefing', { title: 'Briefing', description: 'What needs you.', essential: true, render() {} });
  assert.deepEqual([...a.heading('briefing')], ['Home', 'Your briefing · What needs you.']);
  a.navigate('briefing');
  assert.equal(a.node('page-title').textContent, 'Home');
  a.openPalette();
  const home = a.state.palette.items.find(i => i.label === 'Home');
  assert.equal(home.hint, 'Your briefing');
  assert.equal(a.state.palette.items.filter(i => i.label === 'Briefing').length, 0);
});

test('sidebar markup offers a Customize control and an accessible dialog', async () => {
  const html = await htmlSource();
  assert.match(html, /id="customize-open"[^>]*aria-controls="customize-dialog"/);
  assert.match(html, /<dialog id="customize-dialog"[^>]*aria-labelledby="customize-title"/);
  assert.match(html, /id="customize-reset"/);
  assert.match(html, /<symbol id="i-pin"/);
});
