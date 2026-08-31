#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["telethon>=1.41", "python-socks[asyncio]>=2.4"]
# ///
"""Automated Topoli — user-account mode (MTProto via Telethon).

The Telegram Bot API only ever lets a bot receive an event stream: it cannot
read history, and it must be listening when a message arrives. This logs in as
Parham's own account over MTProto, the same protocol the desktop client speaks,
so it can read any chat backwards and send as him — with no poller and no
server.

The price is the auth key. It is a bearer credential for the whole account that
does not re-prompt for 2FA: whoever holds it is logged in as Parham, and git
history would keep it forever. It therefore lives outside this repository —
`TOPOLI_SESSION_STRING`, or a `*.authkey` file that `.gitignore` blocks — in a
private store alongside `api.json`.

Dependencies are declared inline (PEP 723): `uv run topoli_user.py ...` installs
telethon on first use and needs no venv of its own.
"""

import argparse
import asyncio
import binascii
import contextlib
import json
import os
import struct
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse

try:
    from telethon import TelegramClient, errors, utils
    from telethon.extensions import html as _html
    from telethon.extensions import markdown as _markdown
    from telethon.sessions import SQLiteSession, StringSession
    from telethon.tl.functions.channels import CreateChannelRequest
    from telethon.tl.functions.contacts import GetContactsRequest
    from telethon.tl.functions.messages import (
        GetDialogFiltersRequest,
        SendReactionRequest,
        ToggleDialogFilterTagsRequest,
        UpdateDialogFilterRequest,
    )
    from telethon.tl.types import (
        Channel,
        Chat,
        DialogFilterChatlist,
        MessageEntityBlockquote,
        ReactionEmoji,
        User,
    )
except ImportError:  # pragma: no cover - guidance beats a traceback
    sys.exit(
        "telethon is missing. Run this script with uv so the inline dependency "
        "is honoured:\n    uv run topoli_user.py <command>"
    )

SKILL_DIR = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("TOPOLI_STATE_DIR", Path.home() / ".local/state/automated-topoli"))

# api_id/api_hash identify the *application*, not the account: they cannot log
# anyone in without the phone, the code and the 2FA password, so they live in
# the repo and travel to every machine.
REPO_CONFIG = SKILL_DIR / "api.json"
CONFIG_FILE = STATE_DIR / "user.json"
INDEX_FILE = STATE_DIR / "chat-index.json"  # cached title->id map for fast resolve
PENDING = STATE_DIR / "login-pending.json"

# "wife" is not a Telegram concept, and two contacts can share a display name,
# so a title lookup alone cannot settle who is meant. The alias book is the
# answer to that: a hand-written name -> id map that resolves before anything
# else and never goes ambiguous. It sits beside api.json (so it travels with the
# private repo, to every machine) with a machine-local override in the state dir.
ALIAS_REPO = SKILL_DIR / "aliases.json"
ALIAS_LOCAL = STATE_DIR / "aliases.json"

# The credential is split from the cache, and neither is committed here.
#
# `topoli.authkey` is a telethon session string: one stable ~350-byte line
# holding the dc, its address and the auth key — everything, and only what, it
# takes to be logged in. That one line is the whole login, which is exactly why
# it is a password and why `.gitignore` blocks it.
#
# `topoli.session` is telethon's SQLite session — the same auth key plus a cache
# of entity access-hashes and per-chat pts counters. Telethon restamps every
# cached entity with `int(time.time())` on every run, so keeping it under version
# control rewrote ~141 identical rows in each diff for no information at all. It
# is rebuilt from the authkey on demand and lives in the state directory instead,
# where it can churn freely.
#
# TOPOLI_SESSION moves the cache; TOPOLI_SESSION_STRING supplies the credential
# straight from the environment, with no file on disk at all.
SESSION = Path(os.environ.get("TOPOLI_SESSION", STATE_DIR / "topoli.session"))
SESSION_STRING = SKILL_DIR / "topoli.authkey"


def die(msg: str) -> NoReturn:
    print(f"topoli-user: {msg}", file=sys.stderr)
    raise SystemExit(1)


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------


def load_config() -> dict:
    """Merge the repo defaults with any machine-local override."""
    cfg: dict = {}
    for path in (REPO_CONFIG, CONFIG_FILE):  # later wins
        if path.is_file():
            with contextlib.suppress(json.JSONDecodeError):
                cfg.update(json.loads(path.read_text()))
    return cfg


def read_json(path: Path) -> dict:
    """Whatever the file holds, or {} if it is missing or not valid JSON."""
    if not path.is_file():
        return {}
    with contextlib.suppress(json.JSONDecodeError, OSError):
        return json.loads(path.read_text())
    return {}


def load_aliases() -> dict:
    """The merged alias book, keyed lowercase so lookups are case-insensitive."""
    out: dict = {}
    for path in (ALIAS_REPO, ALIAS_LOCAL):  # later wins
        out.update({str(k).lower(): v for k, v in read_json(path).items()})
    return out


def save_aliases(rows: dict, local: bool) -> Path:
    path = ALIAS_LOCAL if local else ALIAS_REPO
    if local:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return path


def credentials() -> tuple[int, str]:
    cfg = load_config()
    api_id = os.environ.get("TOPOLI_API_ID") or cfg.get("api_id")
    api_hash = os.environ.get("TOPOLI_API_HASH") or cfg.get("api_hash")
    if not api_id or not api_hash:
        die(
            "no api_id/api_hash. Get them from https://my.telegram.org → API "
            "development tools, then run:\n"
            "    uv run topoli_user.py setup --api-id <id> --api-hash <hash>"
        )
    return int(api_id), str(api_hash)


def proxy() -> tuple[str, str, int] | None:
    """Telegram is blocked on the house uplink, so MTProto needs a tunnel.

    Telethon ignores the ambient proxy env vars that urllib honours, so read
    them here: TOPOLI_PROXY wins, otherwise the usual lowercase suspects.
    """
    url = (
        os.environ.get("TOPOLI_PROXY")
        or os.environ.get("all_proxy")  # noqa: SIM112 (proxy vars are lowercase by convention)
        or os.environ.get("https_proxy")
        or os.environ.get("http_proxy")
    )
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"http://{url}")
    scheme = {"socks5h": "socks5", "socks4a": "socks4"}.get(parsed.scheme, parsed.scheme)
    if scheme not in ("http", "socks4", "socks5"):
        die(f"unsupported proxy scheme {parsed.scheme!r} in {url!r}")
    if not parsed.hostname or not parsed.port:
        die(f"proxy {url!r} needs an explicit host and port")
    return (scheme, parsed.hostname, parsed.port)


def session_string() -> tuple[str, str] | None:
    """The stored auth key, unless the environment overrides it."""
    raw = os.environ.get("TOPOLI_SESSION_STRING")
    source = "TOPOLI_SESSION_STRING"
    if raw is None and SESSION_STRING.is_file():
        raw, source = SESSION_STRING.read_text(), str(SESSION_STRING)
    return (raw.strip(), source) if raw and raw.strip() else None


def open_session() -> SQLiteSession:
    """Build the local SQLite cache, seeding it from the stored auth key."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    sess = SQLiteSession(str(SESSION.with_suffix("")))

    found = session_string()
    if not found:
        return sess  # nothing stored yet — `login` will create it
    text, source = found
    try:
        repo = StringSession(text)
    except ValueError, struct.error, binascii.Error:
        die(f"{source} does not hold a valid telethon session string")
    if not repo.auth_key or repo.auth_key.key == getattr(sess.auth_key, "key", None):
        return sess

    # The repo wins on a mismatch. Otherwise rotating the key here would leave
    # every other clone quietly authenticating with a revoked one, and the
    # cached access-hashes belong to that dead session anyway.
    sess.close()
    SESSION.unlink(missing_ok=True)
    sess = SQLiteSession(str(SESSION.with_suffix("")))
    sess.set_dc(repo.dc_id, repo.server_address, repo.port)  # this re-reads the
    sess.auth_key = repo.auth_key  # key, so it is first
    sess.save()
    return sess


def client() -> TelegramClient:
    api_id, api_hash = credentials()
    return TelegramClient(open_session(), api_id, api_hash, proxy=proxy())  # ty: ignore[invalid-argument-type]


def export_session(cli) -> None:
    """Persist the auth key beside the script so later runs skip the login."""
    text = StringSession.save(cli.session)
    if not text:
        return
    SESSION_STRING.write_text(text + "\n")
    SESSION_STRING.chmod(0o600)


def secure_session() -> None:
    for path in (SESSION, SESSION_STRING, CONFIG_FILE):
        if path.is_file():
            path.chmod(0o600)


# --------------------------------------------------------------------------
# formatting / entity resolution
# --------------------------------------------------------------------------


def emit(rows, args) -> bool:
    """Print rows as JSON if --json was asked for; report whether it did.

    Every read command ends with `if emit(...): return`, so the human format
    below it stays the readable default and the machine format is one flag away.
    """
    if not getattr(args, "json", False):
        return False
    print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    return True


def parse_when(text: str | None, flag: str) -> datetime | None:
    """An ISO date or datetime, read as local time unless it carries an offset."""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        die(f"{flag} wants an ISO date like 2026-08-01 or 2026-08-01T14:30, got {text!r}")
    return dt.astimezone(UTC)  # a naive value is taken as this machine's local time


def ts(dt: datetime | None) -> str:
    if not dt:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def label(entity) -> str:
    if isinstance(entity, User):
        name = " ".join(x for x in (entity.first_name, entity.last_name) if x)
        if entity.username:
            return f"{name or entity.username} (@{entity.username})"
        return name or str(entity.id)
    if isinstance(entity, (Chat, Channel)):
        title = getattr(entity, "title", None) or str(entity.id)
        uname = getattr(entity, "username", None)
        return f"{title} (@{uname})" if uname else title
    return str(getattr(entity, "id", entity))


def kind(entity) -> str:
    if isinstance(entity, User):
        return "bot" if getattr(entity, "bot", False) else "private"
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, Channel):
        return "supergroup" if getattr(entity, "megagroup", False) else "channel"
    return "?"


def entity_row(entity) -> dict:
    """One chat flattened the same way everywhere: the index, `chats`, --json."""
    return {
        "id": entity.id,
        "title": label(entity),
        "username": getattr(entity, "username", None),
        "kind": kind(entity),
    }


def bot_api_variants(raw: int) -> list[int]:
    """Bot API chat ids are not MTProto ids; offer the plausible translations.

    A bot-side group reads as -5446268647 while MTProto calls the same chat
    5446268647, and a supergroup carries an extra -100 prefix. An id copied out
    of a bot therefore fails a naive lookup, so try the conversions rather than
    making that the user's problem.
    """
    out = [raw]
    if raw < 0:
        s = str(-raw)
        if s.startswith("100") and len(s) > 3:
            out.append(int(s[3:]))
        out.append(-raw)
    return out


# --------------------------------------------------------------------------
# message formatting
# --------------------------------------------------------------------------

PARSE_CHOICES = ("markdown", "md", "html", "none", "plain")


def _utf16_len(text: str) -> int:
    """Telegram entity offsets/lengths count UTF-16 code units, not chars."""
    return len(text.encode("utf-16-le")) // 2


def build_message(raw: str, parse: str = "markdown", quote: bool = False, expandable: bool = False):
    """Turn raw text into (text, entities) for send_message.

    parse: 'markdown' (default; bold ** italic __ strike ~~ code ` pre ``` link
    [t](url)), 'html' (adds <u> and <blockquote>), or 'none'. Markdown has no
    blockquote/underline/spoiler in Telethon, so use --quote / html for those.
    quote wraps the whole message in a blockquote; expandable makes it the
    collapsed (tap-to-expand) kind and implies quote.
    """
    mode = (parse or "markdown").lower()
    if mode in ("markdown", "md"):
        text, entities = _markdown.parse(raw)
    elif mode == "html":
        text, entities = _html.parse(raw)
    elif mode in ("none", "plain"):
        text, entities = raw, []
    else:
        die(f"unknown parse mode {parse!r}; pick one of {', '.join(PARSE_CHOICES)}")
    entities = list(entities)
    if quote or expandable:
        entities.append(MessageEntityBlockquote(0, _utf16_len(text), collapsed=bool(expandable)))
    return text, entities


def format_summary(entities) -> str:
    if not entities:
        return "plain"
    c = Counter(type(e).__name__.replace("MessageEntity", "") for e in entities)
    return ", ".join(f"{k}x{v}" if v > 1 else k for k, v in c.items())


# --------------------------------------------------------------------------
# cached chat index (title -> id) so name resolution needs no live dialog scan
# --------------------------------------------------------------------------


def load_index() -> dict:
    try:
        return json.loads(INDEX_FILE.read_text())
    except OSError, ValueError:
        return {}


def save_index(rows: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=0))


def index_put(entity) -> None:
    """Record one entity in the cache under its current title/username."""
    rows = load_index()
    rows[str(entity.id)] = entity_row(entity)
    save_index(rows)


def index_lookup(name: str):
    """Return [id,...] whose cached title/username matches name (exact ci, then
    substring). Empty list if the cache has no hit."""
    name = name.lower().lstrip("@")
    rows = load_index().values()
    exact = [r["id"] for r in rows if r.get("title", "").lower() == name or (r.get("username") or "").lower() == name]
    if exact:
        return exact
    return [r["id"] for r in rows if name in r.get("title", "").lower()]


async def refresh_index(cli):
    """Walk every dialog once, rewrite the title->id cache, return the entities.

    A single scan + single file write; callers reuse the returned list instead
    of scanning again.
    """
    rows, ents = {}, []
    async for dialog in cli.iter_dialogs():
        e = dialog.entity
        ents.append(e)
        rows[str(e.id)] = entity_row(e)
    save_index(rows)
    return ents


async def resolve(cli, ref: str):
    """Resolve @username, a t.me link, a numeric id, or a chat *title* to an
    entity. Titles resolve from the cached index first (no live scan); only a
    cache miss falls back to walking the dialog list (which refreshes it)."""
    ref = ref.strip()
    if not ref:
        die("empty chat reference")

    # An alias wins over everything: it exists precisely to name a chat that
    # @username, id and title all fail to pin down unambiguously.
    hit = load_aliases().get(ref.lower())
    if hit is not None:
        ref = str(hit["id"] if isinstance(hit, dict) else hit)

    if not ref.lstrip("-").isdigit():
        if ref.lower() in ("me", "self", "saved"):
            return await cli.get_entity("me")
        # A username / t.me link / phone resolves directly and cheaply.
        if ref.startswith(("@", "+")) or "t.me/" in ref:
            try:
                ent = await cli.get_entity(ref)
                index_put(ent)
                return ent
            except (ValueError, errors.RPCError) as exc:
                die(f"cannot resolve {ref!r}: {exc}")
        # A plain title: hit the cache first so repeat sends are instant.
        ids = index_lookup(ref)
        if len(ids) == 1:
            try:
                ent = await cli.get_entity(ids[0])
                index_put(ent)
                return ent
            except ValueError, errors.RPCError:
                pass
        elif len(ids) > 1:
            rows = load_index()
            opts = "; ".join(f"{i} {rows[str(i)]['title']}" for i in ids)
            die(f"{ref!r} matches several chats: {opts}. Pass the numeric id.")
        # Cache miss (or first run): one live scan, refreshing the cache.
        best = None
        for e in await refresh_index(cli):
            if ref.lower() in label(e).lower():
                best = e
                break
        if best is not None:
            return best
        die(f"no chat matching {ref!r}. Run `chats` to list them, or pass the id.")

    raw = int(ref)
    for candidate in bot_api_variants(raw):
        try:
            ent = await cli.get_entity(candidate)
            index_put(ent)
            return ent
        except ValueError, errors.RPCError:
            continue

    # Telethon can only resolve a bare id it has seen before, so walk the
    # dialog list once (refreshing the cache) and match by id.
    wanted = {abs(v) for v in bot_api_variants(raw)}
    for e in await refresh_index(cli):
        if abs(e.id) in wanted:
            return e

    die(f"no chat with id {ref}. Bot API ids differ from MTProto ids — run `chats` and use the id printed there.")


def msg_row(msg, me_id=None) -> dict:
    """One message flattened for --json, mirroring what the text format prints."""
    return {
        "id": msg.id,
        "date": ts(msg.date),
        "outgoing": msg.sender_id == me_id,
        "sender_id": msg.sender_id,
        "text": body(msg),
    }


def body(msg) -> str:
    if getattr(msg, "text", None):
        return msg.text
    if getattr(msg, "media", None):
        name = type(msg.media).__name__.replace("MessageMedia", "").lower()
        return f"[{name or 'media'}]"
    if getattr(msg, "action", None):
        return f"[{type(msg.action).__name__.replace('MessageAction', '').lower()}]"
    return "[empty]"


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


async def cmd_setup(cli, args) -> None:  # never reaches the network
    api_hash = args.api_hash.strip().lower()
    if len(api_hash) != 32 or any(c not in "0123456789abcdef" for c in api_hash):
        die(f"api_hash should be 32 hex characters, got {len(args.api_hash)}")

    target = CONFIG_FILE if args.local else REPO_CONFIG
    if args.local:
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    cfg = {}
    if target.is_file():
        with contextlib.suppress(json.JSONDecodeError):
            cfg = json.loads(target.read_text())
    cfg["api_id"] = int(args.api_id)
    cfg["api_hash"] = api_hash
    if args.phone:
        cfg["phone"] = args.phone
    target.write_text(json.dumps(cfg, indent=2) + "\n")
    target.chmod(0o600)

    print(f"stored api credentials in {target}")
    print("next: uv run topoli_user.py login")


async def cmd_login(cli, args) -> None:
    """Two-step login, because stdin is not always a terminal.

    Claude Code's `!` prefix and any other non-TTY caller give telethon's
    interactive prompt an immediate EOF, so the phone and the code are passed
    as arguments across two invocations instead. The phone_code_hash that ties
    them together does not survive the process, so it is written to the state
    directory between the two steps.
    """
    if await cli.is_user_authorized():
        me = await cli.get_me()
        print(f"already logged in as {label(me)}  id={me.id}")
        return

    password = args.password or os.environ.get("TOPOLI_2FA_PASSWORD")

    # The code and the password are two separate server-side steps. Once the
    # code has been accepted, it is spent, and only auth.checkPassword remains
    # — which rides the session's auth_key and so survives a process restart.
    # Without this branch a 2FA account could never finish, because re-running
    # with --code would replay an already-consumed code.
    if password and not args.code:
        try:
            await cli.sign_in(password=password)
        except errors.PasswordHashInvalidError:
            die("that 2FA password is wrong")
        PENDING.unlink(missing_ok=True)
        export_session(cli)
        secure_session()
        me = await cli.get_me()
        print(f"logged in as {label(me)}  id={me.id}")
        print(f"auth key written to {SESSION_STRING}")
        print("treat it as a password: it logs anyone holding it in as you, with no 2FA")
        return

    phone = args.phone or load_config().get("phone")
    if phone and not args.code:
        sent = await cli.send_code_request(phone)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        PENDING.write_text(
            json.dumps(
                {
                    "phone": phone,
                    "phone_code_hash": sent.phone_code_hash,
                }
            )
        )
        PENDING.chmod(0o600)
        print(f"code sent to {phone} (check Telegram, not SMS)")
        print("then run:  uv run topoli_user.py login --code <code>")
        print("  add --password <2fa> if the account has two-step verification")
        return

    if not args.code:
        die(
            "no phone on file. Give one, or a code:\n"
            "    uv run topoli_user.py login --phone +98XXXXXXXXXX\n"
            "    uv run topoli_user.py login --code 12345 [--password <2fa>]"
        )

    if not PENDING.is_file():
        die("no login in progress. Start with: login --phone +98XXXXXXXXXX")
    pending = json.loads(PENDING.read_text())

    try:
        await cli.sign_in(
            phone=pending["phone"],
            code=args.code,
            phone_code_hash=pending["phone_code_hash"],
        )
    except errors.SessionPasswordNeededError:
        if not password:
            die(
                "this account has two-step verification. Re-run with "
                "--password <2fa>, or set TOPOLI_2FA_PASSWORD to keep it out "
                "of your shell history."
            )
        await cli.sign_in(password=password)
    except errors.PhoneCodeInvalidError:
        die("that code is wrong. Codes expire fast — request a new one with --phone")
    except errors.PhoneCodeExpiredError:
        PENDING.unlink(missing_ok=True)
        die("that code expired. Request a new one with --phone")

    PENDING.unlink(missing_ok=True)
    export_session(cli)
    secure_session()
    me = await cli.get_me()
    print(f"logged in as {label(me)}  id={me.id}")
    print(f"auth key written to {SESSION_STRING}")
    print("treat it as a password: it logs anyone holding it in as you, with no 2FA")


async def cmd_whoami(cli, args) -> None:
    me = await cli.get_me()
    print(f"account : {label(me)}")
    print(f"id      : {me.id}")
    print(f"phone   : +{me.phone}" if me.phone else "phone   : hidden")
    print(f"premium : {getattr(me, 'premium', False)}")
    print(f"auth key: {SESSION_STRING} (secret — never commit)")
    print(f"cache   : {SESSION} (local, disposable)")


async def cmd_chats(cli, args) -> None:
    n = 0
    rows = {}
    async for dialog in cli.iter_dialogs(limit=args.limit):
        ent = dialog.entity
        rows[str(ent.id)] = entity_row(ent)
        if args.unread and not dialog.unread_count:
            continue
        flag = f"  [{dialog.unread_count} unread]" if dialog.unread_count else ""
        print(f"{ent.id:>16}  {label(ent)}  ({kind(ent)}){flag}")
        if dialog.message:
            print(f"                  last {ts(dialog.message.date)}: {body(dialog.message)[:100]}")
        n += 1
    if rows:  # keep the fast title->id cache current for `send --chat <name>`
        save_index({**load_index(), **rows})
    print(f"\n-- {n} chat(s); cache: {INDEX_FILE}", file=sys.stderr)


async def cmd_history(cli, args) -> None:
    entity = await resolve(cli, args.chat)
    print(f"# {label(entity)} ({kind(entity)}, id={entity.id})\n")

    me = await cli.get_me()
    rows = []
    async for msg in cli.iter_messages(entity, limit=args.limit):
        rows.append(msg)

    for msg in reversed(rows):  # oldest first, like reading a chat
        sender = msg.sender
        who = "me" if msg.sender_id == me.id else (label(sender) if sender else str(msg.sender_id))
        arrow = "->" if msg.sender_id == me.id else "<-"
        print(f"[{ts(msg.date)}] {arrow} {who} (msg {msg.id})")
        print(f"    {body(msg)}")
    print(f"\n-- {len(rows)} message(s)", file=sys.stderr)


async def cmd_search(cli, args) -> None:
    entity = await resolve(cli, args.chat) if args.chat else None
    n = 0
    async for msg in cli.iter_messages(entity, search=args.query, limit=args.limit):
        chat = await msg.get_chat() if entity is None else entity
        print(f"[{ts(msg.date)}] {label(chat)} (msg {msg.id})")
        print(f"    {body(msg)}")
        n += 1
    print(f"\n-- {n} hit(s)", file=sys.stderr)


async def cmd_send(cli, args) -> None:
    entity = await resolve(cli, args.chat)
    text, entities = build_message(args.text, args.parse, args.quote, args.expandable)
    when = parse_when(args.schedule, "--schedule")

    print("about to send")
    print("  as     : your own account — indistinguishable from you typing it")
    print(f"  chat   : {label(entity)} ({kind(entity)}, id={entity.id})")
    print(f"  parse  : {args.parse}{'  +quote' if args.quote else ''}{'  (expandable)' if args.expandable else ''}")
    print(f"  format : {format_summary(entities)}")
    if when:
        print(f"  when   : {ts(when)} (scheduled — Telegram delivers it, not this process)")
    print(f"  text   : {text}")

    if not args.yes:
        print("\nDRY RUN — nothing sent. Re-run with --yes to actually deliver.")
        return

    msg = await cli.send_message(
        entity, text, formatting_entities=entities or None, reply_to=args.reply_to, schedule=when
    )
    print(f"{'scheduled' if when else 'sent'}: message_id={msg.id} at {ts(msg.date)}")


async def cmd_download(cli, args) -> None:
    """Pull the file behind a `[document]` / `[photo]` placeholder onto disk."""
    entity = await resolve(cli, args.chat)
    msgs = await cli.get_messages(entity, ids=args.message)
    out_dir = Path(args.out).expanduser() if args.out else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for mid, msg in zip(args.message, msgs, strict=True):
        if msg is None:
            print(f"message {mid}: not found in {label(entity)}", file=sys.stderr)
            continue
        if not msg.media:
            print(f"message {mid}: no media to download", file=sys.stderr)
            continue
        path = await cli.download_media(msg, file=str(out_dir))
        if path:
            size = Path(path).stat().st_size / 1024 / 1024
            saved.append({"message": mid, "path": str(path), "mb": round(size, 2)})
            print(f"saved: {path}  ({size:.1f} MB)")

    if not saved:
        die("nothing downloaded")


async def cmd_edit(cli, args) -> None:
    """Rewrite one of your own messages in place.

    Telegram keeps this open indefinitely for your own messages, and the chat
    shows an "edited" marker rather than hiding the change.
    """
    entity = await resolve(cli, args.chat)
    msgs = await cli.get_messages(entity, ids=[args.message])
    target = msgs[0] if msgs else None
    if target is None:
        die(f"message {args.message} not found in {label(entity)}")

    me = await cli.get_me()
    if target.sender_id != me.id:
        die(f"message {args.message} is not yours -- you can only edit your own messages")

    text, entities = build_message(args.text, args.parse, args.quote, args.expandable)
    print("about to edit")
    print(f"  chat   : {label(entity)} ({kind(entity)}, id={entity.id})")
    print(f"  message: {args.message} of {ts(target.date)}")
    print(f"  before : {target.text or '[no text]'}")
    print(f"  after  : {text}")
    print(f"  format : {format_summary(entities)}")

    if not args.yes:
        print("\nDRY RUN -- nothing changed. Re-run with --yes to apply.")
        return

    await cli.edit_message(entity, args.message, text, formatting_entities=entities or None)
    print(f"edited: message_id={args.message}")


async def cmd_delete(cli, args) -> None:
    """Delete messages, for everyone by default.

    This is the undo that `send` deliberately does not have, so it prints every
    message it is about to remove and needs --yes like any other write. --only-me
    leaves the copy on the other side alone and just clears your own view.
    """
    entity = await resolve(cli, args.chat)
    msgs = await cli.get_messages(entity, ids=args.message)
    me = await cli.get_me()

    found = [(mid, m) for mid, m in zip(args.message, msgs, strict=True) if m is not None]
    if not found:
        die(f"none of those messages exist in {label(entity)}")

    print("about to delete")
    print(f"  chat  : {label(entity)} ({kind(entity)}, id={entity.id})")
    print(f"  scope : {'your own view only' if args.only_me else 'everyone in this chat'}")
    for mid, msg in found:
        who = "me" if msg.sender_id == me.id else (label(msg.sender) if msg.sender else str(msg.sender_id))
        print(f"  - {mid} [{ts(msg.date)}] {who}: {body(msg)[:120]}")
    missing = [mid for mid, m in zip(args.message, msgs, strict=True) if m is None]
    if missing:
        print(f"  (not found, skipped: {', '.join(str(m) for m in missing)})")

    if not args.yes:
        print("\nDRY RUN -- nothing deleted. Re-run with --yes to apply.")
        return

    await cli.delete_messages(entity, [mid for mid, _ in found], revoke=not args.only_me)
    print(f"deleted: {len(found)} message(s)")


async def cmd_forward(cli, args) -> None:
    """Move messages between chats without retyping or losing attribution."""
    src = await resolve(cli, args.chat)
    dst = await resolve(cli, args.to)
    msgs = await cli.get_messages(src, ids=args.message)
    found = [(mid, m) for mid, m in zip(args.message, msgs, strict=True) if m is not None]
    if not found:
        die(f"none of those messages exist in {label(src)}")

    print("about to forward")
    print(f"  from  : {label(src)} ({kind(src)}, id={src.id})")
    print(f"  to    : {label(dst)} ({kind(dst)}, id={dst.id})")
    for mid, msg in found:
        print(f"  - {mid} [{ts(msg.date)}]: {body(msg)[:120]}")

    if not args.yes:
        print("\nDRY RUN -- nothing forwarded. Re-run with --yes to deliver.")
        return

    sent = await cli.forward_messages(dst, [mid for mid, _ in found], src)
    ids = [m.id for m in (sent if isinstance(sent, list) else [sent])]
    print(f"forwarded: message_id(s)={ids}")


async def cmd_contacts(cli, args) -> None:
    """The saved address book in one RPC.

    Finding a person by walking `iter_dialogs` costs a full scan -- minutes
    through the proxy -- because it pages every conversation the account has
    ever had. contacts.GetContacts returns the whole address book in a single
    round trip, so this is the right way to answer "who is X". It also seeds the
    title->id cache, which makes the *next* `send --chat "<name>"` instant.
    """
    res = await cli(GetContactsRequest(hash=0))
    users = getattr(res, "users", [])
    if users:  # cache every contact, not just the ones this query showed
        save_index({**load_index(), **{str(u.id): entity_row(u) for u in users}})

    q = (args.query or "").lower().lstrip("@")
    hits = [u for u in users if not q or q in label(u).lower() or q in (getattr(u, "username", None) or "").lower()]
    hits.sort(key=lambda u: label(u).lower())

    if emit([{**entity_row(u), "phone": getattr(u, "phone", None)} for u in hits], args):
        return
    for u in hits:
        phone = f"  +{u.phone}" if getattr(u, "phone", None) else ""
        print(f"{u.id:>16}  {label(u)}{phone}")
    print(f"\n-- {len(hits)} contact(s) of {len(users)}; cache: {INDEX_FILE}", file=sys.stderr)


async def cmd_catchup(cli, args) -> None:
    """Everything unread, in one pass -- the "what did I miss" command.

    `history --limit N` makes you guess N. Telegram already tracks exactly how
    many messages are unread per chat, so read that many and no more. Nothing is
    marked read unless --read is passed: seeing a message and acknowledging it
    are different acts, and the second one is visible to the other side.
    """
    me = await cli.get_me()
    found = []  # (entity, unread_count, [message, ...])
    async for dialog in cli.iter_dialogs(limit=args.limit):
        unread = dialog.unread_count
        if not unread:
            continue
        entity = dialog.entity
        msgs = []
        async for msg in cli.iter_messages(entity, limit=min(unread, args.max_per_chat)):
            msgs.append(msg)
        msgs.reverse()  # oldest first, like reading a chat
        found.append((entity, unread, msgs))

    rows = [{**entity_row(e), "unread": n, "messages": [msg_row(m, me.id) for m in msgs]} for e, n, msgs in found]
    if not emit(rows, args):
        for entity, unread, msgs in found:
            more = f" (showing the last {len(msgs)})" if unread > len(msgs) else ""
            print(f"\n# {label(entity)} ({kind(entity)}, id={entity.id}) — {unread} unread{more}")
            for msg in msgs:
                arrow = "->" if msg.sender_id == me.id else "<-"
                print(f"[{ts(msg.date)}] {arrow} (msg {msg.id})")
                print(f"    {body(msg)}")
        total = sum(n for _, n, _ in found)
        print(f"\n-- {total} unread message(s) across {len(found)} chat(s)", file=sys.stderr)

    if args.read and found:
        for entity, _, _ in found:
            await cli.send_read_acknowledge(entity, clear_mentions=True)
        print(f"marked read: {len(found)} chat(s)", file=sys.stderr)


async def cmd_alias(cli, args) -> None:
    """Name a chat once, address it by that name forever.

    `--set` resolves the reference now and stores the numeric id, not the text:
    a display name can be shared by two contacts and a username can be given up
    and re-registered by somebody else, but the id is the account.
    """
    if args.remove:
        for target in (ALIAS_REPO, ALIAS_LOCAL):
            rows = {k.lower(): v for k, v in read_json(target).items()}
            if rows.pop(args.remove.lower(), None) is not None:
                save_aliases(rows, local=target is ALIAS_LOCAL)
                print(f"removed alias {args.remove!r} from {target}")
                return
        die(f"no alias called {args.remove!r}")

    if args.set:
        name, ref = args.set
        entity = await resolve(cli, ref)
        target = ALIAS_LOCAL if args.local else ALIAS_REPO
        rows = {k.lower(): v for k, v in read_json(target).items()}
        rows[name.lower()] = {"id": entity.id, "label": label(entity), "kind": kind(entity)}
        save_aliases(rows, args.local)
        print(f"{name} -> {label(entity)} ({kind(entity)}, id={entity.id})")
        print(f"stored in {target}")
        return

    rows = load_aliases()
    listing = [
        {"alias": k, **(v if isinstance(v, dict) else {"id": v, "label": "?", "kind": "?"})}
        for k, v in sorted(rows.items())
    ]
    if emit(listing, args):
        return
    for r in listing:
        print(f"{r['alias']:<16} {r['id']:>16}  {r.get('label', '?')}")
    print(f"\n-- {len(listing)} alias(es); {ALIAS_REPO} + {ALIAS_LOCAL}", file=sys.stderr)


async def cmd_create_group(cli, args) -> None:
    """Create a group that starts out with nobody in it but Parham.

    Telegram's *basic* groups cannot exist with a single member — CreateChat
    wants someone to invite. So this makes a **megagroup** (supergroup), which
    can, and which is what the app creates for new groups anyway. The practical
    differences that matter later: a supergroup has a numeric id the bot sees
    with a -100 prefix, and it can be given a username or made public.
    """
    print("about to create a group")
    print(f"  title  : {args.title}")
    if args.about:
        print(f"  about  : {args.about}")
    print("  members: you alone — nobody is invited")

    if not args.yes:
        print("\nDRY RUN — nothing created. Re-run with --yes to actually create it.")
        return

    res = await cli(CreateChannelRequest(title=args.title, about=args.about or "", megagroup=True))
    chat = res.chats[0]
    print(f"created: {chat.title} (supergroup, id={chat.id})")
    print(f"  Bot API id for the same chat: -100{chat.id}")


async def cmd_react(cli, args) -> None:
    """React to one message, or clear the reaction with --remove.

    A reaction is public and attributed to Parham exactly like a message is, so
    it gets the same dry-run-then---yes treatment. It is cheaper than a reply
    for the common "seen it, working on it" acknowledgement, which is why it is
    worth having rather than sending a one-word message.
    """
    entity = await resolve(cli, args.chat)
    msgs = await cli.get_messages(entity, ids=[args.message])
    target = msgs[0] if msgs else None
    if target is None:
        die(f"message {args.message} not found in {label(entity)}")

    sender = await target.get_sender()
    who = getattr(sender, "username", None) or getattr(sender, "first_name", "?")

    print("about to react")
    print("  as      : your own account")
    print(f"  chat    : {label(entity)} ({kind(entity)}, id={entity.id})")
    print(f"  message : {args.message} from {who}")
    print(f"  text    : {(target.text or '[no text]')[:200]}")
    print(f"  emoji   : {'(remove existing reaction)' if args.remove else args.emoji}")

    if not args.yes:
        print("\nDRY RUN — nothing sent. Re-run with --yes to actually react.")
        return

    # An empty reaction list is how MTProto clears one; there is no separate
    # delete method.
    reaction = [] if args.remove else [ReactionEmoji(emoticon=args.emoji)]
    try:
        await cli(SendReactionRequest(peer=entity, msg_id=args.message, reaction=reaction))  # ty: ignore[invalid-argument-type]
    except errors.ReactionInvalidError:
        die(f"{args.emoji!r} is not an allowed reaction in this chat — groups can restrict which emoji are available")
    print(f"reacted: {'cleared' if args.remove else args.emoji} on message {args.message}")


async def cmd_send_file(cli, args) -> None:
    entity = await resolve(cli, args.chat)
    paths = [Path(f).expanduser() for f in args.file]
    for p in paths:
        if not p.is_file():
            die(f"no such file: {p}")

    print("about to send")
    print("  as    : your own account")
    print(f"  chat  : {label(entity)} ({kind(entity)}, id={entity.id})")
    for p in paths:
        print(f"  file  : {p}  ({p.stat().st_size / 1024 / 1024:.1f} MB)")
    cap_text, cap_entities = (
        build_message(args.caption, args.parse, args.quote, args.expandable) if args.caption else ("", [])
    )
    if args.caption:
        print(f"  caption: {cap_text}  [{args.parse}{'  +quote' if args.quote else ''}]")

    if not args.yes:
        print("\nDRY RUN — nothing sent. Re-run with --yes to actually deliver.")
        return

    sent = await cli.send_file(
        entity,
        [str(p) for p in paths],
        caption=cap_text or None,
        formatting_entities=cap_entities or None,
        force_document=not args.photo,
        reply_to=args.reply_to,
    )
    ids = [m.id for m in (sent if isinstance(sent, list) else [sent])]
    print(f"sent: message_id(s)={ids}")


async def cmd_read(cli, args) -> None:
    """Mark chats read, clearing the unread badge and any pending @mention.

    There is no un-read. The badge is gone once this runs, and in a private chat
    the other side sees the read receipt — so it names every chat it touched
    rather than printing a count, which is what makes a mistyped id obvious.
    """
    for ref in args.chat:
        entity = await resolve(cli, ref)
        await cli.send_read_acknowledge(entity, clear_mentions=True)
        print(f"marked read: {label(entity)} ({kind(entity)}, id={entity.id})")


# --- folders, which are also Telegram's per-chat tags ----------------------
#
# Telegram has no free-form chat label. What the app shows as a tag is a
# folder: put a chat in one, switch tags on, and the folder's title and colour
# render as a chip on that chat's row in the main list. A chat can belong to
# several folders, so it can carry several tags at once.


def filter_title(f) -> str:
    """Titles are TextWithEntities on current layers and plain str on older ones."""
    title = getattr(f, "title", None)
    return getattr(title, "text", title) or ""


def filter_peers(f) -> list:
    return list(getattr(f, "pinned_peers", []) or []) + list(f.include_peers)


async def dialog_filters(cli):
    """(named filters, tags_enabled). The unnamed default entry is dropped."""
    res = await cli(GetDialogFiltersRequest())
    filters = getattr(res, "filters", res)
    named = [f for f in filters if getattr(f, "id", None) is not None]
    return named, bool(getattr(res, "tags_enabled", False))


def find_filter(filters, name: str):
    wanted = name.strip().lower()
    hits = [f for f in filters if filter_title(f).lower() == wanted]
    if not hits and wanted.lstrip("-").isdigit():
        hits = [f for f in filters if f.id == int(wanted)]
    if not hits:
        die(f"no folder called {name!r}. Run `folders` to see them.")
    if len(hits) > 1:
        die(f"{name!r} matches {len(hits)} folders; use the numeric id instead")
    return hits[0]


async def cmd_folders(cli, args) -> None:
    filters, tags_enabled = await dialog_filters(cli)

    if args.chat:
        entity = await resolve(cli, args.chat)
        target = utils.get_peer_id(entity)
        holding = [f for f in filters if any(utils.get_peer_id(p) == target for p in filter_peers(f))]
        print(f"{label(entity)} ({kind(entity)}, id={entity.id})")
        for f in holding:
            print(f"  {filter_title(f)}  (id={f.id}, colour {f.color})")
        print(f"\n-- in {len(holding)} folder(s)", file=sys.stderr)
        return

    for f in filters:
        pinned = len(getattr(f, "pinned_peers", []) or [])
        excluded = len(getattr(f, "exclude_peers", []) or [])
        colour = f.color if f.color is not None else "-"
        shared = "  [shared chatlist]" if isinstance(f, DialogFilterChatlist) else ""
        print(
            f"{f.id:>4}  {filter_title(f):<16} colour={colour:<3} "
            f"chats={len(filter_peers(f)):<4} pinned={pinned:<3} excluded={excluded}{shared}"
        )

    if tags_enabled:
        print("\ntags: on — each folder's title and colour show as a chip in the chat list")
    else:
        print("\ntags: off — colours are stored but invisible. Reveal them with `tags --on`.")
    print(f"-- {len(filters)} folder(s)", file=sys.stderr)


async def cmd_folder(cli, args) -> None:
    """Add or drop chats in one folder — i.e. tag and untag them.

    A dry run first, because UpdateDialogFilter replaces the folder wholesale
    and there is no undo: a mistyped --remove would silently thin out a folder
    holding a hundred chats.
    """
    filters, _ = await dialog_filters(cli)
    target = find_filter(filters, args.name)
    if isinstance(target, DialogFilterChatlist):
        die(
            f"{filter_title(target)!r} is a shared chatlist folder — its membership "
            "belongs to whoever published the invite link, not to this account"
        )

    pinned = list(getattr(target, "pinned_peers", []) or [])
    include = list(target.include_peers)
    present = {utils.get_peer_id(p) for p in pinned + include}

    added, removed = [], []
    for ref in args.add or []:
        entity = await resolve(cli, ref)
        peer = await cli.get_input_entity(entity)
        if utils.get_peer_id(peer) in present:
            print(f"already there: {label(entity)}")
            continue
        include.append(peer)
        present.add(utils.get_peer_id(peer))
        added.append(label(entity))

    for ref in args.remove or []:
        entity = await resolve(cli, ref)
        pid = utils.get_peer_id(entity)
        before = len(pinned) + len(include)
        pinned = [p for p in pinned if utils.get_peer_id(p) != pid]
        include = [p for p in include if utils.get_peer_id(p) != pid]
        if len(pinned) + len(include) == before:
            print(f"not in {filter_title(target)!r}: {label(entity)}")
            continue
        removed.append(label(entity))

    colour = None if args.color == -1 else args.color
    recolour = args.color is not None and colour != target.color

    if not (added or removed or recolour):
        print("nothing to change")
        return

    print(f"about to edit folder {filter_title(target)!r} (id={target.id})")
    for name in added:
        print(f"  + {name}")
    for name in removed:
        print(f"  - {name}")
    if recolour:
        print(f"  colour {target.color} -> {colour}")

    if not args.yes:
        print("\nDRY RUN — nothing changed. Re-run with --yes to apply.")
        return

    # Mutate the filter that came back rather than building a fresh one: the
    # request replaces the whole object, so every flag we do not touch
    # (contacts, exclude_muted, emoticon, ...) has to be carried over intact.
    target.pinned_peers = pinned
    target.include_peers = include
    if recolour:
        target.color = colour
    await cli(UpdateDialogFilterRequest(id=target.id, filter=target))
    print(f"updated: {len(pinned) + len(include)} chat(s) in {filter_title(target)!r}")


async def cmd_tags(cli, args) -> None:
    """Show, or flip, the switch that renders folders as chat-list chips."""
    filters, enabled = await dialog_filters(cli)

    if not (args.on or args.off):
        print(f"folder tags: {'on' if enabled else 'off'}")
        print(f"{len(filters)} folder(s) would show as chips — see `folders`")
        return

    want = bool(args.on)
    if want == enabled:
        print(f"folder tags already {'on' if enabled else 'off'}")
        return

    try:
        await cli(ToggleDialogFilterTagsRequest(enabled=want))
    except errors.RPCError as exc:
        die(f"could not toggle folder tags: {exc}. This one needs Telegram Premium.")
    print(f"folder tags: {'on' if want else 'off'}")


# --------------------------------------------------------------------------


def add_json(sp):
    sp.add_argument("--json", action="store_true", help="machine-readable output instead of the text format")
    return sp


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="topoli_user.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("setup", help="store api_id/api_hash from my.telegram.org")
    sp.add_argument("--api-id", required=True)
    sp.add_argument("--api-hash", required=True)
    sp.add_argument("--phone", help="phone in international form, stored for login")
    sp.add_argument("--local", action="store_true", help="write to the machine-local state dir instead of the repo")
    sp.set_defaults(fn=cmd_setup, offline=True)

    sp = sub.add_parser("login", help="first-time login: --phone, then --code")
    sp.add_argument("--phone", help="phone in international form, e.g. +98XXXXXXXXXX")
    sp.add_argument("--code", help="the code Telegram sent to step one")
    sp.add_argument("--password", help="2FA password (or set TOPOLI_2FA_PASSWORD)")
    sp.set_defaults(fn=cmd_login)
    sub.add_parser("whoami", help="show the logged-in account").set_defaults(fn=cmd_whoami)

    sp = sub.add_parser("chats", help="list conversations with their MTProto ids")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--unread", action="store_true", help="only chats with unread messages")
    sp.set_defaults(fn=cmd_chats)

    sp = sub.add_parser("download", help="save the file behind a [document]/[photo] placeholder")
    sp.add_argument("--chat", required=True)
    sp.add_argument("--message", action="append", required=True, type=int, help="message id; repeat for several")
    sp.add_argument("--out", help="target directory (default: the working directory)")
    sp.set_defaults(fn=cmd_download)

    sp = add_json(sub.add_parser("contacts", help="the saved address book — far faster than scanning chats"))
    sp.add_argument("--query", help="filter by name or username")
    sp.set_defaults(fn=cmd_contacts)

    sp = add_json(sub.add_parser("catchup", help="everything unread, exactly as much as is unread"))
    sp.add_argument("--limit", type=int, default=100, help="how many chats to scan")
    sp.add_argument("--max-per-chat", type=int, default=20, help="cap the messages shown per chat")
    sp.add_argument("--read", action="store_true", help="mark them read afterwards (the other side sees this)")
    sp.set_defaults(fn=cmd_catchup)

    sp = add_json(sub.add_parser("alias", help="name a chat once, address it by that name forever"))
    sp.add_argument("--set", nargs=2, metavar=("NAME", "CHAT"), help="e.g. --set wife @someone")
    sp.add_argument("--remove", metavar="NAME")
    sp.add_argument("--local", action="store_true", help="store in the state dir instead of beside the script")
    sp.set_defaults(fn=cmd_alias)

    sp = sub.add_parser("history", help="read a chat backwards — the thing bots cannot do")
    sp.add_argument("--chat", required=True, help="id, @username, or t.me link")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(fn=cmd_history)

    sp = sub.add_parser("search", help="full-text search messages")
    sp.add_argument("--query", required=True)
    sp.add_argument("--chat", help="restrict to one chat (default: everywhere)")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(fn=cmd_search)

    sp = sub.add_parser("send", help="send a message as yourself")
    sp.add_argument("--chat", required=True, help="@username, t.me link, numeric id, or chat title")
    sp.add_argument("--text", required=True)
    sp.add_argument(
        "--parse", default="markdown", choices=PARSE_CHOICES, help="text markup: markdown (default), html, or none"
    )
    sp.add_argument("--quote", action="store_true", help="wrap the whole message in a blockquote")
    sp.add_argument("--expandable", action="store_true", help="collapsed tap-to-expand blockquote (implies --quote)")
    sp.add_argument("--reply-to", type=int)
    sp.add_argument("--schedule", help="deliver later; ISO time like 2026-09-01T09:00")
    sp.add_argument("--yes", action="store_true", help="actually send (otherwise dry run)")
    sp.set_defaults(fn=cmd_send)

    sp = sub.add_parser("edit", help="rewrite one of your own messages in place")
    sp.add_argument("--chat", required=True)
    sp.add_argument("--message", required=True, type=int, help="message id, from `history`")
    sp.add_argument("--text", required=True)
    sp.add_argument("--parse", default="markdown", choices=PARSE_CHOICES)
    sp.add_argument("--quote", action="store_true")
    sp.add_argument("--expandable", action="store_true")
    sp.add_argument("--yes", action="store_true", help="actually edit (otherwise dry run)")
    sp.set_defaults(fn=cmd_edit)

    sp = sub.add_parser("delete", help="delete messages — the undo `send` does not have")
    sp.add_argument("--chat", required=True)
    sp.add_argument("--message", action="append", required=True, type=int, help="message id; repeat for several")
    sp.add_argument("--only-me", action="store_true", help="clear your own view only, leaving their copy")
    sp.add_argument("--yes", action="store_true", help="actually delete (otherwise dry run)")
    sp.set_defaults(fn=cmd_delete)

    sp = sub.add_parser("forward", help="forward messages from one chat to another")
    sp.add_argument("--chat", required=True, help="the source chat")
    sp.add_argument("--to", required=True, help="the destination chat")
    sp.add_argument("--message", action="append", required=True, type=int, help="message id; repeat for several")
    sp.add_argument("--yes", action="store_true", help="actually forward (otherwise dry run)")
    sp.set_defaults(fn=cmd_forward)

    sp = sub.add_parser("create-group", help="create a supergroup containing only you")
    sp.add_argument("--title", required=True)
    sp.add_argument("--about", default="", help="optional group description")
    sp.add_argument("--yes", action="store_true", help="actually create (otherwise dry run)")
    sp.set_defaults(fn=cmd_create_group)

    sp = sub.add_parser("react", help="add or clear an emoji reaction on a message")
    sp.add_argument("--chat", required=True)
    sp.add_argument("--message", required=True, type=int, help="message id, from `history`")
    sp.add_argument("--emoji", default="👀")
    sp.add_argument("--remove", action="store_true", help="clear your reaction instead")
    sp.add_argument("--yes", action="store_true", help="actually react (otherwise dry run)")
    sp.set_defaults(fn=cmd_react)

    sp = sub.add_parser("read", help="mark chats as read")
    sp.add_argument("--chat", action="append", required=True, help="repeat for several")
    sp.set_defaults(fn=cmd_read)

    sp = sub.add_parser("folders", help="list chat folders — Telegram's per-chat tags")
    sp.add_argument("--chat", help="instead, show which folders this chat is in")
    sp.set_defaults(fn=cmd_folders)

    sp = sub.add_parser("folder", help="add or drop chats in one folder")
    sp.add_argument("--name", required=True, help="folder title, or its numeric id")
    sp.add_argument("--add", action="append", help="chat to tag with it; repeatable")
    sp.add_argument("--remove", action="append", help="chat to drop from it; repeatable")
    sp.add_argument(
        "--color", type=int, choices=range(-1, 7), metavar="-1..6", help="recolour the chip (-1 clears the colour)"
    )
    sp.add_argument("--yes", action="store_true", help="actually apply (otherwise dry run)")
    sp.set_defaults(fn=cmd_folder)

    sp = sub.add_parser("tags", help="show or toggle folder tags in the chat list")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--on", action="store_true", help="show folder chips on every chat")
    g.add_argument("--off", action="store_true", help="hide them again")
    sp.set_defaults(fn=cmd_tags)

    sp = sub.add_parser("send-file", help="upload files as yourself")
    sp.add_argument("--chat", required=True, help="@username, t.me link, numeric id, or chat title")
    sp.add_argument("--file", action="append", required=True, help="repeat for several")
    sp.add_argument("--caption")
    sp.add_argument(
        "--parse", default="markdown", choices=PARSE_CHOICES, help="caption markup: markdown (default), html, or none"
    )
    sp.add_argument("--quote", action="store_true", help="wrap the caption in a blockquote")
    sp.add_argument("--expandable", action="store_true", help="collapsed blockquote (implies --quote)")
    sp.add_argument("--photo", action="store_true", help="send as photo (recompressed) not document")
    sp.add_argument("--reply-to", type=int)
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(fn=cmd_send_file)

    return p


async def run(args) -> None:
    if getattr(args, "offline", False):
        await args.fn(None, args)
        return

    cli = client()
    # Deliberately connect() rather than `async with cli`: the context manager
    # calls start(), which prompts for a phone number on stdin. In a
    # non-interactive run that surfaces as an EOFError traceback instead of the
    # one-line "you are not logged in" that actually tells you what to do.
    await cli.connect()
    try:
        secure_session()
        if not await cli.is_user_authorized() and args.fn is not cmd_login:
            die("not logged in yet. Run: uv run topoli_user.py login")
        await args.fn(cli, args)
    finally:
        await cli.disconnect()


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
    except errors.FloodWaitError as exc:
        die(f"rate limited by Telegram; retry in {exc.seconds}s")
    except errors.RPCError as exc:
        die(f"Telegram error: {exc}")


if __name__ == "__main__":
    main()
