#!/usr/bin/env python3
"""Fetch messages from a private copy of the WeChat databases instead of the live ones.

Use when reading through the running chatlog times out because WeChat is writing
heavily. Steps: copy every *.db / *.db-wal under db_storage plus all_keys.json
once into a private temp directory, start chatlog-snapshot-reader on that copy
at a random 127.0.0.1 port, run fetch_chatlog.py against it, then stop the
reader and delete the copy. The live databases are only read, never modified.

macOS / Linux only (POSIX process groups and file modes).

Example:
  python3 run_snapshot.py --reader ~/bin/chatlog-snapshot-reader -- \
      --groups groups.json --start 2026-11-15T21:00 --end 2026-11-16T21:00 --out ./export

Exit code: fetch_chatlog.py's code when the fetch ran (0 ok, 1 some groups failed);
3 when copying, starting or cleaning up the snapshot failed; 128+N on signal N.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Only these account fields are carried into the private config: never delivery,
# AI, OCR or autostart settings.
ACCOUNT_FIELDS = ("account", "platform", "version", "full_version", "data_key", "img_key")


class SnapshotError(Exception):
    pass


class Interrupted(Exception):
    def __init__(self, signum: int):
        self.signum = signum


def copy_databases(source: Path, destination: Path) -> None:
    """One plain pass over db_storage. No retries and no checks for changes during the copy."""
    storage = source / "db_storage"
    if storage.is_symlink():
        raise SnapshotError("symlinked db_storage is not supported")
    if not storage.is_dir():
        raise SnapshotError("db_storage not found under the account data directory")

    def failed(exc):  # unreadable directories must fail loudly, not vanish
        raise exc

    count = 0
    for directory, subdirs, names in os.walk(storage, onerror=failed, followlinks=False):
        if any((Path(directory) / name).is_symlink() for name in subdirs):
            raise SnapshotError("symlinked database directories are not supported")
        for name in names:
            if not (name.endswith(".db") or name.endswith(".db-wal")):
                continue
            path = Path(directory) / name
            if path.is_symlink():
                raise SnapshotError("symlinked database files are not supported")
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(path, target)
            target.chmod(0o600)
            count += name.endswith(".db")
    if not count:
        raise SnapshotError("no databases found under db_storage")
    keys = source / "all_keys.json"  # WCDB resolves shard keys relative to the account root
    if keys.is_symlink():
        raise SnapshotError("symlinked all_keys.json is not supported")
    if keys.exists():
        shutil.copyfile(keys, destination / "all_keys.json")
        (destination / "all_keys.json").chmod(0o600)


def free_local_address() -> str:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{listener.getsockname()[1]}"


def output_dir(fetch_args: list[str]) -> Path | None:
    for i, arg in enumerate(fetch_args):
        if arg == "--out" and i + 1 < len(fetch_args):
            return Path(fetch_args[i + 1])
        if arg.startswith("--out="):
            return Path(arg.split("=", 1)[1])
    return None


def load_account(config_dir: Path, account: str | None) -> dict:
    config = json.loads((config_dir / "chatlog.json").read_text(encoding="utf-8"))
    account = account or config.get("last_account")
    profiles = [p for p in config.get("accounts", []) if p.get("account") == account]
    if len(profiles) != 1 or not profiles[0].get("data_key"):
        raise SnapshotError("chatlog account or its database key is missing or ambiguous; pass --account")
    return profiles[0]


class Snapshot:
    """Owns the temp directory and child processes; release() always cleans up."""

    def __init__(self, reader: str, config_dir: Path, account: str | None):
        self.reader_path, self.config_dir, self.account = reader, config_dir, account
        self.directory: Path | None = None
        self.reader = self.child = self.log = None
        self.server = ""

    def start(self) -> None:
        # Create the directory first so every later failure is cleaned up.
        self.directory = Path(tempfile.mkdtemp(prefix="chatlog-snapshot-"))
        profile = load_account(self.config_dir, self.account)
        source = Path(profile["data_dir"]).expanduser()
        if source.name == "db_storage":
            source = source.parent
        if not source.is_absolute():
            raise SnapshotError("account data_dir must be an absolute path")
        self.data = self.directory / "data"
        print("copying databases and WAL files...", file=sys.stderr, flush=True)
        copy_databases(source, self.data)

        addr = free_local_address()
        self.server = "http://" + addr
        isolated = {k: profile[k] for k in ACCOUNT_FIELDS if k in profile}
        isolated.update(data_dir=str(self.data), work_dir=str(self.directory / "work"),
                        hook_paused=True, native_download_enabled=False,
                        voice_transcribe_paused=True, ocr={"enabled": False})
        private_config = self.directory / "config"
        private_config.mkdir(mode=0o700)
        with open(private_config / "chatlog.json", "x", encoding="utf-8",
                  opener=lambda p, flags: os.open(p, flags, 0o600)) as file:
            json.dump({"http_addr": addr, "last_account": profile["account"], "accounts": [isolated]}, file)
        env = dict(os.environ, CHATLOG_DIR=str(private_config), CHATLOG_SKIP_OPEN_CONSOLE="1",
                   TMPDIR=str(self.directory))
        self.log = (self.directory / "reader.log").open("wb")
        self.reader = subprocess.Popen([self.reader_path, "--root", str(self.directory)], env=env,
                                       stdout=self.log, stderr=self.log, start_new_session=True)

    def wait_ready(self, seconds: float) -> None:
        print(f"waiting for the snapshot reader (up to {seconds:g}s)...", file=sys.stderr, flush=True)
        deadline = time.monotonic() + seconds
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            code = self.reader.poll()
            if code is not None:
                raise SnapshotError(f"snapshot reader exited with code {code}; see its log:\n{self.log_tail()}")
            try:
                with opener.open(self.server + "/api/v1/control/status", timeout=1) as response:
                    status = json.load(response).get("status", {})
                if status.get("database_error"):
                    raise SnapshotError("snapshot reader reported a database error")
                if status.get("pid"):
                    raise SnapshotError("snapshot reader unexpectedly attached to a running WeChat process")
                if status.get("data_dir") and Path(status["data_dir"]).resolve() != self.data.resolve():
                    raise SnapshotError("snapshot reader is not using the copied databases")
                if status.get("database_ready") is True:
                    if status.get("data_dir") != str(self.data):
                        raise SnapshotError("snapshot reader did not report its data directory")
                    return
            except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise SnapshotError("snapshot databases did not become ready before the timeout")
            time.sleep(0.1)

    def fetch(self, fetch_args: list[str]) -> int:
        self.child = subprocess.Popen([sys.executable, str(HERE / "fetch_chatlog.py"), *fetch_args,
                                       "--server", self.server], start_new_session=True)
        code = self.child.wait()
        return code if code >= 0 else 128 - code

    def log_tail(self) -> str:
        if self.log:
            self.log.flush()
        try:
            return (self.directory / "reader.log").read_text(errors="replace")[-4000:]
        except OSError:
            return ""

    @staticmethod
    def stop(process) -> None:
        if process is None:
            return
        try:  # only the process groups this run created are signalled
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    def release(self) -> None:
        errors = []
        for process in (self.child, self.reader):
            try:
                self.stop(process)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
        self.child = self.reader = None
        if self.log is not None:
            self.log.close()
            self.log = None
        if errors:  # keep the copy if a process may still be using it
            raise SnapshotError("; ".join(errors))
        if self.directory is not None:
            shutil.rmtree(self.directory)
            self.directory = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="%(prog)s [--reader PATH] [--account ID] [--chatlog-config DIR] [--wait S] -- FETCH_ARGS...")
    parser.add_argument("--reader", default=os.environ.get("CHATLOG_SNAPSHOT_READER"),
                        help="chatlog-snapshot-reader binary (default: $CHATLOG_SNAPSHOT_READER)")
    parser.add_argument("--account", help="chatlog account to copy (default: last_account in chatlog.json)")
    parser.add_argument("--chatlog-config", help="chatlog config directory (default: $CHATLOG_DIR or ~/.chatlog)")
    parser.add_argument("--wait", type=float, default=180, help="seconds to wait for the reader (default 180)")
    argv = sys.argv[1:] if argv is None else argv
    split = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:split])
    fetch_args = argv[split + 1:]

    if os.name != "posix":
        parser.error("run_snapshot.py supports macOS and Linux only")
    if not args.reader or not os.access(args.reader, os.X_OK):
        parser.error("--reader must point to a built chatlog-snapshot-reader (see chatlog-snapshot-reader/README.md)")
    if not fetch_args:
        parser.error("pass fetch_chatlog.py arguments after --")
    if any(a == "--server" or a.startswith("--server=") for a in fetch_args):
        parser.error("--server is set by run_snapshot.py; do not pass it")
    if not 0 <= args.wait < float("inf"):
        parser.error("--wait must be a finite non-negative number")
    config_dir = Path(args.chatlog_config or os.environ.get("CHATLOG_DIR") or Path.home() / ".chatlog").expanduser()

    # Invalidate an earlier run's index before anything can fail, so a snapshot
    # failure (exit 3) never leaves a previous index.json that looks current.
    out = output_dir(fetch_args)
    if out is not None:
        (out / "index.json").unlink(missing_ok=True)

    snapshot = Snapshot(args.reader, config_dir, args.account)

    def interrupted(signum, frame):
        raise Interrupted(signum)

    previous = {s: signal.signal(s, interrupted) for s in (signal.SIGINT, signal.SIGTERM)}
    code = 3
    try:
        snapshot.start()
        snapshot.wait_ready(args.wait)
        code = snapshot.fetch(fetch_args)
    except Interrupted as exc:
        code = 128 + exc.signum
    except (SnapshotError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"snapshot failed: {exc}", file=sys.stderr)
    finally:
        for s in previous:
            signal.signal(s, signal.SIG_IGN)  # cleanup must not be interrupted
        try:
            snapshot.release()
        except (SnapshotError, OSError) as exc:
            print(f"snapshot cleanup failed: {exc}", file=sys.stderr)
            code = code if code else 3
        finally:
            for s, handler in previous.items():
                signal.signal(s, handler)
    return code


if __name__ == "__main__":
    sys.exit(main())
