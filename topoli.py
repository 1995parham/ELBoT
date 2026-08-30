#!/usr/bin/env python3
"""Automated Topoli — Telegram secretary bot helper.

Drives @parham_alvani_bot over the Telegram Bot API. The bot is connected to
Parham's account as a **Telegram Business chatbot**, which is what makes it able
to read his private chats and send messages that appear as him rather than as a
bot.

Deliberately stdlib-only: no pip install on a fresh machine, and no dependency
that could break in the middle of an errand.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

API = "https://api.telegram.org"

SKILL_DIR = Path(__file__).resolve().parent
STATE_DIR = Path(
    os.environ.get("TOPOLI_STATE_DIR", Path.home() / ".local/state/automated-topoli")
)
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = STATE_DIR / "messages.jsonl"


# --------------------------------------------------------------------------
# config / state
# --------------------------------------------------------------------------


def token() -> str:
    tok = os.environ.get("TOPOLI_BOT_TOKEN")
    if tok:
        return tok.strip()

    token_file = Path(os.environ.get("TOPOLI_TOKEN_FILE", SKILL_DIR / "token"))
    if token_file.is_file():
        return token_file.read_text().strip()
    die("no bot token: set TOPOLI_BOT_TOKEN or create the token file")

    die(
        f"no bot token: set TOPOLI_BOT_TOKEN or write it to {token_file}",
    )


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"offset": 0, "connections": {}}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(STATE_FILE)
    STATE_FILE.chmod(0o600)


def die(msg: str) -> NoReturn:
    print(f"topoli: {msg}", file=sys.stderr)
    raise SystemExit(1)


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------


def opener():
    """Build a urllib opener with deterministic proxy behaviour.

    The login shell exports a lowercase `all_proxy`, and urllib would silently
    honour it. api.telegram.org is reachable directly from the house uplink, so
    ambient proxies are ignored unless TOPOLI_PROXY explicitly asks for one.
    Otherwise a stale proxy turns every call into a confusing timeout.
    """
    proxy = os.environ.get("TOPOLI_PROXY")
    handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy} if proxy else {})
    return urllib.request.build_opener(handler)


MAX_UPLOAD = 50 * 1024 * 1024  # Bot API ceiling for a bot-uploaded file


def encode_multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    """Build a multipart/form-data body by hand.

    urlencode cannot carry a file, and the alternative — handing Telegram a
    public URL to fetch — would mean publishing whatever is being sent. These
    are usually private documents, so they go up as a real upload instead.
    """
    boundary = "----topoli" + secrets.token_hex(16)
    sep = f"--{boundary}\r\n".encode()
    body = bytearray()

    for key, value in fields.items():
        if value is None:
            continue
        body += sep
        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        body += str(value).encode() + b"\r\n"

    for key, path in files.items():
        path = Path(path)
        name = path.name.replace('"', "")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body += sep
        body += (
            f'Content-Disposition: form-data; name="{key}"; filename="{name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        body += path.read_bytes() + b"\r\n"

    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def call(method: str, params: dict | None = None, timeout: int = 30,
         files: dict | None = None) -> dict:
    url = f"{API}/bot{token()}/{method}"
    data = None
    headers = {}
    clean = {k: v for k, v in (params or {}).items() if v is not None}

    if files:
        data, content_type = encode_multipart(clean, files)
        headers["Content-Type"] = content_type
    elif clean:
        data = urllib.parse.urlencode(clean).encode()

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with opener().open(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            die(f"{method}: HTTP {exc.code}: {body[:400]}")
        die(f"{method}: {payload.get('error_code')} {payload.get('description')}")
    except urllib.error.URLError as exc:
        die(f"{method}: cannot reach {API}: {exc.reason}")

    if not payload.get("ok"):
        die(f"{method}: {payload.get('description')}")
    return payload["result"]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def ts(epoch: int | None) -> str:
    if not epoch:
        return "?"
    return datetime.fromtimestamp(epoch, UTC).astimezone().strftime("%Y-%m-%d %H:%M")


def who(user: dict | None) -> str:
    if not user:
        return "?"
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
    handle = user.get("username")
    return f"{name} (@{handle})" if handle else name or str(user.get("id"))


def chat_label(chat: dict | None) -> str:
    if not chat:
        return "?"
    if chat.get("title"):
        return chat["title"]
    return who(chat)


def body(msg: dict) -> str:
    if msg.get("text"):
        return msg["text"]
    if msg.get("caption"):
        return f"[{media_kind(msg)}] {msg['caption']}"
    kind = media_kind(msg)
    return f"[{kind}]" if kind else "[non-text message]"


def media_kind(msg: dict) -> str:
    for key in (
        "photo", "video", "voice", "audio", "document", "sticker",
        "animation", "video_note", "contact", "location", "poll",
    ):
        if key in msg:
            return key
    return ""


def primary_connection(state: dict) -> dict:
    conns = [c for c in state.get("connections", {}).values() if c.get("is_enabled")]
    if not conns:
        die(
            "no active business connection recorded. Run `topoli.py poll --once` "
            "to capture it, and check Telegram → Settings → Telegram Business → "
            "Chatbots that @parham_alvani_bot is still connected."
        )
    return max(conns, key=lambda c: c.get("date", 0))


def append_log(records: list[dict]) -> None:
    if not records:
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    LOG_FILE.chmod(0o600)


def read_log(limit: int | None = None, chat: str | None = None) -> list[dict]:
    if not LOG_FILE.is_file():
        return []
    rows = []
    with LOG_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if chat and str(rec.get("chat_id")) != str(chat):
                continue
            rows.append(rec)
    return rows[-limit:] if limit else rows


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_whoami(args) -> None:
    me = call("getMe")
    print(f"bot        : {me.get('first_name')} (@{me.get('username')})  id={me.get('id')}")
    group_reads = (
        "all messages"
        if me.get("can_read_all_group_messages")
        else "mentions/commands only (privacy mode ON)"
    )
    print(f"group reads: {group_reads}")

    state = load_state()
    conns = state.get("connections", {})
    if not conns:
        print("business   : none recorded yet — run `poll --once`")
        return

    for conn in conns.values():
        rights = conn.get("rights", {}) or {}
        print()
        print(f"business connection {conn.get('id')}")
        print(f"  account  : {who(conn.get('user'))}  id={conn.get('user', {}).get('id')}")
        print(f"  enabled  : {conn.get('is_enabled')}")
        print(f"  connected: {ts(conn.get('date'))}")
        print(f"  can read : {rights.get('can_read_messages')}")
        print(f"  can reply: {rights.get('can_reply')}")

    print()
    print(f"captured : {len(read_log())} messages in {LOG_FILE}")


def cmd_poll(args) -> None:
    state = load_state()
    deadline = None if args.follow else time.time() + max(args.timeout, 1)
    seen = 0

    while True:
        updates = call(
            "getUpdates",
            {
                "offset": state["offset"] or None,
                "timeout": args.long_poll,
                "limit": 100,
            },
            timeout=args.long_poll + 20,
        )

        records = []
        for upd in updates:
            state["offset"] = upd["update_id"] + 1

            if "business_connection" in upd:
                conn = upd["business_connection"]
                state.setdefault("connections", {})[conn["id"]] = conn
                print(
                    f"[connection] {who(conn.get('user'))} "
                    f"{'enabled' if conn.get('is_enabled') else 'DISABLED'}"
                )
                continue

            key = next(
                (k for k in ("business_message", "edited_business_message", "message", "edited_message") if k in upd),
                None,
            )
            if not key:
                continue

            msg = upd[key]
            conn_id = msg.get("business_connection_id")
            # Plain `message` updates carry no business_connection_id, so fall
            # back to every account we hold a connection for. Without this, a
            # message Parham sent himself in a group is rendered as incoming.
            owners = {
                c.get("user", {}).get("id")
                for c in state.get("connections", {}).values()
            }
            owners.discard(None)
            owner = None
            if conn_id and conn_id in state.get("connections", {}):
                owner = state["connections"][conn_id].get("user", {}).get("id")

            rec = {
                "update_id": upd["update_id"],
                "kind": key,
                "message_id": msg.get("message_id"),
                "date": msg.get("date"),
                "business_connection_id": conn_id,
                "chat_id": msg.get("chat", {}).get("id"),
                "chat": chat_label(msg.get("chat")),
                "chat_type": msg.get("chat", {}).get("type"),
                "from_id": msg.get("from", {}).get("id"),
                "from": who(msg.get("from")),
                "outgoing": msg.get("from", {}).get("id") in ({owner} if owner else owners),
                "reply_to": (msg.get("reply_to_message") or {}).get("message_id"),
                "text": body(msg),
            }
            records.append(rec)
            arrow = "->" if rec["outgoing"] else "<-"
            print(f"[{ts(rec['date'])}] {arrow} {rec['chat']}: {rec['text'][:120]}")

        append_log(records)
        save_state(state)
        seen += len(records)

        if args.once:
            break
        if deadline and time.time() >= deadline:
            break

    print(f"-- captured {seen} message(s); offset now {state['offset']}", file=sys.stderr)


def cmd_inbox(args) -> None:
    rows = read_log(limit=args.limit, chat=args.chat)
    if not rows:
        print("no messages captured yet — run `topoli.py poll` first (Bot API cannot backfill history)")
        return
    for rec in rows:
        arrow = "->" if rec.get("outgoing") else "<-"
        print(f"[{ts(rec.get('date'))}] {arrow} {rec.get('chat')} (chat_id={rec.get('chat_id')})")
        print(f"    {rec.get('from')}: {rec.get('text')}")


def cmd_chats(args) -> None:
    rows = read_log()
    if not rows:
        print("no chats captured yet — run `topoli.py poll` first")
        return
    chats: dict = {}
    for rec in rows:
        cid = rec.get("chat_id")
        entry = chats.setdefault(cid, {"label": rec.get("chat"), "n": 0, "last": None, "type": rec.get("chat_type")})
        entry["n"] += 1
        if not entry["last"] or (rec.get("date") or 0) >= (entry["last"].get("date") or 0):
            entry["last"] = rec
    for cid, entry in sorted(chats.items(), key=lambda kv: kv[1]["last"].get("date") or 0, reverse=True):
        last = entry["last"]
        print(f"{cid:>16}  {entry['label']}  ({entry['type']}, {entry['n']} msg)")
        print(f"                  last {ts(last.get('date'))}: {last.get('text')[:100]}")


def cmd_send(args) -> None:
    state = load_state()

    params = {
        "chat_id": args.chat,
        "text": args.text,
        "reply_to_message_id": args.reply_to,
    }
    if args.parse_mode:
        params["parse_mode"] = args.parse_mode

    if args.as_bot:
        identity = "the BOT (@parham_alvani_bot)"
    else:
        conn = primary_connection(state)
        if not (conn.get("rights") or {}).get("can_reply"):
            die("business connection does not grant can_reply")
        params["business_connection_id"] = conn["id"]
        identity = f"{who(conn.get('user'))} — appears as a message from Parham himself"

    print("about to send")
    print(f"  as   : {identity}")
    print(f"  chat : {args.chat}")
    print(f"  text : {args.text}")

    if not args.yes:
        print()
        print("DRY RUN — nothing sent. Re-run with --yes to actually deliver.")
        return

    res = call("sendMessage", params)
    print(f"sent: message_id={res.get('message_id')} at {ts(res.get('date'))}")


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}MB"


def cmd_send_file(args) -> None:
    """Upload one or more local files into a chat.

    Sent one per call rather than as a media group: a failure then names the
    file that failed instead of rolling back an opaque batch, and the per-file
    pause keeps a long run under Telegram's flood limit.
    """
    state = load_state()

    paths = []
    for raw in args.file:
        path = Path(raw).expanduser()
        if not path.is_file():
            die(f"not a file: {path}")
        size = path.stat().st_size
        if size > MAX_UPLOAD:
            die(f"{path.name} is {human_size(size)}; the Bot API caps uploads at 50MB")
        paths.append(path)

    method, field = ("sendPhoto", "photo") if args.photo else ("sendDocument", "document")

    base = {"chat_id": args.chat, "reply_to_message_id": args.reply_to}
    if args.as_bot:
        identity = "the BOT (@parham_alvani_bot)"
    else:
        conn = primary_connection(state)
        if not (conn.get("rights") or {}).get("can_reply"):
            die("business connection does not grant can_reply")
        base["business_connection_id"] = conn["id"]
        identity = f"{who(conn.get('user'))} — appears as a message from Parham himself"

    total = sum(path.stat().st_size for path in paths)
    print(f"about to send {len(paths)} file(s) via {method}")
    print(f"  as   : {identity}")
    print(f"  chat : {args.chat}")
    if args.caption:
        print(f"  cap  : {args.caption}   (on the first file only)")
    for path in paths:
        print(f"    - {path.name}  {human_size(path.stat().st_size)}")
    print(f"  total: {human_size(total)}")

    if not args.yes:
        print()
        print("DRY RUN — nothing sent. Re-run with --yes to actually deliver.")
        return

    for i, path in enumerate(paths):
        params = dict(base)
        if args.caption and i == 0:
            params["caption"] = args.caption
        res = call(method, params, timeout=args.timeout, files={field: path})
        print(f"sent {path.name}: message_id={res.get('message_id')}")
        if i != len(paths) - 1:
            time.sleep(args.delay)


def cmd_raw(args) -> None:
    params = {}
    for item in args.params:
        if "=" not in item:
            die(f"bad param {item!r}, expected key=value")
        k, v = item.split("=", 1)
        params[k] = v
    print(json.dumps(call(args.method, params), indent=2, ensure_ascii=False))


def cmd_seed(args) -> None:
    """Record a business connection id by hand (verified against the API)."""
    conn = call("getBusinessConnection", {"business_connection_id": args.connection_id})
    state = load_state()
    state.setdefault("connections", {})[conn["id"]] = conn
    save_state(state)
    print(f"recorded connection {conn['id']} for {who(conn.get('user'))}")


# --------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(prog="topoli.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="show bot identity and business authorisation").set_defaults(fn=cmd_whoami)

    sp = sub.add_parser("poll", help="fetch new updates and append them to the local log")
    sp.add_argument("--once", action="store_true", help="one getUpdates round trip then exit")
    sp.add_argument("--follow", action="store_true", help="poll forever")
    sp.add_argument("--timeout", type=int, default=30, help="stop after N seconds (default 30)")
    sp.add_argument("--long-poll", type=int, default=20, help="server-side long poll seconds")
    sp.set_defaults(fn=cmd_poll)

    sp = sub.add_parser("inbox", help="print captured messages")
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--chat", help="restrict to one chat_id")
    sp.set_defaults(fn=cmd_inbox)

    sub.add_parser("chats", help="list chats seen so far").set_defaults(fn=cmd_chats)

    sp = sub.add_parser("send", help="send a message (as Parham by default)")
    sp.add_argument("--chat", required=True, help="chat_id or @username")
    sp.add_argument("--text", required=True)
    sp.add_argument("--reply-to", type=int, help="message_id to reply to")
    sp.add_argument("--parse-mode", choices=["HTML", "Markdown", "MarkdownV2"])
    sp.add_argument("--as-bot", action="store_true", help="send as the bot instead of as Parham")
    sp.add_argument("--yes", action="store_true", help="actually send (otherwise dry run)")
    sp.set_defaults(fn=cmd_send)

    sp = sub.add_parser("send-file", help="upload files to a chat (as Parham by default)")
    sp.add_argument("--chat", required=True, help="chat_id or @username")
    sp.add_argument("--file", required=True, action="append",
                    help="path to send; repeat for several, sent in order")
    sp.add_argument("--caption", help="caption, applied to the first file only")
    sp.add_argument("--photo", action="store_true",
                    help="send as a photo instead of a document (recompressed by Telegram)")
    sp.add_argument("--reply-to", type=int, help="message_id to reply to")
    sp.add_argument("--as-bot", action="store_true", help="send as the bot instead of as Parham")
    sp.add_argument("--delay", type=float, default=1.0, help="seconds between files (default 1)")
    sp.add_argument("--timeout", type=int, default=180, help="per-upload timeout (default 180)")
    sp.add_argument("--yes", action="store_true", help="actually send (otherwise dry run)")
    sp.set_defaults(fn=cmd_send_file)

    sp = sub.add_parser("seed", help="record a known business_connection_id")
    sp.add_argument("connection_id")
    sp.set_defaults(fn=cmd_seed)

    sp = sub.add_parser("raw", help="call any Bot API method")
    sp.add_argument("method")
    sp.add_argument("params", nargs="*", help="key=value pairs")
    sp.set_defaults(fn=cmd_raw)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
