"""Synthetic backup round trips; no access to bot data, network or credentials."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

from hermes_zalo import backup
from hermes_zalo.ai_usage import AIUsageLimit, AIUsageStore


@unittest.skipUnless(Fernet, "optional backup dependency is not installed")
class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.output = self.root / "snapshot.lcbak"
        self.key = Fernet.generate_key()
        self.state_name = backup.STATE_ROOT + "/work-0123456789abcdef.registration.sqlite3"
        self.database(self.state_name)

    def database(self, name, value="synthetic internal value"):
        path = self.data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE records (value TEXT)")
        db.execute("INSERT INTO records VALUES (?)", (value,))
        db.commit()
        db.close()
        return path

    def fake_archive(self, files, *, details=None, extra_infos=()):
        manifest = dict(format="labcoach-backup", version=1, cross_database_atomic=False,
                        files={name: dict(size=len(value), sha256=hashlib.sha256(value).hexdigest())
                               for name, value in files.items()})
        if details is not None:
            details(manifest)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in files.items():
                archive.writestr(name, value)
            for info, value in extra_infos:
                archive.writestr(info, value)
            archive.writestr("manifest.json", json.dumps(manifest))
        self.output.write_bytes(backup.MAGIC + Fernet(self.key).encrypt(stream.getvalue()))

    def test_round_trip_includes_all_current_databases_and_excludes_secrets_and_archives(self):
        for suffix in ("admin", "conversations", "profiles", "reminders", "issues", "pilot", "schedule-notices", "ai-usage"):
            self.database(backup.STATE_ROOT + f"/work-0123456789abcdef.{suffix}.sqlite3")
        self.database(backup.STATE_ROOT + "/zalo-0123456789abcdef.sqlite3")
        self.database("tickets/lab_live.sqlite3")
        secret = self.data / ".env"
        secret.write_text("SYNTHETIC_SECRET=do-not-copy", encoding="utf-8")
        for relative in ("secrets/google.json", "plugins/private.py", "logs/chat.log",
                         backup.STATE_ROOT + "/archives/old.sqlite3"):
            path = self.data / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("excluded", encoding="utf-8")
        result = backup.create_backup(self.data, self.output, self.key)
        self.assertEqual(11, result["files"])
        self.assertFalse(result["cross_database_atomic"])
        self.assertNotIn(b"synthetic internal value", self.output.read_bytes())
        self.assertNotIn(self.state_name.encode(), self.output.read_bytes())
        self.assertEqual(11, backup.verify_backup(self.output, self.key)["files"])
        stage = self.root / "empty-stage"
        stage.mkdir()
        restored = backup.restore_backup(self.output, stage, self.key)
        self.assertEqual(11, restored["files"])
        self.assertEqual(11, len(list(stage.rglob("*.sqlite3"))))
        self.assertFalse((stage / ".env").exists())
        db = sqlite3.connect(stage / self.state_name)
        self.assertEqual("synthetic internal value", db.execute("SELECT value FROM records").fetchone()[0])
        db.close()

    def test_live_ai_usage_backup_restores_quota_and_duplicate_protection(self):
        name = backup.STATE_ROOT + "/work-0123456789abcdef.ai-usage.sqlite3"
        clock = lambda: 1791972000
        source = AIUsageStore(self.data / name, user_daily_limit=1, clock=clock)
        self.addCleanup(source.close)
        source.reserve("synthetic-user", "synthetic-message")
        result = backup.create_backup(self.data, self.output, self.key)
        self.assertEqual(2, result["files"])
        stage = self.root / "restored-usage"
        backup.restore_backup(self.output, stage, self.key)
        restored = AIUsageStore(stage / name, user_daily_limit=1, clock=clock)
        self.addCleanup(restored.close)
        self.assertEqual(source.snapshot("synthetic-user"), restored.snapshot("synthetic-user"))
        for event, reason in (("synthetic-message", "duplicate"), ("new-message", "user_daily")):
            with self.subTest(event=event), self.assertRaises(AIUsageLimit) as raised:
                restored.reserve("synthetic-user", event)
            self.assertEqual(reason, raised.exception.reason)
        self.assertEqual(1, source.snapshot().total_count)

    def test_live_wal_committed_data_is_snapshotted_without_checkpointing_source(self):
        path = self.data / self.state_name
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("INSERT INTO records VALUES ('latest committed row')")
        db.commit()
        self.assertTrue(Path(str(path) + "-wal").exists())
        backup.create_backup(self.data, self.output, self.key)
        stage = self.root / "restored"
        backup.restore_backup(self.output, stage, self.key)
        restored = sqlite3.connect(stage / self.state_name)
        self.assertEqual(2, restored.execute("SELECT COUNT(*) FROM records").fetchone()[0])
        restored.close()
        self.assertEqual(2, db.execute("SELECT COUNT(*) FROM records").fetchone()[0])

    def test_restore_invalidates_admin_sessions_only_after_verification(self):
        name = backup.STATE_ROOT + "/work-0123456789abcdef.admin.sqlite3"
        path = self.database(name)
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE admin_sessions (hash TEXT)")
        db.execute("INSERT INTO admin_sessions VALUES ('synthetic-session')")
        db.commit()
        db.close()
        backup.create_backup(self.data, self.output, self.key)
        backup.verify_backup(self.output, self.key)
        stage = self.root / "staging"
        result = backup.restore_backup(self.output, stage, self.key)
        self.assertEqual(1, result["invalidated_admin_sessions"])
        for source, count in ((path, 1), (stage / name, 0)):
            db = sqlite3.connect(source)
            self.assertEqual(count, db.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM records").fetchone()[0])
            db.close()

    def test_wrong_key_and_corrupted_ciphertext_leave_staging_empty(self):
        backup.create_backup(self.data, self.output, self.key)
        stage = self.root / "staging"
        stage.mkdir()
        with self.assertRaisesRegex(backup.BackupError, "Không xác thực"):
            backup.restore_backup(self.output, stage, Fernet.generate_key())
        original = self.output.read_bytes()
        changed = bytearray(original)
        changed[-30] ^= 1
        self.output.write_bytes(changed)
        with self.assertRaises(backup.BackupError):
            backup.restore_backup(self.output, stage, self.key)
        self.assertEqual([], list(stage.iterdir()))

    def test_existing_output_or_nonempty_restore_target_is_not_overwritten(self):
        self.output.write_bytes(b"original")
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.data, self.output, self.key)
        self.assertEqual(b"original", self.output.read_bytes())
        stage = self.root / "live"
        stage.mkdir()
        (stage / "original").write_bytes(b"keep")
        with self.assertRaisesRegex(backup.BackupError, "staging trống"):
            backup.restore_backup(self.output, stage, self.key)
        self.assertEqual(b"keep", (stage / "original").read_bytes())

    def test_unknown_database_and_invalid_source_fail_closed(self):
        unknown = self.database(backup.STATE_ROOT + "/new-type.sqlite3")
        with self.assertRaisesRegex(backup.BackupError, "chưa được hỗ trợ"):
            backup.create_backup(self.data, self.output, self.key)
        unknown.unlink()
        (self.data / self.state_name).write_bytes(b"not sqlite")
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.data, self.output, self.key)
        self.assertFalse(self.output.exists())

    def test_known_issue_migration_snapshot_is_excluded_but_similar_unknown_databases_fail(self):
        current = backup.STATE_ROOT + "/work-0123456789abcdef.issues.sqlite3"
        self.database(current)
        historical = self.database(backup.STATE_ROOT +
                                   "/work-0123456789abcdef.issues.pre-managed-20261014-001221-855758.sqlite3")
        original = historical.read_bytes()
        result = backup.create_backup(self.data, self.output, self.key)
        self.assertEqual(2, result["files"])
        stage = self.root / "staging"
        backup.restore_backup(self.output, stage, self.key)
        self.assertTrue((stage / current).exists())
        self.assertFalse((stage / backup.STATE_ROOT / historical.name).exists())
        self.assertEqual(original, historical.read_bytes())
        for name in ("work-0123456789abcdef.issues.pre-managed-unknown.sqlite3",
                     "work-0123456789abcdef.issues.pre-managed-20269999-001221-855758.sqlite3",
                     "work-0123456789abcdef.registration.pre-managed-20261014-001221-855758.sqlite3"):
            with self.subTest(name=name):
                unknown = self.database(backup.STATE_ROOT + "/" + name)
                with self.assertRaisesRegex(backup.BackupError, "chưa được hỗ trợ"):
                    backup.create_backup(self.data, self.root / "next.lcbak", self.key)
                unknown.unlink()

    def test_size_file_count_and_snapshot_time_are_bounded(self):
        for constant, limit in (("MAX_FILE_BYTES", 1), ("MAX_TOTAL_BYTES", 1), ("MAX_FILES", 0),
                                ("SQLITE_TIMEOUT", -1), ("BACKUP_TIMEOUT", -1)):
            with self.subTest(constant=constant), patch.object(backup, constant, limit):
                with self.assertRaises(backup.BackupError):
                    backup.create_backup(self.data, self.output, self.key)
                self.assertFalse(self.output.exists())

    def test_encrypted_input_size_is_bounded(self):
        backup.create_backup(self.data, self.output, self.key)
        with patch.object(backup, "MAX_ENCRYPTED_BYTES", 1), self.assertRaises(backup.BackupError):
            backup.verify_backup(self.output, self.key)

    def test_authenticated_archive_rejects_traversal_and_nonallowlisted_paths(self):
        content = (self.data / self.state_name).read_bytes()
        for name in ("../outside.sqlite3", "/absolute.sqlite3", "tickets/../lab_live.sqlite3",
                     "tickets\\lab_live.sqlite3", "tickets/custom.sqlite3", "secrets/google.json"):
            with self.subTest(name=name):
                self.fake_archive({name: content})
                with self.assertRaises(backup.BackupError):
                    backup.verify_backup(self.output, self.key)

    def test_manifest_hash_size_and_sqlite_integrity_are_checked(self):
        valid = (self.data / self.state_name).read_bytes()
        for content, modifier in (
            (valid, lambda manifest: manifest["files"][self.state_name].update(sha256="0" * 64)),
            (valid, lambda manifest: manifest["files"][self.state_name].update(size=1)),
            (b"not a database", None),
        ):
            self.fake_archive({self.state_name: content}, details=modifier)
            stage = self.root / "staging"
            with self.assertRaises(backup.BackupError):
                backup.restore_backup(self.output, stage, self.key)
            self.assertFalse(stage.exists())

    def test_archive_links_are_rejected(self):
        info = zipfile.ZipInfo("tickets/lab_live.sqlite3")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.fake_archive({self.state_name: (self.data / self.state_name).read_bytes()}, extra_infos=((info, b"/elsewhere"),))
        with self.assertRaises(backup.BackupError):
            backup.verify_backup(self.output, self.key)

    def test_duplicate_archive_members_are_rejected(self):
        info = zipfile.ZipInfo(self.state_name.upper())
        self.fake_archive({self.state_name: (self.data / self.state_name).read_bytes()}, extra_infos=((info, b"duplicate"),))
        with self.assertRaises(backup.BackupError):
            backup.verify_backup(self.output, self.key)

    def test_source_symlink_is_rejected(self):
        source = self.data / self.state_name
        linked = self.data / "tickets" / "lab_live.sqlite3"
        linked.parent.mkdir()
        try:
            linked.symlink_to(source)
        except OSError:
            self.skipTest("symlink permission unavailable")
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.data, self.output, self.key)

    def test_key_generation_file_priority_and_cli_never_print_secrets(self):
        key_file = self.root / "private" / "backup.key"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(0, backup.main(["generate-key", "--output", str(key_file)]))
            key = backup.load_key(key_file, {"LABCOACH_BACKUP_KEY": "invalid ignored env"})
            self.assertEqual(0, backup.main(["create", "--data-dir", str(self.data), "--output", str(self.output),
                                             "--key-file", str(key_file)]))
            self.assertEqual(0, backup.main(["verify", str(self.output), "--key-file", str(key_file)]))
            with patch.dict(os.environ, {"LABCOACH_BACKUP_KEY": "synthetic-invalid-secret"}):
                self.assertEqual(2, backup.main(["verify", str(self.output)]))
        captured = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(key.decode(), captured)
        self.assertNotIn("synthetic-invalid-secret", captured)
        self.assertNotIn("synthetic internal value", captured)
        self.assertEqual(key, backup.load_key(environ={"LABCOACH_BACKUP_KEY": key.decode()}))
        if os.name != "nt":
            self.assertEqual(0o600, stat.S_IMODE(key_file.stat().st_mode))


if __name__ == "__main__":
    unittest.main()
