import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../../../src/hermes_zalo/admin_static/admin.js', import.meta.url), 'utf8');

class Element {
  constructor() { this.value = ''; this.hidden = false; this.open = false; this.listeners = {}; this.children = []; this.disabled = false; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  replaceChildren(...nodes) { this.children = nodes; }
  focus() {}
  reset() {}
  matches() { return false; }
  querySelectorAll() { return []; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
}

function app(fetchImpl) {
  const nodes = new Map(), calls = [];
  const document = {
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
    querySelectorAll(selector) { return selector === 'dialog[open]' ? [...nodes.values()].filter(node => node.open) : []; },
  };
  const context = vm.createContext({ document, URLSearchParams, setTimeout: () => 0, clearTimeout() {},
    fetch: async (url, options) => { calls.push({ url, options }); return fetchImpl(url, options); },
  });
  vm.runInContext(source.replace('  init();', '  globalThis.testApp={state};'), context);
  const state = context.testApp.state;
  state.user = { username: 'synthetic-admin', role: 'admin', permissions: [] };
  state.csrf = 'synthetic-csrf-token';
  const node = id => document.getElementById(id);
  node('change-password').listeners.click();
  node('password-current').value = 'synthetic-old-password';
  node('password-new').value = 'synthetic-new-password';
  node('password-confirm').value = 'synthetic-new-password';
  const submit = () => node('password-form').listeners.submit({ preventDefault() {}, currentTarget: node('password-form') });
  return { node, calls, state, submit };
}

const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data });
const fieldsCleared = instance => ['password-current', 'password-new', 'password-confirm'].forEach(id => assert.equal(instance.node(id).value, ''));

test('own password change sends only password fields and CSRF then returns to login', async () => {
  let finish;
  const instance = app(() => new Promise(resolve => { finish = resolve; }));
  const first = instance.submit();
  await instance.submit();
  assert.equal(instance.calls.length, 1, 'duplicate submit must not send another change');
  const request = instance.calls[0];
  assert.equal(request.url, '/api/password/change');
  assert.equal(request.options.headers['X-CSRF-Token'], 'synthetic-csrf-token');
  assert.deepEqual(JSON.parse(request.options.body), {
    current_password: 'synthetic-old-password', new_password: 'synthetic-new-password', confirm_password: 'synthetic-new-password',
  });
  finish(response({ ok: true }));
  await first;
  assert.equal(instance.state.user, null);
  assert.equal(instance.state.csrf, '');
  assert.equal(instance.node('password-dialog').open, false);
  assert.equal(instance.node('login-view').hidden, false);
  assert.match(instance.node('toast').textContent, /Password changed/);
  fieldsCleared(instance);
});

test('mismatched repeat is explained without calling backend', async () => {
  const instance = app(() => response({ ok: true }));
  instance.node('password-confirm').value = 'synthetic-other-password';
  await instance.submit();
  assert.equal(instance.calls.length, 0);
  assert.match(instance.node('password-error').textContent, /do not match/);
  assert.ok(instance.state.user);
  fieldsCleared(instance);
});

test('rate limited change stays signed in and never repeats a write automatically', async () => {
  const instance = app(() => response({ error: 'Try again in 10 minutes.' }, 429));
  await instance.submit();
  assert.equal(instance.calls.length, 1);
  assert.ok(instance.state.user);
  assert.match(instance.node('password-error').textContent, /10 minutes/);
  fieldsCleared(instance);
});

test('revoked session returns to login and clears all entered passwords', async () => {
  const instance = app(() => response({ error: 'Please sign in to Admin.' }, 401));
  await instance.submit();
  assert.equal(instance.state.user, null);
  assert.equal(instance.node('login-error').hidden, false);
  assert.equal(instance.node('password-dialog').open, false);
  fieldsCleared(instance);
});
