import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../../../src/hermes_zalo/admin_static/admin.js', import.meta.url), 'utf8');

// A small DOM fixture lets the real action handlers run with synthetic fetches.
// No production globals or external browser dependencies are needed.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.listeners = {}; this.hidden = false; this.disabled = false; this.open = false;
    this.value = ''; this.classList = { add() {}, toggle() {} };
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(key, value) { this.attributes[key] = value; }
  focus() {}
  scrollIntoView() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  reset() {}
  setCustomValidity(value) { this.validationMessage = value; }
  reportValidity() { return !this.validationMessage; }
}

const walk = node => [node, ...node.children.flatMap(walk)];
const fixtureTicket = overrides => ({
  id: 'DC-SYNTHETIC', version: 2, status: 'pending_manager', reason: 'Đổi ca mẫu',
  a: { member_name: 'Nguyễn An', member_id: 'demo-a', start: '2030-01-02T08:00:00+07:00', end: '2030-01-02T10:00:00+07:00', course: 'Hóa học', room: 'A01' },
  b: { member_name: 'Trần Bình', member_id: 'demo-b', start: '2030-01-03T08:00:00+07:00', end: '2030-01-03T10:00:00+07:00', course: 'Vật lý', room: 'B02' },
  actions: ['approve', 'reject'], events: [], ...overrides,
});
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const nextTurn = () => new Promise(resolve => setImmediate(resolve));

function app(fetchImpl = async () => response({ summary: { pending_manager: 0 } })) {
  const roots = new Map(), calls = [];
  const all = () => [...roots.values()].flatMap(walk);
  const document = {
    createElement: tag => new Element(tag),
    getElementById(id) {
      const child = all().find(node => node.id === id);
      if (child) return child;
      if (!roots.has(id)) roots.set(id, Object.assign(new Element(), { id }));
      return roots.get(id);
    },
    querySelectorAll: selector => selector === '[data-ticket-control]' ? all().filter(node => node.dataset.ticketControl) : [],
  };
  const context = vm.createContext({
    document, URLSearchParams, setTimeout: () => 0, clearTimeout() {},
    fetch: async (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); },
  });
  assert.ok(source.includes('  init();'));
  vm.runInContext(source.replace('  init();', '  globalThis.testApp={state,canView,renderTicket,ticketMutation,finishConfirmation,loadTicket};'), context);
  const api = context.testApp;
  api.state.user = { username: 'demo-admin', role: 'admin', permissions: ['tickets'] };
  api.state.csrf = 'synthetic_csrf_value';
  api.state.ticket = fixtureTicket();
  api.renderTicket();
  return { ...api, calls, document, nodes: () => walk(document.getElementById('ticket-content')) };
}

test('ticket navigation requires the explicit permission even for faculty', () => {
  const instance = app();
  for (const role of ['admin', 'faculty']) {
    instance.state.user = { role, permissions: ['members', 'issues'] };
    assert.equal(instance.canView('tickets'), false);
    instance.state.user.permissions.push('tickets');
    assert.equal(instance.canView('tickets'), true);
  }
});

test('detail uses backend actions and permits only reconciliation for an uncertain result', () => {
  const instance = app();
  instance.state.ticket = fixtureTicket({ status: 'pending_manager', actions: [] });
  instance.renderTicket();
  assert.equal(instance.nodes().filter(node => node.dataset.ticketControl).length, 0);
  instance.state.ticket = fixtureTicket({ status: 'uncertain', actions: ['approve', 'reject', 'reconcile'] });
  instance.renderTicket();
  const buttons = instance.nodes().filter(node => node.tagName === 'button');
  assert.deepEqual(buttons.map(node => node.textContent), ['Đối soát kết quả']);
});

test('approval waits for confirmation, disables competing actions and sends one versioned CSRF write', async () => {
  let finishWrite;
  const instance = app(async (url, options) => options.method === 'POST'
    ? new Promise(resolve => { finishWrite = resolve; })
    : response({ summary: { pending_manager: 0 } }));
  const pending = instance.ticketMutation('approve');
  assert.equal(instance.calls.length, 0);
  assert.ok(instance.document.getElementById('confirmation-description').textContent.includes('lịch thật trên Google Sheets'));
  assert.ok(instance.nodes().filter(node => node.dataset.ticketControl).every(node => node.disabled));
  instance.finishConfirmation(true);
  await nextTurn();
  await instance.ticketMutation('approve');
  assert.equal(instance.calls.length, 1);
  const sent = instance.calls[0];
  assert.equal(sent.url, '/api/ticket/action');
  assert.equal(sent.options.headers['X-CSRF-Token'], 'synthetic_csrf_value');
  assert.deepEqual(JSON.parse(sent.options.body), { id: 'DC-SYNTHETIC', action: 'approve', expected_version: 2, reason: '', confirm: true });
  finishWrite(response({ ticket: fixtureTicket({ version: 4, status: 'uncertain', actions: ['reconcile'] }) }));
  await pending;
  assert.deepEqual(instance.nodes().filter(node => node.tagName === 'button').map(node => node.textContent), ['Đối soát kết quả']);
  assert.equal(instance.calls.filter(call => call.options.method === 'POST').length, 1);
});

test('a version conflict blocks repeat writes until a successful explicit refresh', async () => {
  const instance = app(async (url, options) => options.method === 'POST'
    ? response({ error: 'Phiên bản đã thay đổi.' }, 409)
    : url.startsWith('/api/ticket?') ? response({ ticket: fixtureTicket({ version: 3 }) })
    : response({ summary: { pending_manager: 1 } }));
  const pending = instance.ticketMutation('approve');
  instance.finishConfirmation(true);
  await pending;
  assert.equal(instance.state.ticketRefreshRequired, 'DC-SYNTHETIC');
  assert.ok(walk(instance.document.getElementById('ticket-action-error')).some(node => node.textContent?.includes('Tải lại phiên bản mới')));
  await instance.ticketMutation('approve');
  assert.equal(instance.calls.filter(call => call.options.method === 'POST').length, 1);
  await instance.loadTicket('DC-SYNTHETIC');
  assert.equal(instance.state.ticketRefreshRequired, null);
  assert.equal(instance.state.ticket.version, 3);
  assert.ok(instance.nodes().filter(node => node.dataset.ticketControl).every(node => !node.disabled));
});

test('canceling confirmation does not write, and rejection requires a nonblank reason', async () => {
  const instance = app();
  const pending = instance.ticketMutation('approve');
  instance.finishConfirmation(false);
  await pending;
  assert.equal(instance.calls.length, 0);
  const form = instance.nodes().find(node => node.tagName === 'form');
  const textarea = walk(form).find(node => node.tagName === 'textarea');
  assert.equal(textarea.required, true);
  textarea.value = '   ';
  form.listeners.submit({ preventDefault() {} });
  assert.equal(textarea.validationMessage, 'Vui lòng nhập lý do từ chối.');
  assert.equal(instance.document.getElementById('confirmation-dialog').open, false);
  assert.equal(instance.calls.length, 0);
});
