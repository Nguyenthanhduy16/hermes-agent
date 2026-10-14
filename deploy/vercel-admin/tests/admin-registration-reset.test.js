import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../../../src/hermes_zalo/admin_static/admin.js', import.meta.url), 'utf8');
const html = await readFile(new URL('../../../src/hermes_zalo/admin_static/index.html', import.meta.url), 'utf8');
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.listeners = {};
    this.value = ''; this.checked = false; this.hidden = false; this.open = false;
    this.disabled = false; this.classList = { add() {}, toggle() {} };
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute() {}
  focus() {}
  reset() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
}
const walk = node => [node, ...node.children.flatMap(walk)];
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const member = { member_id: 'member-test', request_id: 'DK-test', revision: 4, name: 'Nguyễn An', phone_masked: '******0001', phone: '0900000001', status: 'approved', locked: false };

function app(fetchImpl = url => response(url.startsWith('/api/members') ? { items: [], total: 0 } : url.startsWith('/api/member?') ? { member: { ...member, status: 'reset', revision: 5 } } : { ok: true })) {
  const nodes = new Map(), calls = [];
  const document = {
    createElement: tag => new Element(tag),
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    querySelectorAll: selector => selector === 'dialog[open]' ? [...nodes.values()].filter(node => node.open) : [],
  };
  const context = vm.createContext({ document, URLSearchParams, setTimeout: () => 0, clearTimeout() {},
    fetch: async (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); },
  });
  vm.runInContext(source.replace('  init();', '  globalThis.testApp={state,openRegistrationReset,renderProfile,showLogin};'), context);
  const api = context.testApp;
  api.state.user = { username: 'admin-synthetic', permissions: ['members'] };
  api.state.member = { ...member }; api.state.csrf = 'synthetic-csrf';
  const node = id => document.getElementById(id);
  const open = () => { api.openRegistrationReset(); node('registration-reset-reason').value = 'Thành viên muốn đăng ký lại.'; node('registration-reset-verified').checked = true; };
  const submit = () => node('registration-reset-form').listeners.submit({ preventDefault() {} });
  return { ...api, node, calls, open, submit };
}

test('reset button and action require members permission even for faculty', async () => {
  const instance = app();
  for (const role of ['admin', 'faculty']) {
    instance.state.user = { role, permissions: ['issues'] };
    instance.renderProfile(); instance.open(); await instance.submit();
    assert.equal(walk(instance.node('member-profile')).some(node => node.textContent === 'Xóa đăng ký'), false);
    assert.equal(instance.node('registration-reset-dialog').open, false);
    assert.equal(instance.calls.length, 0);
  }
});

test('locked, already reset, or incomplete member snapshot cannot reset', async () => {
  const instance = app();
  for (const patch of [{ locked: true }, { status: 'reset' }, { request_id: '' }, { revision: undefined }]) {
    instance.state.member = { ...member, ...patch };
    instance.renderProfile(); instance.open(); await instance.submit();
    assert.equal(walk(instance.node('member-profile')).find(node => node.textContent === 'Xóa đăng ký').disabled, true);
    assert.equal(instance.node('registration-reset-dialog').open, false);
    assert.equal(instance.calls.length, 0);
  }
});

test('confirmation displays only masked identity, requires reason and checkbox', async () => {
  const instance = app(); instance.open();
  const texts = walk(instance.node('registration-reset-identity')).map(node => node.textContent);
  assert.ok(texts.includes(member.name)); assert.ok(texts.includes(member.phone_masked));
  assert.equal(texts.includes(member.phone), false);
  instance.node('registration-reset-reason').value = 'a'; await instance.submit();
  assert.equal(instance.calls.length, 0);
  instance.node('registration-reset-reason').value = 'Cho đăng ký lại đúng.';
  instance.node('registration-reset-verified').checked = false; await instance.submit();
  assert.equal(instance.calls.length, 0);
  assert.match(instance.node('registration-reset-error').textContent, /Cần xác nhận/);
});

test('reset sends exact snapshot once with CSRF, then refreshes server list and detail', async () => {
  let finish;
  const instance = app(url => url.endsWith('/reset') ? new Promise(resolve => { finish = resolve; })
    : response(url.startsWith('/api/members') ? { items: [], total: 0 } : { member: { ...member, status: 'reset', revision: 5 } }));
  instance.open(); const pending = instance.submit(); await instance.submit();
  assert.equal(instance.calls.length, 1);
  assert.equal(instance.node('registration-reset-submit').disabled, true);
  assert.equal(instance.state.member.status, 'approved', 'no optimistic revocation before response');
  const call = instance.calls[0];
  assert.equal(call.url, '/api/member/registration/reset');
  assert.equal(call.options.headers['X-CSRF-Token'], 'synthetic-csrf');
  assert.deepEqual(JSON.parse(call.options.body), { member_id: member.member_id, request_id: member.request_id, expected_revision: 4, reason: 'Thành viên muốn đăng ký lại.', confirm: true });
  finish(response({ ok: true })); await pending;
  assert.equal(instance.node('registration-reset-dialog').open, false);
  assert.equal(instance.node('registration-reset-reason').value, '');
  assert.equal(instance.state.member.status, 'reset');
  assert.ok(instance.calls.some(call => call.url.startsWith('/api/members?')));
  assert.ok(instance.calls.some(call => call.url.startsWith('/api/member?id=')));
  assert.equal(walk(instance.node('member-profile')).find(node => node.textContent === 'Xóa đăng ký').disabled, true);
});

test('stale snapshot and network failure need reload with no automatic retry', async () => {
  for (const result of [() => response({ error: 'Đăng ký đã thay đổi.' }, 409), () => Promise.reject(new Error('secret network details'))]) {
    const instance = app(result); instance.open(); await instance.submit(); await instance.submit();
    assert.equal(instance.calls.length, 1);
    assert.equal(instance.state.member.status, 'approved');
    assert.equal(instance.node('registration-reset-submit').hidden, true);
    assert.equal(instance.node('registration-reset-reload').hidden, false);
    assert.match(instance.node('registration-reset-error').textContent, /Tải lại hồ sơ/);
    assert.equal(instance.node('registration-reset-error').textContent.includes('secret network'), false);
  }
});

test('closing a pending reset cannot repaint another profile or open a second reset', async () => {
  let finish; const instance = app(() => new Promise(resolve => { finish = resolve; }));
  instance.open(); const pending = instance.submit();
  instance.node('registration-reset-dialog').close();
  instance.state.member = { ...member, member_id: 'different-person' };
  instance.openRegistrationReset(); assert.equal(instance.node('registration-reset-dialog').open, false);
  finish(response({ ok: true })); await pending;
  assert.equal(instance.state.member.member_id, 'different-person');
  assert.equal(instance.node('registration-reset-identity').children.length, 0);
  assert.equal(instance.calls.length, 1);
});

test('session expiry clears the reset reason and member preview', async () => {
  const instance = app(() => response({ error: 'Đăng nhập lại.' }, 401));
  instance.open(); await instance.submit();
  assert.equal(instance.state.user, null);
  assert.equal(instance.node('registration-reset-dialog').open, false);
  assert.equal(instance.node('registration-reset-reason').value, '');
  assert.equal(instance.node('registration-reset-identity').children.length, 0);
});

test('markup exposes reset status and makes scope plus re-registration explicit', () => {
  assert.match(html, /<option value="reset">Đã xóa đăng ký<\/option>/);
  assert.match(html, /<option value="approved">Đã duyệt<\/option>/);
  assert.match(html, /Workload và lịch trên Sheets không thay đổi/);
  assert.match(html, /id="registration-reset-verified" type="checkbox" required/);
});
