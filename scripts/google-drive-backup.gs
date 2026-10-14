/**
 * LabCoach encrypted backups: copy into one private, standalone Apps Script.
 * Set the three Script Properties documented in docs/backup-google-drive.md.
 * No web-app deployment, plaintext backup, key, or token belongs in this file.
 */
const LABCOACH_BACKUP_SOURCE_ = 'https://hermes-zalo-bot-production.up.railway.app/api/backup';
const LABCOACH_BACKUP_ZONE_ = 'Asia/Ho_Chi_Minh';
const LABCOACH_BACKUP_MAX_BYTES_ = 45 * 1024 * 1024;
const LABCOACH_BACKUP_RETAIN_ = 7;
const LABCOACH_BACKUP_MARKER_ = 'labcoach-encrypted-backup/v1';

/** Authorize and install one daily trigger owned by the current Google user. */
function setupLabCoachBackup() {
  return labCoachSafeRun_(function () {
    const lock = LockService.getScriptLock();
    if (!lock.tryLock(1000)) labCoachFail_('BUSY');
    try {
      const config = labCoachConfig_();
      labCoachFolder_(config);
      const previous = ScriptApp.getProjectTriggers().filter(function (trigger) {
        return trigger.getHandlerFunction() === 'backupLabCoachDaily';
      });
      // Create first: a failed creation does not remove a working schedule.
      ScriptApp.newTrigger('backupLabCoachDaily').timeBased().everyDays(1)
        .atHour(2).nearMinute(0).inTimezone(LABCOACH_BACKUP_ZONE_).create();
      previous.forEach(function (trigger) { ScriptApp.deleteTrigger(trigger); });
      console.info('LabCoach: daily backup trigger configured; no backup uploaded by setup.');
    } finally {
      lock.releaseLock();
    }
  });
}

/** Can also be run manually after Restricted sharing and backend are ready. */
function backupLabCoachDaily() {
  return labCoachSafeRun_(function () {
    const lock = LockService.getScriptLock();
    if (!lock.tryLock(1000)) labCoachFail_('BUSY');
    try {
      const config = labCoachConfig_();
      const folder = labCoachFolder_(config);
      const day = Utilities.formatDate(new Date(), LABCOACH_BACKUP_ZONE_, 'yyyy-MM-dd');
      const name = 'LabCoach-backup-' + day + '.lcbak';
      const existing = folder.getFilesByName(name);
      if (existing.hasNext()) {
        const file = existing.next();
        const marker = labCoachMarker_(file, config);
        if (existing.hasNext() || !marker || marker.date !== day) labCoachFail_('DAILY_FILE_CONFLICT');
        labCoachVerifyFile_(file, marker.sha256, marker.bytes);
        console.info('LabCoach: today already has a verified encrypted backup.');
        return;
      }

      const response = UrlFetchApp.fetch(config.url, {
        method: 'get',
        headers: { 'X-LabCoach-Backup-Key': config.token, 'Accept': 'application/octet-stream' },
        followRedirects: false,
        validateHttpsCertificates: true,
        muteHttpExceptions: true
      });
      if (response.getResponseCode() !== 200) labCoachFail_('BACKEND_HTTP_ERROR');
      const headers = response.getAllHeaders();
      const type = labCoachHeader_(headers, 'Content-Type').split(';')[0].trim().toLowerCase();
      if (type !== 'application/octet-stream') labCoachFail_('INVALID_CONTENT_TYPE');
      const digest = labCoachHeader_(headers, 'X-LabCoach-Backup-SHA256').toLowerCase();
      if (!/^[0-9a-f]{64}$/.test(digest)) labCoachFail_('INVALID_HASH_HEADER');
      const declared = labCoachHeader_(headers, 'Content-Length', true);
      if (declared && (!/^\d+$/.test(declared) || Number(declared) >= LABCOACH_BACKUP_MAX_BYTES_)) {
        labCoachFail_('INVALID_SIZE');
      }
      const bytes = response.getContent();
      labCoachCheckSize_(bytes.length);
      if (declared && Number(declared) !== bytes.length) labCoachFail_('INVALID_SIZE');
      if (labCoachSha256_(bytes) !== digest) labCoachFail_('DOWNLOAD_HASH_MISMATCH');

      // Recheck sharing after the network call, immediately before creating a file.
      labCoachRestricted_(folder);
      const file = folder.createFile(Utilities.newBlob(bytes, 'application/octet-stream', name));
      // A partial attempt is intentionally retained for inspection, never promoted
      // to a successful backup or automatically overwritten on a later retry.
      file.setDescription(JSON.stringify({
        app: LABCOACH_BACKUP_MARKER_, script: config.scriptId, source: config.url,
        fileId: file.getId(), date: day, state: 'pending', sha256: digest, bytes: bytes.length
      }));
      labCoachVerifyFile_(file, digest, bytes.length);
      labCoachRestricted_(folder);
      file.setDescription(JSON.stringify({
        app: LABCOACH_BACKUP_MARKER_, script: config.scriptId, source: config.url,
        fileId: file.getId(), date: day, state: 'verified', sha256: digest, bytes: bytes.length
      }));
      // Retention is reached only after a new upload was read back and verified.
      labCoachPrune_(folder, config, file.getId());
      console.info('LabCoach: encrypted backup uploaded and verified; retention complete.');
    } finally {
      lock.releaseLock();
    }
  });
}

/** Remove only this function's triggers owned by the current Google user. */
function stopLabCoachBackup() {
  return labCoachSafeRun_(function () {
    const lock = LockService.getScriptLock();
    if (!lock.tryLock(1000)) labCoachFail_('BUSY');
    try {
      ScriptApp.getProjectTriggers().forEach(function (trigger) {
        if (trigger.getHandlerFunction() === 'backupLabCoachDaily') ScriptApp.deleteTrigger(trigger);
      });
      console.info('LabCoach: daily trigger stopped; existing backups unchanged.');
    } finally {
      lock.releaseLock();
    }
  });
}

function labCoachConfig_() {
  const properties = PropertiesService.getScriptProperties();
  const url = properties.getProperty('LABCOACH_BACKUP_URL');
  const token = properties.getProperty('LABCOACH_BACKUP_ACCESS_TOKEN');
  const folderId = properties.getProperty('LABCOACH_BACKUP_FOLDER_ID');
  if (url !== LABCOACH_BACKUP_SOURCE_) labCoachFail_('INVALID_SOURCE_URL');
  if (typeof token !== 'string' || !/^[\x21-\x7e]{32,256}$/.test(token)) labCoachFail_('INVALID_ACCESS_TOKEN');
  if (typeof folderId !== 'string' || !/^[a-zA-Z0-9_-]{10,200}$/.test(folderId)) labCoachFail_('INVALID_FOLDER_ID');
  return { url: url, token: token, folderId: folderId, scriptId: ScriptApp.getScriptId() };
}

function labCoachFolder_(config) {
  const folder = DriveApp.getFolderById(config.folderId);
  if (folder.isTrashed()) labCoachFail_('FOLDER_TRASHED');
  labCoachRestricted_(folder);
  return folder;
}

function labCoachRestricted_(item) {
  // PRIVATE still permits explicitly invited collaborators; it rejects public
  // links and domain-wide access. It does not change any sharing permissions.
  if (item.getSharingAccess() !== DriveApp.Access.PRIVATE) labCoachFail_('FOLDER_NOT_RESTRICTED');
}

function labCoachHeader_(headers, name, optional) {
  const keys = Object.keys(headers).filter(function (key) { return key.toLowerCase() === name.toLowerCase(); });
  if (!keys.length && optional) return '';
  if (keys.length !== 1 || Array.isArray(headers[keys[0]])) labCoachFail_('INVALID_HEADERS');
  return String(headers[keys[0]]).trim();
}

function labCoachCheckSize_(size) {
  if (!Number.isSafeInteger(size) || size <= 0 || size >= LABCOACH_BACKUP_MAX_BYTES_) labCoachFail_('INVALID_SIZE');
}

function labCoachSha256_(bytes) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, bytes).map(function (value) {
    return ((value + 256) % 256).toString(16).padStart(2, '0');
  }).join('');
}

function labCoachVerifyFile_(file, digest, size) {
  labCoachCheckSize_(size);
  if (file.isTrashed() || file.getSize() !== size || file.getMimeType() !== 'application/octet-stream') {
    labCoachFail_('DRIVE_VERIFICATION_FAILED');
  }
  labCoachRestricted_(file);
  const stored = file.getBlob().getBytes();
  if (stored.length !== size || labCoachSha256_(stored) !== digest) labCoachFail_('DRIVE_HASH_MISMATCH');
}

function labCoachMarker_(file, config) {
  if (file.isTrashed() || file.getMimeType() !== 'application/octet-stream') return null;
  const match = /^LabCoach-backup-(\d{4}-\d{2}-\d{2})\.lcbak$/.exec(file.getName());
  if (!match) return null;
  let marker;
  try { marker = JSON.parse(file.getDescription()); } catch (_) { return null; }
  if (!marker || marker.app !== LABCOACH_BACKUP_MARKER_ || marker.script !== config.scriptId ||
      marker.source !== config.url || marker.fileId !== file.getId() ||
      marker.state !== 'verified' || marker.date !== match[1] ||
      !/^[0-9a-f]{64}$/.test(marker.sha256) || !Number.isSafeInteger(marker.bytes) ||
      marker.bytes <= 0 || marker.bytes >= LABCOACH_BACKUP_MAX_BYTES_) return null;
  const date = new Date(match[1] + 'T00:00:00Z');
  if (isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== match[1]) return null;
  return marker;
}

function labCoachInFolder_(file, config) {
  const parents = file.getParents();
  while (parents.hasNext()) {
    if (parents.next().getId() === config.folderId) return true;
  }
  return false;
}

function labCoachPrune_(folder, config, newestId) {
  labCoachRestricted_(folder);
  const iterator = folder.getFiles();
  const own = [];
  while (iterator.hasNext()) {
    const file = iterator.next();
    const marker = labCoachMarker_(file, config);
    if (marker && labCoachInFolder_(file, config)) {
      own.push({ file: file, date: marker.date, digest: marker.sha256, bytes: marker.bytes });
    }
  }
  if (!own.some(function (item) { return item.file.getId() === newestId; })) labCoachFail_('NEW_BACKUP_NOT_FOUND');
  own.sort(function (a, b) {
    return b.date.localeCompare(a.date) || a.file.getId().localeCompare(b.file.getId());
  });
  const remove = own.slice(LABCOACH_BACKUP_RETAIN_);
  // Never remove the just-verified backup, even if old metadata contains a future date.
  if (remove.some(function (item) { return item.file.getId() === newestId; })) labCoachFail_('RETENTION_DATE_CONFLICT');
  remove.forEach(function (item) {
    labCoachRestricted_(folder);
    const current = labCoachMarker_(item.file, config);
    if (!current || !labCoachInFolder_(item.file, config) || current.date !== item.date ||
        current.sha256 !== item.digest || current.bytes !== item.bytes) {
      labCoachFail_('RETENTION_METADATA_CHANGED');
    }
    item.file.setTrashed(true);
  });
}

function labCoachFail_(code) {
  const error = new Error('LabCoach backup: ' + code);
  error.labCoachSafeCode = code;
  throw error;
}

function labCoachSafeRun_(operation) {
  try {
    return operation();
  } catch (error) {
    // Native HTTP/Drive exceptions may contain response data or identifiers.
    // Throw a short safe error so Apps Script's normal failure emails still work.
    const code = error && error.labCoachSafeCode || 'OPERATION_FAILED';
    throw new Error('LabCoach backup: ' + code + '. Xem docs/backup-google-drive.md.');
  }
}
