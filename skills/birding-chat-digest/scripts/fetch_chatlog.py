#!/usr/bin/env python3
"""Fetch every message in a time window from a running chatlog HTTP service.

Read-only: only GET requests to chatlog's local API (/api/v1/history,
/api/v1/members, /api/v1/contacts). Writes index.json plus one JSON file per
group, the input format described in references/input-format.md.

Examples:
  python3 fetch_chatlog.py --groups groups.json --date 2026-11-16 --out ./export
  python3 fetch_chatlog.py --chat 12345678@chatroom=示例观鸟群 \
      --start 2026-11-15T21:00 --end 2026-11-16T21:00 --tz Asia/Shanghai --out ./export
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_SERVER = "http://127.0.0.1:5030"
PAGE_SIZE = 500
NAMING_POLICY = "group_nickname_then_wechat_nickname"


class FetchError(RuntimeError):
    """A request that still failed after retries. Message never contains chat ids."""


def log(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), file=sys.stderr, flush=True)


def chat_hash(chat: str) -> str:
    return hashlib.sha256(chat.encode()).hexdigest()[:12]


def get_json(url: str, timeout: int, retries: int = 5) -> dict:
    """GET with retries on timeouts, connection errors, 500 and 503."""
    path = urllib.parse.urlsplit(url).path
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code not in (500, 503):
                raise
            reason = f"HTTP {exc.code}"
            exc.close()
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = type(exc).__name__
        log("request_failed", endpoint=path, attempt=attempt, reason=reason, will_retry=attempt < retries)
        if attempt < retries:
            time.sleep(min(8, 2 ** (attempt - 1)))
    raise FetchError(f"{path} failed after {retries} attempts ({reason})")


def message_key(message: dict) -> str:
    if message.get("message_id"):
        return f"id:{message['message_id']}"
    raw = "|".join(str(message.get(k) or "") for k in ("timestamp", "sender_id", "sender", "type", "content"))
    return "h:" + hashlib.sha1(raw.encode()).hexdigest()


def fetch_day(server: str, chat: str, day: str, timeout: int, page_size: int = PAGE_SIZE) -> tuple[int, list[dict]]:
    """Fetch all pages of one calendar day. Returns (server total_count, unique messages)."""
    seen: dict[str, dict] = {}
    offset, total, limit = 0, 0, page_size
    while True:
        query = urllib.parse.urlencode({"chat": chat, "time": day, "limit": limit, "offset": offset})
        try:
            data = get_json(f"{server}/api/v1/history?{query}", timeout)
        except urllib.error.HTTPError as exc:
            # Newer chatlog builds answer 413 with a smaller retry_limit when a page
            # is too large. Retry the same offset so no message is skipped.
            if exc.code != 413:
                raise
            with exc:
                try:
                    budget = json.loads(exc.read(64 * 1024))
                except ValueError:
                    raise exc
            retry = budget.get("retry_limit") if isinstance(budget, dict) else None
            if not (isinstance(budget, dict) and budget.get("retryable") is True
                    and type(retry) is int and 0 < retry < limit):
                raise
            limit = retry
            continue
        total = int(data.get("total_count") or 0)
        page = data.get("messages") or []
        for message in page:
            seen[message_key(message)] = message
        offset += len(page)
        if len(page) < limit or (total and offset >= total) or offset > 500_000:
            break
    return total, list(seen.values())


def fetch_window(server: str, chat: str, start: dt.datetime, end: dt.datetime,
                 timeout: int, page_size: int = PAGE_SIZE) -> tuple[list[dict], list[dict]]:
    """Fetch every calendar day the window touches, keep messages in [start, end)."""
    kept: dict[str, dict] = {}
    coverage = []
    day = start.date()
    last_day = (end - dt.timedelta(microseconds=1)).date()
    while day <= last_day:
        total, messages = fetch_day(server, chat, day.isoformat(), timeout, page_size)
        coverage.append({"date": day.isoformat(), "total_count": total, "fetched": len(messages)})
        for message in messages:
            # A missing or invalid timestamp must fail loudly, not silently drop the message.
            if start.timestamp() <= float(message["timestamp"]) < end.timestamp():
                kept[message_key(message)] = message
        day += dt.timedelta(days=1)
    ordered = sorted(kept.values(), key=lambda m: (m["timestamp"], m.get("message_id") or 0))
    return ordered, coverage


def apply_public_names(server: str, chat: str, messages: list[dict], timeout: int) -> dict:
    """Name senders by group nickname, then WeChat nickname. Never by the owner's contact remark."""
    names: dict[str, tuple[str, str]] = {}
    if messages and chat.endswith("@chatroom"):
        data = get_json(f"{server}/api/v1/members?{urllib.parse.urlencode({'chat': chat})}", timeout)
        if not isinstance(data.get("members"), list):
            raise FetchError("members lookup returned no member list")
        for member in data["members"]:
            uid, name = member.get("username"), (member.get("display") or "").strip()
            source = member.get("display_source", "group_nickname")  # older builds omit the field
            if uid and name and name != uid and source in ("group_nickname", "contact_nickname"):
                names[uid] = (name, "group_nickname" if source == "group_nickname" else "wechat_nickname")

    resolved: dict[str, tuple[str, str]] = {}

    def resolve(uid: str) -> tuple[str, str]:
        if not uid:
            return "", "unavailable"
        if uid not in resolved:
            name, source = names.get(uid, ("", "unavailable"))
            if not name:
                query = urllib.parse.urlencode({"query": uid, "limit": 500})
                data = get_json(f"{server}/api/v1/contacts?{query}", timeout)
                if not isinstance(data.get("contacts"), list):
                    raise FetchError("contacts lookup returned no contact list")
                name = next(((c.get("nickname") or "").strip() for c in data["contacts"]
                             if c.get("username") == uid), "")
                source = "wechat_nickname" if name else "unavailable"
            resolved[uid] = (name, source)
        return resolved[uid]

    def fix_quotes(value) -> None:
        # Quoted messages carry the old display name, which may be a contact remark.
        if isinstance(value, dict):
            if "senderName" in value and "sender" in value:
                value["senderName"] = resolve(value.get("sender") or "")[0]
            for child in value.values():
                fix_quotes(child)
        elif isinstance(value, list):
            for child in value:
                fix_quotes(child)

    senders = set()
    for message in messages:
        if message.get("type") == "system":
            message["sender"], message["sender_name_source"] = "", "system"
            continue
        uid = message.get("sender_id") or ""
        if uid:
            senders.add(uid)
        message["sender"], message["sender_name_source"] = resolve(uid)
        fix_quotes(message.get("contents"))
    unresolved = sum(1 for uid in senders if resolve(uid)[1] == "unavailable")
    return {"policy": NAMING_POLICY, "unresolved_senders": unresolved}


def parse_time(value: str, zone: ZoneInfo) -> dt.datetime:
    moment = dt.datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=zone)


def load_groups(args: argparse.Namespace) -> list[dict]:
    groups = []
    if args.groups:
        data = json.loads(Path(args.groups).read_text(encoding="utf-8"))
        groups = data["groups"] if isinstance(data, dict) else data
    for item in args.chat or []:
        chat, _, name = item.partition("=")
        groups.append({"chat": chat, "name": name or chat})
    if not groups:
        raise SystemExit("provide --groups FILE or at least one --chat")
    for group in groups:
        if not group.get("chat"):
            raise SystemExit(f"group without chat id: {group}")
        group.setdefault("name", group["chat"])
    return groups


def slugify(chat: str) -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "-", re.sub(r"@chatroom$", "", chat)).strip("-")
    return value[:50] or "chat"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--groups", help='JSON file: {"groups": [{"name": "...", "chat": "...@chatroom"}]}')
    parser.add_argument("--chat", action="append", help="chat id, optionally ID=显示名; repeatable")
    parser.add_argument("--date", help="whole calendar day YYYY-MM-DD (alternative to --start/--end)")
    parser.add_argument("--start", help="window start, inclusive, ISO datetime")
    parser.add_argument("--end", help="window end, exclusive, ISO datetime")
    parser.add_argument("--tz", default="Asia/Shanghai", help="timezone for dates and naive datetimes")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE, choices=range(1, 501), metavar="1-500")
    args = parser.parse_args(argv)

    zone = ZoneInfo(args.tz)
    if args.date and not (args.start or args.end):
        start = dt.datetime.combine(dt.date.fromisoformat(args.date), dt.time(), zone)
        end = start + dt.timedelta(days=1)
    elif args.start and args.end and not args.date:
        start, end = parse_time(args.start, zone), parse_time(args.end, zone)
    else:
        parser.error("use either --date, or both --start and --end")
    if end <= start:
        parser.error("--end must be later than --start")

    groups = load_groups(args)
    server = args.server.rstrip("/")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # Remove results of an earlier run in this directory first, so a group that
    # fails now can never be read from a stale file.
    for stale in [out / "index.json", *(out / f"{slugify(g['chat'])}.json" for g in groups)]:
        stale.unlink(missing_ok=True)
    index = {
        "date": (end - dt.timedelta(microseconds=1)).astimezone(zone).date().isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "timezone": args.tz,
        "groups": [],
    }
    failures = total = 0
    for group in groups:
        entry = {"name": group["name"]}
        try:
            messages, coverage = fetch_window(server, group["chat"], start, end, args.timeout, args.page_size)
            naming = apply_public_names(server, group["chat"], messages, args.timeout)
            path = out / f"{slugify(group['chat'])}.json"
            payload = {
                "name": group["name"],
                "window_start": index["window_start"],
                "window_end": index["window_end"],
                "fetched": len(messages),
                "source_coverage": coverage,
                "naming_coverage": naming,
                "type_counts": dict(Counter(m.get("type") or "unknown" for m in messages)),
                "messages": messages,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            entry.update(file=path.name, fetched=len(messages), source_coverage=coverage,
                         naming_coverage=naming, type_counts=payload["type_counts"])
            total += len(messages)
        except Exception as exc:  # one failed group must not hide the others
            failures += 1
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
            log("group_failed", chat_hash=chat_hash(group["chat"]), error=entry["error"])
        index["groups"].append(entry)
    index.update(total_messages=total, failures=failures)
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": failures == 0, "index": str(out / "index.json"),
                      "total_messages": total, "failures": failures}, ensure_ascii=False))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
