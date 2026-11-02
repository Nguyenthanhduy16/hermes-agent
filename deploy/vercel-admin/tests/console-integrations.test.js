import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const read = path => readFile(new URL('../../../src/hermes_zalo/admin_static/' + path, import.meta.url), 'utf8');
const consoleSource = await read('console.js');
const integrationsSource = await read('console-integrations.js');
const boot = consoleSource.lastIndexOf('  api("/api/session").then');
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
  set outerHTML(_) { throw new Error('Untrusted content must never use outerHTML'); }
  insertAdjacentHTML() { throw new Error('Untrusted content must never be parsed as HTML'); }
  append(...nodes) { for (const n of nodes) { if (n.tag === 'fragment') this.append(...n.children); else { n.parent = this; this.children.push(n); } } }
  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { if (name === 'style') throw new Error('Inline style attributes are blocked by CSP'); this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  get lastChild() { return this.children.at(-1); }
  get childElementCount() { return this.children.length; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); }
  querySelectorAll() { return []; }
  focus() {}
  reset() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
}

const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data });
function app(fetchImpl = async () => response({ items: [] })) {
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
  const context = vm.createContext({ document, window, Node: Element, URL, URLSearchParams, AbortController, Intl, Date,
    location: { hash: '' }, history: { replaceState() {} }, setTimeout: () => 0, clearTimeout() {},
    fetch: async (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); } });
  vm.runInContext(consoleSource.slice(0, boot) + 'globalThis.consoleTest={state,views,extensions,paletteEntries,render,allowed,submitAction,sectionActions};})();', context);
  vm.runInContext(integrationsSource, context);
  const api = context.consoleTest;
  api.state.user = { username: 'owner', role: 'faculty', permissions: [] };
  api.state.csrf = 'synthetic-csrf';
  return { ...api, node: get, calls, tags, window };
}

const walk = n => [n, ...n.children.flatMap(walk)];
const content = a => a.node('content');
const buttons = a => walk(content(a)).filter(n => n.tag === 'button');
const press = (a, label) => { const b = buttons(a).find(n => n.textContent === label); assert.ok(b, 'button ' + label); b.listeners.click(); return b; };
const accept = a => { a.node('confirm-dialog').returnValue = 'accept'; a.node('confirm-dialog').close(); };

const hostile = '<img src=x onerror=alert(1)>';
const connectors = () => ({
  section: 'connectors', available: true, source: 'hermes-home', message: 'Connectors are MCP servers.',
  summary: { enabled: 1, installed: 1, needs_credentials: 1, bridge_needed: 1, total: 3 },
  categories: [{ id: 'social', label: 'Social', count: 1 }, { id: 'productivity', label: 'Productivity', count: 1 }, { id: 'developer', label: 'Developer', count: 1 }],
  items: [
    { id: 'zalo-oa', name: 'Zalo Official Account', category: 'social', category_label: 'Social', initials: 'Za', kind: 'bridge',
      kind_label: 'Bridge needed', status: 'bridge_needed', status_label: 'Bridge needed', description: hostile, api: 'Zalo OA Open API',
      note: 'No official MCP server.', capabilities: [{ label: 'Read follower statistics', access: 'read', access_label: 'Read only' },
        { label: 'Send messages to followers', access: 'messages', access_label: 'Can send messages' }],
      credentials: [{ variable: 'ZALO_OA_ACCESS_TOKEN', label: 'OA token', help: 'Expires.', writable: true, configured: false, source: null }],
      docs: [{ label: 'Zalo for Developers', url: 'https://developers.zalo.me/' }, { label: 'Bad link', url: 'javascript:alert(1)' }, { label: 'Plain', url: 'http://example.com/' }],
      servers: [] },
    { id: 'notion', name: 'Notion', publisher: 'Notion', category: 'productivity', category_label: 'Productivity', initials: 'N', kind: 'local',
      kind_label: 'Local server', status: 'needs_credentials', status_label: 'Needs credentials', description: 'Pages.', api: 'Notion API',
      capabilities: [{ label: 'Create pages', access: 'write', access_label: 'Changes data' }],
      credentials: [{ variable: 'NOTION_TOKEN', label: 'Token', help: 'Secret.', writable: true, configured: false }],
      snippet: 'mcp_servers:\n  notion:\n    command: npx\n', docs: [], servers: [] },
    { id: 'github', name: 'GitHub', publisher: 'GitHub engineering', category: 'developer', category_label: 'Developer', initials: 'GH', kind: 'remote',
      kind_label: 'Remote MCP server', status: 'enabled', status_label: 'Enabled', description: 'Repos.', api: 'GitHub MCP Server',
      capabilities: [], credentials: [], docs: [],
      servers: [{ name: 'github', transport: 'http', host: 'api.githubcopilot.com', enabled: true, exposed_to: ['zalo'], missing_credentials: [],
        last_test: { ok: true, count: 9, tools: ['get_me', 'list_issues'], at: '2026-10-31T08:00:00+00:00' } }] },
    { id: 'custom', name: 'Custom MCP server', category: 'custom', publisher: 'Your server', kind: 'custom', credentials: [], servers: [], capabilities: [], docs: [] },
  ],
  actions: [
    { id: 'install', label: 'Add server', revision: 'cfg.env', target: 'custom', fields: [
      { name: 'template', type: 'hidden', value: 'custom' },
      { name: 'name', label: 'Server name', type: 'text', required: true },
      { name: 'url', label: 'Server URL', type: 'text', required: true },
      { name: 'authentication', label: 'Authentication', type: 'select', value: 'none', options: [{value:'none',label:'No authentication'},{value:'bearer',label:'Bearer token'}] },
      { name: 'token', label: 'Token', type: 'password', required: true, when: {field:'authentication',value:'bearer'} },
      { name: 'timeout', label: 'Timeout', type: 'number', value: 120 },
      { name: 'enabled', label: 'Enable after restart', type: 'checkbox', value: false },
    ] },
    { id: 'set_credential', label: 'Set NOTION_TOKEN', revision: 'env-rev', target: 'notion', fields: [
      { name: 'template', type: 'hidden', value: 'notion' }, { name: 'variable', type: 'hidden', value: 'NOTION_TOKEN' },
      { name: 'value', label: 'Token', type: 'password', required: true, value: 'must-never-prefill' }] },
    { id: 'test_connection', label: 'Test github', revision: 'cfg-rev', target: 'github', fields: [
      { name: 'template', type: 'hidden', value: 'github' }, { name: 'name', type: 'hidden', value: 'github' }] },
  ],
});

test('integrations pages register in their sidebar group and stay owner-only', () => {
  const a = app();
  assert.equal(a.views.connectors[0], 'Connectors');
  assert.equal(a.views.reports[0], 'Reports');
  assert.equal(a.allowed('connectors'), true);
  a.state.user.role = 'admin';
  assert.equal(a.allowed('connectors'), false);
  assert.equal(a.allowed('reports'), false);
});

const connect = (a, name) => {
  const b = buttons(a).find(n => n.attributes['aria-label'] === 'Connect ' + name); assert.ok(b, name); b.listeners.click();
};
const manage = (a, name) => {
  const b = buttons(a).find(n => n.attributes['aria-label'] === 'Manage ' + name); assert.ok(b, name); b.listeners.click();
};
const cards = a => walk(content(a)).filter(n => n.className === 'co-card');
const field = (a, name) => {
  const action = a.state.control.action;
  return a.node('action-field-' + action.fields.findIndex(f => f.name === name));
};

test('Yours shows saved apps without claiming an enabled server is live', () => {
  const a = app(); a.state.view = 'connectors'; a.state.data = connectors(); a.render();
  assert.equal(cards(a).length, 1);
  assert.ok(content(a).textContent.includes('GitHub'));
  assert.ok(!content(a).textContent.includes('ZALO_OA_ACCESS_TOKEN'));
  assert.ok(content(a).textContent.includes('Enabled in config'));
  assert.ok(!content(a).textContent.includes('Connected'));
  assert.equal(buttons(a).find(b => b.textContent === 'Yours').attributes['aria-selected'], 'true');
});

test('Discover cards are concise, searchable by publisher and filterable by category', () => {
  const a = app(); a.state.view = 'connectors'; a.state.data = connectors(); a.render(); press(a, 'Discover');
  assert.equal(cards(a).length, 4);
  assert.ok(content(a).textContent.includes(hostile), 'hostile description is text');
  assert.ok(!a.tags.includes('img') && !a.tags.includes('script'));
  assert.ok(!content(a).textContent.includes('ZALO_OA_ACCESS_TOKEN'));
  const search = walk(content(a)).find(n => n.attributes['aria-label'] === 'Search connectors');
  search.value = 'Notion'; search.listeners.input(); assert.equal(cards(a).length, 1);
  assert.ok(cards(a)[0].textContent.includes('Notion'));
  search.value = 'engineering'; search.listeners.input(); assert.equal(cards(a).length, 1); assert.ok(cards(a)[0].textContent.includes('GitHub'));
  search.value = ''; search.listeners.input();
  const category = walk(content(a)).find(n => n.attributes['aria-label'] === 'Filter by category');
  category.value = 'developer'; category.listeners.change(); assert.equal(cards(a).length, 1);
  assert.ok(cards(a)[0].textContent.includes('GitHub'));
  search.value = 'no-such-app'; search.listeners.input(); assert.ok(content(a).textContent.includes('No matching connectors'));
});

test('an empty Yours library offers real discovery and includes credential-only setups', () => {
  const a = app(); const data = connectors(); data.items.forEach(i => i.servers = []);
  a.state.view = 'connectors'; a.state.data = data; a.render();
  assert.ok(content(a).textContent.includes('Your apps, together')); assert.equal(cards(a).length, 0);
  press(a, 'Discover connectors'); assert.equal(cards(a).length, 4);
  data.items.find(i => i.id === 'notion').credentials[0].configured = true; press(a, 'Yours');
  assert.equal(cards(a).length, 1); assert.ok(cards(a)[0].textContent.includes('Needs setup'));
});

test('details show real bridge prerequisites, scoped capabilities and only safe documentation links', () => {
  const a = app(); a.state.view = 'connectors'; a.state.data = connectors(); a.render(); press(a, 'Discover'); connect(a, 'Zalo Official Account');
  const dialog = walk(content(a)).find(n => n.tag === 'dialog'); assert.ok(dialog.open);
  assert.equal(dialog.attributes['aria-labelledby'], 'connector-dialog-title');
  for (const expected of ['What you\'re allowing', 'Can send messages', 'How to connect', 'Saving a provider API key alone does not create a bridge', 'Browser OAuth is not available']) assert.ok(dialog.textContent.includes(expected), expected);
  const links = walk(dialog).filter(n => n.tag === 'a');
  assert.deepEqual(links.map(l => l.href), ['https://developers.zalo.me/']);
  assert.equal(links[0].rel, 'noopener noreferrer');
  press(a, 'Close'); assert.ok(!walk(content(a)).some(n => n.tag === 'dialog'));
});

test('local setup explains operator steps and credential fields remain write-only', async () => {
  const a = app(async (url, options) => options?.method === 'POST' ? response({ ok: true, message: 'NOTION_TOKEN saved.' }) : response(connectors()));
  a.state.view = 'connectors'; a.state.data = connectors(); a.render(); press(a, 'Discover'); connect(a, 'Notion');
  assert.ok(content(a).textContent.includes('Operator setup'));
  assert.ok(content(a).textContent.includes('Saving a credential does not install or start'));
  press(a, 'Set NOTION_TOKEN'); assert.equal(a.node('action-title').textContent, 'Set NOTION_TOKEN');
  const password = walk(a.node('action-fields')).find(n => n.type === 'password'); assert.equal(password.value, '');
  password.value = 'secret-from-owner'; const pending = a.submitAction({ preventDefault() {} }); accept(a); await pending;
  const post = a.calls.find(c => c.options?.method === 'POST');
  assert.equal(post.url, '/api/assistant/connectors/action');
  assert.deepEqual(JSON.parse(post.options.body), { action: 'set_credential', expected_revision: 'env-rev', confirm: true,
    values: { template: 'notion', variable: 'NOTION_TOKEN', value: 'secret-from-owner' } });
  assert.match(a.node('toast').textContent, /NOTION_TOKEN saved/);
});

test('custom Add opens a validated two-step setup with conditional secrets and collapsed advanced settings', async () => {
  const a = app(async (_, options) => options?.method === 'POST' ? response({ ok: true, message: 'Server saved disabled.' }) : response(connectors()));
  a.state.view = 'connectors'; a.state.data = connectors(); a.render(); press(a, '+ Add'); press(a, 'Continue');
  assert.equal(a.node('action-dialog').open, true); assert.equal(a.node('action-submit').hidden, true);
  const advanced = walk(a.node('action-fields')).find(n => n.className === 'action-advanced'); assert.ok(advanced); assert.equal(advanced.open, false);
  field(a, 'name').value = 'my-tools'; field(a, 'url').value = 'javascript:alert(1)'; a.node('action-next').onclick();
  assert.equal(a.node('action-error').hidden, false); assert.equal(a.state.control.step, 0);
  for (const url of ['https://user:pass@example.com/mcp', 'http://remote.example/mcp', 'https://example.com/mcp?token=secret', 'https://example.com/mcp#private']) {
    field(a, 'url').value = url; a.node('action-next').onclick(); assert.equal(a.state.control.step, 0, url);
  }
  field(a, 'url').value = 'https://tools.example/mcp';
  const password = field(a, 'token'); assert.equal(password.value, ''); assert.equal(password.parent.hidden, true);
  field(a, 'authentication').value = 'bearer'; field(a, 'authentication').listeners.change();
  assert.equal(password.parent.hidden, false); a.node('action-next').onclick(); assert.equal(a.state.control.step, 0, 'token required');
  password.value = 'write-only-synthetic-secret'; a.node('action-next').onclick();
  assert.equal(a.state.control.step, 1); assert.equal(a.node('action-submit').hidden, false);
  const review = walk(a.node('action-fields')).find(n => n.className === 'action-review');
  assert.ok(review.textContent.includes('Provided')); assert.ok(!review.textContent.includes(password.value));
  a.node('action-back').onclick(); assert.equal(a.state.control.step, 0); assert.equal(field(a, 'url').value, 'https://tools.example/mcp');
  a.node('action-next').onclick(); const pending = a.submitAction({ preventDefault() {} });
  assert.equal(a.node('action-submit').disabled, true); accept(a); await pending;
  const body = JSON.parse(a.calls.find(c => c.options?.method === 'POST').options.body);
  assert.equal(body.values.token, 'write-only-synthetic-secret'); assert.equal(body.values.enabled, false);
  assert.equal(body.values.authentication, 'bearer'); assert.equal(body.expected_revision, 'cfg.env'); assert.equal(body.confirm, true);
  assert.match(a.node('toast').textContent, /saved disabled/);
});

test('changing auth to none wipes hidden secrets and no-auth setup succeeds', async () => {
  const a = app(async (_, options) => options?.method === 'POST' ? response({ ok: true }) : response(connectors()));
  a.state.view = 'connectors'; a.state.data = connectors(); a.render(); press(a, '+ Add'); press(a, 'Continue');
  field(a, 'name').value = 'local-tools'; field(a, 'url').value = 'http://127.0.0.1:8080/mcp';
  field(a, 'authentication').value = 'bearer'; field(a, 'authentication').listeners.change(); field(a, 'token').value = 'temporary-secret';
  field(a, 'authentication').value = 'none'; field(a, 'authentication').listeners.change(); assert.equal(field(a, 'token').value, '');
  a.node('action-next').onclick(); const pending = a.submitAction({ preventDefault() {} }); accept(a); await pending;
  const body = JSON.parse(a.calls.find(c => c.options?.method === 'POST').options.body); assert.equal(body.values.token, ''); assert.equal(body.values.authentication, 'none');
});

test('wizard errors clear secrets and require refresh without retrying; cancel never posts', async () => {
  const a = app(async (_, options) => options?.method === 'POST' ? response({ error: 'Stale revision' }, 409) : response(connectors()));
  a.state.view = 'connectors'; a.state.data = connectors(); a.render(); press(a, '+ Add'); press(a, 'Continue');
  field(a, 'name').value = 'tools'; field(a, 'url').value = 'https://tools.example/mcp';
  field(a, 'authentication').value = 'bearer'; field(a, 'authentication').listeners.change(); field(a, 'token').value = 'temporary-secret';
  a.node('action-next').onclick(); let pending = a.submitAction({ preventDefault() {} }); a.node('confirm-dialog').close(); await pending;
  assert.equal(a.calls.filter(c => c.options?.method === 'POST').length, 0);
  pending = a.submitAction({ preventDefault() {} }); accept(a); await pending;
  assert.equal(a.calls.filter(c => c.options?.method === 'POST').length, 1); assert.equal(field(a, 'token').value, '');
  assert.match(a.node('action-error').textContent, /Refresh this section/); assert.equal(a.node('action-submit').disabled, true);
});

test('Manage exposes test and actual state without conflating tests with runtime availability', () => {
  const a = app(); a.state.view = 'connectors'; a.state.data = connectors(); a.render(); manage(a, 'GitHub');
  assert.ok(content(a).textContent.includes('Runtime use is not verified'));
  assert.ok(content(a).textContent.includes('Last test')); assert.ok(content(a).textContent.includes('9 tools'));
  press(a, 'Test connection'); assert.equal(a.node('action-title').textContent, 'Test github'); assert.equal(a.node('action-dialog').open, true);
});

test('tabs support keyboard navigation and configured metadata is not treated as a provider verification badge', () => {
  const a = app(); a.state.view = 'connectors'; a.state.data = connectors(); a.render();
  const yours = buttons(a).find(b => b.textContent === 'Yours');
  yours.listeners.keydown({ key: 'ArrowRight', preventDefault() {} }); assert.equal(cards(a).length, 4);
  assert.equal(yours.attributes['aria-selected'], 'false'); assert.ok(!/Verified provider|verified by/.test(content(a).textContent));
});


test('Manage token replacement, disable and disconnect retain confirmation and revision checks', async () => {
  for (const [id, label, fields, expected] of [
    ['replace_token', 'Replace token for github', [{name:'value',label:'Token',type:'password',required:true}], {value:'new-write-only-token'}],
    ['toggle_server', 'Disable github', [{name:'enabled',label:'Enabled',type:'checkbox',value:false}], {enabled:false}],
    ['remove_server', 'Remove github', [], {}],
  ]) {
    const data = connectors();
    data.actions.push({id,label,target:'github',revision:'fresh-revision',fields:[{name:'template',type:'hidden',value:'github'},{name:'name',type:'hidden',value:'github'},...fields]});
    const a = app(async (_, options) => options?.method === 'POST' ? response({ok:true}) : response(data));
    a.state.view='connectors';a.state.data=data;a.render();manage(a,'GitHub');
    press(a, id==='replace_token'?'Replace token':id==='remove_server'?'Disconnect':'Disable github');
    if (id==='replace_token') { assert.equal(field(a,'value').value,'');field(a,'value').value=expected.value; }
    const pending=a.submitAction({preventDefault(){}});assert.equal(a.calls.filter(c=>c.options?.method==='POST').length,0);accept(a);await pending;
    const payload=JSON.parse(a.calls.find(c=>c.options?.method==='POST').options.body);
    assert.deepEqual(payload,{action:id,expected_revision:'fresh-revision',confirm:true,values:{template:'github',name:'github',...expected}});
  }
});

test('multi-server Manage keeps all actions inside a separate scrollable footer', async () => {
  const data=connectors(), custom=data.items.find(i=>i.id==='custom');
  custom.servers=[1,2,3].map(n=>({name:'tools-'+n,enabled:false,transport:'http',missing_credentials:[],exposed_to:[]}));
  for (const s of custom.servers) for (const id of ['test_connection','toggle_server','remove_server']) data.actions.push({id,label:id+' '+s.name,target:'custom',revision:'cfg',fields:[{name:'name',type:'hidden',value:s.name}]});
  const a=app();a.state.view='connectors';a.state.data=data;a.render();manage(a,'Custom MCP server');
  const dialog=walk(content(a)).find(n=>n.tag==='dialog'), footer=walk(dialog).find(n=>n.className==='co-dialog-actions');
  assert.equal(footer.children.length,10,'3 servers x3 actions plus Add another server');
  assert.ok(footer.children.every(n=>n.tag==='button'));
  const css=await readFile(new URL('../../../src/hermes_zalo/admin_static/console-integrations.css',import.meta.url),'utf8');
  assert.match(css,/\.co-dialog\[open\]\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*max-height:/);
  assert.match(css,/\.co-dialog-body\s*\{[^}]*flex:\s*1 1 auto;[^}]*overflow-y:\s*auto;[^}]*min-height:\s*0/);
  assert.match(css,/\.co-dialog-actions\s*\{[^}]*flex:\s*none;[^}]*max-height:[^}]*overflow-y:\s*auto/);
  assert.match(css,/\.co-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(css,/@media\s*\(max-width:\s*700px\)\s*\{\s*\.co-grid\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
});

const reports = (extra = {}) => ({
  section: 'reports', available: true, total: 1, scheduler_active: true, message: 'Saved in this bot home.',
  summary: { scheduled: 1, paused: 0, saved: 2, failing: 0 },
  templates: [{ id: 'zalo-oa-weekly', name: 'Weekly Zalo OA engagement report', description: 'Followers.', readiness: 'needs_connector', featured: true,
    readiness_label: 'Needs an enabled connector', schedule: 'every monday 8am', sections: ['Summary', 'Followers'], reports: 1,
    connectors: [{ id: 'zalo-oa', name: 'Zalo Official Account', status: 'installed', status_label: 'Installed (disabled)' }] },
  { id: 'website-traffic', name: 'Website traffic digest', description: 'Visitors.', readiness: 'ready', featured: false, sections: [], connectors: [] }],
  items: [{ id: 'abc123', name: 'Report · Zalo OA weekly · Café', template: 'zalo-oa-weekly', template_name: 'Weekly Zalo OA engagement report',
    account: 'Café', schedule: 'every monday 8am', enabled: true, state: 'scheduled', next_run_pending: true, last_status: 'ok', deliver: 'local', outputs: 2,
    latest_output: { name: '2026-10-31_08-00-12.md', excerpt: '# Report\n' + hostile, truncated: false } }],
  saved: [{ id: 'abc123/2026-10-31_08-00-12.md', job: 'abc123', file: '2026-10-31_08-00-12.md', at: '2026-10-31T08:00:12', title: 'Weekly Zalo OA engagement report', account: 'Café' },
    { id: 'abc123/2026-10-24_08-00-02.md', job: 'abc123', file: '2026-10-24_08-00-02.md', at: '2026-10-24T08:00:02', title: 'Weekly Zalo OA engagement report', account: 'Café' }],
  reader: { job: 'abc123', file: '2026-10-31_08-00-12.md', at: '2026-10-31T08:00:12', title: 'Zalo OA · week 44', template_name: 'Weekly Zalo OA engagement report', account: 'Café',
    text: '# Zalo OA · week 44\n\n## Summary\n- **Followers** up *12%*\n- See [docs](https://developers.zalo.me/)\n\n| Metric | Value |\n|---|---:|\n| Followers | 1,204 |\n', truncated: false },
  reader_error: null,
  actions: [{ id: 'create_report', label: 'Create report · Weekly Zalo OA engagement report', revision: 'jobs-rev', target: 'zalo-oa-weekly', fields: [
    { name: 'template', type: 'hidden', value: 'zalo-oa-weekly' }, { name: 'account', label: 'Account', type: 'text', required: true },
    { name: 'use.zalo-oa', label: 'Use Zalo Official Account', type: 'checkbox', value: true },
    { name: 'schedule_preset', label: 'Schedule', type: 'select', value: 'template', options: [{ value: 'template', label: 'Suggested' }, { value: 'daily-8', label: 'Every day at 8:00' }, { value: 'custom', label: 'Custom' }] },
    { name: 'schedule', label: 'Custom schedule', type: 'text', value: 'every monday 8am' },
    { name: 'delivery', label: 'Delivery', type: 'select', value: 'local', options: [{ value: 'local', label: 'Save in this bot home' }] },
    { name: 'language', label: 'Language', type: 'select', value: 'vi', options: [{ value: 'vi', label: 'Tiếng Việt' }, { value: 'en', label: 'English' }] }] },
    { id: 'pause_routine', label: 'Pause', revision: 'jobs-rev', target: 'abc123', fields: [{ name: 'id', type: 'hidden', value: 'abc123' }] },
    { id: 'run_routine', label: 'Run now', revision: 'jobs-rev', target: 'abc123', fields: [{ name: 'id', type: 'hidden', value: 'abc123' }] }],
  ...extra,
});

const readerOf = a => walk(content(a)).find(n => n.className === 'int-md');

test('reports render the saved report list, the reader and job cards as text', () => {
  const a = app(); a.state.view = 'reports'; a.state.data = reports(); a.render();
  const text = content(a).textContent;
  for (const expected of ['Waiting for the scheduler', 'Saved in this bot home', 'Latest saved report', hostile, 'Café', '31 Oct 2026 · 08:00',
    '24 Oct 2026 · 08:00', 'Zalo OA · week 44', 'Saved reports']) assert.ok(text.includes(expected), expected);
  assert.ok(!a.tags.includes('img'));
  const md = readerOf(a);
  assert.ok(md, 'reader renders markdown');
  const tags = walk(md).map(n => n.tag);
  for (const tag of ['h4', 'ul', 'li', 'strong', 'em', 'a', 'table', 'th', 'td']) assert.ok(tags.includes(tag), tag);
  assert.ok(!tags.includes('h3'), 'a leading heading that repeats the reader title is dropped');
  assert.equal(walk(md).filter(n => n.textContent === 'Zalo OA · week 44').length, 0);
  const link = walk(md).find(n => n.tag === 'a');
  assert.equal(link.href, 'https://developers.zalo.me/');
  assert.equal(link.rel, 'noopener noreferrer');
  assert.equal(walk(md).find(n => n.tag === 'td' && n.textContent === '1,204').className, 'md-align-right');
  const labels = buttons(a).map(b => b.textContent);
  assert.ok(labels.includes('Pause') && labels.includes('Run now') && labels.includes('New report'));
  assert.ok(!labels.includes('Use template'), 'the template picker opens on demand once reports exist');
});

test('opening a saved report reloads with the job and file query', async () => {
  const a = app(async () => response(reports())); a.state.view = 'reports'; a.state.data = reports(); a.render();
  const older = buttons(a).find(b => b.className.includes('int-saved-row') && b.textContent.includes('24 Oct'));
  older.listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  const get = a.calls.at(-1);
  assert.equal(get.url, '/api/assistant/reports?job=abc123&file=2026-10-24_08-00-02.md');
});

test('a reader error is shown with a way back to the latest report', () => {
  const a = app(); a.state.view = 'reports'; a.state.data = reports({ reader: null, reader_error: 'This saved report no longer exists.' }); a.render();
  assert.ok(content(a).textContent.includes('This saved report no longer exists.'));
  assert.ok(buttons(a).some(b => b.textContent === 'Show the latest report'));
});

test('the Home report bridge requests exactly its job and file, and refuses paths', async () => {
  const a = app(async () => response(reports())); a.state.view = 'overview';
  assert.equal(a.window.HermesReports.open('abc123', '2026-10-24_08-00-02.md'), true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.state.view, 'reports');
  assert.equal(a.calls[0].url, '/api/assistant/reports?job=abc123&file=2026-10-24_08-00-02.md');
  const before = a.calls.length;
  for (const [job, file] of [['../outside', '2026-10-24_08-00-02.md'], ['abc123', '../private.md']]) {
    assert.equal(a.window.HermesReports.open(job, file), false);
  }
  assert.equal(a.calls.length, before);
  assert.ok(!content(a).textContent.includes('Pending scheduler tick'));
  assert.ok(!content(a).textContent.includes('0 8 * * *'));
});

test('Reports palette commands open the latest reader and the template composer', async () => {
  const a = app(async () => response(reports()));
  a.state.view = 'overview';
  a.window.HermesReports.open('abc123', '2026-10-24_08-00-02.md');
  await new Promise(resolve => setImmediate(resolve));
  const commands = a.extensions.get('reports').commands;
  commands.find(c => c.label === 'Open latest report').run(a.window.HermesConsole.kit);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(a.calls.at(-1).url, '/api/assistant/reports');
  a.paletteEntries().find(c => c.label === 'New report').run();
  assert.ok(buttons(a).some(b => b.textContent === 'Use template'), 'opens the useful template flow');
  assert.ok(!a.calls.some(c => c.options.method === 'POST'), 'opening the composer does not create a job');
});

test('new report flow: stepper, template picker and the create descriptor with presets and delivery', async () => {
  const a = app(async (url, options) => options?.method === 'POST' ? response({ ok: true, message: 'Report created.' }) : response(reports()));
  a.state.view = 'reports'; a.state.data = reports(); a.render();
  press(a, 'New report');
  const text = content(a).textContent;
  for (const expected of ['Template', 'Connectors', 'Schedule', 'Delivery', 'Confirm', 'Needs a connector', 'Zalo Official Account · Installed (disabled)',
    'Summary · Followers', 'More templates · 1']) assert.ok(text.includes(expected), expected);
  press(a, 'Use template');
  assert.equal(a.node('action-title').textContent, 'Create report · Weekly Zalo OA engagement report');
  const fields = walk(a.node('action-fields'));
  const control = id => fields.find(n => n.id === id);
  const inputs = fields.filter(n => ['input', 'select'].includes(n.tag) && n.id);
  inputs.find(n => n.type === 'text').value = 'Hermes Café';
  const preset = inputs.find(n => n.tag === 'select' && n.children.some(o => o.value === 'daily-8'));
  preset.value = 'daily-8';
  assert.ok(control);
  const pending = a.submitAction({ preventDefault() {} }); accept(a); await pending;
  const post = a.calls.find(c => c.options?.method === 'POST');
  assert.equal(post.url, '/api/assistant/reports/action');
  const body = JSON.parse(post.options.body);
  assert.equal(body.action, 'create_report');
  assert.deepEqual(body.values, { template: 'zalo-oa-weekly', account: 'Hermes Café', 'use.zalo-oa': true, schedule_preset: 'daily-8',
    schedule: 'every monday 8am', delivery: 'local', language: 'vi' });
});

test('markdown renderer never creates markup from report text (XSS)', () => {
  const attack = [
    '# Title <script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '<a href="javascript:alert(1)" onclick="alert(2)">click</a>',
    '[bad](javascript:alert(1)) [data](data:text/html;base64,PHNjcmlwdD4=) [vb](vbscript:msgbox) [creds](https://user:pw@evil.example/)',
    '[ok](https://example.com/path?q=1 "title")',
    '- **<b onmouseover=alert(1)>bold</b>**',
    '| a <svg onload=alert(1)> | b |', '|---|---|', '| [x](javascript:void(0)) | `<script>` |',
    '```', '<script>alert("in code")</script>', '```',
    '> <iframe src="https://evil.example"></iframe>',
  ].join('\n');
  const a = app(); a.state.view = 'reports';
  a.state.data = reports({ reader: { job: 'abc123', file: '2026-10-31_08-00-12.md', at: '2026-10-31T08:00:12', title: 'Attack', text: attack } }); a.render();
  const md = readerOf(a), nodes = walk(md);
  for (const tag of ['script', 'img', 'iframe', 'svg', 'b', 'object', 'embed', 'style']) assert.ok(!nodes.some(n => n.tag === tag), 'no ' + tag);
  for (const n of nodes) {
    for (const name of Object.keys(n.attributes)) assert.ok(!/^on/i.test(name), 'no event handler attribute ' + name);
    if (n.tag === 'a') {
      assert.match(n.href, /^https?:\/\//);
      assert.equal(n.rel, 'noopener noreferrer');
      assert.equal(n.target, '_blank');
    }
  }
  assert.deepEqual(nodes.filter(n => n.tag === 'a').map(n => n.href), [], 'unsafe or malformed links stay text');
  const shown = md.textContent;
  for (const raw of ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>', 'javascript:alert(1)', '<script>alert("in code")</script>', '<iframe']) assert.ok(shown.includes(raw), 'shown as text: ' + raw);
  assert.ok(nodes.some(n => n.tag === 'pre'), 'code block');
  assert.ok(nodes.some(n => n.tag === 'table'), 'table');
});

test('markdown renderer keeps safe links and bounds its input', () => {
  const big = '# Big\n' + 'word '.repeat(20000) + '\n\n' + Array.from({ length: 3000 }, (_, i) => 'para ' + i).join('\n\n');
  const a = app(); a.state.view = 'reports';
  a.state.data = reports({ reader: { job: 'abc123', file: 'f', at: '', title: 'Big', text: big + '\n\nSee [site](https://example.com/a).' } }); a.render();
  const md = readerOf(a);
  assert.ok(md.textContent.length <= 64100, 'input is capped');
  assert.ok(md.children.length <= 1501, 'block count is capped');
  const b = app(); b.state.view = 'reports';
  b.state.data = reports({ reader: { job: 'abc123', file: 'f', at: '', title: 'Links', text: 'A [safe](https://example.com/a) and [http](http://example.org/) link, `[no](https://x.example)` in code, snake_case_name stays.' } }); b.render();
  const nodes = walk(readerOf(b));
  assert.deepEqual(nodes.filter(n => n.tag === 'a').map(n => n.href), ['https://example.com/a', 'http://example.org/']);
  assert.ok(!nodes.some(n => n.tag === 'em'), 'underscores inside words are not emphasis');
  assert.ok(nodes.some(n => n.tag === 'code' && n.textContent === '[no](https://x.example)'));
});

test('both integrations pages are essential', () => {
  const src = integrationsSource;
  assert.equal((src.match(/essential: true/g) || []).length, 2);
});
