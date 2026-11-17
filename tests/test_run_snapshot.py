"""Tests for run_snapshot.py: copying, the full start/fetch/cleanup cycle with a fake
reader, and an optional integration test with a real chatlog-snapshot-reader.

Run: python3 -m unittest discover -s tests
Real reader: CHATLOG_SNAPSHOT_READER=/path/to/chatlog-snapshot-reader python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import textwrap
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "birding-chat-digest" / "scripts"))
import run_snapshot  # noqa: E402

FAKE_READER = textwrap.dedent('''\
    #!{python}
    """Minimal stand-in for chatlog-snapshot-reader: serves status and one message."""
    import json, os, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    root = sys.argv[sys.argv.index("--root") + 1]
    if os.environ.get("FAKE_READER_CRASH"):
        sys.exit(7)
    config = json.load(open(os.path.join(os.environ["CHATLOG_DIR"], "chatlog.json")))
    data_dir = config["accounts"][0]["data_dir"]
    assert data_dir.startswith(root), "reader must only see the copy"
    assert os.path.exists(os.path.join(data_dir, "db_storage/message/message_0.db"))
    host, port = config["http_addr"].split(":")
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path.startswith("/api/v1/control/status"):
                body = {{"status": {{"database_ready": True, "data_dir": data_dir}}}}
            elif self.path.startswith("/api/v1/history"):
                ts = 1794787200  # 2026-11-16 08:00 +08:00
                body = {{"total_count": 1, "messages": [{{"message_id": 1, "timestamp": ts,
                        "sender_id": "", "type": "text", "content": "snapshot message"}}]}}
            else:
                body = {{"members": [], "contacts": []}}
            data = json.dumps(body).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)
    HTTPServer((host, int(port)), H).serve_forever()
''')


class SnapshotTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "wechat-account"
        self.db = self.source / "db_storage/message/message_0.db"
        self.db.parent.mkdir(parents=True)
        self.db.write_bytes(b"database")
        Path(str(self.db) + "-wal").write_bytes(b"wal")
        (self.db.parent / "message_0.db-shm").write_bytes(b"shm")
        (self.source / "db_storage/cache.tmp").write_bytes(b"skip")
        (self.source / "all_keys.json").write_text('{"message/message_0.db": {"enc_key": "fixture"}}')
        self.config = self.root / "chatlog-config"
        self.config.mkdir()
        (self.config / "chatlog.json").write_text(json.dumps({"last_account": "fixture", "accounts": [
            {"account": "fixture", "platform": "darwin", "version": 4, "data_dir": str(self.source),
             "data_key": "00" * 32, "webhook": "must-not-be-copied"}]}))
        self.private = self.root / "private"

    def snapshot_files(self):
        return {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}


class CopyTests(SnapshotTestCase):
    def test_copies_databases_wal_and_keys_only(self):
        dest = self.root / "dest"
        run_snapshot.copy_databases(self.source, dest)
        copied = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
        self.assertEqual(copied, ["all_keys.json", "db_storage/message/message_0.db",
                                  "db_storage/message/message_0.db-wal"])
        self.assertEqual((dest / "all_keys.json").stat().st_mode & 0o777, 0o600)

    def test_symlinked_directory_is_rejected_not_skipped(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.source / "db_storage/linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(run_snapshot.SnapshotError):
            run_snapshot.copy_databases(self.source, self.root / "dest")

    def test_missing_databases_is_an_error(self):
        empty = self.root / "empty"
        (empty / "db_storage").mkdir(parents=True)
        with self.assertRaises(run_snapshot.SnapshotError):
            run_snapshot.copy_databases(empty, self.root / "dest")


class RunTests(SnapshotTestCase):
    def fake_reader(self):
        path = self.root / "fake-reader"
        path.write_text(FAKE_READER.format(python=sys.executable))
        path.chmod(0o755)
        return str(path)

    def run_main(self, *args, env=None):
        stderr = io.StringIO()
        with mock.patch.object(run_snapshot.tempfile, "mkdtemp", return_value=str(self.private)), \
                mock.patch.dict(os.environ, env or {}), \
                contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            self.private.mkdir()
            code = run_snapshot.main(list(args))
        return code, stderr.getvalue()

    def test_full_cycle_fetches_from_copy_and_cleans_up(self):
        before = self.snapshot_files()
        out = self.root / "export"
        code, logs = self.run_main("--reader", self.fake_reader(), "--chatlog-config", str(self.config),
                                   "--wait", "20", "--", "--chat", "111@chatroom=示例群",
                                   "--date", "2026-11-16", "--tz", "Asia/Shanghai", "--out", str(out))
        self.assertEqual(code, 0, logs)
        index = json.loads((out / "index.json").read_text())
        self.assertEqual(index["groups"][0]["fetched"], 1)
        self.assertIn("snapshot message", (out / "111.json").read_text())
        self.assertFalse(self.private.exists(), "private copy must be deleted")
        self.assertEqual(before, self.snapshot_files(), "source must not change")

    def test_reader_crash_reports_failure_and_cleans_up(self):
        code, logs = self.run_main("--reader", self.fake_reader(), "--chatlog-config", str(self.config),
                                   "--", "--chat", "1@chatroom", "--date", "2026-11-16", "--out", str(self.root / "o"),
                                   env={"FAKE_READER_CRASH": "1"})
        self.assertEqual(code, 3)
        self.assertIn("exited with code 7", logs)
        self.assertFalse(self.private.exists())

    def test_snapshot_failure_removes_previous_index(self):
        out = self.root / "export"
        out.mkdir()
        (out / "index.json").write_text('{"failures": 0}')
        code, logs = self.run_main("--reader", self.fake_reader(), "--chatlog-config", str(self.root / "missing"),
                                   "--", "--chat", "1@chatroom", "--date", "2026-11-16", "--out", str(out))
        self.assertEqual(code, 3)
        self.assertFalse((out / "index.json").exists(), "an earlier index must not survive a snapshot failure")

    def test_server_argument_is_rejected(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run_snapshot.main(["--reader", sys.executable, "--", "--server", "http://x", "--out", "o"])

    def test_private_config_keeps_only_reader_fields(self):
        snap = run_snapshot.Snapshot(self.fake_reader(), self.config, None)
        with mock.patch.object(run_snapshot.tempfile, "mkdtemp", return_value=str(self.private)), \
                mock.patch.object(run_snapshot.subprocess, "Popen"), contextlib.redirect_stderr(io.StringIO()):
            self.private.mkdir()
            snap.start()
            snap.log.close()
        config = json.loads((self.private / "config/chatlog.json").read_text())
        account = config["accounts"][0]
        self.assertNotIn("webhook", account)
        self.assertEqual(account["data_dir"], str(self.private / "data"))
        self.assertTrue(account["hook_paused"])


@unittest.skipUnless(os.environ.get("CHATLOG_SNAPSHOT_READER"), "set CHATLOG_SNAPSHOT_READER to a built reader")
class RealReaderTests(SnapshotTestCase):
    def test_real_reader_reads_committed_wal_from_copy(self):
        db_path = self.source / "db_storage/contact/contact.db"
        db_path.parent.mkdir(parents=True)
        db = sqlite3.connect(db_path)
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE contact(username TEXT,local_type INTEGER,flag INTEGER,delete_flag INTEGER,"
                   "verify_flag INTEGER,alias TEXT,remark TEXT,nick_name TEXT,description TEXT,"
                   "big_head_url TEXT,small_head_url TEXT,extra_buffer BLOB)")
        db.execute("CREATE TABLE chat_room(username TEXT,owner TEXT,ext_buffer BLOB)")
        db.execute("CREATE TABLE openim_wording(lang_id INTEGER,app_id TEXT,wording_id TEXT,wording TEXT,"
                   "pinyin TEXT,quan_pin TEXT)")
        db.execute("CREATE TABLE contact_label(label_id_ INTEGER,label_name_ TEXT,sort_order_ INTEGER)")
        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("INSERT INTO contact VALUES('fixture',0,1,0,0,'','','WAL nickname','','','',NULL)")
        db.commit()
        self.db.unlink()  # the fixture message db is not a real SQLite file
        Path(str(self.db) + "-wal").unlink()
        before = self.snapshot_files()
        snap = run_snapshot.Snapshot(os.environ["CHATLOG_SNAPSHOT_READER"], self.config, None)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                snap.start()
                snap.wait_ready(30)
            with urllib.request.urlopen(snap.server + "/api/v1/contacts?key=fixture&limit=10", timeout=5) as r:
                self.assertIn("WAL nickname", r.read().decode())
        except Exception:
            print(snap.log_tail())
            raise
        finally:
            private, reader = snap.directory, snap.reader
            snap.release()
        self.assertFalse(private.exists())
        self.assertIsNotNone(reader.poll())
        self.assertEqual(before, self.snapshot_files())


if __name__ == "__main__":
    unittest.main()
