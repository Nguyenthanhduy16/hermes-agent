"""Synthetic transfer tests: no real bot state, credentials or network."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
import zipfile

from hermes_zalo.state_transfer import TransferError, export_state, import_state, main


REG = "hermes-home/data/zalo/work-0123456789abcdef.registration.sqlite3"
TICKET = "tickets/lab_live.sqlite3"


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.archive = self.root / "transfer.zip"
        self.target = self.root / "volume"

    def database(self, relative, value="approved"):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE records(value TEXT)")
        db.execute("INSERT INTO records VALUES (?)", (value,))
        db.commit()
        db.close()
        return path

    def seed(self):
        self.database(".runtime/hermes-home/data/zalo/" + REG.split("/")[-1])
        self.database(".runtime/tickets/lab_live.sqlite3", "pending")
        knowledge = self.project / "data/knowledge"
        knowledge.mkdir(parents=True)
        (knowledge / "manifest.json").write_text('{"documents":[]}', encoding="utf-8")
        (knowledge / "guide.md").write_text("Quy định thử nghiệm.", encoding="utf-8")
        for filename in (".env", "config.yaml", "auth.json", "state.db", "gateway.pid"):
            path = self.project / ".runtime/hermes-home" / filename
            path.write_text("private-placeholder", encoding="utf-8")
        (self.project / ".runtime/tickets/lab_live.lock").write_text("0")
        (knowledge / "secret.json").write_text('{"key":"not-for-export"}')

    def export(self):
        return export_state(self.project, self.archive, bot_stopped=True)

    def crafted(self, payloads, changes=None, unix_type=None):
        files = {name: {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
                 for name, payload in payloads.items()}
        if changes:
            changes(files)
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"format": "labcoach-state", "version": 1, "files": files}))
            for name, payload in payloads.items():
                if unix_type:
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = (unix_type | 0o600) << 16
                    archive.writestr(info, payload)
                else:
                    archive.writestr(name, payload)

    def test_round_trip_registration_tickets_knowledge_and_excludes_secrets(self):
        self.seed()
        self.assertEqual(4, self.export()["files"])
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual({"manifest.json", REG, TICKET, "knowledge/manifest.json", "knowledge/guide.md"},
                             set(archive.namelist()))
        self.assertEqual(4, import_state(self.archive, self.target, bot_stopped=True)["files"])
        # Closing and reopening a new process's databases preserves approvals/requests.
        for relative, expected in ((REG, "approved"), (TICKET, "pending")):
            db = sqlite3.connect(self.target / relative)
            self.assertEqual(expected, db.execute("SELECT value FROM records").fetchone()[0])
            db.close()
        self.assertEqual("Quy định thử nghiệm.", (self.target / "knowledge/guide.md").read_text(encoding="utf-8"))

    def test_sqlite_backup_includes_committed_wal(self):
        source = self.database(".runtime/hermes-home/data/zalo/" + REG.split("/")[-1], "original")
        db = sqlite3.connect(source)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("INSERT INTO records VALUES ('committed-in-wal')")
            db.commit()
            self.assertTrue(Path(str(source) + "-wal").is_file())
            self.export()
            import_state(self.archive, self.target, bot_stopped=True)
            restored = sqlite3.connect(self.target / REG)
            try:
                self.assertEqual(2, restored.execute("SELECT count(*) FROM records").fetchone()[0])
            finally:
                restored.close()
        finally:
            db.close()

    def test_explicit_stopped_ack_required_for_both_actions(self):
        self.seed()
        with self.assertRaises(TransferError):
            export_state(self.project, self.archive, bot_stopped=False)
        self.assertFalse(self.archive.exists())
        self.export()
        with self.assertRaises(TransferError):
            import_state(self.archive, self.target, bot_stopped=False)
        self.assertFalse(self.target.exists())

    def test_refuses_overwrite_existing_export(self):
        self.seed()
        self.archive.write_bytes(b"keep-existing")
        with self.assertRaises(TransferError):
            self.export()
        self.assertEqual(b"keep-existing", self.archive.read_bytes())

    def test_refuses_existing_registration_tickets_or_knowledge(self):
        self.seed()
        self.export()
        for relative in (REG, TICKET, "knowledge/guide.md"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as folder:
                target = Path(folder)
                existing = target / relative
                existing.parent.mkdir(parents=True)
                existing.write_bytes(b"existing-state")
                with self.assertRaises(TransferError):
                    import_state(self.archive, target, bot_stopped=True)
                self.assertEqual(b"existing-state", existing.read_bytes())

    def test_allows_empty_target_data_directories_and_preserves_other_runtime(self):
        self.seed()
        self.export()
        (self.target / "hermes-home/data/zalo").mkdir(parents=True)
        (self.target / "tickets").mkdir()
        (self.target / "knowledge").mkdir()
        config = self.target / "hermes-home/config.yaml"
        config.write_text("preserve-runtime")
        import_state(self.archive, self.target, bot_stopped=True)
        self.assertEqual("preserve-runtime", config.read_text())

    def test_rejects_traversal_unsafe_paths_and_non_data_files(self):
        for name in ("../escape.txt", "/outside.md", "knowledge/../escape.md",
                     "knowledge\\escape.md", "knowledge/C:drive.md", "knowledge/CON.txt",
                     "knowledge/.env", "knowledge/odd.txt ", "hermes-home/.env",
                     "tickets/lab_live.lock", "knowledge/code.py"):
            with self.subTest(name=name):
                self.crafted({name: b"payload"})
                with self.assertRaises(TransferError):
                    import_state(self.archive, self.target, bot_stopped=True)
        self.assertFalse((self.root / "escape.txt").exists())
        self.assertFalse((self.target / "knowledge").exists())

    def test_rejects_checksum_mismatch_before_publishing_any_files(self):
        self.crafted({"knowledge/good.md": b"okay", "knowledge/bad.md": b"changed"},
                     lambda files: files["knowledge/bad.md"].update(sha256="0" * 64))
        with self.assertRaises(TransferError):
            import_state(self.archive, self.target, bot_stopped=True)
        self.assertFalse((self.target / "knowledge").exists())
        self.assertEqual([], list(self.target.iterdir()))

    def test_rejects_zip_symlink_and_duplicate_names(self):
        self.crafted({"knowledge/linked.txt": b"/elsewhere"}, unix_type=stat.S_IFLNK)
        with self.assertRaises(TransferError):
            import_state(self.archive, self.target, bot_stopped=True)
        self.crafted({"knowledge/guide.md": b"first", "knowledge/GUIDE.md": b"second"})
        with self.assertRaises(TransferError):
            import_state(self.archive, self.target, bot_stopped=True)

    def test_rejects_manifest_file_list_and_size_mismatches(self):
        for change in (lambda files: files["knowledge/guide.md"].update(size=999),
                       lambda files: files.pop("knowledge/guide.md")):
            self.crafted({"knowledge/guide.md": b"guide"}, change)
            with self.assertRaises(TransferError):
                import_state(self.archive, self.target, bot_stopped=True)

    def test_rejects_non_database_payload_and_keeps_destination_empty(self):
        self.crafted({REG: b"not SQLite"})
        with self.assertRaises(TransferError):
            import_state(self.archive, self.target, bot_stopped=True)
        self.assertFalse((self.target / "hermes-home/data/zalo").exists())

    def test_corrupt_source_database_leaves_no_partial_export(self):
        source = self.project / ".runtime/tickets/lab_live.sqlite3"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"not a database")
        with self.assertRaises(TransferError):
            self.export()
        self.assertFalse(self.archive.exists())
        self.assertEqual(b"not a database", source.read_bytes())

    def test_rejects_duplicate_manifest_fields_and_boolean_version(self):
        payload = b"guide"
        details = {"knowledge/guide.md": {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}}
        invalid_manifests = [
            '{"format":"labcoach-state","version":1,"version":1,"files":' + json.dumps(details) + '}',
            json.dumps({"format": "labcoach-state", "version": True, "files": details}),
        ]
        for manifest in invalid_manifests:
            with zipfile.ZipFile(self.archive, "w") as archive:
                archive.writestr("manifest.json", manifest)
                archive.writestr("knowledge/guide.md", payload)
            with self.assertRaises(TransferError):
                import_state(self.archive, self.target, bot_stopped=True)
        self.assertFalse((self.target / "knowledge").exists())

    def test_excludes_nested_maintenance_snapshots_but_keeps_active_databases(self):
        self.seed()
        old = self.database(".runtime/tickets/maintenance-backups/old/lab_live.before.sqlite3", "old")
        other = self.database(".runtime/hermes-home/data/zalo/maintenance/old.sqlite3", "old")
        originals = [old.read_bytes(), other.read_bytes()]
        self.export()
        with zipfile.ZipFile(self.archive) as archive:
            self.assertIn(REG, archive.namelist())
            self.assertIn(TICKET, archive.namelist())
            self.assertFalse(any("maintenance" in name or "before" in name for name in archive.namelist()))
        self.assertEqual(originals, [old.read_bytes(), other.read_bytes()])

    def test_unknown_top_level_sqlite_still_blocks_export(self):
        self.seed()
        self.database(".runtime/tickets/unknown.sqlite3")
        with self.assertRaises(TransferError):
            self.export()
        self.assertFalse(self.archive.exists())

    def test_direct_windows_style_cli_prints_vietnamese_under_legacy_encoding(self):
        import os
        import subprocess
        import sys
        env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        script = Path(__file__).resolve().parents[1] / "scripts/transfer_state.py"
        args = [sys.executable, str(script), "export", "--project-root", str(self.project),
                "--output", str(self.archive)]
        denied = subprocess.run(args, env=env, capture_output=True, timeout=20)
        self.assertEqual(2, denied.returncode)
        self.assertIn("Dừng mọi bản bot", denied.stderr.decode("utf-8"))
        self.assertNotIn(b"UnicodeEncodeError", denied.stderr)
        self.seed()
        success = subprocess.run([*args, "--bot-stopped"], env=env, capture_output=True, timeout=20)
        self.assertEqual(0, success.returncode, success.stderr.decode("utf-8"))
        self.assertIn("Đạt:", success.stdout.decode("utf-8"))
        self.assertNotIn(b"UnicodeEncodeError", success.stderr)

    def test_source_symlink_rejected(self):
        knowledge = self.project / "data/knowledge"
        knowledge.mkdir(parents=True)
        outside = self.root / "outside.txt"
        outside.write_text("private")
        try:
            (knowledge / "linked.txt").symlink_to(outside)
        except OSError:
            self.skipTest("Windows account cannot create symlinks.")
        with self.assertRaises(TransferError):
            self.export()
        self.assertFalse(self.archive.exists())

    def test_target_symlink_rejected(self):
        self.seed()
        self.export()
        self.target.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.target / "knowledge").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Windows account cannot create symlinks.")
        with self.assertRaises(TransferError):
            import_state(self.archive, self.target, bot_stopped=True)
        self.assertEqual([], list(outside.iterdir()))

    def test_cli_failure_does_not_expose_paths_or_file_content(self):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = main(["import", str(self.root / "sensitive-account.zip"),
                           "--data-dir", str(self.target), "--bot-stopped"])
        self.assertEqual(2, result)
        self.assertNotIn("sensitive-account", errors.getvalue())
        self.assertEqual("", output.getvalue())


if __name__ == "__main__":
    unittest.main()
