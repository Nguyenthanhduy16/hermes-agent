import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const read = path => readFile(new URL('../../../src/hermes_zalo/admin_static/' + path, import.meta.url), 'utf8');
const consoleSource = await read('console.js');
const benchSource = await read('console-bench.js');
const boot = consoleSource.lastIndexOf('  api("/api/session").then');
assert.ok(boot > 0);

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; this.hidden = false; this.disabled = false;
    this.open = false; this.dataset = {}; this.attributes = {}; this.style = {}; this._text = ''; this.className = '';
    const classes = new Set(); this.classes = classes;
    this.classList = { add: c => classes.add(c), remove: c => classes.delete(c), toggle() {}, contains: c => classes.has(c) };
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
  click() { this.listeners.click?.({ preventDefault() {} }); }
  focus() {} reset() {} close() { this.open = false; } showModal() { this.open = true; }
}

function all(root, predicate, found = []) {
  if (predicate(root)) found.push(root);
  for (const child of root.children || []) all(child, predicate, found);
  return found;
}
const byTag = (root, tag) => all(root, n => n.tag === tag);
const buttonNamed = (root, text) => byTag(root, 'button').find(b => b.textContent === text);
const response = data => ({ ok: true, status: 200, json: async () => data });

function app(fetchImpl = async () => response({ section: 'x', available: true, items: [] })) {
  const nodes = new Map(), calls = [], tags = [], timers = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const document = {
    getElementById: get,
    createElement(tag) { tags.push(tag); return new Element(tag); },
    createElementNS(_, tag) { return new Element(tag); },
    createTextNode(text) { const n = new Element('text'); n.textContent = text; return n; },
    createDocumentFragment() { return new Element('fragment'); },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const window = { addEventListener() {} };
  const context = vm.createContext({ document, window, Node: Element, URL, URLSearchParams, AbortController, Intl, Date,
    location: { hash: '' }, history: { replaceState() {} }, setTimeout: (fn, ms) => { timers.push(ms); return timers.length; }, clearTimeout() {},
    fetch: async (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); } });
  vm.runInContext(consoleSource.slice(0, boot) + 'globalThis.consoleTest={state,views,load,render,openAction};})();', context);
  vm.runInContext(benchSource, context);
  const api = context.consoleTest;
  api.state.user = { username: 'owner', role: 'faculty', permissions: [] };
  api.state.csrf = 'synthetic-csrf';
  const show = (view, data) => { api.state.view = view; api.state.data = data; api.render(); return get('content'); };
  return { ...api, show, node: get, calls, tags, timers };
}

const hostile = '<img src=x onerror=alert(1)>';
const run = (over = {}) => ({ id: '20261031-090000-aaaaaa', suite: 'assistant-basics', label: 'B1', status: 'done', total: 28, completed: 28,
  passed: 20, errors: 1, pass_rate: 20 / 28, progress: 1, config_hash: 'a1f0c3d2e4b5a6c7', target_host: '127.0.0.1:8642', started_at: 1793000000, ...over });
const benchData = (over = {}) => ({ section: 'benchmarks', available: true, configured: true, target: { host: '127.0.0.1:8642', model: 'hermes' },
  setup: [], suites: [{ name: 'assistant-basics', description: 'Everyday', source: 'bundled', tasks: 14, levels: { L1: 5, L2: 5, L3: 4 } }],
  suite_problems: [], items: [run(), run({ id: '20261031-100000-bbbbbb', label: 'B2', pass_rate: 0.8 })], active: null, run: null, compare: null,
  actions: [{ id: 'start_run', label: 'Start benchmark run', revision: 'r1', description: 'Sends real model requests. This costs money.',
    fields: [{ name: 'suite', label: 'Suite', type: 'select', options: [{ value: 'assistant-basics', label: 'assistant-basics · 14 tasks' }] },
      { name: 'label', label: 'Label', type: 'text', max_length: 60 }, { name: 'repeats', label: 'Repeats', type: 'number', value: 1 },
      { name: 'concurrency', label: 'Parallel requests', type: 'select', value: 1, options: [1, 2, 3, 4].map(n => ({ value: n, label: String(n) })) }] },
    { id: 'delete_run', label: 'Delete', target: '20261031-090000-aaaaaa', revision: '20261031-090000-aaaaaa:done', fields: [{ name: 'run_id', type: 'hidden', value: '20261031-090000-aaaaaa' }] }],
  ...over });

test('bench pages register in the observe group', () => {
  const a = app();
  assert.equal(a.views.experiments[0], 'Experiments');
  assert.equal(a.views.benchmarks[0], 'Benchmarks');
});

test('untrusted run, suite and reply strings render as text only', () => {
  const a = app();
  const content = a.show('benchmarks', benchData({
    items: [run({ label: hostile, suite: hostile })],
    suites: [{ name: hostile, description: hostile, source: 'home', tasks: 1, levels: { L1: 1 } }],
    suite_problems: [{ file: hostile, error: hostile }],
    run: { ...run({ label: hostile }), summary: { pass_rate: 0.5, levels: {} }, settings: [{ key: hostile, value: hostile }],
      results: [{ task_id: hostile, attempt: 1, level: 'L1', category: hostile, passed: false, latency_s: 1.2, total_tokens: 10,
        error: hostile, checks: [{ type: 'contains', passed: false, detail: hostile }], response_excerpt: hostile }] } }));
  assert.ok(content.textContent.includes(hostile));
  assert.ok(!a.tags.includes('img') && !a.tags.includes('script'));
});

test('start action is offered with a cost warning and opens a descriptor dialog', () => {
  const a = app();
  const content = a.show('benchmarks', benchData());
  const start = buttonNamed(content, 'Start benchmark run');
  assert.ok(start, 'start button rendered from the descriptor');
  assert.match(start.title, /costs money/);
  start.click();
  assert.equal(a.node('action-dialog').open, true);
  assert.equal(a.node('action-title').textContent, 'Start benchmark run');
  assert.ok(buttonNamed(content, 'Delete'), 'row action sits with its run');
});

test('unconfigured target explains setup and offers no start control', () => {
  const a = app();
  const content = a.show('benchmarks', benchData({ configured: false, target: null, actions: [], target_message: 'No Hermes API server is configured.',
    setup: ['Set HERMES_BENCH_API_URL on the server.'] }));
  assert.match(content.textContent, /HERMES_BENCH_API_URL/);
  assert.equal(buttonNamed(content, 'Start benchmark run'), undefined);
});

test('an active run shows progress and polls every 3 seconds', () => {
  const a = app();
  const active = run({ status: 'running', completed: 7, total: 28, passed: 5 });
  const content = a.show('benchmarks', benchData({ active, items: [active], actions: [{ id: 'cancel_run', label: 'Cancel', target: active.id, revision: active.id + ':running', fields: [{ name: 'run_id', type: 'hidden', value: active.id }] }] }));
  const bar = byTag(content, 'progress')[0];
  assert.equal(bar.value, 7); assert.equal(bar.max, 28);
  assert.ok(a.timers.includes(3000));
  assert.ok(buttonNamed(content, 'Cancel'));
});

test('selecting two runs requests a comparison through the allowlisted query', async () => {
  const a = app(async () => response(benchData()));
  let content = a.show('benchmarks', benchData());
  const boxes = byTag(content, 'input').filter(n => n.type === 'checkbox');
  assert.equal(boxes.length, 2);
  for (const box of boxes) { box.checked = true; box.listeners.change(); }
  content = a.node('content');
  const compare = buttonNamed(content, 'Compare');
  assert.equal(compare.disabled, false);
  compare.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.calls.at(-1).url, '/api/assistant/benchmarks?compare=20261031-090000-aaaaaa%2C20261031-100000-bbbbbb');
});

test('comparison matrix keeps unknown values unknown and highlights the best pass rate', () => {
  const a = app();
  const content = a.show('benchmarks', benchData({ compare: { same_suite: false, columns: [{ id: 'a', label: 'B1' }, { id: 'b', label: 'B2' }],
    rows: [{ key: 'pass_rate', label: 'Pass rate', format: 'percent', values: [0.5, 0.75] }, { key: 'cost_per_task', label: 'Cost per task', format: 'money', values: [null, null] }],
    settings: { rows: [{ key: 'model.default', values: ['cheap', 'strong'] }], same: 3, missing: [] } } }));
  const text = content.textContent;
  assert.match(text, /75%/); assert.match(text, /No price/); assert.doesNotMatch(text, /\$0\.00/);
  assert.match(text, /different suites/); assert.match(text, /model\.default/);
  const best = all(content, n => n.classes?.has('best'));
  assert.deepEqual(best.map(n => n.textContent), ['75%']);
});

test('experiments compare labels and show settings snapshots without inventing costs', async () => {
  const data = { section: 'experiments', available: true, pricing: false, labels: [{ label: 'B1', runs: 2, configs: 1 }, { label: 'B2', runs: 1, configs: 2 }],
    items: [{ id: 'r1', label: hostile, started_at: 1793000000, hermes_rev: 'abc', config_hash: 'h1', active_s: 3600, turns: 3, api_calls: 6, input_tokens: 10, output_tokens: 5, errors: 0, tool_calls: 2, tool_failures: 1, has_snapshot: false }],
    compare: null };
  const a = app(async () => response(data));
  let content = a.show('experiments', data);
  assert.match(content.textContent, /Not captured/);
  assert.match(content.textContent, /Add pricing\.yaml/);
  const boxes = byTag(content, 'input').filter(n => n.type === 'checkbox');
  boxes[0].checked = true; boxes[0].listeners.change(); boxes[1].checked = true; boxes[1].listeners.change();
  buttonNamed(a.node('content'), 'Compare').click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.calls.at(-1).url, '/api/assistant/experiments?compare=label%3AB1%2Clabel%3AB2');
  content = a.show('experiments', { ...data, compare: { columns: [{ key: 'label:B1', title: 'B1', kind: 'label', runs: ['r1', 'r2'], config_hashes: ['h1'], models: [] }, { key: 'label:B2', title: 'B2', kind: 'label', runs: ['r3'], config_hashes: ['h2', 'h3'], models: [] }],
    rows: [{ key: 'cost_usd', label: 'Estimated cost', format: 'money', values: [null, 0.5] }], settings: { rows: [], same: 4, missing: [1] } } });
  assert.match(content.textContent, /No price/);
  assert.match(content.textContent, /No settings snapshot for: Label B2/);
});
