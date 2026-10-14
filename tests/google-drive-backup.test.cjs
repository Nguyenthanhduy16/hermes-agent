'use strict';
// node --test tests/google-drive-backup.test.cjs — no real Google/Railway calls.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../scripts/google-drive-backup.gs'), 'utf8');
const source = 'https://hermes-zalo-bot-production.up.railway.app/api/backup';
const folderId = 'synthetic_folder_id';
const scriptId = 'synthetic_script_id';
const today = '2026-10-15';
const payload = Buffer.from('synthetic encrypted backup bytes; no real data');
const hash = bytes => crypto.createHash('sha256').update(Buffer.from(bytes)).digest('hex');
const iterator = items => {
  let index = 0;
  return { hasNext: () => index < items.length, next: () => items[index++] };
};

function fixture(options = {}) {
  const state = { files: [], requests: [], logs: [], uploads: 0, released: 0, triggers: [], createdTriggers: [], deletedTriggers: [] };
  state.props = {
    LABCOACH_BACKUP_URL: source,
    LABCOACH_BACKUP_ACCESS_TOKEN: 'synthetic-token-not-real-0123456789abcdef',
    LABCOACH_BACKUP_FOLDER_ID: folderId,
    ...options.props,
  };
  const folder = {
    access: options.access || 'PRIVATE',
    isTrashed: () => false,
    getId: () => folderId,
    getSharingAccess() { return this.access; },
    getFilesByName: name => iterator(state.files.filter(file => !file.trashed && file.name === name)),
    getFiles: () => iterator(state.files.filter(file => !file.trashed)),
    createFile(blob) {
      state.uploads++;
      const file = makeFile(blob.name, { bytes: blob.bytes });
      if (options.corruptUpload) file.bytes = Buffer.from('wrong bytes');
      return file;
    },
  };
  function makeFile(name, settings = {}) {
    const file = {
      name, id: 'file-' + (state.files.length + 1), bytes: Buffer.from(settings.bytes || payload),
      description: '', trashed: false, access: 'PRIVATE', parent: folderId,
      getId() { return this.id; }, getName() { return this.name; },
      isTrashed() { return this.trashed; }, getSize() { return this.bytes.length; },
      getMimeType: () => settings.mime || 'application/octet-stream',
      getSharingAccess() { return this.access; },
      getBlob() { return { getBytes: () => Array.from(this.bytes) }; },
      getDescription() { return this.description; },
      setDescription(value) { this.description = value; return this; },
      setTrashed(value) { this.trashed = value; return this; },
      getParents() { return iterator([{ getId: () => this.parent }]); },
    };
    state.files.push(file);
    if (settings.own) {
      file.description = JSON.stringify({
        app: 'labcoach-encrypted-backup/v1', script: scriptId, source,
        fileId: file.id, date: name.slice(16, 26), state: 'verified',
        sha256: hash(file.bytes), bytes: file.bytes.length, ...settings.marker,
      });
    }
    return file;
  }
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : ['2026-10-14T19:00:00Z'])); }
  }
  const context = {
    Date: FixedDate,
    console: { info: message => state.logs.push(message) },
    PropertiesService: { getScriptProperties: () => ({ getProperty: name => state.props[name] ?? null }) },
    LockService: { getScriptLock: () => ({ tryLock: () => options.lock !== false, releaseLock: () => state.released++ }) },
    DriveApp: { Access: { PRIVATE: 'PRIVATE' }, getFolderById: id => {
      assert.equal(id, folderId);
      return folder;
    } },
    Utilities: {
      DigestAlgorithm: { SHA_256: 'SHA_256' },
      formatDate: (date, zone, pattern) => {
        assert.equal(zone, 'Asia/Ho_Chi_Minh');
        assert.equal(pattern, 'yyyy-MM-dd');
        return new Date(date.getTime() + 7 * 3600000).toISOString().slice(0, 10);
      },
      computeDigest: (algorithm, bytes) => {
        assert.equal(algorithm, 'SHA_256');
        return Array.from(crypto.createHash('sha256').update(Buffer.from(bytes)).digest(), n => n > 127 ? n - 256 : n);
      },
      newBlob: (bytes, type, name) => ({ bytes, type, name }),
    },
    UrlFetchApp: { fetch: (url, params) => {
      state.requests.push({ url, params });
      if (options.fetchError) throw new Error(options.fetchError);
      if (options.afterFetch) options.afterFetch(folder);
      return {
        getResponseCode: () => options.status || 200,
        getAllHeaders: () => ({ 'Content-Type': 'application/octet-stream',
          'X-LabCoach-Backup-SHA256': hash(payload), 'Content-Length': String(payload.length), ...options.headers }),
        getContent: () => options.content || Array.from(payload),
      };
    } },
    ScriptApp: {
      getScriptId: () => scriptId,
      getProjectTriggers: () => state.triggers.slice(),
      deleteTrigger: trigger => {
        state.deletedTriggers.push(trigger);
        state.triggers = state.triggers.filter(item => item !== trigger);
      },
      newTrigger: handler => {
        const settings = { handler };
        const builder = {};
        for (const method of ['timeBased', 'everyDays', 'atHour', 'nearMinute', 'inTimezone']) {
          builder[method] = value => { settings[method] = value ?? true; return builder; };
        }
        builder.create = () => {
          state.createdTriggers.push(settings);
          const trigger = { getHandlerFunction: () => handler };
          state.triggers.push(trigger);
          return trigger;
        };
        return builder;
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(script, context, { filename: 'google-drive-backup.gs' });
  return { state, context, folder, makeFile };
}

test('setup replaces only this user/function triggers, no fetch or upload', () => {
  const f = fixture();
  const ours = { getHandlerFunction: () => 'backupLabCoachDaily' };
  const other = { getHandlerFunction: () => 'someOtherBackup' };
  f.state.triggers.push(ours, other);
  f.context.setupLabCoachBackup();
  assert.deepEqual(f.state.deletedTriggers, [ours]);
  assert.ok(f.state.triggers.includes(other));
  assert.deepEqual(f.state.createdTriggers, [{ handler: 'backupLabCoachDaily', timeBased: true,
    everyDays: 1, atHour: 2, nearMinute: 0, inTimezone: 'Asia/Ho_Chi_Minh' }]);
  assert.equal(f.state.requests.length, 0);
  assert.equal(f.state.uploads, 0);
});

test('public/domain folder prevents setup and backup, including existing daily file', () => {
  for (const access of ['ANYONE', 'ANYONE_WITH_LINK', 'DOMAIN', 'DOMAIN_WITH_LINK']) {
    const f = fixture({ access });
    f.makeFile('LabCoach-backup-' + today + '.lcbak', { own: true });
    assert.throws(() => f.context.setupLabCoachBackup(), /FOLDER_NOT_RESTRICTED/);
    assert.throws(() => f.context.backupLabCoachDaily(), /FOLDER_NOT_RESTRICTED/);
    assert.equal(f.state.requests.length, 0);
    assert.equal(f.state.uploads, 0);
    assert.equal(f.state.createdTriggers.length, 0);
  }
});

test('rejects URL changes, credentials, queries and HTTP before requesting', () => {
  for (const url of [source + '?token=secret', source + '/', source.replace('https:', 'http:'),
    source.replace('hermes-zalo-bot-production', 'other-service'), source.replace('https://', 'https://user:secret@')]) {
    const f = fixture({ props: { LABCOACH_BACKUP_URL: url } });
    assert.throws(() => f.context.backupLabCoachDaily(), /INVALID_SOURCE_URL/);
    assert.equal(f.state.requests.length, 0);
  }
});

test('rejects missing or header-unsafe token and invalid folder ID', () => {
  for (const token of ['', 'short', 'x'.repeat(32) + '\nheader', 'x'.repeat(257)]) {
    const f = fixture({ props: { LABCOACH_BACKUP_ACCESS_TOKEN: token } });
    assert.throws(() => f.context.backupLabCoachDaily(), /INVALID_ACCESS_TOKEN/);
    assert.equal(f.state.requests.length, 0);
  }
  assert.throws(() => fixture({ props: { LABCOACH_BACKUP_FOLDER_ID: '../folder' } }).context.backupLabCoachDaily(), /INVALID_FOLDER_ID/);
});

test('uploads encrypted bytes once for Vietnam day with dedicated header, no redirects, verified marker', () => {
  const f = fixture();
  f.context.backupLabCoachDaily();
  assert.equal(f.state.uploads, 1);
  const request = f.state.requests[0];
  assert.equal(request.url, source);
  assert.equal(request.params.headers['X-LabCoach-Backup-Key'], f.state.props.LABCOACH_BACKUP_ACCESS_TOKEN);
  assert.equal(request.params.followRedirects, false);
  assert.equal(request.params.validateHttpsCertificates, true);
  assert.equal(f.state.files[0].name, 'LabCoach-backup-' + today + '.lcbak');
  assert.equal(JSON.parse(f.state.files[0].description).state, 'verified');
  f.context.backupLabCoachDaily();
  assert.equal(f.state.requests.length, 1);
  assert.equal(f.state.uploads, 1);
  assert.equal(f.state.released, 2);
  assert.ok(f.state.logs.every(line => !line.includes(f.state.props.LABCOACH_BACKUP_ACCESS_TOKEN)));
});

test('HTTP failure or redirect does not create a file', () => {
  for (const status of [301, 302, 401, 429, 500]) {
    const f = fixture({ status });
    assert.throws(() => f.context.backupLabCoachDaily(), /BACKEND_HTTP_ERROR/);
    assert.equal(f.state.requests.length, 1);
    assert.equal(f.state.uploads, 0);
  }
});

test('content type, hash headers and duplicate headers must be exact', () => {
  for (const [headers, error] of [
    [{ 'Content-Type': 'application/json' }, 'INVALID_CONTENT_TYPE'],
    [{ 'X-LabCoach-Backup-SHA256': '0'.repeat(64) }, 'DOWNLOAD_HASH_MISMATCH'],
    [{ 'X-LabCoach-Backup-SHA256': ['a'.repeat(64)] }, 'INVALID_HEADERS'],
    [{ 'x-labcoach-backup-sha256': 'a'.repeat(64) }, 'INVALID_HEADERS'],
  ]) {
    const f = fixture({ headers });
    assert.throws(() => f.context.backupLabCoachDaily(), new RegExp(error));
    assert.equal(f.state.uploads, 0);
  }
});

test('strict size cap, malformed and mismatched lengths prevent upload', () => {
  for (const length of ['0', '-1', 'not-a-number', String(45 * 1024 * 1024), String(payload.length + 1)]) {
    const f = fixture({ headers: { 'Content-Length': length } });
    assert.throws(() => f.context.backupLabCoachDaily(), /INVALID_SIZE/);
    assert.equal(f.state.uploads, 0);
  }
  const f = fixture({ headers: { 'Content-Length': '' }, content: { length: 45 * 1024 * 1024 } });
  assert.throws(() => f.context.backupLabCoachDaily(), /INVALID_SIZE/);
  assert.equal(f.state.uploads, 0);
});

test('sharing rechecked after download, before creating a file', () => {
  const f = fixture({ afterFetch: folder => { folder.access = 'ANYONE_WITH_LINK'; } });
  assert.throws(() => f.context.backupLabCoachDaily(), /FOLDER_NOT_RESTRICTED/);
  assert.equal(f.state.uploads, 0);
});

test('readback mismatch leaves pending file and never prunes previous backups', () => {
  const f = fixture({ corruptUpload: true });
  for (let day = 1; day <= 8; day++) f.makeFile(`LabCoach-backup-2026-10-${String(day).padStart(2, '0')}.lcbak`, { own: true });
  assert.throws(() => f.context.backupLabCoachDaily(), /DRIVE_VERIFICATION_FAILED/);
  assert.equal(f.state.files.filter(file => file.trashed).length, 0);
  assert.equal(JSON.parse(f.state.files.at(-1).description).state, 'pending');
  assert.throws(() => f.context.backupLabCoachDaily(), /DAILY_FILE_CONFLICT/);
  assert.equal(f.state.uploads, 1);
});

test('retains seven own verified files; leaves foreign, copied, pending and unrelated files untouched', () => {
  const f = fixture();
  const originals = [];
  for (let day = 1; day <= 8; day++) originals.push(f.makeFile(`LabCoach-backup-2026-10-${String(day).padStart(2, '0')}.lcbak`, { own: true }));
  const untouched = [
    f.makeFile('LabCoach-backup-2026-01-01.lcbak'),
    f.makeFile('LabCoach-backup-2026-01-02.lcbak', { own: true, marker: { script: 'another-script' } }),
    f.makeFile('LabCoach-backup-2026-01-03.lcbak', { own: true, marker: { state: 'pending' } }),
    f.makeFile('notes.txt', { own: true }),
    f.makeFile('LabCoach-backup-2026-02-30.lcbak', { own: true }),
    f.makeFile('LabCoach-backup-2026-01-05.lcbak', { own: true, marker: { source: 'https://other.test' } }),
    f.makeFile('LabCoach-backup-2026-01-06.lcbak', { own: true, marker: { fileId: 'copied-metadata-file-id' } }),
  ];
  f.context.backupLabCoachDaily();
  assert.deepEqual(originals.filter(file => file.trashed), originals.slice(0, 2));
  assert.ok(untouched.every(file => !file.trashed));
  assert.equal(f.state.files.at(-1).trashed, false);
});

test('existing same-name foreign file or multiple copies stop without fetching or overwriting', () => {
  for (const own of [false, true]) {
    const f = fixture();
    f.makeFile('LabCoach-backup-' + today + '.lcbak', { own });
    if (own) f.makeFile('LabCoach-backup-' + today + '.lcbak', { own: true });
    assert.throws(() => f.context.backupLabCoachDaily(), /DAILY_FILE_CONFLICT/);
    assert.equal(f.state.requests.length, 0);
    assert.equal(f.state.uploads, 0);
  }
});

test('existing verified daily file is checked, corruption is not silently accepted', () => {
  const f = fixture();
  const file = f.makeFile('LabCoach-backup-' + today + '.lcbak', { own: true });
  file.bytes[0] ^= 1;
  assert.throws(() => f.context.backupLabCoachDaily(), /DRIVE_HASH_MISMATCH/);
  assert.equal(f.state.requests.length, 0);
});

test('future metadata cannot cause removal of just-created backup', () => {
  const f = fixture();
  for (let day = 1; day <= 8; day++) f.makeFile(`LabCoach-backup-2027-01-${String(day).padStart(2, '0')}.lcbak`, { own: true });
  assert.throws(() => f.context.backupLabCoachDaily(), /RETENTION_DATE_CONFLICT/);
  assert.equal(f.state.files.filter(file => file.trashed).length, 0);
});

test('a backup moved out of configured folder is never trashed', () => {
  const f = fixture();
  const moved = f.makeFile('LabCoach-backup-2026-01-01.lcbak', { own: true });
  moved.parent = 'other_folder';
  for (let day = 1; day <= 7; day++) f.makeFile(`LabCoach-backup-2026-10-${String(day).padStart(2, '0')}.lcbak`, { own: true });
  f.context.backupLabCoachDaily();
  assert.equal(moved.trashed, false);
});

test('lock prevents concurrent requests and file changes', () => {
  const f = fixture({ lock: false });
  assert.throws(() => f.context.backupLabCoachDaily(), /BUSY/);
  assert.equal(f.state.requests.length, 0);
  assert.equal(f.state.uploads, 0);
});

test('native error message and payload are not exposed in errors or logs', () => {
  const secret = 'synthetic-sensitive-sdk-response';
  const f = fixture({ fetchError: secret });
  assert.throws(() => f.context.backupLabCoachDaily(), error =>
    /OPERATION_FAILED/.test(error.message) && !error.message.includes(secret));
  assert.ok(f.state.logs.every(line => !line.includes(secret)));
  assert.equal(f.state.released, 1);
});

test('stop removes only matching triggers, does not change files or fetch', () => {
  const f = fixture();
  const own = { getHandlerFunction: () => 'backupLabCoachDaily' };
  const other = { getHandlerFunction: () => 'unrelated' };
  f.state.triggers.push(own, other);
  f.makeFile('LabCoach-backup-' + today + '.lcbak', { own: true });
  f.context.stopLabCoachBackup();
  assert.deepEqual(f.state.triggers, [other]);
  assert.equal(f.state.files[0].trashed, false);
  assert.equal(f.state.requests.length, 0);
});
