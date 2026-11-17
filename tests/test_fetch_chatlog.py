"""End-to-end tests for fetch_chatlog.py against a fake chatlog HTTP server.

Run: python3 -m unittest discover -s tests
"""

import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "birding-chat-digest" / "scripts"))
import fetch_chatlog  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
GROUP = "111@chatroom"


def ts(day: int, hour: int, minute: int = 0) -> int:
    return int(dt.datetime(2026, 11, day, hour, minute, tzinfo=TZ).timestamp())


def msg(mid, day, hour, sender_id="u1", type_="text", content="", **extra):
    return {"message_id": mid, "timestamp": ts(day, hour), "sender_id": sender_id,
            "sender": "备注名-不应出现", "type": type_, "content": content, **extra}


class FakeChatlog:
    """Serves /api/v1/history, /members, /contacts from in-memory fixtures."""

    def __init__(self):
        self.history = {}           # (chat, day) -> [messages]
        self.members = {}           # chat -> [members]
        self.contacts = []
        self.fail_once = set()      # paths that answer 500 once
        self.max_page = None        # answer 413 with retry_limit above this size
        self.requests = []

    def __enter__(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                fake.requests.append((url.path, q))
                if url.path in fake.fail_once:
                    fake.fail_once.discard(url.path)
                    return self.reply(500, {"error": "boom"})
                if url.path == "/api/v1/history":
                    key = (q["chat"], q["time"])
                    if q["chat"] not in {c for c, _ in fake.history}:
                        return self.reply(404, {"error": "chat not found"})
                    limit, offset = int(q["limit"]), int(q["offset"])
                    if fake.max_page and limit > fake.max_page:
                        return self.reply(413, {"code": "history_page_budget", "retryable": True,
                                                "retry_limit": fake.max_page})
                    rows = fake.history.get(key, [])
                    return self.reply(200, {"total_count": len(rows), "messages": rows[offset:offset + limit]})
                if url.path == "/api/v1/members":
                    return self.reply(200, {"members": fake.members.get(q["chat"], [])})
                if url.path == "/api/v1/contacts":
                    return self.reply(200, {"contacts": [c for c in fake.contacts if q["query"] in c["username"]]})
                return self.reply(404, {"error": "unknown"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def run(fake, out, *args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
            patch.object(fetch_chatlog.time, "sleep"):
        code = fetch_chatlog.main(["--server", fake.url, "--out", str(out), "--tz", "Asia/Shanghai",
                                   "--timeout", "5", *args])
    return code, json.loads((Path(out) / "index.json").read_text()), stderr.getvalue()


class FetchChatlogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def group_file(self, name="111"):
        return json.loads((self.out / f"{name}.json").read_text())

    def test_window_across_two_days_keeps_only_messages_in_range(self):
        with FakeChatlog() as fake:
            fake.history[(GROUP, "2026-11-15")] = [msg(1, 15, 20), msg(2, 15, 21), msg(3, 15, 23, type_="image")]
            fake.history[(GROUP, "2026-11-16")] = [msg(4, 16, 7), msg(5, 16, 21), msg(3, 15, 23, type_="image")]
            fake.members[GROUP] = [{"username": "u1", "display": "山雀", "display_source": "group_nickname"}]
            code, index, _ = run(fake, self.out, "--chat", f"{GROUP}=示例群", "--page-size", "2",
                                 "--start", "2026-11-15T21:00", "--end", "2026-11-16T21:00")
        self.assertEqual(code, 0)
        data = self.group_file()
        # 20:00 is before the window, 21:00 next day is the exclusive end; duplicate id 3 counted once
        self.assertEqual([m["message_id"] for m in data["messages"]], [2, 3, 4])
        self.assertEqual(data["source_coverage"], [
            {"date": "2026-11-15", "total_count": 3, "fetched": 3},
            {"date": "2026-11-16", "total_count": 3, "fetched": 3}])
        self.assertEqual(data["type_counts"], {"text": 2, "image": 1})
        self.assertEqual(index["date"], "2026-11-16")
        self.assertEqual(index["window_start"], "2026-11-15T21:00:00+08:00")
        self.assertEqual(index["failures"], 0)

    def test_names_use_group_then_wechat_nickname_never_remark(self):
        quoted = {"refer": {"sender": "u2", "senderName": "备注名-不应出现"}}
        with FakeChatlog() as fake:
            fake.history[(GROUP, "2026-11-16")] = [
                msg(1, 16, 8, "u1"), msg(2, 16, 9, "u2", contents=quoted),
                msg(3, 16, 10, "u3"), msg(4, 16, 11, "", type_="system")]
            fake.members[GROUP] = [
                {"username": "u1", "display": "山雀", "display_source": "group_nickname"},
                {"username": "u2", "display": "我的备注", "display_source": "remark"}]
            fake.contacts = [{"username": "u2", "nickname": "长脚鹬"}]
            code, index, _ = run(fake, self.out, "--chat", GROUP, "--date", "2026-11-16")
        self.assertEqual(code, 0)
        names = [(m["sender"], m["sender_name_source"]) for m in self.group_file()["messages"]]
        self.assertEqual(names, [("山雀", "group_nickname"), ("长脚鹬", "wechat_nickname"),
                                 ("", "unavailable"), ("", "system")])
        self.assertEqual(self.group_file()["messages"][1]["contents"]["refer"]["senderName"], "长脚鹬")
        self.assertEqual(index["groups"][0]["naming_coverage"]["unresolved_senders"], 1)
        self.assertNotIn("备注", (self.out / "111.json").read_text())

    def test_failed_group_is_reported_and_other_groups_still_written(self):
        with FakeChatlog() as fake:
            fake.history[(GROUP, "2026-11-16")] = [msg(1, 16, 8)]
            code, index, logs = run(fake, self.out, "--chat", f"{GROUP}=好群",
                                    "--chat", "999@chatroom=坏群", "--date", "2026-11-16")
        self.assertEqual(code, 1)
        self.assertEqual(index["failures"], 1)
        bad = index["groups"][1]
        self.assertEqual(bad["name"], "坏群")
        self.assertIn("404", bad["error"])
        self.assertNotIn("fetched", bad)
        self.assertNotIn("999@chatroom", logs)  # chat ids stay out of logs
        self.assertEqual(index["groups"][0]["fetched"], 1)

    def test_rerun_failure_removes_stale_group_file(self):
        with FakeChatlog() as fake:
            fake.history[(GROUP, "2026-11-16")] = [msg(1, 16, 8)]
            code, _, _ = run(fake, self.out, "--chat", GROUP, "--date", "2026-11-16")
        self.assertEqual(code, 0)
        self.assertTrue((self.out / "111.json").exists())
        with FakeChatlog() as fake:  # the group is unknown now, so fetching it fails
            fake.history[("999@chatroom", "2026-11-16")] = []
            code, index, _ = run(fake, self.out, "--chat", GROUP, "--date", "2026-11-16")
        self.assertEqual(code, 1)
        self.assertIn("error", index["groups"][0])
        self.assertFalse((self.out / "111.json").exists(), "stale file from the earlier run must be removed")

    def test_page_budget_413_retries_same_offset_with_smaller_page(self):
        rows = [msg(i, 16, 8 + i % 10) for i in range(1, 8)]
        with FakeChatlog() as fake:
            fake.history[(GROUP, "2026-11-16")] = rows
            fake.max_page = 3
            code, _, _ = run(fake, self.out, "--chat", GROUP, "--date", "2026-11-16")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(m["message_id"] for m in self.group_file()["messages"]), list(range(1, 8)))

    def test_transient_500_is_retried(self):
        with FakeChatlog() as fake:
            fake.history[(GROUP, "2026-11-16")] = [msg(1, 16, 8)]
            fake.fail_once.add("/api/v1/history")
            code, index, logs = run(fake, self.out, "--chat", GROUP, "--date", "2026-11-16")
        self.assertEqual(code, 0)
        self.assertEqual(index["groups"][0]["fetched"], 1)
        self.assertIn("request_failed", logs)

    def test_rejects_ambiguous_or_reversed_window(self):
        with FakeChatlog() as fake, contextlib.redirect_stderr(io.StringIO()):
            for args in (["--date", "2026-11-16", "--start", "2026-11-16T00:00"],
                         ["--start", "2026-11-16T21:00", "--end", "2026-11-16T12:00"]):
                with self.assertRaises(SystemExit):
                    fetch_chatlog.main(["--server", fake.url, "--out", str(self.out), "--chat", GROUP, *args])


if __name__ == "__main__":
    unittest.main()
