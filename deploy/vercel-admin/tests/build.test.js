import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, readdir, rm, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { build } from '../scripts/build.js';

async function fixture(t) {
  const base = await realpath(tmpdir());
  const root = await mkdtemp(join(base, 'labcoach-vercel-test-'));
  const source = join(root, 'source');
  const output = join(root, 'public');
  await mkdir(source);
  for (const name of ['index.html', 'admin.css', 'admin.js']) await writeFile(join(source, name), 'synthetic ' + name);
  t.after(async () => {
    const checked = await realpath(root);
    assert.ok(checked.startsWith(base + sep) && checked === resolve(root));
    await rm(checked, { recursive: true });
  });
  return { root, source, output };
}

test('build copies exactly three shared assets, with no env expansion or extra source files', async t => {
  const { source, output } = await fixture(t);
  await writeFile(join(source, '.env'), 'LABCOACH_PROXY_SECRET=synthetic-do-not-copy');
  await writeFile(join(source, 'private.sqlite3'), 'synthetic-private-data');
  await writeFile(join(source, 'admin.js'), 'const untouched = "${LABCOACH_PROXY_SECRET}";');
  assert.deepEqual(await build({ source, output }), ['index.html', 'static/admin.css', 'static/admin.js']);
  assert.deepEqual((await readdir(output)).sort(), ['index.html', 'static']);
  assert.deepEqual((await readdir(join(output, 'static'))).sort(), ['admin.css', 'admin.js']);
  assert.equal(await readFile(join(output, 'static/admin.js'), 'utf8'), 'const untouched = "${LABCOACH_PROXY_SECRET}";');
  await build({ source, output });
});

test('build refuses unexpected output so accidental private files cannot be deployed', async t => {
  const { source, output } = await fixture(t);
  await mkdir(output);
  await writeFile(join(output, '.env'), 'synthetic-secret');
  await assert.rejects(build({ source, output }), /unexpected files/);
  assert.equal(await readFile(join(output, '.env'), 'utf8'), 'synthetic-secret');
});

test('missing outside-root sources gives actionable Vercel setting error', async t => {
  const { root, output } = await fixture(t);
  await assert.rejects(build({ source: join(root, 'not-present'), output }), /Include source files outside the Root Directory/);
});
