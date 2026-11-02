import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../../../src/hermes_zalo/admin_static/admin.js', import.meta.url), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.listeners = {};
    this.value = ''; this.checked = false; this.hidden = false; this.open = false;
    this.disabled = false; this.classList = { add() {}, toggle() {} };
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  focus() {}
  reset() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
}
const walk = node => [node, ...node.children.flatMap(walk)];
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const member = { member_id: 'member-before', name: 'Nguyễn An', phone_masked: '******0001', zalo_user_id: 'zalo-synthetic', private_chat_id: 'dm-synthetic', status: 'approved' };
const preview = { preview_token: 'synthetic-preview', before: member, after: { member_id: 'member-after', name: 'Trần Bình', phone_masked: '******0002' }, message: 'Thay đổi danh tính đã được kiểm tra.' };

function app(fetchImpl = () => response(preview)) {
  const nodes = new Map(), calls = [];
  const document = {
    createElement: tag => new Element(tag),
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    querySelectorAll: selector => selector === 'dialog[open]' ? [...nodes.values()].filter(node => node.open) : [],
  };
  const context = vm.createContext({ document, URLSearchParams, setTimeout: () => 0, clearTimeout() {},
    fetch: async (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); },
  });
  vm.runInContext(source.replace('  init();', '  globalThis.testApp={state,openRegistrationRepair,renderProfile,showLogin};'), context);
  const api = context.testApp;
  api.state.user = { username: 'synthetic-admin', permissions: ['members'] };
  api.state.member = { ...member }; api.state.csrf = 'synthetic-csrf';
  const node = id => document.getElementById(id);
  const open = () => { api.openRegistrationRepair(); node('registration-repair-phone').value = '0900000002'; node('registration-repair-reason').value = 'Đã xác minh chủ tài khoản.'; };
  const submit = () => node('registration-repair-form').listeners.submit({ preventDefault() {} });
  return { ...api, node, calls, open, submit };
}

test('button and dialog require members permission for both admin and faculty', async () => {
  const instance = app();
  for (const role of ['admin', 'faculty']) {
    instance.state.user = { role, permissions: ['issues'] };
    instance.renderProfile();
    assert.equal(walk(instance.node('member-profile')).some(node => node.textContent === 'Correct registration'), false);
    instance.openRegistrationRepair();
    assert.equal(instance.node('registration-repair-dialog').open, false);
    await instance.submit();
    assert.equal(instance.calls.length, 0);
  }
  instance.state.user.permissions.push('members');
  instance.renderProfile();
  assert.equal(walk(instance.node('member-profile')).some(node => node.textContent === 'Correct registration'), true);
});

test('preview is read before confirmation, only masked identity is displayed and phone is cleared', async () => {
  const instance = app(() => response({ ...preview, after: { ...preview.after, phone: '0900000002' } }));
  instance.open();
  await instance.submit();
  assert.equal(instance.calls.length, 1);
  assert.equal(instance.calls[0].url, '/api/member/registration/preview');
  assert.equal(instance.calls[0].options.headers['X-CSRF-Token'], 'synthetic-csrf');
  assert.deepEqual(JSON.parse(instance.calls[0].options.body), { member_id: 'member-before', phone: '0900000002', reason: 'Đã xác minh chủ tài khoản.' });
  assert.equal(instance.node('registration-repair-phone').value, '');
  assert.equal(instance.node('registration-repair-phone').disabled, true);
  const texts = walk(instance.node('registration-repair-preview')).map(node => node.textContent).filter(Boolean);
  assert.ok(texts.includes('Trần Bình'));
  assert.ok(texts.includes('******0002'));
  assert.ok(texts.includes('dm-synthetic'));
  assert.equal(texts.includes('0900000002'), false);
  assert.equal(instance.node('registration-repair-verified').required, true);
  await instance.submit();
  assert.equal(instance.calls.length, 1, 'unchecked identity verification must not confirm');
  assert.match(instance.node('registration-repair-error').textContent, /verified the Zalo account owner/);
});

test('confirmation sends token only once and reloads corrected member after success', async () => {
  let finish;
  const instance = app((url) => url.endsWith('/preview') ? response(preview)
    : url.endsWith('/confirm') ? new Promise(resolve => { finish = resolve; })
    : response({ member: { ...member, member_id: 'member-after', name: 'Trần Bình' } }));
  instance.open(); await instance.submit();
  instance.node('registration-repair-verified').checked = true;
  const pending = instance.submit();
  await instance.submit();
  assert.equal(instance.calls.filter(call => call.url.endsWith('/confirm')).length, 1);
  assert.deepEqual(JSON.parse(instance.calls[1].options.body), { preview_token: 'synthetic-preview', confirm: true });
  assert.equal(instance.node('registration-repair-submit').disabled, true);
  finish(response({ member_id: 'member-after', name: 'Trần Bình', message: 'Đã sửa đăng ký.' }));
  await pending;
  assert.equal(instance.node('registration-repair-dialog').open, false);
  assert.equal(instance.node('registration-repair-phone').value, '');
  assert.equal(instance.node('registration-repair-reason').value, '');
  assert.equal(instance.state.member.member_id, 'member-after');
  assert.match(instance.calls.at(-1).url, /id=member-after/);
});

test('preview errors clear phone and preserve no confirmable token', async () => {
  const instance = app(() => response({ error: 'Số này không có trong Workload.' }, 409));
  instance.open(); await instance.submit();
  assert.equal(instance.node('registration-repair-phone').value, '');
  assert.equal(instance.node('registration-repair-phone').disabled, false);
  assert.match(instance.node('registration-repair-error').textContent, /Workload/);
  await instance.submit();
  assert.equal(instance.calls.length, 1);
  assert.equal(instance.node('registration-repair-preview').hidden, true);
});

test('confirmation timeout is not retried and requires reloading the profile', async () => {
  const instance = app(url => url.endsWith('/preview') ? response(preview) : Promise.reject(new Error('private network details')));
  instance.open(); await instance.submit(); instance.node('registration-repair-verified').checked = true;
  await instance.submit();
  assert.equal(instance.node('registration-repair-submit').hidden, true);
  assert.equal(instance.node('registration-repair-reload').hidden, false);
  assert.match(instance.node('registration-repair-error').textContent, /Reload the profile/);
  assert.equal(instance.node('registration-repair-error').textContent.includes('private network'), false);
  await instance.submit();
  assert.equal(instance.calls.length, 2);
});

test('closing while preview is pending cannot repopulate sensitive dialog', async () => {
  let finish;
  const instance = app(() => new Promise(resolve => { finish = resolve; }));
  instance.open(); const pending = instance.submit();
  instance.node('registration-repair-dialog').close();
  finish(response(preview)); await pending;
  assert.equal(instance.node('registration-repair-dialog').open, false);
  assert.equal(instance.node('registration-repair-preview').hidden, true);
  assert.equal(instance.node('registration-repair-preview').children.length, 0);
  assert.equal(instance.node('registration-repair-phone').value, '');
  assert.equal(instance.node('registration-repair-reason').value, '');
});

test('session revocation clears pending phone, reason and identity preview', async () => {
  const instance = app(() => response({ error: 'Please sign in again.' }, 401));
  instance.open(); await instance.submit();
  assert.equal(instance.state.user, null);
  assert.equal(instance.node('registration-repair-dialog').open, false);
  assert.equal(instance.node('registration-repair-phone').value, '');
  assert.equal(instance.node('registration-repair-reason').value, '');
  assert.equal(instance.node('registration-repair-preview').children.length, 0);
});
