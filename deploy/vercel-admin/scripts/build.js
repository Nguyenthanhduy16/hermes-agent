/** Copy only the reviewed frontend assets. No bundler/env substitution. */
import { lstat, mkdir, readdir, copyFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const project = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sourceDirectory = resolve(project, '../../src/hermes_zalo/admin_static');
const files = Object.freeze([
  ['console.html', 'index.html'],
  ['index.html', 'legacy.html'],
  ['admin.css', 'static/admin.css'],
  ['admin.js', 'static/admin.js'],
  ['console.css', 'static/console.css'],
  ['console.js', 'static/console.js'],
  ...['observe', 'bench', 'integrations', 'operations', 'briefing'].flatMap(name => [
    [`console-${name}.js`, `static/console-${name}.js`], [`console-${name}.css`, `static/console-${name}.css`]]),
]);

async function inspectOutput(root, relative = '') {
  let entries;
  try { entries = await readdir(join(root, relative), { withFileTypes: true }); } catch (error) {
    if (error.code === 'ENOENT') return;
    throw error;
  }
  const allowed = new Set(files.map(([, target]) => target));
  for (const entry of entries) {
    const name = relative ? relative + '/' + entry.name : entry.name;
    if (entry.isSymbolicLink() || (entry.isDirectory() ? name !== 'static' : !allowed.has(name))) {
      throw new Error('Public output contains unexpected files; review it before building.');
    }
    if (entry.isDirectory()) await inspectOutput(root, name);
  }
}

export async function build({ source = sourceDirectory, output = join(project, 'public') } = {}) {
  try {
    const outputStat = await lstat(output);
    if (!outputStat.isDirectory() || outputStat.isSymbolicLink()) throw new Error('Output must be a plain directory.');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  await inspectOutput(output);
  for (const [name] of files) {
    let stat;
    try { stat = await lstat(join(source, name)); } catch {
      throw new Error('Frontend source missing. Enable Include source files outside the Root Directory in Vercel settings.');
    }
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('Frontend assets must be regular files.');
  }
  await mkdir(join(output, 'static'), { recursive: true });
  for (const [name, target] of files) await copyFile(join(source, name), join(output, target));
  return files.map(([, target]) => target);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  build().then(() => process.stdout.write('Built ' + files.length + ' Admin assets; no environment values or runtime data copied.\n'))
    .catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
}
