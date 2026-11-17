#!/usr/bin/env python3
"""Build chatlog-snapshot-reader from an existing chatlog source tree.

The reader's Go files are added with `go build -overlay`, so the chatlog source
tree is never modified. Requires Go with CGO and a chatlog source version that
provides modules.PlatformFactory and chatlog.NewApplicationWithFactory.

Example:
  python3 build_reader.py --chatlog-src ~/src/chatlog --out ~/bin/chatlog-snapshot-reader
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = [
    "internal/chatlog/modules/snapshot.go",
    "internal/chatlog/modules/snapshot_test.go",
    "cmd/snapshot-reader/main.go",
]
MODULE = "module github.com/sjzar/chatlog"
API_HINT = ("this chatlog source is probably too old: the snapshot reader needs "
            "modules.PlatformFactory, messagehook.NewWithNative, ports.KeyCaptureTarget "
            "and chatlog.NewApplicationWithFactory")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chatlog-src", required=True, help="chatlog source directory (contains go.mod)")
    parser.add_argument("--out", required=True, help="output binary path, e.g. ~/bin/chatlog-snapshot-reader")
    parser.add_argument("--skip-tests", action="store_true", help="do not run the snapshot Go tests")
    args = parser.parse_args(argv)

    src = Path(args.chatlog_src).expanduser().resolve()
    go_mod = src / "go.mod"
    if not go_mod.is_file() or MODULE not in go_mod.read_text(encoding="utf-8").splitlines()[0:5]:
        print(f"error: {src} is not a chatlog source tree ({MODULE} not found in go.mod)", file=sys.stderr)
        return 2
    if shutil.which("go") is None:
        print("error: go is not installed or not on PATH", file=sys.stderr)
        return 2
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ, CGO_ENABLED="1")
    with tempfile.TemporaryDirectory(prefix="snapshot-reader-overlay-") as tmp:
        overlay = Path(tmp) / "overlay.json"
        replace = {str(src / rel): str(HERE / "src" / rel) for rel in SOURCES}
        overlay.write_text(json.dumps({"Replace": replace}), encoding="utf-8")
        steps = []
        if not args.skip_tests:
            steps.append(["go", "test", "-overlay", str(overlay), "-tags", "sqlite_fts5",
                          "./internal/chatlog/modules/", "-run", "Snapshot"])
        steps.append(["go", "build", "-overlay", str(overlay), "-tags", "sqlite_fts5",
                      "-o", str(out), "./cmd/snapshot-reader"])
        for command in steps:
            print("+ " + " ".join(command), flush=True)
            result = subprocess.run(command, cwd=src, env=env, capture_output=True, text=True)
            output = result.stdout + result.stderr
            if output.strip():
                print(output.rstrip())
            if result.returncode != 0:
                if "undefined:" in output:
                    print("hint: " + API_HINT, file=sys.stderr)
                return result.returncode
    print(f"built {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
