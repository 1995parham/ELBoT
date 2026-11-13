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
import atexit
import binascii
import contextlib
import fcntl
import io
import json
import os
import platform
import random
import re
import struct
import sys
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote, urlparse

try:
    from telethon import TelegramClient, errors, events, utils
    from telethon.extensions import html as _html
    from telethon.extensions import markdown as _markdown
    from telethon.helpers import add_surrogate, del_surrogate, strip_text
    from telethon.sessions import SQLiteSession, StringSession
    from telethon.tl.functions.account import (
        GetAccountTTLRequest,
        GetAuthorizationsRequest,
        GetGlobalPrivacySettingsRequest,
        GetPrivacyRequest,
        ResetAuthorizationRequest,
        SetAccountTTLRequest,
        SetGlobalPrivacySettingsRequest,
        SetPrivacyRequest,
        UpdateBirthdayRequest,
        UpdateNotifySettingsRequest,
        UpdateProfileRequest,
        UpdateUsernameRequest,
    )
    from telethon.tl.functions.channels import (
        CreateChannelRequest,
        EditPhotoRequest,
        GetFullChannelRequest,
        InviteToChannelRequest,
        JoinChannelRequest,
    )
    from telethon.tl.functions.contacts import DeleteContactsRequest, GetContactsRequest

    # Forum topics: the RPC and its parameter name both moved. It is
    # `messages.GetForumTopicsRequest(peer=…)` in the layer this script pins;
    # older telethon had `channels.GetForumTopicsRequest(channel=…)`, and the
    # PEP 723 header requires a version new enough that only the first exists.
    from telethon.tl.functions.messages import (
        AddChatUserRequest,
        DeleteScheduledMessagesRequest,
        EditChatPhotoRequest,
        GetDialogFiltersRequest,
        GetForumTopicsRequest,
        GetFullChatRequest,
        GetMessageReadParticipantsRequest,
        GetOutboxReadDateRequest,
        GetPeerDialogsRequest,
        GetScheduledHistoryRequest,
        GetUnreadMentionsRequest,
        ImportChatInviteRequest,
        MarkDialogUnreadRequest,
        SaveDraftRequest,
        SendReactionRequest,
        SendScheduledMessagesRequest,
        ToggleDialogFilterTagsRequest,
        TranscribeAudioRequest,
        UpdateDialogFilterRequest,
    )
    from telethon.tl.functions.photos import DeletePhotosRequest, GetUserPhotosRequest, UploadProfilePhotoRequest
    from telethon.tl.functions.users import GetFullUserRequest
    from telethon.tl.types import (
        AccountDaysTTL,
        Birthday,
        Channel,
        ChannelParticipantsAdmins,
        Chat,
        DialogFilterChatlist,
        InputChatPhotoEmpty,
        InputChatUploadedPhoto,
        InputDialogPeer,
        InputMediaPoll,
        InputNotifyPeer,
        InputPeerNotifySettings,
        InputPhoto,
        InputPrivacyKeyAbout,
        InputPrivacyKeyAddedByPhone,
        InputPrivacyKeyBirthday,
        InputPrivacyKeyChatInvite,
        InputPrivacyKeyForwards,
        InputPrivacyKeyPhoneCall,
        InputPrivacyKeyPhoneNumber,
        InputPrivacyKeyPhoneP2P,
        InputPrivacyKeyProfilePhoto,
        InputPrivacyKeySavedMusic,
        InputPrivacyKeyStarGiftsAutoSave,
        InputPrivacyKeyStatusTimestamp,
        InputPrivacyKeyVoiceMessages,
        InputPrivacyValueAllowAll,
        InputPrivacyValueAllowChatParticipants,
        InputPrivacyValueAllowContacts,
        InputPrivacyValueAllowUsers,
        InputPrivacyValueDisallowAll,
        InputPrivacyValueDisallowChatParticipants,
        InputPrivacyValueDisallowUsers,
        InputReplyToMessage,
        MessageEntityBlockquote,
        MessageEntityCode,
        MessageEntityPre,
        MessageEntitySpoiler,
        Poll,
        PollAnswer,
        ReactionEmoji,
        TextWithEntities,
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
ACCOUNT_CONFIG = SKILL_DIR / "api-topoli.json"  # re-pointed by set_account
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

# --------------------------------------------------------------------------
# accounts — one auth key per Telegram account, side by side
# --------------------------------------------------------------------------
#
# A person can have several Telegram accounts (a main one, an Iranian number, a
# support account), and the secretary should be able to act as any of them. An
# account is just a name: `<name>.authkey` beside the script is its login,
# `<name>.session` in the state dir its cache, and the title index and the
# pending-login file are kept per name too, because a chat id one account can
# reach the other may not.
#
# Which account runs without --account is `"account"` in api.json (then the
# TOPOLI_ACCOUNT environment variable, which wins). With neither it is
# `topoli`, the layout from before accounts existed, whose cache, index and
# pending login keep their old unsuffixed names so nothing already set up moves.
#
# An account may also carry files of its own beside the shared ones:
# `api-<name>.json` is merged over api.json (its phone, a device name of its
# own), and `aliases-<name>.json` / `tags-<name>.json`, when they exist,
# *replace* the shared books for that account. A support account that talks to
# clients must never resolve "wife" to somebody, so it gets a book of its own.

LEGACY_ACCOUNT = "topoli"
ACCOUNT = LEGACY_ACCOUNT
_ACCOUNT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


def default_account() -> str:
    """The account used when none is named: env, then api.json, then `topoli`."""
    named = os.environ.get("TOPOLI_ACCOUNT") or read_json(SKILL_DIR / "api.json").get("account")
    return str(named or LEGACY_ACCOUNT).strip().lower()


def own_or_shared(stem: str, suffix: str = ".json") -> Path:
    """`<stem>-<account>.json` when this account has one, else the shared `<stem>.json`."""
    own = SKILL_DIR / f"{stem}-{ACCOUNT}{suffix}"
    return own if own.is_file() else SKILL_DIR / f"{stem}{suffix}"


def set_account(name: str | None) -> None:
    """Point every per-account path at `name` (None: the default account)."""
    global ACCOUNT, SESSION, SESSION_STRING, INDEX_FILE, PENDING, ALIAS_REPO, TAGS_FILE, ACCOUNT_CONFIG
    name = (name or default_account()).strip().lower()
    if not _ACCOUNT_NAME.fullmatch(name):
        die(f"account names are lowercase letters, digits, - and _, got {name!r}")
    ACCOUNT = name
    ACCOUNT_CONFIG = SKILL_DIR / f"api-{name}.json"
    ALIAS_REPO = own_or_shared("aliases")
    TAGS_FILE = own_or_shared("tags")
    SESSION_STRING = SKILL_DIR / f"{name}.authkey"
    if name == LEGACY_ACCOUNT:
        SESSION = Path(os.environ.get("TOPOLI_SESSION", STATE_DIR / "topoli.session"))
        INDEX_FILE = STATE_DIR / "chat-index.json"
        PENDING = STATE_DIR / "login-pending.json"
        return
    SESSION = Path(os.environ.get("TOPOLI_SESSION", STATE_DIR / f"{name}.session"))
    INDEX_FILE = STATE_DIR / f"chat-index-{name}.json"
    PENDING = STATE_DIR / f"login-pending-{name}.json"


def invocation() -> str:
    """How to re-run this tool for the current account, for printed hints."""
    base = "uv run topoli_user.py"
    return base if default_account() == ACCOUNT else f"{base} --account {ACCOUNT}"


VERSION = "0.5.0"

#: Set while a command runs with --json, so `die` can answer in the same
#: format: a caller parsing stdout must get an object, not an empty string.
JSON_ERRORS = False

#: Set while the MCP server is running. Commands that could otherwise wait
#: forever (`listen`) take a bounded default under it, because an agent's tool
#: call has no Ctrl-C.
IN_MCP = False


def die(msg: str) -> NoReturn:
    print(f"topoli-user: {msg}", file=sys.stderr)
    if JSON_ERRORS:
        print(json.dumps({"error": msg}, ensure_ascii=False))
    raise SystemExit(1)


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------


def load_config() -> dict:
    """Merge the repo defaults with any machine-local override."""
    cfg: dict = {}
    for path in (REPO_CONFIG, ACCOUNT_CONFIG, CONFIG_FILE):  # later wins
        if path.is_file():
            with contextlib.suppress(json.JSONDecodeError):
                cfg.update(json.loads(path.read_text()))
    cfg.pop("account", None)  # which account is the default is not a setting of this one
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
    """The stored auth key, unless the environment overrides it.

    The override applies to the default account only. Otherwise a key exported
    for one account would quietly log `--account raha` in as somebody else, and
    every message "from support" would go out under that person's name.
    """
    raw = os.environ.get("TOPOLI_SESSION_STRING") if default_account() == ACCOUNT else None
    source = "TOPOLI_SESSION_STRING"
    if raw is None and SESSION_STRING.is_file():
        raw, source = SESSION_STRING.read_text(), str(SESSION_STRING)
    return (raw.strip(), source) if raw and raw.strip() else None


def open_session(path: Path | None = None) -> SQLiteSession:
    """Build the local SQLite cache, seeding it from the stored auth key."""
    path = path or SESSION
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    sess = SQLiteSession(str(path.with_suffix("")))

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
    path.unlink(missing_ok=True)
    sess = SQLiteSession(str(path.with_suffix("")))
    sess.set_dc(repo.dc_id, repo.server_address, repo.port)  # this re-reads the
    sess.auth_key = repo.auth_key  # key, so it is first
    sess.save()
    return sess


# --------------------------------------------------------------------------
# device identity — the name this session wears in Settings → Devices
# --------------------------------------------------------------------------
#
# Every MTProto connection opens with initConnection, which tells Telegram the
# device model, the system version and the app version. The server stores them
# on the authorization, and that is the row every other device of the account
# shows under Settings → Devices — and where the account owner goes to
# terminate a session they do not recognise. Telethon fills them with the
# machine's platform strings ("arm64", "Darwin 25.1.0"), which makes an
# automated session look like a stray laptop. Naming it is what lets a person
# see at a glance that this one is the AI secretary, and kill it if they want.

DEVICE_KEYS = {
    "device_model": "TOPOLI_DEVICE_MODEL",
    "system_version": "TOPOLI_SYSTEM_VERSION",
    "app_version": "TOPOLI_APP_VERSION",
}


def default_device() -> dict:
    return {
        "device_model": "ELBoT",
        "system_version": f"{platform.system()} {platform.release()}".strip(),
        "app_version": f"ELBoT {VERSION}",
    }


def device_identity() -> dict:
    """The identity to announce: environment, then config, then the default."""
    cfg, base = load_config(), default_device()
    return {key: os.environ.get(env) or cfg.get(key) or base[key] for key, env in DEVICE_KEYS.items()}


#: Lock files held for the life of the process; closing one releases its cache.
_HELD: list = []


def cache_path(cache: str) -> Path:
    """The SQLite cache a long-running process should use.

    The named one (`topoli-mcp.session`) when it is free, so its entity cache
    survives restarts; a private one for this process when another holds it —
    two Claude sessions each start their own MCP server, and the second must
    not die on "database is locked". A private cache is deleted on exit.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for path in (
        SESSION.with_name(f"{SESSION.stem}-{cache}.session"),
        SESSION.with_name(f"{SESSION.stem}-{cache}-{os.getpid()}.session"),
    ):
        lock = path.with_suffix(".lock").open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            continue
        _HELD.append(lock)
        if str(os.getpid()) in path.name:
            atexit.register(lambda p=path: [q.unlink(missing_ok=True) for q in (p, p.with_suffix(".lock"))])
        return path
    die(f"cannot lock a cache for {cache!r} in {STATE_DIR}")


def client(cache: str | None = None) -> TelegramClient:
    """A client on the shared auth key.

    `cache` names a separate SQLite cache for a long-running process (the MCP
    server, `listen`). The auth key is the same, and Telegram is happy with two
    connections on one key, but two processes writing one SQLite file are not:
    the second gets "database is locked". A cache of its own sidesteps that.
    """
    api_id, api_hash = credentials()
    path = cache_path(cache) if cache else SESSION
    who = device_identity()
    return TelegramClient(
        open_session(path),
        api_id,
        api_hash,
        proxy=proxy(),  # ty: ignore[invalid-argument-type]
        device_model=who["device_model"],
        system_version=who["system_version"],
        app_version=who["app_version"],
    )


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


def say(args, *parts, **kw) -> None:
    """Narration for a person: the dry-run block, progress, notes.

    It goes to stdout in the text format and to stderr under --json, so that
    with --json stdout carries exactly one JSON document (or one per line for
    `listen`) and a program can parse it without stripping prose first.
    """
    print(*parts, file=sys.stderr if getattr(args, "json", False) else sys.stdout, **kw)


def result(args, data: dict, text: str | None = None) -> None:
    """The outcome of a command: `data` as JSON under --json, else `text`."""
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif text:
        print(text)


def dry_run(args, data: dict, text: str) -> None:
    """Report a dry run in the caller's format; `data` says what *would* happen."""
    result(args, {"dry_run": True, **data}, f"\n{text}")


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


def is_forum(entity) -> bool:
    """Whether this supergroup is split into topics.

    A forum is still a supergroup — `kind` keeps saying so, because that is what
    it is — but a message sent to one without naming a topic lands in **General**
    and is invisible to anyone reading the topic the conversation is actually in.
    Everything topic-aware hangs off this flag.
    """
    return bool(getattr(entity, "forum", False))


#: The General topic. Telegram gives it id 1 in every forum and never names it
#: in the topic list the way the others are named, so it is spelled out here
#: rather than looked up.
GENERAL_TOPIC_ID = 1


def entity_row(entity) -> dict:
    """One chat flattened the same way everywhere: the index, `chats`, --json."""
    return {
        "id": entity.id,
        "title": label(entity),
        "username": getattr(entity, "username", None),
        "kind": kind(entity),
        "forum": is_forum(entity),
    }


def topic_row(topic) -> dict:
    """One forum topic flattened for `topics` and --json."""
    return {
        "id": topic.id,
        "title": getattr(topic, "title", ""),
        "unread": getattr(topic, "unread_count", 0),
        "closed": bool(getattr(topic, "closed", False)),
        "pinned": bool(getattr(topic, "pinned", False)),
        "general": topic.id == GENERAL_TOPIC_ID,
    }


def message_topic_id(msg) -> int | None:
    """Which forum topic a message sits in, or None if that cannot be told.

    Telegram does not stamp the topic onto the message; it is carried by the
    reply header, and reading it wrong is how a reply ends up in the wrong
    thread. Three shapes, in the order they are checked:

    - a reply *inside* a topic points at the topic root through
      ``reply_to_top_id``;
    - the first reply to the root has only ``reply_to_msg_id``, which *is* the
      root;
    - a message in **General**, or in a group with no topics at all, carries no
      header — so None means "not in a named topic", never "unknown".
    """
    header = getattr(msg, "reply_to", None)
    if header is None or not getattr(header, "forum_topic", False):
        return None
    return getattr(header, "reply_to_top_id", None) or getattr(header, "reply_to_msg_id", None)


def send_reply_to(reply_to: int | None, topic_id: int | None) -> int | None:
    """The single id `send_message` may be given, from a reply and/or a topic.

    Telethon's ``reply_to`` takes one integer, not a reply *and* a topic — it
    wraps whatever it is handed in ``InputReplyToMessage``, so passing a
    prepared object double-wraps it and the send fails. That is fine, because
    Telegram derives the topic from the message being replied to: a reply
    always lands in its parent's topic, which makes ``--reply-to`` the stronger
    of the two and ``--topic`` redundant beside it.

    So a reply wins. Where the two disagree, the caller is expected to have
    refused already (see ``check_topic_matches``) rather than silently
    delivering to the other thread.
    """
    return reply_to if reply_to is not None else topic_id


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


# Telethon's markdown is Telegram Desktop's dialect with three holes, all of
# which fail quietly rather than with an error: `||spoiler||` goes out as
# literal pipes, a `> ` line goes out as a literal `>`, and the info string of a
# fenced block (```python) stays in the text as the first line of code with no
# highlighting. The delimiter is a telethon extension point; the other two are
# patched around its parser below.
MARKDOWN_DELIMITERS = {**_markdown.DEFAULT_DELIMITERS, "||": MessageEntitySpoiler}

# A fence whose opening line is only an info string. ```one-liner``` and code
# written on the fence line itself (```x = 1) do not match and are left to
# telethon, which renders both as a plain pre block.
_FENCE = re.compile(r"```([\w+#.-]*)\n(.*?)\n?```", re.DOTALL)
_CODE = (MessageEntityCode, MessageEntityPre)


def _cut(text: str, entities, at: int, n: int) -> str:
    """Delete n UTF-16 units at `at` from surrogate text, keeping entities aligned."""
    for e in entities:
        lost = max(0, min(e.offset + e.length, at + n) - max(e.offset, at))
        if e.offset >= at + n:
            e.offset -= n
        elif e.offset > at:
            e.offset = at
        e.length -= lost
    return text[:at] + text[at + n :]


def _quote_lines(text: str, entities) -> tuple[str, list]:
    """Turn runs of `>`-prefixed lines into blockquote entities, as the desktop does.

    Lines inside code keep their `>` — a shell prompt or a diff in a code
    block is content, not markup.
    """
    s = add_surrogate(text)
    quotes: list[list[int]] = []
    pos = 0
    while pos < len(s):
        eol = s.find("\n", pos)
        eol = len(s) if eol == -1 else eol
        in_code = any(e.offset <= pos < e.offset + e.length for e in entities if isinstance(e, _CODE))
        if s.startswith(">", pos) and not in_code:
            n = 2 if s.startswith("> ", pos) else 1
            s = _cut(s, entities, pos, n)
            eol -= n
            if quotes and quotes[-1][1] == pos - 1:
                quotes[-1][1] = eol
            else:
                quotes.append([pos, eol])
        pos = eol + 1
    entities.extend(MessageEntityBlockquote(a, b - a) for a, b in quotes if b > a)
    return del_surrogate(s), entities


def parse_markdown(raw: str) -> tuple[str, list]:
    languages: list[str] = []

    def fence(m: re.Match) -> str:
        languages.append(m.group(1))
        return f"```{m.group(2)}```"

    text, entities = _markdown.parse(_FENCE.sub(fence, raw), delimiters=MARKDOWN_DELIMITERS)
    entities = list(entities)
    pres = sorted((e for e in entities if isinstance(e, MessageEntityPre)), key=lambda e: e.offset)
    # A fence inside inline code would throw the pairing off; better no
    # highlighting than the wrong language on every block after it.
    if len(pres) == len(languages):
        for e, lang in zip(pres, languages, strict=True):
            e.language = lang
    return _quote_lines(text, entities)


class _HTMLParser(_html.HTMLToTelegramParser):
    """Telethon's HTML parser plus the spoiler, in both of Telegram's spellings."""

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if tag == "tg-spoiler" or (tag == "span" and "tg-spoiler" in classes):
            self._open_tags.appendleft(tag)
            self._open_tags_meta.appendleft(None)
            self._building_entities.setdefault(tag, MessageEntitySpoiler(offset=len(self.text), length=0))
            return
        super().handle_starttag(tag, attrs)


def parse_html(raw: str) -> tuple[str, list]:
    """telethon.extensions.html.parse, on the parser above."""
    if not raw:
        return raw, []
    parser = _HTMLParser()
    parser.feed(add_surrogate(raw))
    text = strip_text(parser.text, parser.entities)
    parser.entities.reverse()
    parser.entities.sort(key=lambda e: e.offset)
    return del_surrogate(text), parser.entities


def build_message(raw: str, parse: str = "markdown", quote: bool = False, expandable: bool = False):
    """Turn raw text into (text, entities) for send_message.

    parse: 'markdown' (default; bold ** italic __ strike ~~ spoiler || code `
    pre ```lang, link [t](url), and `>` lines as a blockquote), 'html' (adds
    <u> underline and <tg-spoiler>), or 'none'.
    quote wraps the whole message in a blockquote; expandable makes it the
    collapsed (tap-to-expand) kind and implies quote.
    """
    mode = (parse or "markdown").lower()
    if mode in ("markdown", "md"):
        text, entities = parse_markdown(raw)
    elif mode == "html":
        text, entities = parse_html(raw)
    elif mode in ("none", "plain"):
        text, entities = raw, []
    else:
        die(f"unknown parse mode {parse!r}; pick one of {', '.join(PARSE_CHOICES)}")
    entities = list(entities)
    if (quote or expandable) and text:
        # Telegram rejects a blockquote inside a blockquote, so the whole-message
        # one replaces any the markup made.
        entities = [e for e in entities if not isinstance(e, MessageEntityBlockquote)]
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


def row_names(row) -> list[str]:
    """The names a cached row answers to, lowercased.

    `label` appends " (@username)" to the title, so the stored title of a chat
    that has a username never equals the name anyone would type. Offer the bare
    title alongside it, or exact matching silently degrades to substring
    matching for every chat with a username.
    """
    title = (row.get("title") or "").lower()
    uname = (row.get("username") or "").lower()
    names = [title]
    suffix = f" (@{uname})"
    if uname and title.endswith(suffix):
        names.append(title[: -len(suffix)])
    return names


def index_lookup(name: str):
    """Return [id,...] whose cached title/username matches name (exact ci, then
    substring). Empty list if the cache has no hit."""
    name = name.lower().lstrip("@")
    rows = load_index().values()
    exact = [r["id"] for r in rows if name in row_names(r) or (r.get("username") or "").lower() == name]
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
    # Merge rather than replace: `contacts` seeds this same cache with people
    # who have no dialog yet, and replacing it here would throw them away on
    # the next scan.
    save_index({**load_index(), **rows})
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

        async def by_title(ids):
            """One candidate -> the entity; several -> stop and ask for the id."""
            if len(ids) > 1:
                rows = load_index()
                opts = "; ".join(f"{i} {rows[str(i)]['title']}" for i in ids)
                die(f"{ref!r} matches several chats: {opts}. Pass the numeric id.")
            if not ids:
                return None
            try:
                ent = await cli.get_entity(ids[0])
            except ValueError, errors.RPCError:
                return None
            index_put(ent)
            return ent

        # A plain title: hit the cache first so repeat sends are instant.
        ent = await by_title(index_lookup(ref))
        if ent is not None:
            return ent
        # Cache miss (or first run): one live scan, which refreshes the cache —
        # then the same lookup again, so an ambiguous title stops and asks here
        # too. Taking the first dialog whose name merely *contains* the text is
        # how a message reaches the wrong person, and that cannot be taken back.
        await refresh_index(cli)
        ent = await by_title(index_lookup(ref))
        if ent is not None:
            return ent
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


def sender_name(msg, me_id=None) -> str:
    """Who sent it, by name. iter_messages caches the sender, so this is free."""
    if me_id is not None and msg.sender_id == me_id:
        return "me"
    sender = getattr(msg, "sender", None)
    return label(sender) if sender else str(msg.sender_id)


def msg_row(msg, me_id=None) -> dict:
    """One message flattened for --json, mirroring what the text format prints.

    ``reply_to`` and ``topic_id`` are here because without them a group reads as
    one flat conversation: four messages answering someone look like four
    unprompted remarks, and a thread nobody can see is a thread nobody can
    follow. Both are None in an ordinary chat.
    """
    header = getattr(msg, "reply_to", None)
    return {
        "id": msg.id,
        "date": ts(msg.date),
        "outgoing": me_id is not None and msg.sender_id == me_id,
        "sender_id": msg.sender_id,
        "sender": sender_name(msg, me_id),
        "reply_to": getattr(header, "reply_to_msg_id", None) if header else None,
        "topic_id": message_topic_id(msg),
        "text": body(msg),
        "media": media_row(msg),
        "forwarded": bool(getattr(msg, "fwd_from", None)),
        "edited": ts(msg.edit_date) if getattr(msg, "edit_date", None) else None,
    }


#: Checked in order: a voice note is also a document and an audio file, so the
#: most specific name has to win or every voice message reads as "[document]".
MEDIA_KINDS = ("voice", "video_note", "sticker", "gif", "video", "audio", "photo", "poll", "geo", "contact")


def media_row(msg) -> dict | None:
    """What a message carries besides text, enough to decide whether to fetch it.

    `body` prints only a placeholder; an agent reading --json needs to know that
    "[document]" is a 40 MB PDF called invoice.pdf, or that a voice note is 12
    seconds long and worth `transcribe`, without downloading it first.
    """
    media = getattr(msg, "media", None)
    if media is None:
        return None
    row: dict = {"type": type(media).__name__.replace("MessageMedia", "").lower() or "media"}
    for name in MEDIA_KINDS:
        if getattr(msg, name, None):
            row["type"] = name
            break
    f = getattr(msg, "file", None)
    if f is not None:
        for key, attr in (("name", "name"), ("mime", "mime_type"), ("size", "size"), ("duration", "duration")):
            with contextlib.suppress(Exception):  # telethon's File raises on media it cannot describe
                value = getattr(f, attr, None)
                if value is not None:
                    row[key] = value
    return row


def message_text(args) -> str:
    """The text of a send/edit/draft, from --text or --text-file.

    --text-file exists for agents and scripts: a reply with quotes, backticks,
    `$` and Persian in it survives a file untouched, where the same text pushed
    through a shell argument needs quoting that is easy to get subtly wrong.
    `-` reads standard input.
    """
    path = getattr(args, "text_file", None)
    if path:
        if path == "-":
            return sys.stdin.read()
        src = Path(path).expanduser()
        if not src.is_file():
            die(f"no such file: {src}")
        return src.read_text()
    text = getattr(args, "text", None)
    if text is None:
        die("give the message as --text or --text-file")
    return text


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
    print(f"next: {invocation()} login")


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

    # A phone in api-<account>.json is that account's. The one in the shared
    # api.json belongs to the default account; any other account must name its
    # own, or a login meant for it would go to the wrong number.
    own = read_json(ACCOUNT_CONFIG).get("phone")
    shared = read_json(REPO_CONFIG).get("phone") if default_account() == ACCOUNT else None
    phone = args.phone or own or shared
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
        print(f"then run:  {invocation()} login --code <code>")
        print("  add --password <2fa> if the account has two-step verification")
        return

    if not args.code:
        die(
            "no phone on file. Give one, or a code:\n"
            f"    {invocation()} login --phone +98XXXXXXXXXX\n"
            f"    {invocation()} login --code 12345 [--password <2fa>]"
        )

    if not PENDING.is_file():
        die(f"no login in progress. Start with: {invocation()} login --phone +98XXXXXXXXXX")
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
    if emit(
        {
            **entity_row(me),
            "phone": f"+{me.phone}" if me.phone else None,
            "premium": bool(getattr(me, "premium", False)),
            "device": device_identity(),
        },
        args,
    ):
        return
    print(f"account : {label(me)}")
    print(f"id      : {me.id}")
    print(f"phone   : +{me.phone}" if me.phone else "phone   : hidden")
    print(f"premium : {getattr(me, 'premium', False)}")
    print(f"auth key: {SESSION_STRING} (secret — never commit)")
    print(f"cache   : {SESSION} (local, disposable)")
    print(f"device  : {device_identity()['device_model']} — how this session shows in Settings → Devices")


async def cmd_chats(cli, args) -> None:
    rows, shown = {}, []
    only = tagged_ids(args.tag)
    async for dialog in cli.iter_dialogs(limit=args.limit):
        ent = dialog.entity
        rows[str(ent.id)] = entity_row(ent)
        if args.unread and not dialog.unread_count:
            continue
        if only is not None and ent.id not in only:
            continue
        shown.append(
            {
                **entity_row(ent),
                "tags": tags_of(ent.id),
                "unread": dialog.unread_count,
                "last_date": ts(dialog.message.date) if dialog.message else None,
                "last_text": body(dialog.message) if dialog.message else None,
            }
        )
    if rows:  # keep the fast title->id cache current for `send --chat <name>`
        save_index({**load_index(), **rows})

    if emit(shown, args):
        return
    for r in shown:
        flag = f"  [{r['unread']} unread]" if r["unread"] else ""
        print(f"{r['id']:>16}  {r['title']}  ({r['kind']}){flag}")
        if r["last_date"]:
            print(f"                  last {r['last_date']}: {(r['last_text'] or '')[:100]}")
    print(f"\n-- {len(shown)} chat(s); cache: {INDEX_FILE}", file=sys.stderr)


async def cmd_history(cli, args) -> None:
    entity = await resolve(cli, args.chat)
    me = await cli.get_me()

    since = parse_when(args.since, "--since")
    until = parse_when(args.until, "--until")
    from_user = await resolve(cli, args.from_user) if args.from_user else None

    # iter_messages walks backwards, so --until is where it starts and --since
    # is where it stops; the server has no "from this date" offset to give it.
    rows = []
    async for msg in cli.iter_messages(entity, limit=args.limit, offset_date=until, from_user=from_user):
        if since and msg.date and msg.date.astimezone(UTC) < since:
            break
        rows.append(msg)
    rows.reverse()  # oldest first, like reading a chat

    if emit({**entity_row(entity), "messages": [msg_row(m, me.id) for m in rows]}, args):
        return
    print(f"# {label(entity)} ({kind(entity)}, id={entity.id})\n")
    for msg in rows:
        arrow = "->" if msg.sender_id == me.id else "<-"
        print(f"[{ts(msg.date)}] {arrow} {sender_name(msg, me.id)} (msg {msg.id})")
        print(f"    {body(msg)}")
    print(f"\n-- {len(rows)} message(s)", file=sys.stderr)


async def cmd_search(cli, args) -> None:
    entity = await resolve(cli, args.chat) if args.chat else None
    me = await cli.get_me()
    since = parse_when(args.since, "--since")
    until = parse_when(args.until, "--until")

    rows = []
    async for msg in cli.iter_messages(entity, search=args.query, limit=args.limit, offset_date=until):
        if since and msg.date and msg.date.astimezone(UTC) < since:
            break
        chat = await msg.get_chat() if entity is None else entity
        rows.append({**msg_row(msg, me.id), "chat": label(chat), "chat_id": getattr(chat, "id", None)})

    if emit(rows, args):
        return
    for r in rows:
        print(f"[{r['date']}] {r['chat']} — {r['sender']} (msg {r['id']})")
        print(f"    {r['text']}")
    print(f"\n-- {len(rows)} hit(s)", file=sys.stderr)


def match_topic(topics: list[dict], ref: str) -> dict:
    """Pick one topic from a title, an id, or the word "general".

    Exact title first, then a unique case-insensitive substring — the same
    ladder `resolve` walks for chat titles, and for the same reason: two
    threads called "deploy" and "deploy v2" must not silently resolve to
    whichever came back first. An ambiguous reference raises with the matches
    listed, so the caller can print them and ask for an id.
    """
    raw = ref.strip()
    if raw.lstrip("-").isdigit():
        wanted = int(raw)
        for topic in topics:
            if topic["id"] == wanted:
                return topic
        raise LookupError(f"no topic with id {wanted} in this group")

    if raw.casefold() == "general":
        for topic in topics:
            if topic["general"]:
                return topic
        raise LookupError("this group has no General topic")

    exact = [t for t in topics if t["title"].casefold() == raw.casefold()]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        named = ", ".join(f"{t['title']} (id={t['id']})" for t in exact)
        raise LookupError(f"{len(exact)} topics are called {raw!r}: {named}")

    partial = [t for t in topics if raw.casefold() in t["title"].casefold()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise LookupError(f"no topic matching {raw!r}. Run `topics --chat …` to see them")
    named = ", ".join(f"{t['title']} (id={t['id']})" for t in partial)
    raise LookupError(f"{raw!r} matches {len(partial)} topics: {named}")


async def fetch_topics(cli, entity) -> list[dict]:
    """Every topic in a forum, newest activity first."""
    res = await cli(
        GetForumTopicsRequest(peer=entity, offset_date=None, offset_id=0, offset_topic=0, limit=100, q=None)
    )
    # ForumTopicDeleted carries an id and nothing else; it is not a destination.
    return [topic_row(t) for t in res.topics if getattr(t, "title", None) is not None]


async def resolve_topic(cli, entity, ref: str) -> dict:
    """A --topic reference to one topic, or a clean exit explaining why not."""
    if not is_forum(entity):
        die(f"{label(entity)} has no topics — it is a {kind(entity)}, not a forum. Drop --topic.")
    try:
        return match_topic(await fetch_topics(cli, entity), ref)
    except LookupError as exc:
        die(str(exc))


async def check_topic_matches(cli, entity, reply_to: int, topic: dict) -> None:
    """Refuse a --reply-to and a --topic that point at different threads.

    A reply always lands in its parent's topic, so when the two disagree the
    `--topic` is not merely ignored — the message goes somewhere the caller
    explicitly said it should not. Better to stop than to deliver it there.
    """
    parent = await cli.get_messages(entity, ids=reply_to)
    if parent is None:
        die(f"no message {reply_to} in {label(entity)}")
    where = message_topic_id(parent) or GENERAL_TOPIC_ID
    if where != topic["id"]:
        die(
            f"message {reply_to} is in topic {where}, not {topic['title']!r} (id={topic['id']}). "
            "A reply always lands in its parent's topic — drop one of --reply-to / --topic."
        )


async def cmd_topics(cli, args) -> None:
    """List a forum's topics, so --topic has something to name."""
    entity = await resolve(cli, args.chat)
    if not is_forum(entity):
        if args.json:
            print(json.dumps({"forum": False, "topics": []}, ensure_ascii=False, indent=2))
        else:
            print(f"{label(entity)} is a {kind(entity)} with no topics.")
        return

    topics = await fetch_topics(cli, entity)
    if args.json:
        print(json.dumps({"forum": True, "chat": entity_row(entity), "topics": topics}, ensure_ascii=False, indent=2))
        return

    print(f"{label(entity)} — {len(topics)} topic(s)")
    for topic in topics:
        marks = "".join(
            [
                " [general]" if topic["general"] else "",
                " [pinned]" if topic["pinned"] else "",
                " [closed]" if topic["closed"] else "",
            ]
        )
        unread = f"  {topic['unread']} unread" if topic["unread"] else ""
        print(f"  {topic['id']:>8}  {topic['title']}{marks}{unread}")


async def target_topic(cli, entity, args) -> dict | None:
    """The topic this send is aimed at, after every consistency check.

    Also warns when a forum is about to be posted to without naming a topic:
    that message lands in General, which in a group that lives in its topics is
    the one place nobody is reading. It is a warning rather than a refusal
    because General is a legitimate destination — but a silent one is how four
    review comments end up somewhere the person who asked for them never looks.
    """
    topic = await resolve_topic(cli, entity, args.topic) if getattr(args, "topic", None) else None
    reply_to = getattr(args, "reply_to", None)
    if topic and reply_to is not None:
        await check_topic_matches(cli, entity, reply_to, topic)
    if is_forum(entity) and topic is None and reply_to is None:
        say(args, f"  note   : {label(entity)} has topics and none was named — this goes to General")
    return topic


async def cmd_send(cli, args) -> None:
    entity = await resolve(cli, args.chat)
    text, entities = build_message(message_text(args), args.parse, args.quote, args.expandable)
    if not text.strip():
        die("refusing to send an empty message")
    when = parse_when(args.schedule, "--schedule")

    topic = await target_topic(cli, entity, args)

    say(args, "about to send")
    say(args, "  as     : your own account — indistinguishable from you typing it")
    say(args, f"  chat   : {label(entity)} ({kind(entity)}, id={entity.id})")
    if topic:
        say(args, f"  topic  : {topic['title']} (id={topic['id']})")
    if args.reply_to:
        say(args, f"  reply  : to message {args.reply_to}")
    say(args, f"  parse  : {args.parse}{'  +quote' if args.quote else ''}{'  (expandable)' if args.expandable else ''}")
    say(args, f"  format : {format_summary(entities)}")
    if when:
        say(args, f"  when   : {ts(when)} (scheduled — Telegram delivers it, not this process)")
    if args.silent:
        say(args, "  notify : silently — no sound or vibration on their side")
    say(args, f"  text   : {text}")

    plan = {
        "chat": entity_row(entity),
        "topic": topic,
        "reply_to": args.reply_to,
        "schedule": ts(when) if when else None,
        "silent": args.silent,
        "format": format_summary(entities),
        "text": text,
    }
    if not args.yes:
        dry_run(args, plan, "DRY RUN — nothing sent. Re-run with --yes to actually deliver.")
        return

    stop_typing(entity.id)
    # parse_mode=None matters: telethon re-parses the text with its own default
    # markdown whenever formatting_entities is empty, which is exactly the
    # `--parse none` case — the one where the backticks and underscores in a
    # path or an env var have to survive.
    msg = await cli.send_message(
        entity,
        text,
        formatting_entities=entities or None,
        parse_mode=None,
        reply_to=send_reply_to(args.reply_to, topic["id"] if topic else None),
        schedule=when,
        silent=args.silent,
        link_preview=not args.no_preview,
    )
    result(
        args,
        {**plan, "dry_run": False, "message_id": msg.id, "date": ts(msg.date)},
        f"{'scheduled' if when else 'sent'}: message_id={msg.id} at {ts(msg.date)}",
    )


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
            say(args, f"saved: {path}  ({size:.1f} MB)")

    if not saved:
        die("nothing downloaded")
    result(args, {"chat": entity_row(entity), "saved": saved})


def photo_request(entity, photo):
    """The RPC that changes one chat's photo, which is not one RPC.

    A basic group and a supergroup take different requests with different
    parameter names — `messages.EditChatPhoto(chat_id=…)` against a `Chat`,
    `channels.EditPhoto(channel=…)` against a `Channel`. Sending the wrong one
    fails with a type error rather than a permission error, which reads as a
    bug in the tool rather than the wrong call, so the choice is made here and
    covered by a test.

    A `User` has no chat photo to edit. Changing your *own* avatar is
    `photos.UploadProfilePhoto`, a different thing with a much wider blast
    radius, so this refuses rather than quietly doing it: that is
    `profile --photo`, which says so in its own preview.
    """
    if isinstance(entity, Chat):
        return EditChatPhotoRequest(chat_id=entity.id, photo=photo)
    if isinstance(entity, Channel):
        return EditPhotoRequest(channel=entity, photo=photo)
    die("a chat photo belongs to a group or channel; this is a private chat")


async def cmd_chat_photo(cli, args) -> None:
    """Read, replace or clear a group's photo.

    With neither --set nor --remove this only reports what is there, and
    --out saves it. That asymmetry is deliberate: fetching a photo is how you
    find out what you are about to overwrite, and it is the half that has no
    consequences.

    Replacing one is a write like any other here — everyone in the group sees
    it, and Telegram keeps no undo — so it prints what it is about to do and
    needs --yes. There is no way to restore the previous photo except by
    setting it again, which is why --out exists and why this prints a reminder
    to use it first.
    """
    entity = await resolve(cli, args.chat)

    if args.set and args.remove:
        die("--set replaces the photo and --remove clears it; pick one")

    if not args.set and not args.remove:
        out_dir = Path(args.out).expanduser() if args.out else None
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
        path = await cli.download_profile_photo(entity, file=str(out_dir) if out_dir else bytes, download_big=True)
        if not path:
            result(
                args,
                {"chat": entity_row(entity), "photo": False},
                f"{label(entity)} ({kind(entity)}, id={entity.id}) has no photo",
            )
            return
        if out_dir:
            size = Path(path).stat().st_size / 1024
            result(
                args, {"chat": entity_row(entity), "photo": True, "path": str(path)}, f"saved: {path}  ({size:.0f} KB)"
            )
        else:
            result(
                args,
                {"chat": entity_row(entity), "photo": True, "bytes": len(path)},
                f"{label(entity)} has a photo ({len(path)} bytes); pass --out DIR to save it",
            )
        return

    say(args, "about to change a chat photo")
    say(args, f"  chat  : {label(entity)} ({kind(entity)}, id={entity.id})")
    if args.set:
        src = Path(args.set).expanduser()
        if not src.is_file():
            die(f"no such file: {src}")
        say(args, f"  set   : {src}  ({src.stat().st_size / 1024:.0f} KB)")
    else:
        say(args, "  remove: the group will have no photo")
    say(args, "  note  : everyone in the chat sees this, and there is no undo —")
    say(args, "          save the current one first with --out if it matters")

    plan = {"chat": entity_row(entity), "set": args.set, "remove": args.remove}
    if not args.yes:
        dry_run(args, plan, "DRY RUN -- nothing changed. Re-run with --yes to apply.")
        return

    if args.set:
        uploaded = await cli.upload_file(str(Path(args.set).expanduser()))
        photo = InputChatUploadedPhoto(file=uploaded)
    else:
        photo = InputChatPhotoEmpty()

    try:
        await cli(photo_request(entity, photo))
    except errors.ChatAdminRequiredError:
        die(f"changing the photo of {label(entity)} needs admin rights in that chat")
    result(args, {**plan, "dry_run": False}, "chat photo updated" if args.set else "chat photo removed")


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

    text, entities = build_message(message_text(args), args.parse, args.quote, args.expandable)
    say(args, "about to edit")
    say(args, f"  chat   : {label(entity)} ({kind(entity)}, id={entity.id})")
    say(args, f"  message: {args.message} of {ts(target.date)}")
    say(args, f"  before : {target.text or '[no text]'}")
    say(args, f"  after  : {text}")
    say(args, f"  format : {format_summary(entities)}")

    plan = {"chat": entity_row(entity), "message_id": args.message, "before": target.text, "after": text}
    if not args.yes:
        dry_run(args, plan, "DRY RUN -- nothing changed. Re-run with --yes to apply.")
        return

    await cli.edit_message(entity, args.message, text, formatting_entities=entities or None, parse_mode=None)
    result(args, {**plan, "dry_run": False}, f"edited: message_id={args.message}")


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

    say(args, "about to delete")
    say(args, f"  chat  : {label(entity)} ({kind(entity)}, id={entity.id})")
    say(args, f"  scope : {'your own view only' if args.only_me else 'everyone in this chat'}")
    for mid, msg in found:
        who = "me" if msg.sender_id == me.id else (label(msg.sender) if msg.sender else str(msg.sender_id))
        say(args, f"  - {mid} [{ts(msg.date)}] {who}: {body(msg)[:120]}")
    missing = [mid for mid, m in zip(args.message, msgs, strict=True) if m is None]
    if missing:
        say(args, f"  (not found, skipped: {', '.join(str(m) for m in missing)})")

    plan = {
        "chat": entity_row(entity),
        "message_ids": [mid for mid, _ in found],
        "missing": missing,
        "for_everyone": not args.only_me,
    }
    if not args.yes:
        dry_run(args, plan, "DRY RUN -- nothing deleted. Re-run with --yes to apply.")
        return

    await cli.delete_messages(entity, [mid for mid, _ in found], revoke=not args.only_me)
    result(args, {**plan, "dry_run": False}, f"deleted: {len(found)} message(s)")


async def cmd_forward(cli, args) -> None:
    """Move messages between chats without retyping or losing attribution."""
    src = await resolve(cli, args.chat)
    dst = await resolve(cli, args.to)
    msgs = await cli.get_messages(src, ids=args.message)
    found = [(mid, m) for mid, m in zip(args.message, msgs, strict=True) if m is not None]
    if not found:
        die(f"none of those messages exist in {label(src)}")

    say(args, "about to forward")
    say(args, f"  from  : {label(src)} ({kind(src)}, id={src.id})")
    say(args, f"  to    : {label(dst)} ({kind(dst)}, id={dst.id})")
    for mid, msg in found:
        say(args, f"  - {mid} [{ts(msg.date)}]: {body(msg)[:120]}")

    plan = {"from": entity_row(src), "to": entity_row(dst), "message_ids": [mid for mid, _ in found]}
    if not args.yes:
        dry_run(args, plan, "DRY RUN -- nothing forwarded. Re-run with --yes to deliver.")
        return

    sent = await cli.forward_messages(dst, [mid for mid, _ in found], src)
    ids = [m.id for m in (sent if isinstance(sent, list) else [sent])]
    result(args, {**plan, "dry_run": False, "new_message_ids": ids}, f"forwarded: message_id(s)={ids}")


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


async def cmd_delete_contact(cli, args) -> None:
    """Remove people from the saved address book.

    Only the contact entry goes: the chat and its history stay, and the person
    is not told. What they lose is what a contact gets, such as a phone number
    shown under the "My Contacts" privacy rule. It is still a write with no undo
    here (adding someone back needs their number), so it is a dry run until
    --yes, and anyone who is not a saved contact is refused rather than skipped.
    """
    res = await cli(GetContactsRequest(hash=0))
    saved = {u.id: u for u in getattr(res, "users", [])}
    people = []
    for ref in args.user:
        entity = await resolve(cli, ref)
        if not isinstance(entity, User):
            die(f"{ref} is {kind(entity)} {label(entity)!r}, not a person")
        if entity.id not in saved:
            die(f"{label(entity)} (id={entity.id}) is not a saved contact")
        if entity.id not in {p.id for p in people}:
            people.append(saved[entity.id])
    rows = [{**entity_row(u), "phone": getattr(u, "phone", None)} for u in people]
    for u in people:
        phone = f" +{u.phone}" if getattr(u, "phone", None) else ""
        say(args, f"about to delete contact: {label(u)}{phone} (id={u.id})")
    say(args, "  note   : the chat and its history stay; they are not notified")
    plan = {"action": "delete-contact", "contacts": rows}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — no contact deleted. Re-run with --yes to delete.")
        return
    await cli(DeleteContactsRequest(id=people))
    result(args, {**plan, "dry_run": False}, f"deleted {len(people)} contact(s)")


async def cmd_catchup(cli, args) -> None:
    """Everything unread, in one pass -- the "what did I miss" command.

    `history --limit N` makes you guess N. Telegram already tracks exactly how
    many messages are unread per chat, so read that many and no more. Nothing is
    marked read unless --read is passed: seeing a message and acknowledging it
    are different acts, and the second one is visible to the other side.
    """
    me = await cli.get_me()
    only = tagged_ids(args.tag)
    found = []  # (entity, unread_count, [message, ...])
    async for dialog in cli.iter_dialogs(limit=args.limit):
        unread = dialog.unread_count
        if not unread:
            continue
        entity = dialog.entity
        if only is not None and entity.id not in only:
            continue
        msgs = []
        async for msg in cli.iter_messages(entity, limit=min(unread, args.max_per_chat)):
            msgs.append(msg)
        msgs.reverse()  # oldest first, like reading a chat
        found.append((entity, unread, msgs))

    rows = [
        {**entity_row(e), "tags": tags_of(e.id), "unread": n, "messages": [msg_row(m, me.id) for m in msgs]}
        for e, n, msgs in found
    ]
    if not emit(rows, args):
        for entity, unread, msgs in found:
            more = f" (showing the last {len(msgs)})" if unread > len(msgs) else ""
            print(f"\n# {label(entity)} ({kind(entity)}, id={entity.id}) — {unread} unread{more}")
            for msg in msgs:
                arrow = "->" if msg.sender_id == me.id else "<-"
                print(f"[{ts(msg.date)}] {arrow} {sender_name(msg, me.id)} (msg {msg.id})")
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
        # Both books, not the first hit: the local one shadows the shared one,
        # so stopping early would leave the alias apparently still set.
        gone = []
        for target in (ALIAS_REPO, ALIAS_LOCAL):
            rows = {k.lower(): v for k, v in read_json(target).items()}
            if rows.pop(args.remove.lower(), None) is not None:
                save_aliases(rows, local=target is ALIAS_LOCAL)
                gone.append(target)
        if not gone:
            die(f"no alias called {args.remove!r}")
        for target in gone:
            say(args, f"removed alias {args.remove!r} from {target}")

    if args.set:
        name, ref = args.set
        entity = await resolve(cli, ref)
        target = ALIAS_LOCAL if args.local else ALIAS_REPO
        rows = {k.lower(): v for k, v in read_json(target).items()}
        rows[name.lower()] = {"id": entity.id, "label": label(entity), "kind": kind(entity)}
        save_aliases(rows, args.local)
        say(args, f"{name} -> {label(entity)} ({kind(entity)}, id={entity.id})")
        say(args, f"stored in {target}")
        return

    rows = load_aliases()
    listing = [
        {"alias": k, **(v if isinstance(v, dict) else {"id": v, "label": "?", "kind": "?"})}
        for k, v in sorted(rows.items())
    ]
    if emit(listing, args):
        return
    for r in listing:
        say(args, f"{r['alias']:<16} {r['id']:>16}  {r.get('label', '?')}")
    print(f"\n-- {len(listing)} alias(es); {ALIAS_REPO} + {ALIAS_LOCAL}", file=sys.stderr)


async def cmd_create_group(cli, args) -> None:
    """Create a group that starts out with nobody in it but Parham.

    Telegram's *basic* groups cannot exist with a single member — CreateChat
    wants someone to invite. So this makes a **megagroup** (supergroup), which
    can, and which is what the app creates for new groups anyway. The practical
    differences that matter later: a supergroup has a numeric id the bot sees
    with a -100 prefix, and it can be given a username or made public.
    """
    say(args, "about to create a group")
    say(args, f"  title  : {args.title}")
    if args.about:
        say(args, f"  about  : {args.about}")
    say(args, "  members: you alone — nobody is invited")

    plan = {"title": args.title, "about": args.about}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — nothing created. Re-run with --yes to actually create it.")
        return

    res = await cli(CreateChannelRequest(title=args.title, about=args.about or "", megagroup=True))
    chat = res.chats[0]
    result(
        args,
        {**plan, "dry_run": False, "id": chat.id, "bot_api_id": int(f"-100{chat.id}")},
        f"created: {chat.title} (supergroup, id={chat.id})\n  Bot API id for the same chat: -100{chat.id}",
    )


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

    say(args, "about to react")
    say(args, "  as      : your own account")
    say(args, f"  chat    : {label(entity)} ({kind(entity)}, id={entity.id})")
    say(args, f"  message : {args.message} from {who}")
    say(args, f"  text    : {(target.text or '[no text]')[:200]}")
    say(args, f"  emoji   : {'(remove existing reaction)' if args.remove else args.emoji}")

    plan = {"chat": entity_row(entity), "message_id": args.message, "emoji": None if args.remove else args.emoji}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — nothing sent. Re-run with --yes to actually react.")
        return

    # An empty reaction list is how MTProto clears one; there is no separate
    # delete method.
    reaction = [] if args.remove else [ReactionEmoji(emoticon=args.emoji)]
    try:
        await cli(SendReactionRequest(peer=entity, msg_id=args.message, reaction=reaction))  # ty: ignore[invalid-argument-type]
    except errors.ReactionInvalidError:
        die(f"{args.emoji!r} is not an allowed reaction in this chat — groups can restrict which emoji are available")
    result(
        args,
        {**plan, "dry_run": False},
        f"reacted: {'cleared' if args.remove else args.emoji} on message {args.message}",
    )


async def cmd_send_file(cli, args) -> None:
    entity = await resolve(cli, args.chat)
    paths = [Path(f).expanduser() for f in args.file]
    for p in paths:
        if not p.is_file():
            die(f"no such file: {p}")

    topic = await target_topic(cli, entity, args)

    say(args, "about to send")
    say(args, "  as    : your own account")
    say(args, f"  chat  : {label(entity)} ({kind(entity)}, id={entity.id})")
    if topic:
        say(args, f"  topic : {topic['title']} (id={topic['id']})")
    if args.reply_to:
        say(args, f"  reply : to message {args.reply_to}")
    for p in paths:
        say(args, f"  file  : {p}  ({p.stat().st_size / 1024 / 1024:.1f} MB)")
    cap_text, cap_entities = (
        build_message(args.caption, args.parse, args.quote, args.expandable) if args.caption else ("", [])
    )
    if args.caption:
        say(args, f"  caption: {cap_text}  [{args.parse}{'  +quote' if args.quote else ''}]")

    plan = {
        "chat": entity_row(entity),
        "topic": topic,
        "reply_to": args.reply_to,
        "files": [str(p) for p in paths],
        "caption": cap_text or None,
    }
    if not args.yes:
        dry_run(args, plan, "DRY RUN — nothing sent. Re-run with --yes to actually deliver.")
        return

    stop_typing(entity.id)
    sent = await cli.send_file(
        entity,
        [str(p) for p in paths],
        caption=cap_text or None,
        formatting_entities=cap_entities or None,
        parse_mode=None,  # the caption is already parsed; see cmd_send
        force_document=not args.photo,
        reply_to=send_reply_to(args.reply_to, topic["id"] if topic else None),
        silent=args.silent,
    )
    ids = [m.id for m in (sent if isinstance(sent, list) else [sent])]
    result(args, {**plan, "dry_run": False, "message_ids": ids}, f"sent: message_id(s)={ids}")


async def cmd_read(cli, args) -> None:
    """Mark chats read, clearing the unread badge and any pending @mention.

    There is no un-read. The badge is gone once this runs, and in a private chat
    the other side sees the read receipt — so it names every chat it touched
    rather than printing a count, which is what makes a mistyped id obvious.
    """
    done = []
    for ref in args.chat:
        entity = await resolve(cli, ref)
        if args.unread:
            # The reverse is only a flag on the dialog: it brings the badge back
            # on Parham's own devices, and the other side sees nothing.
            peer = InputDialogPeer(await cli.get_input_entity(entity))
            await cli(MarkDialogUnreadRequest(peer=peer, unread=True))
        else:
            await cli.send_read_acknowledge(entity, clear_mentions=True)
        done.append(entity_row(entity))
        say(args, f"marked {'unread' if args.unread else 'read'}: {label(entity)} ({kind(entity)}, id={entity.id})")
    result(args, {"marked": "unread" if args.unread else "read", "chats": done})


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
        rows = [{"id": f.id, "title": filter_title(f), "color": f.color} for f in holding]
        if emit({**entity_row(entity), "folders": rows, "tags_enabled": tags_enabled}, args):
            return
        print(f"{label(entity)} ({kind(entity)}, id={entity.id})")
        for f in holding:
            print(f"  {filter_title(f)}  (id={f.id}, colour {f.color})")
        print(f"\n-- in {len(holding)} folder(s)", file=sys.stderr)
        return

    if emit(
        [
            {
                "id": f.id,
                "title": filter_title(f),
                "color": f.color,
                "chats": len(filter_peers(f)),
                "pinned": len(getattr(f, "pinned_peers", []) or []),
                "excluded": len(getattr(f, "exclude_peers", []) or []),
                "shared": isinstance(f, DialogFilterChatlist),
                "tags_enabled": tags_enabled,
            }
            for f in filters
        ],
        args,
    ):
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
            say(args, f"already there: {label(entity)}")
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
            say(args, f"not in {filter_title(target)!r}: {label(entity)}")
            continue
        removed.append(label(entity))

    colour = None if args.color == -1 else args.color
    recolour = args.color is not None and colour != target.color

    if not (added or removed or recolour):
        result(args, {"changed": False}, "nothing to change")
        return

    say(args, f"about to edit folder {filter_title(target)!r} (id={target.id})")
    for name in added:
        say(args, f"  + {name}")
    for name in removed:
        say(args, f"  - {name}")
    if recolour:
        say(args, f"  colour {target.color} -> {colour}")

    plan = {"folder": filter_title(target), "id": target.id, "added": added, "removed": removed}
    if recolour:
        plan["color"] = colour
    if not args.yes:
        dry_run(args, plan, "DRY RUN — nothing changed. Re-run with --yes to apply.")
        return

    # Mutate the filter that came back rather than building a fresh one: the
    # request replaces the whole object, so every flag we do not touch
    # (contacts, exclude_muted, emoticon, ...) has to be carried over intact.
    target.pinned_peers = pinned
    target.include_peers = include
    if recolour:
        target.color = colour
    await cli(UpdateDialogFilterRequest(id=target.id, filter=target))
    result(
        args,
        {**plan, "dry_run": False, "chats": len(pinned) + len(include)},
        f"updated: {len(pinned) + len(include)} chat(s) in {filter_title(target)!r}",
    )


async def cmd_tags(cli, args) -> None:
    """Show, or flip, the switch that renders folders as chat-list chips."""
    filters, enabled = await dialog_filters(cli)

    if not (args.on or args.off):
        result(
            args,
            {"tags_enabled": enabled, "folders": len(filters)},
            f"folder tags: {'on' if enabled else 'off'}\n{len(filters)} folder(s) would show as chips — see `folders`",
        )
        return

    want = bool(args.on)
    if want == enabled:
        result(args, {"tags_enabled": enabled, "changed": False}, f"folder tags already {'on' if enabled else 'off'}")
        return

    try:
        await cli(ToggleDialogFilterTagsRequest(enabled=want))
    except errors.RPCError as exc:
        die(f"could not toggle folder tags: {exc}. This one needs Telegram Premium.")
    result(args, {"tags_enabled": want, "changed": True}, f"folder tags: {'on' if want else 'off'}")


# --- the session itself: its name, and the account's other devices ----------


async def cmd_accounts(cli, args) -> None:  # never reaches the network
    """The accounts this tool can act as: one per `*.authkey` beside the script."""
    rows = [
        {
            "account": key.stem,
            "default": key.stem == default_account(),
            "selected": key.stem == ACCOUNT,
            "authkey": str(key),
        }
        for key in sorted(SKILL_DIR.glob("*.authkey"))
    ]
    if emit(rows, args):
        return
    for r in rows:
        mark = "*" if r["selected"] else " "
        print(f"{mark} {r['account']:<16} {r['authkey']}{'  (default)' if r['default'] else ''}")
    if not rows:
        print("no accounts yet. Log one in with: uv run topoli_user.py [--account NAME] login --phone <number>")
    print("\n-- pick one with --account NAME or TOPOLI_ACCOUNT=NAME; whoami says who it is", file=sys.stderr)


async def cmd_device(cli, args) -> None:
    """Show, or rename, how this session appears in Settings → Devices.

    Renaming writes the config only; the new name goes out with the next
    connection's initConnection, which is when Telegram records it. So a rename
    is offline, and `sessions` on the next run is how to see it took.
    """
    if args.name or args.system or args.app or args.reset:
        target = CONFIG_FILE if args.local else ACCOUNT_CONFIG if args.this_account else REPO_CONFIG
        if args.local:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
        cfg = read_json(target)
        if args.reset:
            for key in DEVICE_KEYS:
                cfg.pop(key, None)
        for key, value in (("device_model", args.name), ("system_version", args.system), ("app_version", args.app)):
            if value:
                cfg[key] = value
        target.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
        target.chmod(0o600)
        now = device_identity()
        result(
            args,
            {"stored_in": str(target), "device": now},
            f"device name: {now['device_model']}  ({now['system_version']}, {now['app_version']})\n"
            f"stored in {target}; Telegram records it on the next connection — check with `sessions`",
        )
        return

    now = device_identity()
    shadowed = [env for env in DEVICE_KEYS.values() if os.environ.get(env)]
    result(
        args,
        {"device": now, "default": default_device(), "env_overrides": shadowed},
        "\n".join(
            [
                f"device : {now['device_model']}",
                f"system : {now['system_version']}",
                f"app    : {now['app_version']}",
                *([f"(set by the environment: {', '.join(shadowed)})"] if shadowed else []),
            ]
        ),
    )


def authorization_row(a) -> dict:
    return {
        "hash": a.hash,
        "current": bool(a.current),
        "device": a.device_model,
        "platform": a.platform,
        "system": a.system_version,
        "app": f"{a.app_name} {a.app_version}".strip(),
        "official": bool(a.official_app),
        "api_id": a.api_id,
        "ip": a.ip,
        "location": ", ".join(x for x in (a.region, a.country) if x),
        "created": ts(a.date_created),
        "active": ts(a.date_active),
    }


async def cmd_sessions(cli, args) -> None:
    """Every device logged in to the account, and the means to log one out.

    This is the same list as Settings → Devices, which makes it the way to
    check that a rename took (the current row carries the new name) and the
    way to find a session nobody recognises. Terminating one is a write with
    no undo — that device is logged out and needs a fresh login — so it is a
    dry run until --yes, and it refuses the session it is running on.
    """
    res = await cli(GetAuthorizationsRequest())
    rows = [authorization_row(a) for a in res.authorizations]
    rows.sort(key=lambda r: (not r["current"], r["active"]), reverse=False)

    if args.terminate is not None:
        hit = next((r for r in rows if r["hash"] == args.terminate), None)
        if hit is None:
            die(f"no session with hash {args.terminate}. Run `sessions` to list them.")
        if hit["current"]:
            die("that is this session. Log it out from another device, or delete the auth key.")
        say(args, "about to log out a device")
        say(args, f"  device : {hit['device']} — {hit['app']} ({hit['system']})")
        say(args, f"  where  : {hit['ip']} {hit['location']}, last active {hit['active']}")
        if not args.yes:
            dry_run(args, {"terminate": hit}, "DRY RUN — nothing changed. Re-run with --yes to log it out.")
            return
        await cli(ResetAuthorizationRequest(hash=args.terminate))
        result(args, {"dry_run": False, "terminated": hit}, f"logged out: {hit['device']}")
        return

    if emit(rows, args):
        return
    for r in rows:
        mark = "* " if r["current"] else "  "
        print(f"{mark}{r['device']}  —  {r['app']}  ({r['system']})")
        print(f"    hash={r['hash']}  {r['ip']} {r['location']}  active {r['active']}")
    print(f"\n-- {len(rows)} session(s); * is this one", file=sys.stderr)


# --- a single message, with what surrounds it --------------------------------


async def cmd_get(cli, args) -> None:
    """Fetch messages by id, optionally with the ones around them.

    `history` reads from the end; a reply, a mention or a search hit points
    into the middle of a chat, and this reads exactly there — the message a
    reply answers (`reply_to` in --json) is one call away.
    """
    entity = await resolve(cli, args.chat)
    me = await cli.get_me()
    wanted: list[int] = []
    for mid in args.message:
        wanted.extend(range(mid - args.context, mid + args.context + 1))
    wanted = sorted({i for i in wanted if i > 0})
    msgs = [m for m in await cli.get_messages(entity, ids=wanted) if m is not None]
    if emit({**entity_row(entity), "messages": [msg_row(m, me.id) for m in msgs]}, args):
        return
    print(f"# {label(entity)} ({kind(entity)}, id={entity.id})\n")
    for msg in msgs:
        arrow = "->" if msg.sender_id == me.id else "<-"
        star = "  <==" if msg.id in args.message else ""
        print(f"[{ts(msg.date)}] {arrow} {sender_name(msg, me.id)} (msg {msg.id}){star}")
        print(f"    {body(msg)}")
    if not msgs:
        die(f"none of those messages exist in {label(entity)}")


async def cmd_mentions(cli, args) -> None:
    """Unread @mentions and replies to Parham, across every chat.

    `catchup` shows what is unread; in a busy group that is two hundred
    messages, of which the one that matters is the one that names him.
    Telegram counts those separately per dialog, so this reads exactly them.
    """
    me = await cli.get_me()
    out: list[dict] = []
    total = 0
    only = tagged_ids(args.tag)
    async for dialog in cli.iter_dialogs(limit=args.limit):
        if not getattr(dialog, "unread_mentions_count", 0):
            continue
        entity = dialog.entity
        if only is not None and entity.id not in only:
            continue
        res = await cli(
            GetUnreadMentionsRequest(
                peer=entity, offset_id=0, add_offset=0, limit=args.max_per_chat, max_id=0, min_id=0
            )
        )
        names = {u.id: label(u) for u in getattr(res, "users", [])}
        rows = []
        for msg in sorted(res.messages, key=lambda m: m.id):
            row = msg_row(msg, me.id)
            row["sender"] = names.get(msg.sender_id, row["sender"])
            rows.append(row)
        total += dialog.unread_mentions_count
        out.append({**entity_row(entity), "mentions": dialog.unread_mentions_count, "messages": rows})

    if emit(out, args):
        return
    for chat in out:
        print(f"\n# {chat['title']} ({chat['kind']}, id={chat['id']}) — {chat['mentions']} mention(s)")
        for r in chat["messages"]:
            print(f"[{r['date']}] <- {r['sender']} (msg {r['id']})")
            print(f"    {r['text']}")
    print(f"\n-- {total} unread mention(s) in {len(out)} chat(s)", file=sys.stderr)


async def cmd_transcribe(cli, args) -> None:
    """Turn a voice or video note into text with Telegram's own recogniser.

    A voice message is a blank `[voice]` to anything reading text, which is
    most of what an assistant is. Telegram transcribes server-side for Premium
    accounts (and gives others a few free tries a week). The first call often
    answers "pending"; asking again returns the same transcription once it is
    done, so this polls rather than subscribing to the update.
    """
    entity = await resolve(cli, args.chat)
    msgs = await cli.get_messages(entity, ids=[args.message])
    msg = msgs[0] if msgs else None
    if msg is None:
        die(f"message {args.message} not found in {label(entity)}")
    if not (getattr(msg, "voice", None) or getattr(msg, "video_note", None) or getattr(msg, "audio", None)):
        die(f"message {args.message} is not a voice, audio or video note")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + args.timeout
    try:
        res = await cli(TranscribeAudioRequest(peer=entity, msg_id=args.message))
        while getattr(res, "pending", False) and loop.time() < deadline:
            await asyncio.sleep(2)
            res = await cli(TranscribeAudioRequest(peer=entity, msg_id=args.message))
    except errors.RPCError as exc:
        if "PREMIUM" in str(exc).upper():
            die("transcription needs Telegram Premium (or a free trial this week)")
        raise
    row = {
        "chat": entity_row(entity),
        "message_id": args.message,
        "pending": bool(getattr(res, "pending", False)),
        "text": res.text,
        "trial_remains": getattr(res, "trial_remains_num", None),
    }
    result(args, row, res.text + ("\n(still pending — run it again)" if row["pending"] else ""))


# --- drafts: the reply Parham sends himself ---------------------------------


async def cmd_draft(cli, args) -> None:
    """Write a reply into a chat's input box, for Parham to read and send.

    This is the safest thing an assistant can do with a reply: the text lands
    in the chat's draft on every device he is logged in to, nobody else sees
    it, and nothing goes out until he presses send himself. With no --chat it
    lists the drafts that are waiting.

    Setting one replaces whatever he had half-typed in that chat, which is the
    one thing here that cannot be undone, so it is a dry run until --yes and
    the preview shows what is there now.
    """
    if not args.chat:
        rows = []
        async for draft in cli.iter_drafts():
            if not draft.text:
                continue
            rows.append({**entity_row(draft.entity), "date": ts(draft.date), "text": draft.text})
        if emit(rows, args):
            return
        for r in rows:
            print(f"{r['id']:>16}  {r['title']}  ({r['date']})")
            print(f"    {r['text']}")
        print(f"\n-- {len(rows)} draft(s)", file=sys.stderr)
        return

    entity = await resolve(cli, args.chat)
    current = await cli.get_drafts(entity)
    before = getattr(current, "text", "") or ""
    if args.clear:
        text, entities = "", []
    else:
        text, entities = build_message(message_text(args), args.parse, args.quote, args.expandable)

    say(args, "about to " + ("clear the draft" if args.clear else "write a draft"))
    say(args, f"  chat   : {label(entity)} ({kind(entity)}, id={entity.id})")
    say(args, f"  now    : {before or '(empty)'}")
    if not args.clear:
        say(args, f"  draft  : {text}")
        say(args, "  note   : only Parham sees a draft; nothing is sent")
    plan = {"chat": entity_row(entity), "before": before, "text": text, "reply_to": args.reply_to}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — draft untouched. Re-run with --yes to write it.")
        return

    stop_typing(entity.id)
    await cli(
        SaveDraftRequest(
            peer=entity,
            message=text,
            entities=entities or None,
            reply_to=InputReplyToMessage(reply_to_msg_id=args.reply_to) if args.reply_to else None,
        )
    )
    result(
        args,
        {**plan, "dry_run": False},
        "draft cleared" if args.clear else "draft saved — it is in the chat's input box",
    )


# --- chat housekeeping: pin, mute, archive, join, leave ----------------------


async def cmd_pin(cli, args) -> None:
    """Pin or unpin one message. Everyone in the chat sees a pin."""
    entity = await resolve(cli, args.chat)
    msgs = await cli.get_messages(entity, ids=[args.message])
    target = msgs[0] if msgs else None
    if target is None:
        die(f"message {args.message} not found in {label(entity)}")
    verb = "unpin" if args.unpin else "pin"
    say(args, f"about to {verb}")
    say(args, f"  chat   : {label(entity)} ({kind(entity)}, id={entity.id})")
    say(args, f"  message: {args.message}: {body(target)[:200]}")
    if not args.unpin:
        say(args, f"  notify : {'nobody (silent)' if args.silent else 'members get a notification'}")
    plan = {"chat": entity_row(entity), "message_id": args.message, "action": verb, "silent": args.silent}
    if not args.yes:
        dry_run(args, plan, f"DRY RUN — nothing {verb}ned. Re-run with --yes to apply.")
        return
    try:
        if args.unpin:
            await cli.unpin_message(entity, args.message)
        else:
            await cli.pin_message(entity, args.message, notify=not args.silent)
    except errors.ChatAdminRequiredError:
        die(f"pinning in {label(entity)} needs admin rights there")
    result(args, {**plan, "dry_run": False}, f"{verb}ned message {args.message}")


_DURATION = re.compile(r"(\d+)\s*([smhdw])")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}

#: Telegram's "forever": the largest 32-bit timestamp, which is what the apps send.
MUTE_FOREVER = 2**31 - 1


def parse_duration(text: str) -> timedelta:
    """'8h', '1d12h', '30m', '2w' -> a timedelta. Refuses anything else."""
    raw = text.strip().lower().replace(" ", "")
    parts = _DURATION.findall(raw)
    if not parts or "".join(n + u for n, u in parts) != raw:
        die(f"--for wants a duration like 30m, 8h, 1d or 2w, got {text!r}")
    total = timedelta()
    for n, u in parts:
        total += timedelta(**{_UNITS[u]: int(n)})
    return total


def mute_until(duration: str | None, off: bool) -> int:
    """The mute_until value for a chat: 0 unmutes, MUTE_FOREVER is forever."""
    if off:
        return 0
    if not duration or duration.lower() in ("forever", "always", "inf"):
        return MUTE_FOREVER
    return int((datetime.now(UTC) + parse_duration(duration)).timestamp())


async def cmd_mute(cli, args) -> None:
    """Mute or unmute a chat's notifications. Only Parham's devices notice."""
    entity = await resolve(cli, args.chat)
    until = mute_until(args.for_, args.off)
    when = "unmuted" if until == 0 else "forever" if until == MUTE_FOREVER else ts(datetime.fromtimestamp(until, UTC))
    plan = {"chat": entity_row(entity), "mute_until": when}
    say(args, f"about to {'unmute' if until == 0 else 'mute'} {label(entity)} — {when}")
    if not args.yes:
        dry_run(args, plan, "DRY RUN — nothing changed. Re-run with --yes to apply.")
        return
    peer = InputNotifyPeer(await cli.get_input_entity(entity))
    await cli(UpdateNotifySettingsRequest(peer=peer, settings=InputPeerNotifySettings(mute_until=until)))  # ty: ignore[invalid-argument-type]
    result(args, {**plan, "dry_run": False}, f"{'unmuted' if until == 0 else 'muted until ' + when}: {label(entity)}")


async def cmd_archive(cli, args) -> None:
    """Move chats into the Archive, or back out. Only Parham's chat list changes."""
    folder = 0 if args.unarchive else 1
    entities = [await resolve(cli, ref) for ref in args.chat]
    verb = "unarchive" if args.unarchive else "archive"
    for e in entities:
        say(args, f"about to {verb}: {label(e)} ({kind(e)}, id={e.id})")
    plan = {"action": verb, "chats": [entity_row(e) for e in entities]}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — nothing moved. Re-run with --yes to apply.")
        return
    for e in entities:
        await cli.edit_folder(e, folder)
    result(args, {**plan, "dry_run": False}, f"{verb}d {len(entities)} chat(s)")


_INVITE = re.compile(r"(?:t\.me/(?:\+|joinchat/)|tg://join\?invite=)([\w-]+)")


async def cmd_join(cli, args) -> None:
    """Join a public group or channel, or a private one through an invite link."""
    ref = args.link.strip()
    invite = _INVITE.search(ref)
    say(args, f"about to join: {ref}")
    say(args, "  note   : the group sees Parham join; leaving is `leave`")
    plan = {"link": ref, "invite": bool(invite)}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — not joined. Re-run with --yes to join.")
        return
    if invite:
        try:
            res = await cli(ImportChatInviteRequest(invite.group(1)))
        except errors.UserAlreadyParticipantError:
            die("already a member of that chat")
        chat = res.chats[0]
    else:
        chat = await cli.get_entity(ref)
        if isinstance(chat, User):
            die(f"{ref} is a person, not a group or channel")
        await cli(JoinChannelRequest(chat))
    index_put(chat)
    result(args, {**plan, "dry_run": False, "chat": entity_row(chat)}, f"joined: {label(chat)} (id={chat.id})")


async def cmd_leave(cli, args) -> None:
    """Leave a group or channel. Its members see Parham leave."""
    entity = await resolve(cli, args.chat)
    if isinstance(entity, User):
        die("that is a private chat — there is nothing to leave. To hide it, use `archive`.")
    say(args, f"about to leave: {label(entity)} ({kind(entity)}, id={entity.id})")
    say(args, "  note   : a private group needs a new invite to come back")
    plan = {"chat": entity_row(entity)}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — still a member. Re-run with --yes to leave.")
        return
    await cli.delete_dialog(entity)
    result(args, {**plan, "dry_run": False}, f"left: {label(entity)}")


async def cmd_invite(cli, args) -> None:
    """Add people to a group or channel. Everyone in it sees them arrive."""
    chat = await resolve(cli, args.chat)
    if isinstance(chat, User):
        die("that is a private chat — invite people into a group or channel instead")
    users = []
    for ref in args.user:
        user = await resolve(cli, ref)
        if not isinstance(user, User):
            die(f"{ref} is a {kind(user)}, not a person")
        users.append(user)
    say(args, f"about to add to {label(chat)} ({kind(chat)}, id={chat.id}):")
    for u in users:
        say(args, f"  + {label(u)} (id={u.id})")
    say(args, "  note   : the members see them join; this needs the right to add members")
    plan = {"chat": entity_row(chat), "users": [entity_row(u) for u in users]}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — nobody added. Re-run with --yes to add them.")
        return
    added, missing = [], []
    try:
        if isinstance(chat, Chat):  # a basic group adds one person per call
            for u in users:
                try:
                    await cli(AddChatUserRequest(chat_id=chat.id, user_id=u, fwd_limit=100))
                    added.append(u)
                except errors.UserAlreadyParticipantError:
                    say(args, f"  already a member: {label(u)}")
        else:
            res = await cli(InviteToChannelRequest(chat, [utils.get_input_user(u) for u in users]))
            skipped = {getattr(m, "user_id", None) for m in getattr(res, "missing_invitees", None) or []}
            for u in users:
                (missing if u.id in skipped else added).append(u)
    except errors.ChatWriteForbiddenError, errors.ChatAdminRequiredError:
        die(
            f"{label(chat)} does not let you add members — ask one of its admins "
            "(`members --admins`) to add them, or for an invite link they can `join`"
        )
    except errors.UserPrivacyRestrictedError:
        die("their privacy settings block being added — send them an invite link instead")
    for u in missing:
        say(args, f"  not added (privacy settings): {label(u)} — send an invite link")
    result(
        args,
        {
            **plan,
            "dry_run": False,
            "added": [entity_row(u) for u in added],
            "missing": [entity_row(u) for u in missing],
        },
        f"added {len(added)} of {len(users)} to {label(chat)}",
    )


# --- people: who is in a group, who someone is, and Parham's own profile -----


def status_text(user) -> str | None:
    """Last seen, in words, from a User's status object."""
    status = getattr(user, "status", None)
    if status is None:
        return None
    name = type(status).__name__.replace("UserStatus", "").lower()
    if name == "offline" and getattr(status, "was_online", None):
        return f"last seen {ts(status.was_online)}"
    if name == "online":
        return "online"
    return {
        "recently": "last seen recently",
        "lastweek": "last seen within a week",
        "lastmonth": "last seen within a month",
    }.get(name, name)


def role_of(user) -> str:
    """creator / admin / member, from the participant telethon attaches."""
    part = type(getattr(user, "participant", None)).__name__.lower()
    if "creator" in part:
        return "creator"
    if "admin" in part:
        return "admin"
    if "banned" in part:
        return "banned"
    return "member"


async def cmd_members(cli, args) -> None:
    """Who is in a group, with their role; --admins narrows it to the admins."""
    entity = await resolve(cli, args.chat)
    if isinstance(entity, User):
        die("a private chat has no member list")
    kwargs = {"filter": ChannelParticipantsAdmins()} if args.admins else {}
    rows = []
    try:
        async for user in cli.iter_participants(entity, limit=args.limit, search=args.query or "", **kwargs):
            rows.append({**entity_row(user), "role": role_of(user), "status": status_text(user)})
    except errors.ChatAdminRequiredError:
        die(f"the member list of {label(entity)} is visible to its admins only")
    if rows:
        save_index(
            {
                **load_index(),
                **{str(r["id"]): {k: r[k] for k in ("id", "title", "username", "kind", "forum")} for r in rows},
            }
        )
    if emit(rows, args):
        return
    for r in rows:
        role = f"  [{r['role']}]" if r["role"] != "member" else ""
        print(f"{r['id']:>16}  {r['title']}{role}  {r['status'] or ''}")
    print(f"\n-- {len(rows)} member(s) of {label(entity)}", file=sys.stderr)


async def cmd_info(cli, args) -> None:
    """Everything known about one person, group or channel, in one place.

    The bio, the phone, when they were last seen and how many groups Parham
    shares with them for a person; the description and member count for a
    group. It is what an assistant should read before writing to someone it
    has not written to before.
    """
    entity = await resolve(cli, args.chat)
    row: dict = {**entity_row(entity)}
    if isinstance(entity, User):
        full = (await cli(GetFullUserRequest(entity))).full_user
        row |= {
            "first_name": entity.first_name,
            "last_name": entity.last_name,
            "phone": f"+{entity.phone}" if entity.phone else None,
            "bio": full.about,
            "status": status_text(entity),
            "contact": bool(entity.contact),
            "mutual_contact": bool(entity.mutual_contact),
            "premium": bool(getattr(entity, "premium", False)),
            "verified": bool(entity.verified),
            "bot": bool(entity.bot),
            "common_chats": full.common_chats_count,
            "blocked": bool(full.blocked),
        }
    elif isinstance(entity, Chat):
        full = (await cli(GetFullChatRequest(entity.id))).full_chat
        row |= {"about": full.about, "members": getattr(entity, "participants_count", None)}
    else:
        full = (await cli(GetFullChannelRequest(entity))).full_chat
        row |= {
            "about": full.about,
            "members": full.participants_count,
            "admins": full.admins_count,
            "online": getattr(full, "online_count", None),
            "linked_chat_id": full.linked_chat_id,
            "slowmode_seconds": full.slowmode_seconds,
        }
    if emit(row, args):
        return
    width = max(len(k) for k in row)
    for key, value in row.items():
        if value not in (None, "", False):
            print(f"{key:<{width}} : {value}")


async def cmd_profile(cli, args) -> None:
    """Show Parham's own profile, or change his name, bio, username, birthday or photo.

    Every contact sees the change, so writes are a dry run until --yes. A new
    --photo goes on top and the old ones stay in the profile's history;
    --remove-photo deletes only the current one, so the previous photo comes
    back. --photo-out saves the current photo, which is the only undo there is.
    """
    if args.photo and args.remove_photo:
        die("--photo sets a new photo and --remove-photo deletes the current one; pick one")
    me = await cli.get_me()
    full = (await cli(GetFullUserRequest(me))).full_user
    photos = await cli(GetUserPhotosRequest(user_id=me, offset=0, max_id=0, limit=1))
    count = getattr(photos, "count", len(photos.photos))
    now = {
        "first_name": me.first_name,
        "last_name": me.last_name,
        "bio": full.about,
        "username": me.username,
        "birthday": birthday_text(full.birthday),
        "photos": count,
    }
    wanted = {
        k: v
        for k, v in (
            ("first_name", args.first),
            ("last_name", args.last),
            ("bio", args.bio),
            ("username", args.username),
        )
        if v is not None
    }
    birthday = None
    if args.birthday is not None:
        birthday = parse_birthday(args.birthday)
        wanted["birthday"] = birthday_text(birthday)
    src = None
    if args.photo:
        src = Path(args.photo).expanduser()
        if not src.is_file():
            die(f"no such file: {src}")
        wanted["photo"] = str(src)
    if args.remove_photo:
        if not photos.photos:
            die("there is no profile photo to remove")
        wanted["photo"] = None

    if args.photo_out:
        out_dir = Path(args.photo_out).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = await cli.download_profile_photo(me, file=str(out_dir), download_big=True)
        say(args, f"saved the current photo: {path}" if path else "there is no photo to save")
        now["saved_photo"] = str(path) if path else None

    if not wanted:
        result(args, {**entity_row(me), **now}, "\n".join(f"{k:<10} : {v or ''}" for k, v in now.items()))
        return

    say(args, "about to change the profile — every contact sees this")
    for key, value in wanted.items():
        if key == "photo":
            if value is None:
                say(args, f"  {'photo':<10}: delete the current one ({count} in history; the previous comes back)")
            else:
                say(args, f"  {'photo':<10}: {value}  ({Path(value).stat().st_size / 1024:.0f} KB), on top of {count}")
            continue
        say(args, f"  {key:<10}: {now[key] or '(empty)'} -> {value or '(empty)'}")
    plan = {"before": now, "after": {**now, **wanted}}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — profile unchanged. Re-run with --yes to apply.")
        return
    fields = {k: wanted[k] for k in ("first_name", "last_name") if k in wanted}
    if "bio" in wanted:
        fields["about"] = wanted["bio"]
    if fields:
        await cli(UpdateProfileRequest(**fields))
    if "username" in wanted:
        try:
            await cli(UpdateUsernameRequest(wanted["username"].lstrip("@")))
        except errors.UsernameOccupiedError:
            die(f"@{wanted['username']} is taken")
    if args.birthday is not None:
        await cli(UpdateBirthdayRequest(birthday=birthday))
    if src is not None:
        uploaded = await cli.upload_file(str(src))
        video = src.suffix.lower() in (".mp4", ".mov", ".webm")
        await cli(UploadProfilePhotoRequest(**({"video": uploaded} if video else {"file": uploaded})))
    elif args.remove_photo:
        p = photos.photos[0]
        await cli(
            DeletePhotosRequest(id=[InputPhoto(id=p.id, access_hash=p.access_hash, file_reference=p.file_reference)])
        )
    result(args, {**plan, "dry_run": False}, "profile updated")


# --- profile: photo, birthday, privacy ---------------------------------------


def parse_birthday(text: str):
    """`DD-MM` or `DD-MM-YYYY` as a Birthday; `none` clears it (returns None).

    Day first, as on every form outside the US, and with the year optional
    because Telegram treats it as optional: without it, people see the day but
    not the age.
    """
    if text.strip().lower() in ("none", "clear", ""):
        return None
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})(?:-(\d{4}))?", text.strip())
    if not m:
        die(f"--birthday wants DD-MM or DD-MM-YYYY (or none), not {text!r}")
    day, month = int(m[1]), int(m[2])
    year = int(m[3]) if m[3] else None
    try:
        datetime(year or 2000, month, day)  # 2000 is a leap year, so 29-02 passes without one
    except ValueError:
        die(f"{text} is not a real day")
    return Birthday(day=day, month=month, year=year)


def birthday_text(b) -> str | None:
    if b is None:
        return None
    return f"{b.day:02d}-{b.month:02d}" + (f"-{b.year}" if b.year else "")


#: The privacy settings by the names Settings → Privacy uses, to the key the
#: API takes. Order is the order the app lists them in.
PRIVACY_KEYS = {
    "phone": InputPrivacyKeyPhoneNumber,
    "find-by-phone": InputPrivacyKeyAddedByPhone,
    "last-seen": InputPrivacyKeyStatusTimestamp,
    "photo": InputPrivacyKeyProfilePhoto,
    "bio": InputPrivacyKeyAbout,
    "birthday": InputPrivacyKeyBirthday,
    "forwards": InputPrivacyKeyForwards,
    "calls": InputPrivacyKeyPhoneCall,
    "p2p": InputPrivacyKeyPhoneP2P,
    "voice": InputPrivacyKeyVoiceMessages,
    "invites": InputPrivacyKeyChatInvite,
    "gifts": InputPrivacyKeyStarGiftsAutoSave,
    "music": InputPrivacyKeySavedMusic,
}

#: The base rule each setting starts from; the exceptions are added to it.
PRIVACY_BASE = {
    "everybody": InputPrivacyValueAllowAll,
    "contacts": InputPrivacyValueAllowContacts,
    "nobody": InputPrivacyValueDisallowAll,
}


def privacy_rules(base: str, allow_users=(), deny_users=(), allow_chats=(), deny_chats=()) -> list:
    """The rule list for account.setPrivacy: the exceptions first, the base last.

    Telegram reads the list in order and the first rule that matches wins, so
    "contacts, except X" has to put the disallow of X ahead of AllowContacts.
    An exception that only repeats the base ("everybody, but allow X") would
    be dead, and is refused so that the preview says what really happens.
    """
    if base not in PRIVACY_BASE:
        die(f"--rule is one of {', '.join(PRIVACY_BASE)}")
    if base == "everybody" and (allow_users or allow_chats):
        die("everybody can already see it; an --allow exception does nothing there")
    if base == "nobody" and (deny_users or deny_chats):
        die("nobody can already see it; a --deny exception does nothing there")
    rules: list = []
    if allow_users:
        rules.append(InputPrivacyValueAllowUsers(users=list(allow_users)))
    if allow_chats:
        rules.append(InputPrivacyValueAllowChatParticipants(chats=list(allow_chats)))
    if deny_users:
        rules.append(InputPrivacyValueDisallowUsers(users=list(deny_users)))
    if deny_chats:
        rules.append(InputPrivacyValueDisallowChatParticipants(chats=list(deny_chats)))
    rules.append(PRIVACY_BASE[base]())
    return rules


def describe_rules(rules, names: dict[int, str] | None = None) -> dict:
    """A PrivacyRule list as {"rule": base, "allow": [...], "deny": [...]}.

    `names` maps the user and chat ids in the reply to readable names; the
    reply to getPrivacy carries those users and chats beside the rules.
    """
    names = names or {}
    out: dict = {"rule": "nobody", "allow": [], "deny": []}
    for r in rules:
        kind_ = type(r).__name__.removeprefix("PrivacyValue")
        if kind_ == "AllowAll":
            out["rule"] = "everybody"
        elif kind_ == "AllowContacts":
            out["rule"] = "contacts"
        elif kind_ == "DisallowAll":
            out["rule"] = "nobody"
        else:
            ids = getattr(r, "users", None) or getattr(r, "chats", None) or []
            who = [names.get(i, str(i)) for i in ids] or [kind_.removeprefix("Allow").removeprefix("Disallow").lower()]
            out["allow" if kind_.startswith("Allow") else "deny"] += who
    return out


def rules_text(d: dict) -> str:
    text = d["rule"]
    if d["allow"]:
        text += f", also {', '.join(d['allow'])}"
    if d["deny"]:
        text += f", except {', '.join(d['deny'])}"
    return text


async def privacy_targets(cli, refs: list[str] | None) -> tuple[list, list[int], list[str]]:
    """Resolve --allow/--deny references into (input users, chat ids, names)."""
    users, chats, names = [], [], []
    for ref in refs or []:
        entity = await resolve(cli, ref)
        names.append(label(entity))
        if isinstance(entity, User):
            users.append(await cli.get_input_entity(entity))
        else:
            chats.append(entity.id)
    return users, chats, names


async def read_privacy(cli) -> dict[str, dict]:
    settings = {}
    for name, key in PRIVACY_KEYS.items():
        try:
            res = await cli(GetPrivacyRequest(key=key()))
        except errors.RPCError as exc:  # a key this account or layer does not offer
            settings[name] = {"error": str(exc)}
            continue
        names = {u.id: label(u) for u in res.users} | {c.id: label(c) for c in res.chats}
        settings[name] = describe_rules(res.rules, names)
    return settings


async def cmd_privacy(cli, args) -> None:
    """Show or change Settings → Privacy and Security.

    With no change asked for it lists every privacy setting, how long the
    account survives being away, and whether chats from strangers are archived.
    A change is a dry run until --yes: it decides who can see the phone
    number or call, and a mistake there is quiet until someone uses it.
    """
    wants_rule = args.key is not None or args.rule is not None
    if wants_rule and (args.key is None or args.rule is None):
        die("--key and --rule go together: which setting, and who may see it")
    if (args.allow or args.deny) and not wants_rule:
        die("--allow and --deny are exceptions to a --key/--rule change")
    if args.away_days is not None and not 30 <= args.away_days <= 730:
        die("--away-days is between 30 and 730")
    if not (wants_rule or args.away_days is not None or args.archive_strangers):
        settings = await read_privacy(cli)
        ttl = (await cli(GetAccountTTLRequest())).days
        glob = await cli(GetGlobalPrivacySettingsRequest())
        data = {
            "privacy": settings,
            "away_days": ttl,
            "archive_strangers": bool(glob.archive_and_mute_new_noncontact_peers),
        }
        lines = [f"{k:<14}: {v.get('error') or rules_text(v)}" for k, v in settings.items()]
        lines.append(f"{'away-days':<14}: delete the account after {ttl} days away")
        lines.append(f"{'archive':<14}: {'on' if data['archive_strangers'] else 'off'} (new chats from non-contacts)")
        result(args, data, "\n".join(lines))
        return

    plan: dict = {}
    say(args, "about to change privacy settings")
    if wants_rule:
        if args.key not in PRIVACY_KEYS:
            die(f"--key is one of {', '.join(PRIVACY_KEYS)}")
        if args.key == "find-by-phone" and args.rule == "nobody":
            die("find-by-phone takes everybody or contacts; Telegram has no nobody for it")
        allow_u, allow_c, allow_n = await privacy_targets(cli, args.allow)
        deny_u, deny_c, deny_n = await privacy_targets(cli, args.deny)
        rules = privacy_rules(args.rule, allow_u, deny_u, allow_c, deny_c)
        res = await cli(GetPrivacyRequest(key=PRIVACY_KEYS[args.key]()))
        names = {u.id: label(u) for u in res.users} | {c.id: label(c) for c in res.chats}
        before = describe_rules(res.rules, names)
        after = {"rule": args.rule, "allow": allow_n, "deny": deny_n}
        say(args, f"  {args.key:<12}: {rules_text(before)} -> {rules_text(after)}")
        plan[args.key] = {"before": before, "after": after}
    if args.away_days is not None:
        ttl = (await cli(GetAccountTTLRequest())).days
        say(args, f"  {'away-days':<12}: {ttl} -> {args.away_days}")
        plan["away_days"] = {"before": ttl, "after": args.away_days}
    if args.archive_strangers:
        glob = await cli(GetGlobalPrivacySettingsRequest())
        now = bool(glob.archive_and_mute_new_noncontact_peers)
        say(args, f"  {'archive':<12}: {'on' if now else 'off'} -> {args.archive_strangers}")
        plan["archive_strangers"] = {"before": now, "after": args.archive_strangers == "on"}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — privacy unchanged. Re-run with --yes to apply.")
        return
    if wants_rule:
        await cli(SetPrivacyRequest(key=PRIVACY_KEYS[args.key](), rules=rules))
    if args.away_days is not None:
        await cli(SetAccountTTLRequest(ttl=AccountDaysTTL(days=args.away_days)))
    if args.archive_strangers:
        glob.archive_and_mute_new_noncontact_peers = args.archive_strangers == "on"
        await cli(SetGlobalPrivacySettingsRequest(settings=glob))
    result(args, {**plan, "dry_run": False}, "privacy updated")


# --- polls -------------------------------------------------------------------


def build_poll(question: str, options: list[str], multiple: bool, public: bool, quiz: int | None):
    """An InputMediaPoll. Answers are identified by one byte each, 0..n-1."""
    if not 2 <= len(options) <= 12:
        die("a poll needs between 2 and 12 --option values")
    if quiz is not None and not 0 <= quiz < len(options):
        die(f"--quiz wants the index of the right answer, 0..{len(options) - 1}")
    if quiz is not None and multiple:
        die("a quiz has one right answer; drop --multiple")
    poll = Poll(
        id=random.getrandbits(62),
        question=TextWithEntities(text=question, entities=[]),
        answers=[
            PollAnswer(text=TextWithEntities(text=o, entities=[]), option=bytes([i])) for i, o in enumerate(options)
        ],
        hash=0,
        multiple_choice=multiple or None,
        public_voters=public or None,
        quiz=(quiz is not None) or None,
    )
    return InputMediaPoll(poll=poll, correct_answers=[quiz] if quiz is not None else None)


async def cmd_poll(cli, args) -> None:
    """Send a poll (or a quiz) as Parham."""
    entity = await resolve(cli, args.chat)
    media = build_poll(args.question, args.option, args.multiple, args.public, args.quiz)
    topic = await target_topic(cli, entity, args)
    say(args, "about to send a poll")
    say(args, f"  chat    : {label(entity)} ({kind(entity)}, id={entity.id})")
    say(args, f"  question: {args.question}")
    for i, option in enumerate(args.option):
        say(args, f"  {i}. {option}{'  ✓' if args.quiz == i else ''}")
    plan = {"chat": entity_row(entity), "question": args.question, "options": args.option, "topic": topic}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — nothing sent. Re-run with --yes to actually deliver.")
        return
    stop_typing(entity.id)
    msg = await cli.send_file(entity, media, reply_to=send_reply_to(args.reply_to, topic["id"] if topic else None))
    result(args, {**plan, "dry_run": False, "message_id": msg.id}, f"poll sent: message_id={msg.id}")


# --- streaming and export ----------------------------------------------------


async def cmd_listen(cli, args) -> None:
    """Wait for new messages and print each as it arrives.

    The other commands look back; this looks forward, which is what an agent
    needs to wait for an answer it just asked for. With --json each message is
    one JSON object on its own line (JSON Lines), printed and flushed the moment
    it arrives. It stops after --count messages or --timeout seconds; under the
    MCP server it never waits longer than a minute unless told to, because a
    tool call cannot be interrupted.
    """
    chats = [await resolve(cli, ref) for ref in args.chat or []]
    for chat_id in sorted(tagged_ids(args.tag) or []):
        chats.append(await resolve(cli, str(chat_id)))
    me = await cli.get_me()
    timeout = args.timeout
    if timeout is None and args.count is None and IN_MCP:
        timeout = 60
    queue: asyncio.Queue = asyncio.Queue()

    async def on_message(event) -> None:
        await queue.put(event.message)

    builder = events.NewMessage(chats=chats or None, incoming=True if args.incoming else None)
    cli.add_event_handler(on_message, builder)
    say(
        args,
        f"listening on {', '.join(label(c) for c in chats) or 'every chat'}"
        + (f" for {timeout}s" if timeout else "")
        + (f", up to {args.count} message(s)" if args.count else ""),
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout if timeout else None
    seen = 0
    try:
        while args.count is None or seen < args.count:
            wait = None if deadline is None else deadline - loop.time()
            if wait is not None and wait <= 0:
                break
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=wait)
            except TimeoutError:
                break
            chat = await msg.get_chat()
            row = {**msg_row(msg, me.id), "chat": label(chat), "chat_id": getattr(chat, "id", None)}
            seen += 1
            if getattr(args, "json", False):
                print(json.dumps(row, ensure_ascii=False, default=str), flush=True)
            else:
                print(f"[{row['date']}] {row['chat']} — {row['sender']} (msg {row['id']})", flush=True)
                print(f"    {row['text']}", flush=True)
    finally:
        cli.remove_event_handler(on_message, builder)
    say(args, f"-- {seen} message(s)")


def export_markdown(entity, rows: list[dict]) -> str:
    lines = [f"# {label(entity)}", ""]
    for r in rows:
        lines.append(f"**{r['sender']}** — {r['date']} (msg {r['id']})")
        lines.append("")
        lines.append(r["text"])
        if r.get("file"):
            lines.append(f"[{r['file']}]({r['file']})")
        lines.append("")
    return "\n".join(lines)


async def cmd_export(cli, args) -> None:
    """Save a whole conversation to a file, oldest first.

    `history` is for reading a screenful; this is for keeping one: JSON Lines
    for a program (one message per line, the same fields as `history --json`)
    or Markdown for a person, with the media saved beside it on request.
    """
    entity = await resolve(cli, args.chat)
    me = await cli.get_me()
    since = parse_when(args.since, "--since")
    until = parse_when(args.until, "--until")
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    media_dir = Path(args.media).expanduser() if args.media else None
    if media_dir:
        media_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    async for msg in cli.iter_messages(entity, reverse=True, offset_date=since, limit=args.limit or None):
        if until and msg.date and msg.date.astimezone(UTC) > until:
            break
        row = msg_row(msg, me.id)
        if media_dir and msg.media:
            path = await cli.download_media(msg, file=str(media_dir))
            if path:
                row["file"] = str(path)
        rows.append(row)
        if len(rows) % 500 == 0:
            say(args, f"  {len(rows)} message(s)…")

    if args.format == "md":
        out.write_text(export_markdown(entity, rows) + "\n")
    else:
        out.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows))
    result(
        args,
        {"chat": entity_row(entity), "path": str(out), "format": args.format, "messages": len(rows)},
        f"exported {len(rows)} message(s) from {label(entity)} to {out}",
    )


# --- internal tags: labels Telegram does not have to know about -------------
#
# Telegram's own per-chat tags are folders shown as chips, and the chips need
# Premium. These are ELBoT's: a label like "client" or "family" on any chat,
# kept beside the alias book (so they travel with the private repo) and used to
# filter `catchup`, `chats`, `mentions`, `listen` and the watcher. They cost
# nothing, need no account feature, and nobody else can see them.

TAGS_FILE = SKILL_DIR / "tags.json"
_TAG_NAME = re.compile(r"[^\s,]{1,32}")


def load_tags() -> dict[str, dict[str, str]]:
    """{tag: {chat id: label at tagging time}}, tags lowercase."""
    raw = read_json(TAGS_FILE)
    return {str(k).lower(): {str(i): str(n) for i, n in (v or {}).items()} for k, v in raw.items()}


def save_tags(tags: dict) -> None:
    clean = {k: dict(sorted(v.items())) for k, v in sorted(tags.items()) if v}
    TAGS_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n")


def tag_name(raw: str) -> str:
    name = raw.strip().lower().lstrip("#")
    if not _TAG_NAME.fullmatch(name):
        die(f"a tag is one word of up to 32 characters, got {raw!r}")
    return name


def tagged_ids(names: list[str] | None, strict: bool = True) -> set[int] | None:
    """The chat ids carrying any of these tags; None when no tag was asked for.

    An unknown tag stops a command (a typo would otherwise filter everything
    out and look like "nothing unread"); the watcher passes strict=False, so
    one rule naming a tag nobody has used yet does not stop the others.
    """
    if not names:
        return None
    tags = load_tags()
    out: set[int] = set()
    for raw in names:
        name = tag_name(raw)
        if name not in tags:
            if strict:
                die(f"no tag called {name!r}. Run `tag` to list them.")
            print(f"topoli-user: warning: no chat is tagged #{name} yet", file=sys.stderr)
            continue
        out |= {int(i) for i in tags[name]}
    return out


def tags_of(chat_id: int) -> list[str]:
    return sorted(name for name, chats in load_tags().items() if str(chat_id) in chats)


async def cmd_tag(cli, args) -> None:
    """Label chats with ELBoT's own tags, list them, or list a tag's chats.

    No dry run: a tag lives only in tags.json and nobody else ever sees it.
    """
    tags = load_tags()

    if args.delete:
        name = tag_name(args.delete)
        if tags.pop(name, None) is None:
            die(f"no tag called {name!r}")
        save_tags(tags)
        result(args, {"deleted": name}, f"deleted tag {name!r}")
        return

    if args.chat:
        entities = [await resolve(cli, ref) for ref in args.chat]
        for name in map(tag_name, args.add or []):
            for e in entities:
                tags.setdefault(name, {})[str(e.id)] = label(e)
        for name in map(tag_name, args.remove or []):
            for e in entities:
                tags.get(name, {}).pop(str(e.id), None)
        if args.add or args.remove:
            save_tags(tags)
        rows = [{**entity_row(e), "tags": tags_of(e.id)} for e in entities]
        if emit(rows, args):
            return
        for r in rows:
            print(f"{r['id']:>16}  {r['title']}  " + (" ".join(f"#{t}" for t in r["tags"]) or "(no tags)"))
        return

    if args.name:
        name = tag_name(args.name)
        chats = [{"id": int(i), "title": n} for i, n in tags.get(name, {}).items()]
        if emit({"tag": name, "chats": chats}, args):
            return
        for c in chats:
            print(f"{c['id']:>16}  {c['title']}")
        print(f"\n-- {len(chats)} chat(s) tagged #{name}", file=sys.stderr)
        return

    listing = [{"tag": k, "chats": len(v)} for k, v in sorted(tags.items())]
    if emit(listing, args):
        return
    for row in listing:
        print(f"#{row['tag']:<20} {row['chats']} chat(s)")
    print(f"\n-- {len(listing)} tag(s) in {TAGS_FILE}", file=sys.stderr)


# --- typing: let the other side see that a reply is being written ----------

#: chat id -> the task that keeps "typing…" alive there. Only the MCP server
#: and the watcher live long enough to hold one.
_TYPING: dict[int, asyncio.Task] = {}

TYPING_ACTIONS = ("typing", "record-audio", "record-video", "photo", "document", "choose-sticker")


async def _keep_typing(cli, entity, action: str, seconds: float) -> None:
    try:
        async with cli.action(entity, action):
            await asyncio.sleep(seconds)
    finally:
        _TYPING.pop(entity.id, None)


def stop_typing(chat_id: int) -> bool:
    """Stop a background "typing…" in this chat; true if one was running."""
    task = _TYPING.pop(chat_id, None)
    if task is None:
        return False
    task.cancel()
    return True


async def cmd_typing(cli, args) -> None:
    """Show "typing…" (or another action) in a chat while a reply is composed.

    Under the MCP server it starts in the background and returns at once, so an
    agent can call it, read the thread and write its reply while the other side
    sees it typing; a `send`, `send-file`, `poll` or `draft` to the same chat
    stops it, and so does --stop or the time running out. From the shell it
    simply types for --seconds and returns. Telegram itself drops the
    indicator after about five seconds unless it is renewed, so nothing lingers
    if the process dies.
    """
    entity = await resolve(cli, args.chat)
    if args.stop:
        stopped = stop_typing(entity.id)
        if not stopped:
            await cli.action(entity, "cancel")
        result(args, {"chat": entity_row(entity), "typing": False}, f"stopped typing in {label(entity)}")
        return
    seconds = max(1, min(args.seconds, 300))
    if IN_MCP:
        stop_typing(entity.id)
        _TYPING[entity.id] = asyncio.create_task(_keep_typing(cli, entity, args.action, seconds))
        result(
            args,
            {"chat": entity_row(entity), "typing": True, "action": args.action, "seconds": seconds},
            f"{args.action} in {label(entity)} for up to {seconds}s",
        )
        return
    say(args, f"{args.action} in {label(entity)} for {seconds}s")
    await _keep_typing(cli, entity, args.action, seconds)
    result(args, {"chat": entity_row(entity), "typing": False, "seconds": seconds})


# --- scheduled messages and reminders ----------------------------------------


def parse_at(at: str | None, after: str | None) -> datetime:
    """--at an ISO time, or --in a duration from now; exactly one."""
    if bool(at) == bool(after):
        die("give exactly one of --at 2026-11-12T09:00 or --in 2h")
    if after:
        return datetime.now(UTC) + parse_duration(after)
    when = parse_when(at, "--at")
    assert when is not None
    if when <= datetime.now(UTC):
        die(f"--at {at} is in the past")
    return when


async def cmd_scheduled(cli, args) -> None:
    """List a chat's scheduled messages, cancel some, or send them now.

    `send --schedule` and `remind` hand delivery to Telegram; this is the
    other half — seeing what is queued and taking it back. Cancelling or
    sending early is a dry run until --yes, like every other write.
    """
    entity = await resolve(cli, args.chat)
    res = await cli(GetScheduledHistoryRequest(peer=entity, hash=0))
    msgs = sorted(getattr(res, "messages", []), key=lambda m: m.date)
    rows: list[dict] = [{"id": m.id, "date": ts(m.date), "text": body(m), "media": media_row(m)} for m in msgs]

    target = args.cancel or args.now
    if not target:
        if emit({**entity_row(entity), "scheduled": rows}, args):
            return
        for r in rows:
            print(f"{r['id']:>8}  {r['date']}  {r['text'][:160]}")
        print(f"\n-- {len(rows)} scheduled in {label(entity)}", file=sys.stderr)
        return

    known = {r["id"]: r for r in rows}
    missing = [i for i in target if i not in known]
    if missing:
        die(f"not scheduled in {label(entity)}: {', '.join(map(str, missing))}. Run `scheduled --chat …` to list them.")
    verb = "cancel" if args.cancel else "send now"
    say(args, f"about to {verb} in {label(entity)}:")
    for i in target:
        say(args, f"  - {i} [{known[i]['date']}] {known[i]['text'][:120]}")
    plan = {"chat": entity_row(entity), "action": verb, "messages": [known[i] for i in target]}
    if not args.yes:
        dry_run(args, plan, "DRY RUN — nothing changed. Re-run with --yes to apply.")
        return
    if args.cancel:
        await cli(DeleteScheduledMessagesRequest(peer=entity, id=target))
    else:
        await cli(SendScheduledMessagesRequest(peer=entity, id=target))
    result(args, {**plan, "dry_run": False}, f"{'cancelled' if args.cancel else 'sent'}: {len(target)} message(s)")


async def cmd_remind(cli, args) -> None:
    """A reminder: a message Telegram delivers to Saved Messages later.

    A scheduled message to yourself arrives as a "Reminder" with a
    notification on every device — Telegram's own reminder feature, with no
    process kept alive. To Saved Messages it needs no --yes (nobody else sees
    it); `--chat` someone else makes it an ordinary scheduled send, which does.
    """
    entity = await resolve(cli, args.chat)
    me = await cli.get_me()
    when = parse_at(args.at, getattr(args, "in_", None))
    text, entities = build_message(message_text(args), args.parse, args.quote, args.expandable)
    self_only = entity.id == me.id
    plan = {"chat": entity_row(entity), "when": ts(when), "text": text, "reminder": self_only}
    say(args, f"{'reminder' if self_only else 'scheduled send'} to {label(entity)} at {ts(when)}: {text}")
    if not self_only and not args.yes:
        dry_run(args, plan, "DRY RUN — it goes to someone else, so re-run with --yes to schedule it.")
        return
    msg = await cli.send_message(entity, text, formatting_entities=entities or None, parse_mode=None, schedule=when)
    result(
        args,
        {**plan, "dry_run": False, "message_id": msg.id},
        f"{'reminder' if self_only else 'scheduled'}: {ts(when)} (id {msg.id}; `scheduled` lists or cancels it)",
    )


# --- read receipts -----------------------------------------------------------


async def cmd_receipts(cli, args) -> None:
    """Who has read one of Parham's messages, and when.

    A private chat answers with the read date when the other side's privacy
    allows it (and whether it was read at all regardless). A group answers
    with every member who read it — Telegram keeps that only for groups of up
    to 100 members and for seven days after the message.
    """
    entity = await resolve(cli, args.chat)
    msgs = await cli.get_messages(entity, ids=[args.message])
    msg = msgs[0] if msgs else None
    if msg is None:
        die(f"message {args.message} not found in {label(entity)}")
    me = await cli.get_me()
    if msg.sender_id != me.id:
        die(f"message {args.message} is not yours — receipts exist only for what you sent")

    row: dict = {"chat": entity_row(entity), "message_id": args.message, "sent": ts(msg.date)}
    if isinstance(entity, User):
        peers = await cli(GetPeerDialogsRequest(peers=[InputDialogPeer(await cli.get_input_entity(entity))]))
        read_max = peers.dialogs[0].read_outbox_max_id if peers.dialogs else 0
        row["read"] = args.message <= read_max
        row["read_at"] = None
        if row["read"]:
            with contextlib.suppress(errors.RPCError):  # hidden by their privacy settings, or too old
                res = await cli(GetOutboxReadDateRequest(peer=entity, msg_id=args.message))
                row["read_at"] = ts(res.date)
        text = f"{'read' if row['read'] else 'not read yet'}" + (f" at {row['read_at']}" if row["read_at"] else "")
    else:
        try:
            res = await cli(GetMessageReadParticipantsRequest(peer=entity, msg_id=args.message))
        except errors.RPCError as exc:
            if "EXPIRED" in str(exc).upper() or "chat_read_mark_expire_period" in str(exc):
                die("read receipts in groups expire after 7 days, and this message is older")
            if "TOO_BIG" in str(exc).upper():
                die("Telegram keeps read receipts only in groups of up to 100 members")
            die(f"no read receipts for this message: {exc}")
        readers = []
        for p in res:
            uid = getattr(p, "user_id", p)
            name = str(uid)
            with contextlib.suppress(ValueError, errors.RPCError):
                name = label(await cli.get_entity(uid))
            readers.append({"id": uid, "name": name, "date": ts(getattr(p, "date", None))})
        row["readers"] = readers
        text = "\n".join(f"{r['date']}  {r['name']}" for r in readers) or "nobody has read it yet"
    result(args, row, text)


# --- the watcher: standing rules on incoming messages ------------------------
#
# `listen` prints what arrives; `watch` decides what to do about it, unattended,
# from rules written down in advance. A rule names what it matches (chats,
# tags, senders, words, a mention of Parham) and what to do: a desktop
# notification, a reaction, a line in a log, a forward, a command to run, or
# marking the chat read. Writing the rule is the authorisation for its
# actions — the same standing-instruction model as the morning standup — so a
# rule that reacts or forwards does it without asking, and `watch --check`
# shows exactly what each rule will do before it is left running.

WATCH_FILE = SKILL_DIR / "watch.json"
RULE_KEYS = {
    "name",
    "accounts",
    "chats",
    "tags",
    "from",
    "keywords",
    "regex",
    "mentions",
    "incoming",
    "private",
    "actions",
}
ACTION_KEYS = {"notify", "react", "log", "forward", "exec", "mark_read"}


def load_rules(path: Path | None = None) -> list[dict]:
    """The rules that apply to this account, validated; dies on a bad file."""
    path = path or WATCH_FILE
    if not path.is_file():
        die(f"no rules at {path}. Write one; see `watch --example`.")
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        die(f"{path} is not valid JSON: {exc}")
    rules = raw.get("rules", []) if isinstance(raw, dict) else raw
    out = []
    for i, rule in enumerate(rules):
        where = f"rule {i} ({rule.get('name', 'unnamed')})"
        unknown = set(rule) - RULE_KEYS
        if unknown:
            die(f"{where}: unknown keys {sorted(unknown)}; allowed: {sorted(RULE_KEYS)}")
        actions = rule.get("actions") or []
        if not actions:
            die(f"{where}: no actions")
        for action in actions:
            bad = set(action) - ACTION_KEYS
            if bad:
                die(f"{where}: unknown action {sorted(bad)}; allowed: {sorted(ACTION_KEYS)}")
        if rule.get("regex"):
            try:
                re.compile(rule["regex"])
            except re.error as exc:
                die(f"{where}: bad regex: {exc}")
        accounts = rule.get("accounts")
        if accounts and ACCOUNT not in accounts:
            continue
        out.append({**rule, "name": rule.get("name") or f"rule {i}"})
    return out


WATCH_EXAMPLE = {
    "rules": [
        {
            "name": "family",
            "accounts": ["parham"],
            "chats": ["wife"],
            "actions": [{"notify": True}],
        },
        {
            "name": "clients asking",
            "tags": ["client"],
            "keywords": ["urgent", "فوری", "asap"],
            "actions": [{"notify": True}, {"react": "👀"}, {"log": "~/.local/state/automated-topoli/clients.jsonl"}],
        },
        {
            "name": "mentioned in a group",
            "mentions": True,
            "actions": [{"notify": True}, {"forward": "me"}],
        },
        {
            "name": "iranian account to main",
            "accounts": ["topoli"],
            "private": True,
            "actions": [{"exec": "jq -r .text >> ~/iran-inbox.txt"}],
        },
    ]
}


class Watched:
    """What a rule is matched against: one message and where it came from."""

    def __init__(
        self, row: dict, chat_id: int, chat_title: str, private: bool, mentioned: bool, sender_names: list[str]
    ):
        self.row = row
        self.chat_id = chat_id
        self.chat_title = chat_title
        self.private = private
        self.mentioned = mentioned
        self.sender_names = [n.lower() for n in sender_names]


def rule_matches(rule: dict, w: Watched, chat_ids: set[int] | None, tag_ids: set[int] | None) -> bool:
    """Every condition the rule names must hold; within one, any value will do.

    `chat_ids` and `tag_ids` are the rule's chats and tags already resolved to
    ids (None when the rule names none), so this stays pure and testable.
    """
    if rule.get("incoming", True) and w.row.get("outgoing"):
        return False
    if chat_ids is not None and w.chat_id not in chat_ids:
        return False
    if tag_ids is not None and w.chat_id not in tag_ids:
        return False
    if "private" in rule and bool(rule["private"]) != w.private:
        return False
    if rule.get("mentions") and not w.mentioned:
        return False
    senders = [str(s).lower().lstrip("@") for s in rule.get("from") or []]
    if senders and not any(s in name for s in senders for name in w.sender_names):
        return False
    text = (w.row.get("text") or "").casefold()
    words = [str(k).casefold() for k in rule.get("keywords") or []]
    if words and not any(k in text for k in words):
        return False
    return not (rule.get("regex") and not re.search(rule["regex"], w.row.get("text") or "", re.IGNORECASE))


def desktop_notify(title: str, text: str) -> None:
    """A notification on this machine: osascript on macOS, notify-send on Linux."""
    title, text = title[:80], text[:240]
    if sys.platform == "darwin":
        script = f"display notification {json.dumps(text)} with title {json.dumps(title)}"
        cmd = ["osascript", "-e", script]
    else:
        cmd = ["notify-send", "--app-name=ELBoT", title, text]
    with contextlib.suppress(OSError):
        import subprocess

        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def run_actions(cli, rule: dict, msg, w: Watched, dry: bool) -> list[str]:
    """Carry out a matched rule's actions; return what was done, for the log."""
    done = []
    for action in rule["actions"]:
        for kind_, value in action.items():
            if not value:
                continue
            what = f"{kind_}={value}" if value is not True else kind_
            done.append(what)
            if dry:
                continue
            try:
                if kind_ == "notify":
                    desktop_notify(f"{w.row['sender']} — {w.chat_title}", w.row["text"])
                elif kind_ == "react":
                    await cli(
                        SendReactionRequest(peer=msg.peer_id, msg_id=msg.id, reaction=[ReactionEmoji(emoticon=value)])
                    )
                elif kind_ == "log":
                    path = Path(str(value)).expanduser()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a") as fh:
                        fh.write(
                            json.dumps(
                                {**w.row, "chat": w.chat_title, "chat_id": w.chat_id, "rule": rule["name"]},
                                ensure_ascii=False,
                                default=str,
                            )
                            + "\n"
                        )
                elif kind_ == "forward":
                    await cli.forward_messages(await resolve(cli, str(value)), msg.id, msg.peer_id)
                elif kind_ == "mark_read":
                    await cli.send_read_acknowledge(msg.peer_id, max_id=msg.id)
                elif kind_ == "exec":
                    payload = json.dumps(
                        {**w.row, "chat": w.chat_title, "chat_id": w.chat_id, "rule": rule["name"]},
                        ensure_ascii=False,
                        default=str,
                    )
                    proc = await asyncio.create_subprocess_shell(
                        str(value),
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        env={
                            **os.environ,
                            "ELBOT_RULE": rule["name"],
                            "ELBOT_CHAT": w.chat_title,
                            "ELBOT_TEXT": w.row["text"],
                        },
                    )
                    await proc.communicate(payload.encode())
            except (errors.RPCError, OSError, ValueError, SystemExit) as exc:
                print(f"  ! {rule['name']}: {kind_} failed: {exc}", file=sys.stderr)
    return done


async def cmd_watch(cli, args) -> None:
    """Run the standing rules in watch.json against every incoming message.

    `--check` validates the rules and resolves their chats and tags, without
    listening; `--dry` listens and logs what each rule *would* do; `--example`
    prints a starting rules file. It runs until stopped (or --timeout), so it
    belongs in a systemd or launchd unit, like the standup timer.
    """
    if args.example:
        print(json.dumps(WATCH_EXAMPLE, ensure_ascii=False, indent=2))
        return
    rules = load_rules(Path(args.rules).expanduser() if args.rules else None)

    resolved: list[tuple[dict, set[int] | None, set[int] | None]] = []
    for rule in rules:
        chat_ids = {(await resolve(cli, str(c))).id for c in rule["chats"]} if rule.get("chats") else None
        tag_ids = tagged_ids(rule.get("tags"), strict=False)
        resolved.append((rule, chat_ids, tag_ids))
        actions = ", ".join(k if v is True else f"{k}={v}" for a in rule["actions"] for k, v in a.items())
        scope = (
            "every chat"
            if chat_ids is None and tag_ids is None
            else f"{len((chat_ids or set()) | (tag_ids or set()))} chat(s)"
        )
        say(args, f"rule {rule['name']!r}: {scope} → {actions}")

    if args.check:
        result(
            args, {"account": ACCOUNT, "rules": [r["name"] for r in rules], "valid": True}, f"{len(rules)} rule(s) OK"
        )
        return

    me = await cli.get_me()
    queue: asyncio.Queue = asyncio.Queue()

    async def on_message(event) -> None:
        await queue.put(event.message)

    builder = events.NewMessage()
    cli.add_event_handler(on_message, builder)
    say(args, f"watching as {label(me)} with {len(rules)} rule(s){' (dry run)' if args.dry else ''}")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + args.timeout if args.timeout else None
    try:
        while True:
            wait = None if deadline is None else deadline - loop.time()
            if wait is not None and wait <= 0:
                break
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=wait)
            except TimeoutError:
                break
            chat = await msg.get_chat()
            sender = await msg.get_sender()
            row = msg_row(msg, me.id)
            names = [label(sender)] if sender else []
            if sender is not None and getattr(sender, "username", None):
                names.append(sender.username)
            w = Watched(
                row, chat.id, label(chat), isinstance(chat, User), bool(getattr(msg, "mentioned", False)), names
            )
            for rule, chat_ids, tag_ids in resolved:
                if rule_matches(rule, w, chat_ids, tag_ids):
                    done = await run_actions(cli, rule, msg, w, args.dry)
                    line = {
                        "rule": rule["name"],
                        "chat": w.chat_title,
                        "message_id": row["id"],
                        "did": done,
                        "dry": args.dry,
                    }
                    if getattr(args, "json", False):
                        print(json.dumps(line, ensure_ascii=False), flush=True)
                    else:
                        print(
                            f"[{row['date']}] {rule['name']}: {w.chat_title} — {row['sender']} → {', '.join(done)}",
                            flush=True,
                        )
    finally:
        cli.remove_event_handler(on_message, builder)


# --------------------------------------------------------------------------
# AI interface: the command line as tools, over MCP or as a JSON schema
# --------------------------------------------------------------------------
#
# Every subcommand is already a tool: a name, a help line, typed flags, a
# --json result and a --yes gate on anything that writes. So the tool list is
# *generated from the argument parser* rather than written a second time — a
# flag added to the CLI is a parameter the agent can use, with no second copy
# to drift. `mcp` serves that list over the Model Context Protocol (JSON-RPC on
# stdio, which Claude Code, Claude Desktop and most agent frameworks speak);
# `schema` prints the same list for anything that wants function definitions
# instead (OpenAI-style tools, a prompt, a test).

#: Commands that make no sense as a tool: login is interactive by nature, and
#: the server cannot serve itself.
NOT_TOOLS = {"setup", "login", "mcp", "schema", "accounts", "watch"}

MCP_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

MCP_INSTRUCTIONS = """\
These tools act on the user's own Telegram account: whatever they send appears as the user typing it.

- Every write (send, send_file, edit, delete, forward, react, draft, pin, mute, archive, join, leave, poll, \
profile, privacy, folder, chat_photo, create_group, sessions with terminate) is a DRY RUN unless `yes` is true. \
Call it without `yes` first, show the user the recipient and the exact text from the preview, and call again \
with `yes: true` only after they agree. Never compose and deliver in one step.
- When a reply is wanted but not yet approved, prefer `draft`: it puts the text in the chat's input box for \
the user to send themselves, and nobody else sees it.
- `chat` takes an alias, @username, t.me link, numeric id, "me" (Saved Messages) or a title. If a title \
matches several chats the call fails with the candidates — ask which one; never guess a recipient.
- Start with `catchup` (unread), `mentions` (unread @mentions) or `chats`; read a thread with `history`, a \
specific message with `get`, a voice note with `transcribe`. `listen` waits for new messages (bounded to 60s \
unless `timeout`/`count` is given).
- When you start working on a reply for a chat, call `typing` for it first: the other side sees "typing…" while \
you read and write, and it stops by itself when you send or draft there, or after `seconds`.
- `tag` holds the user's own labels on chats (client, family, …); `catchup`, `chats`, `mentions` and `listen` \
take `tag` to filter by them. Reminders: `remind` (Saved Messages, delivered by Telegram), listed and cancelled \
with `scheduled`. `receipts` says who read a message.
- Message text uses Telegram markdown: **bold**, __italic__, ~~strike~~, ||spoiler||, `code`, ```lang fences, \
[label](url), and lines starting with > as a quote. Single * and _ are literal. Pass parse="none" for logs.
"""


def subcommands(parser: argparse.ArgumentParser) -> dict[str, tuple[argparse.ArgumentParser, str]]:
    """name -> (subparser, its one-line help), straight from the parser."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {a.dest: a.help or "" for a in action._choices_actions}
            return {name: (sp, helps.get(name, "")) for name, sp in action.choices.items()}
    raise AssertionError("the parser has no subcommands")


def _option(action) -> str:
    return next((o for o in action.option_strings if o.startswith("--")), action.option_strings[0])


def param_schema(action) -> dict:
    """One argparse action as a JSON-schema property."""
    item: dict = {"type": "integer"} if action.type is int else {"type": "string"}
    schema: dict = {}
    if isinstance(action, argparse._StoreTrueAction):
        schema["type"] = "boolean"
    elif isinstance(action, argparse._AppendAction):
        schema |= {"type": "array", "items": item}
    elif action.nargs == 2:
        schema |= {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2}
    else:
        schema |= item
    if action.choices is not None and not isinstance(action, argparse._StoreTrueAction):
        choices = list(action.choices)
        # An append action's schema holds `item` itself, so the enum lands on
        # each element rather than on the array.
        (item if isinstance(action, argparse._AppendAction) else schema)["enum"] = choices
    help_text = (action.help or "").replace("%%", "%")
    if action.default not in (None, False, argparse.SUPPRESS) and not isinstance(action, argparse._StoreTrueAction):
        help_text = f"{help_text} (default: {action.default})".strip()
    if help_text:
        schema["description"] = help_text
    return schema


def tool_name(cmd: str) -> str:
    return cmd.replace("-", "_")


def tool_specs(parser: argparse.ArgumentParser | None = None) -> list[dict]:
    """MCP tool definitions for every subcommand worth exposing."""
    parser = parser or build_parser()
    tools = []
    for cmd, (sp, help_text) in subcommands(parser).items():
        if cmd in NOT_TOOLS:
            continue
        props, required = {}, []
        writes = False
        for action in sp._actions:
            if isinstance(action, argparse._HelpAction) or action.dest == "json" or not action.option_strings:
                continue
            props[action.dest] = param_schema(action)
            writes = writes or action.dest == "yes"
            if action.required:
                required.append(action.dest)
        desc = help_text[:1].upper() + help_text[1:] + "."
        if writes:
            desc += " Dry run unless yes=true: show the user the preview first, then repeat with yes=true."
        tools.append(
            {
                "name": tool_name(cmd),
                "description": desc,
                "inputSchema": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": not writes, "openWorldHint": True},
            }
        )
    return tools


def tool_argv(parser: argparse.ArgumentParser, name: str, arguments: dict) -> list[str]:
    """Turn a tool call back into the command line it stands for (+ --json)."""
    cmds = {tool_name(c): c for c in subcommands(parser) if c not in NOT_TOOLS}
    if name not in cmds:
        raise ValueError(f"unknown tool {name!r}")
    cmd = cmds[name]
    sp = subcommands(parser)[cmd][0]
    actions = {a.dest: a for a in sp._actions if a.option_strings and not isinstance(a, argparse._HelpAction)}
    argv = [cmd]
    for key, value in (arguments or {}).items():
        action = actions.get(key)
        if action is None:
            raise ValueError(f"{name} has no parameter {key!r}")
        if value is None:
            continue
        opt = _option(action)
        if isinstance(action, argparse._StoreTrueAction):
            if value:
                argv.append(opt)
        elif isinstance(action, argparse._AppendAction):
            for v in value if isinstance(value, list) else [value]:
                argv += [opt, str(v)]
        elif action.nargs == 2:
            argv += [opt, *map(str, value)]
        else:
            argv += [opt, str(value)]
    if "json" in {a.dest for a in sp._actions}:
        argv.append("--json")
    return argv


async def call_tool(cli, parser: argparse.ArgumentParser, name: str, arguments: dict) -> dict:
    """Run one tool call on the open client and wrap its output as MCP content.

    The command runs exactly as it would from a shell, with stdout and stderr
    captured: stdout is the JSON result, stderr the narration (the dry-run
    block, notes). `die` raises SystemExit, which here is a failed call rather
    than the end of the server.
    """
    global JSON_ERRORS
    out, err = io.StringIO(), io.StringIO()
    failed = False
    try:
        argv = tool_argv(parser, name, arguments)
    except ValueError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args = parser.parse_args(argv)
            JSON_ERRORS = bool(getattr(args, "json", False))
            await args.fn(cli, args)
        except SystemExit as exc:
            failed = exc.code not in (0, None)
        except errors.FloodWaitError as exc:
            failed = True
            print(f"rate limited by Telegram; retry in {exc.seconds}s", file=sys.stderr)
        except errors.RPCError as exc:
            failed = True
            print(f"Telegram error: {exc}", file=sys.stderr)
        finally:
            JSON_ERRORS = False
    content = []
    if out.getvalue().strip():
        content.append({"type": "text", "text": out.getvalue().strip()})
    if err.getvalue().strip():
        content.append({"type": "text", "text": err.getvalue().strip()})
    if not content:
        content.append({"type": "text", "text": "done"})
    return {"content": content, "isError": failed}


# --- MCP prompts and resources ------------------------------------------------
#
# Tools are verbs; prompts are the errands a person actually asks for ("what
# did I miss", "answer my wife"), written once with the order of tool calls and
# the rules that make them safe, so the client can offer them as slash
# commands. Resources are the nouns an agent reads without deciding to call a
# tool: the unread list, the mentions, the drafts, a chat's recent history.

#: name -> (title, description, [(argument, description, required)], template)
MCP_PROMPTS: dict[str, tuple[str, str, list[tuple[str, str, bool]], str]] = {
    "triage": (
        "Triage my Telegram",
        "Go through everything unread and the @mentions, sorted by what needs Parham.",
        [("tag", "only chats with this ELBoT tag, e.g. client", False)],
        "Triage my Telegram{tag_clause}. Call `catchup`{tag_arg} and `mentions`{tag_arg}. Group what you find into: "
        "needs a reply from me, FYI, and noise. For each chat that needs me, say who, what they want, since when, and "
        "propose a short reply in my voice (match the language and tone I use in that chat; read my earlier messages "
        "with `history` if unsure). Do not send or mark anything read. Offer to put the replies in as drafts with "
        "`draft` — they wait in the input box for me to send.",
    ),
    "reply": (
        "Reply to a chat",
        "Read a conversation, show typing, and draft a reply for approval.",
        [
            ("chat", "who to answer: alias, @username, id or title", True),
            ("intent", "what the reply should say", False),
        ],
        "Reply to {chat} on Telegram{intent_clause}. First call `typing` for {chat} so they see me typing while you "
        "work. Read the recent thread with `history` (limit 30) and anything it points to with `get`. Write the reply "
        "in my voice — same language, length and register as my earlier messages there, no assistant phrasing. Show "
        "it to me with the `send` dry run (no `yes`), and only send after I agree; if I am unsure, use `draft`.",
    ),
    "summarize": (
        "Summarize a chat",
        "What happened in a chat or group over a period.",
        [("chat", "alias, @username, id or title", True), ("since", "ISO date to start from, e.g. 2026-11-01", False)],
        "Summarize the Telegram chat {chat}{since_clause}. Use `history` with --since (raise the limit until the "
        "period is covered) and `transcribe` on voice notes that matter. Give: the decisions made, open questions, "
        "what was asked of me and whether I answered, and dates. Quote short lines in their original language.",
    ),
    "waiting": (
        "Who is waiting on me",
        "Private chats where the last message is theirs and unanswered.",
        [("tag", "only chats with this ELBoT tag", False)],
        "Find who is waiting on me on Telegram{tag_clause}. Call `chats` (limit 100){tag_arg} and, for each private "
        "chat whose last message is not mine, read the tail with `history`. List them oldest first with how long "
        "they have waited and what they asked. Use `receipts` on my last message where it matters. Send nothing.",
    ),
    "remind": (
        "Remind me",
        "Turn a request into a Telegram reminder in Saved Messages.",
        [("what", "what to be reminded of", True), ("when", "when, e.g. tomorrow 9:00 or in 2h", True)],
        "Set a Telegram reminder: {what} — {when}. Work out the exact local time (Europe/Madrid unless I say "
        "otherwise), then call `remind` with `at` (ISO) or `in` (30m, 2h, 1d). Confirm the time back to me; "
        "`scheduled` lists reminders and cancels them.",
    ),
}


def prompt_specs() -> list[dict]:
    return [
        {
            "name": name,
            "title": title,
            "description": desc,
            "arguments": [{"name": a, "description": d, "required": r} for a, d, r in params],
        }
        for name, (title, desc, params, _) in MCP_PROMPTS.items()
    ]


def render_prompt(name: str, arguments: dict) -> dict:
    """prompts/get: the template filled in, as one user message."""
    if name not in MCP_PROMPTS:
        raise ValueError(f"unknown prompt {name!r}")
    title, _, params, template = MCP_PROMPTS[name]
    for arg, _, required in params:
        if required and not (arguments or {}).get(arg):
            raise ValueError(f"prompt {name!r} needs {arg!r}")
    a = {k: str(v) for k, v in (arguments or {}).items() if v}
    fields = {
        "chat": a.get("chat", ""),
        "what": a.get("what", ""),
        "when": a.get("when", ""),
        "tag_clause": f" for chats tagged #{a['tag']}" if a.get("tag") else "",
        "tag_arg": f" with tag={a['tag']!r}" if a.get("tag") else "",
        "intent_clause": f"; the gist: {a['intent']}" if a.get("intent") else "",
        "since_clause": f" since {a['since']}" if a.get("since") else " over the last week",
    }
    return {
        "description": title,
        "messages": [{"role": "user", "content": {"type": "text", "text": template.format(**fields)}}],
    }


#: uri -> (name, description, tool, arguments)
MCP_RESOURCES: dict[str, tuple[str, str, str, dict]] = {
    "telegram://unread": ("Unread", "Everything unread, per chat (catchup)", "catchup", {}),
    "telegram://mentions": ("Mentions", "Unread @mentions and replies to me", "mentions", {}),
    "telegram://drafts": ("Drafts", "Replies waiting in input boxes", "draft", {}),
    "telegram://tags": ("Tags", "ELBoT's chat tags", "tag", {}),
    "telegram://aliases": ("Aliases", "Named chats", "alias", {}),
    "telegram://reminders": ("Reminders", "Scheduled messages in Saved Messages", "scheduled", {"chat": "me"}),
    "telegram://me": ("Account", "Who this server acts as, and its device name", "whoami", {}),
}

#: uriTemplate -> (name, description, tool, argument builder)
MCP_RESOURCE_TEMPLATES: dict[str, tuple[str, str, str, Callable[[dict], dict]]] = {
    "telegram://chat/{chat}": (
        "Chat history",
        "The last 50 messages of a chat",
        "history",
        lambda v: {"chat": v["chat"]},
    ),
    "telegram://chat/{chat}/info": ("Chat info", "Bio, members, last seen", "info", lambda v: {"chat": v["chat"]}),
    "telegram://tag/{tag}/unread": (
        "Unread by tag",
        "Unread in chats with a tag",
        "catchup",
        lambda v: {"tag": [v["tag"]]},
    ),
}


def resource_call(uri: str) -> tuple[str, dict]:
    """The tool call a resource URI stands for."""
    if uri in MCP_RESOURCES:
        _, _, tool, arguments = MCP_RESOURCES[uri]
        return tool, dict(arguments)
    for template, (_, _, tool, build) in MCP_RESOURCE_TEMPLATES.items():
        pattern = "^" + re.sub(r"\\{(\w+)\\}", r"(?P<\1>[^/]+)", re.escape(template)) + "$"
        m = re.match(pattern, uri)
        if m:
            values = {k: unquote(v) for k, v in m.groupdict().items()}
            return tool, build(values)
    raise ValueError(f"unknown resource {uri!r}")


async def read_resource(cli, parser, uri: str) -> dict:
    tool, arguments = resource_call(uri)
    res = await call_tool(cli, parser, tool, arguments)
    if res["isError"]:
        raise ValueError(" ".join(c["text"] for c in res["content"]))
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": res["content"][0]["text"]}]}


async def mcp_handle(cli, parser, specs: list[dict], msg: dict) -> dict | None:
    """One JSON-RPC message in, the response out (None for a notification)."""
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if rid is None:
        return None  # notifications/initialized, cancelled, …: nothing to answer
    if method == "initialize":
        asked = params.get("protocolVersion")
        res: dict = {
            "protocolVersion": asked if asked in MCP_PROTOCOLS else MCP_PROTOCOLS[0],
            "capabilities": {
                "tools": {"listChanged": False},
                "prompts": {"listChanged": False},
                "resources": {"listChanged": False, "subscribe": False},
            },
            "serverInfo": {"name": "elbot", "title": "ELBoT — Telegram secretary", "version": VERSION},
            "instructions": MCP_INSTRUCTIONS,
        }
    elif method == "ping":
        res = {}
    elif method == "tools/list":
        res = {"tools": specs}
    elif method == "tools/call":
        res = await call_tool(cli, parser, params.get("name", ""), params.get("arguments") or {})
    elif method in ("prompts/list", "resources/list", "resources/templates/list", "prompts/get", "resources/read"):
        try:
            if method == "prompts/list":
                res = {"prompts": prompt_specs()}
            elif method == "prompts/get":
                res = render_prompt(params.get("name", ""), params.get("arguments") or {})
            elif method == "resources/list":
                res = {
                    "resources": [
                        {"uri": uri, "name": name, "description": desc, "mimeType": "application/json"}
                        for uri, (name, desc, _, _) in MCP_RESOURCES.items()
                    ]
                }
            elif method == "resources/templates/list":
                res = {
                    "resourceTemplates": [
                        {"uriTemplate": uri, "name": name, "description": desc, "mimeType": "application/json"}
                        for uri, (name, desc, _, _) in MCP_RESOURCE_TEMPLATES.items()
                    ]
                }
            else:
                res = await read_resource(cli, parser, params.get("uri", ""))
        except ValueError as exc:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": str(exc)}}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": res}


async def cmd_mcp(cli, args) -> None:
    """Serve every command as an MCP tool on stdin/stdout.

    One connection stays open for the whole session, so a tool call costs one
    round trip to Telegram instead of a fresh login handshake each time. Calls
    run one at a time, in order — the commands share the client and the
    title cache, and Telegram's flood limits punish bursts anyway.

        claude mcp add elbot -- uv run /path/to/topoli_user.py mcp
    """
    global IN_MCP
    IN_MCP = True
    wire = sys.stdout  # captured before any call redirects stdout
    parser = build_parser()
    specs = tool_specs(parser)
    loop = asyncio.get_running_loop()
    me = await cli.get_me()
    print(
        f"elbot mcp [{ACCOUNT}]: serving {len(specs)} tools as {label(me)} ({device_identity()['device_model']})",
        file=sys.stderr,
    )
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            reply: dict | None = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        else:
            reply = await mcp_handle(cli, parser, specs, msg)
        if reply is not None:
            wire.write(json.dumps(reply, ensure_ascii=False, default=str) + "\n")
            wire.flush()


async def cmd_schema(cli, args) -> None:  # never reaches the network
    """Print the tool definitions, for an agent framework that is not MCP."""
    specs = tool_specs()
    if args.format == "openai":
        specs = [
            {
                "type": "function",
                "function": {"name": t["name"], "description": t["description"], "parameters": t["inputSchema"]},
            }
            for t in specs
        ]
    elif args.format == "anthropic":
        specs = [{"name": t["name"], "description": t["description"], "input_schema": t["inputSchema"]} for t in specs]
    print(json.dumps(specs, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------


def add_json(sp):
    sp.add_argument("--json", action="store_true", help="machine-readable output instead of the text format")
    return sp


#: Shared so `send` and `send-file` cannot drift into describing it differently.
TOPIC_HELP = 'forum topic to post into: its id, its title, or "general". Run `topics` to list them'
CHAT_HELP = 'alias, @username, t.me link, numeric id, "me" (Saved Messages), or chat title'


def add_text(sp, required: bool = True) -> None:
    """--text or --text-file, plus the markup flags every message-writing command shares."""
    g = sp.add_mutually_exclusive_group(required=required)
    g.add_argument("--text", help="the message text")
    g.add_argument("--text-file", help="read the text from this file (- for stdin); safest for quotes and $")
    sp.add_argument(
        "--parse",
        default="markdown",
        choices=PARSE_CHOICES,
        help="text markup: markdown (default; ** __ ~~ || ` ```lang [t](url) and > lines), html, or none",
    )
    sp.add_argument("--quote", action="store_true", help="wrap the whole message in a blockquote")
    sp.add_argument("--expandable", action="store_true", help="collapsed tap-to-expand blockquote (implies --quote)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="topoli_user.py", description=__doc__.splitlines()[0])
    p.add_argument(
        "--account",
        default=os.environ.get("TOPOLI_ACCOUNT"),
        help='which login to act as: <name>.authkey beside the script (default: "account" in api.json)',
    )
    p.add_argument("--version", action="version", version=f"elbot {VERSION}")
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

    sp = add_json(sub.add_parser("chats", help="list conversations with their MTProto ids"))
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--unread", action="store_true", help="only chats with unread messages")
    sp.add_argument("--tag", action="append", help="only chats with this ELBoT tag; repeatable")
    sp.set_defaults(fn=cmd_chats)

    sp = add_json(sub.add_parser("contacts", help="the saved address book — far faster than scanning chats"))
    sp.add_argument("--query", help="filter by name or username")
    sp.set_defaults(fn=cmd_contacts)

    sp = sub.add_parser("delete-contact", help="remove people from the saved address book; their chats stay")
    sp.add_argument("--user", action="append", required=True, help="@username, id, alias or name; repeat for several")
    sp.add_argument("--yes", action="store_true", help="actually delete (otherwise dry run)")
    sp.set_defaults(fn=cmd_delete_contact)

    sp = add_json(sub.add_parser("catchup", help="everything unread, exactly as much as is unread"))
    sp.add_argument("--limit", type=int, default=100, help="how many chats to scan")
    sp.add_argument("--max-per-chat", type=int, default=20, help="cap the messages shown per chat")
    sp.add_argument("--read", action="store_true", help="mark them read afterwards (the other side sees this)")
    sp.add_argument("--tag", action="append", help="only chats with this ELBoT tag; repeatable")
    sp.set_defaults(fn=cmd_catchup)

    sp = add_json(sub.add_parser("alias", help="name a chat once, address it by that name forever"))
    sp.add_argument("--set", nargs=2, metavar=("NAME", "CHAT"), help="e.g. --set wife @someone")
    sp.add_argument("--remove", metavar="NAME")
    sp.add_argument("--local", action="store_true", help="store in the state dir instead of beside the script")
    sp.set_defaults(fn=cmd_alias)

    sp = add_json(sub.add_parser("history", help="read a chat backwards — the thing bots cannot do"))
    sp.add_argument("--chat", required=True, help="id, @username, alias, title, or t.me link")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--since", help="stop at this ISO date, e.g. 2026-08-01")
    sp.add_argument("--until", help="start from this ISO date, reading backwards")
    sp.add_argument("--from-user", help="only messages from this person (useful in groups)")
    sp.set_defaults(fn=cmd_history)

    sp = add_json(sub.add_parser("search", help="full-text search messages"))
    sp.add_argument("--query", required=True)
    sp.add_argument("--chat", help="restrict to one chat (default: everywhere)")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--since", help="stop at this ISO date")
    sp.add_argument("--until", help="start from this ISO date, reading backwards")
    sp.set_defaults(fn=cmd_search)

    sp = sub.add_parser("download", help="save the file behind a [document]/[photo] placeholder")
    sp.add_argument("--chat", required=True)
    sp.add_argument("--message", action="append", required=True, type=int, help="message id; repeat for several")
    sp.add_argument("--out", help="target directory (default: the working directory)")
    sp.set_defaults(fn=cmd_download)

    sp = sub.add_parser("chat-photo", help="read, replace or clear a group's photo")
    sp.add_argument("--chat", required=True)
    sp.add_argument("--out", help="save the current photo into this directory")
    sp.add_argument("--set", metavar="FILE", help="replace the photo with this image")
    sp.add_argument("--remove", action="store_true", help="clear the photo entirely")
    sp.add_argument("--yes", action="store_true", help="apply; without it this is a dry run")
    sp.set_defaults(fn=cmd_chat_photo)

    sp = add_json(sub.add_parser("topics", help="list a forum group's topics"))
    sp.add_argument("--chat", required=True, help="id, @username, alias, title, or t.me link")
    sp.set_defaults(fn=cmd_topics)

    sp = sub.add_parser("send", help="send a message as yourself")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    add_text(sp)
    sp.add_argument("--reply-to", type=int, help="message id to reply to")
    sp.add_argument("--topic", help=TOPIC_HELP)
    sp.add_argument("--schedule", help="deliver later; ISO time like 2026-09-01T09:00")
    sp.add_argument("--silent", action="store_true", help="deliver without a notification sound")
    sp.add_argument("--no-preview", action="store_true", help="no link preview under the message")
    sp.add_argument("--yes", action="store_true", help="actually send (otherwise dry run)")
    sp.set_defaults(fn=cmd_send)

    sp = sub.add_parser("edit", help="rewrite one of your own messages in place")
    sp.add_argument("--chat", required=True)
    sp.add_argument("--message", required=True, type=int, help="message id, from `history`")
    add_text(sp)
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

    sp = sub.add_parser("read", help="mark chats as read (or --unread to bring the badge back)")
    sp.add_argument("--chat", action="append", required=True, help="repeat for several")
    sp.add_argument("--unread", action="store_true", help="mark as unread instead; only you see this")
    sp.set_defaults(fn=cmd_read)

    sp = add_json(sub.add_parser("folders", help="list chat folders — Telegram's per-chat tags"))
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
    sp.add_argument("--reply-to", type=int, help="message id to reply to")
    sp.add_argument("--topic", help=TOPIC_HELP)
    sp.add_argument("--silent", action="store_true", help="deliver without a notification sound")
    sp.add_argument("--yes", action="store_true", help="actually send (otherwise dry run)")
    sp.set_defaults(fn=cmd_send_file)

    # --- the session ------------------------------------------------------
    sp = sub.add_parser("accounts", help="the accounts this tool can act as, one per *.authkey")
    sp.set_defaults(fn=cmd_accounts, offline=True)

    sp = sub.add_parser("device", help="show or rename how this session appears in Telegram's device list")
    sp.add_argument("--name", help='the device name others see, e.g. "Claude" or "ELBoT AI"')
    sp.add_argument("--system", help='the system line under it, e.g. "AI secretary"')
    sp.add_argument("--app", help='the app line, e.g. "ELBoT 0.3"')
    sp.add_argument("--reset", action="store_true", help="forget the stored identity and use the default")
    sp.add_argument("--local", action="store_true", help="store on this machine only, not beside the script")
    sp.add_argument("--this-account", action="store_true", help="store in api-<account>.json, for this account only")
    sp.set_defaults(fn=cmd_device, offline=True)

    sp = sub.add_parser("sessions", help="every device logged in to the account; log one out")
    sp.add_argument("--terminate", type=int, metavar="HASH", help="log out the session with this hash")
    sp.add_argument("--yes", action="store_true", help="actually log it out (otherwise dry run)")
    sp.set_defaults(fn=cmd_sessions)

    # --- reading more precisely --------------------------------------------
    sp = sub.add_parser("get", help="fetch messages by id, with --context around them")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.add_argument("--message", action="append", required=True, type=int, help="message id; repeat for several")
    sp.add_argument("--context", type=int, default=0, help="also show this many messages either side")
    sp.set_defaults(fn=cmd_get)

    sp = sub.add_parser("mentions", help="unread @mentions and replies to you, across every chat")
    sp.add_argument("--limit", type=int, default=200, help="how many chats to scan")
    sp.add_argument("--max-per-chat", type=int, default=20)
    sp.add_argument("--tag", action="append", help="only chats with this ELBoT tag; repeatable")
    sp.set_defaults(fn=cmd_mentions)

    sp = sub.add_parser("transcribe", help="voice or video note to text (Telegram Premium)")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.add_argument("--message", required=True, type=int, help="the voice message's id")
    sp.add_argument("--timeout", type=int, default=30, help="seconds to wait for a pending transcription")
    sp.set_defaults(fn=cmd_transcribe)

    sp = sub.add_parser("members", help="who is in a group, with roles and last seen")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.add_argument("--query", help="filter by name")
    sp.add_argument("--admins", action="store_true", help="admins only")
    sp.add_argument("--limit", type=int, default=200)
    sp.set_defaults(fn=cmd_members)

    sp = sub.add_parser("info", help="bio, phone, last seen, members — everything about one chat or person")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.set_defaults(fn=cmd_info)

    sp = sub.add_parser("listen", help="wait for new messages and print each as it arrives")
    sp.add_argument("--chat", action="append", help="only this chat; repeatable (default: all)")
    sp.add_argument("--tag", action="append", help="only chats with this ELBoT tag; repeatable")
    sp.add_argument("--incoming", action="store_true", help="ignore messages you send yourself")
    sp.add_argument("--count", type=int, help="stop after this many messages")
    sp.add_argument("--timeout", type=int, help="stop after this many seconds")
    sp.set_defaults(fn=cmd_listen, cache="listen")

    sp = sub.add_parser("export", help="save a whole conversation to JSON Lines or Markdown")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.add_argument("--out", required=True, help="the file to write")
    sp.add_argument("--format", choices=("jsonl", "md"), default="jsonl")
    sp.add_argument("--since", help="start at this ISO date")
    sp.add_argument("--until", help="stop at this ISO date")
    sp.add_argument("--limit", type=int, default=0, help="at most this many messages (0: all)")
    sp.add_argument("--media", metavar="DIR", help="also download every file into DIR")
    sp.set_defaults(fn=cmd_export)

    # --- writing that only Parham sees, and housekeeping --------------------
    sp = sub.add_parser("draft", help="put a reply in a chat's input box for you to send; no --chat lists drafts")
    sp.add_argument("--chat", help=CHAT_HELP)
    add_text(sp, required=False)
    sp.add_argument("--reply-to", type=int, help="make the draft a reply to this message")
    sp.add_argument("--clear", action="store_true", help="empty the draft instead")
    sp.add_argument("--yes", action="store_true", help="actually write it (otherwise dry run)")
    sp.set_defaults(fn=cmd_draft)

    sp = sub.add_parser("pin", help="pin or unpin a message")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.add_argument("--message", required=True, type=int)
    sp.add_argument("--unpin", action="store_true")
    sp.add_argument("--silent", action="store_true", help="pin without notifying members")
    sp.add_argument("--yes", action="store_true", help="actually apply (otherwise dry run)")
    sp.set_defaults(fn=cmd_pin)

    sp = sub.add_parser("mute", help="mute or unmute a chat's notifications")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.add_argument("--for", dest="for_", metavar="DURATION", help="30m, 8h, 1d, 2w, or forever (default)")
    sp.add_argument("--off", action="store_true", help="unmute")
    sp.add_argument("--yes", action="store_true", help="actually apply (otherwise dry run)")
    sp.set_defaults(fn=cmd_mute)

    sp = sub.add_parser("archive", help="move chats into the Archive, or out with --unarchive")
    sp.add_argument("--chat", action="append", required=True, help="repeat for several")
    sp.add_argument("--unarchive", action="store_true")
    sp.add_argument("--yes", action="store_true", help="actually apply (otherwise dry run)")
    sp.set_defaults(fn=cmd_archive)

    sp = sub.add_parser("join", help="join a group or channel by @username or invite link")
    sp.add_argument("--link", required=True, help="@name, t.me/name, or t.me/+invite")
    sp.add_argument("--yes", action="store_true", help="actually join (otherwise dry run)")
    sp.set_defaults(fn=cmd_join)

    sp = sub.add_parser("leave", help="leave a group or channel")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.add_argument("--yes", action="store_true", help="actually leave (otherwise dry run)")
    sp.set_defaults(fn=cmd_leave)

    sp = sub.add_parser("invite", help="add people to a group or channel")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.add_argument("--user", action="append", required=True, help="the person to add, any chat reference; repeatable")
    sp.add_argument("--yes", action="store_true", help="actually add them (otherwise dry run)")
    sp.set_defaults(fn=cmd_invite)

    sp = sub.add_parser("profile", help="show your profile, or change name, bio, username, birthday or photo")
    sp.add_argument("--first", help="first name")
    sp.add_argument("--last", help="last name")
    sp.add_argument("--bio", help="the about line")
    sp.add_argument("--username", help="the public @username")
    sp.add_argument("--birthday", help="DD-MM or DD-MM-YYYY; none clears it")
    sp.add_argument("--photo", metavar="FILE", help="set a new profile photo (an image, or an .mp4 for a video)")
    sp.add_argument("--remove-photo", action="store_true", help="delete the current photo; the previous one returns")
    sp.add_argument("--photo-out", metavar="DIR", help="save the current photo into this directory first")
    sp.add_argument("--yes", action="store_true", help="actually apply (otherwise dry run)")
    sp.set_defaults(fn=cmd_profile)

    sp = sub.add_parser("privacy", help="show or change privacy: who sees your phone, last seen, photo, and more")
    sp.add_argument("--key", choices=list(PRIVACY_KEYS), help="the setting to change")
    sp.add_argument("--rule", choices=list(PRIVACY_BASE), help="who may: everybody, contacts or nobody")
    sp.add_argument("--allow", action="append", help="an exception who may anyway: a person or a group; repeatable")
    sp.add_argument("--deny", action="append", help="an exception who may not: a person or a group; repeatable")
    sp.add_argument("--away-days", type=int, help="delete the account after this many days away (30-730)")
    sp.add_argument("--archive-strangers", choices=["on", "off"], help="archive and mute new chats from non-contacts")
    sp.add_argument("--yes", action="store_true", help="actually apply (otherwise dry run)")
    sp.set_defaults(fn=cmd_privacy)

    sp = sub.add_parser("poll", help="send a poll or a quiz")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.add_argument("--question", required=True)
    sp.add_argument("--option", action="append", required=True, help="an answer; repeat 2-12 times")
    sp.add_argument("--multiple", action="store_true", help="allow several answers")
    sp.add_argument("--public", action="store_true", help="show who voted for what")
    sp.add_argument("--quiz", type=int, metavar="INDEX", help="make it a quiz; INDEX of the right answer, from 0")
    sp.add_argument("--reply-to", type=int)
    sp.add_argument("--topic", help=TOPIC_HELP)
    sp.add_argument("--yes", action="store_true", help="actually send (otherwise dry run)")
    sp.set_defaults(fn=cmd_poll)

    # --- tags, typing, scheduling, receipts, the watcher --------------------
    sp = sub.add_parser("tag", help="ELBoT's own chat tags: label chats, list tags, list a tag's chats")
    sp.add_argument("--chat", action="append", help=CHAT_HELP + "; repeatable")
    sp.add_argument("--add", action="append", help="tag to put on --chat; repeatable")
    sp.add_argument("--remove", action="append", help="tag to take off --chat; repeatable")
    sp.add_argument("--name", help="list the chats carrying this tag")
    sp.add_argument("--delete", metavar="TAG", help="drop a tag from every chat")
    sp.set_defaults(fn=cmd_tag)

    sp = sub.add_parser("typing", help='show "typing…" in a chat while a reply is written')
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.add_argument("--seconds", type=int, default=30, help="how long, at most (1-300); a send to the chat stops it")
    sp.add_argument("--action", choices=TYPING_ACTIONS, default="typing", help="what the other side sees")
    sp.add_argument("--stop", action="store_true", help="stop it now")
    sp.set_defaults(fn=cmd_typing)

    sp = sub.add_parser("scheduled", help="list a chat's scheduled messages; cancel or send them now")
    sp.add_argument("--chat", default="me", help=CHAT_HELP + ' (default: "me", where reminders live)')
    g = sp.add_mutually_exclusive_group()
    g.add_argument(
        "--cancel", action="append", type=int, metavar="ID", help="cancel this scheduled message; repeatable"
    )
    g.add_argument("--now", action="append", type=int, metavar="ID", help="send this one now; repeatable")
    sp.add_argument("--yes", action="store_true", help="actually apply (otherwise dry run)")
    sp.set_defaults(fn=cmd_scheduled)

    sp = sub.add_parser("remind", help="a reminder in Saved Messages at a time; Telegram delivers it")
    sp.add_argument("--chat", default="me", help='who gets it (default: "me" — a reminder)')
    add_text(sp)
    sp.add_argument("--at", help="ISO time, e.g. 2026-11-12T09:00 (local)")
    sp.add_argument("--in", dest="in_", metavar="DURATION", help="from now: 30m, 2h, 1d")
    sp.add_argument("--yes", action="store_true", help="needed only when --chat is not you")
    sp.set_defaults(fn=cmd_remind)

    sp = sub.add_parser("receipts", help="who has read one of your messages, and when")
    sp.add_argument("--chat", required=True, help=CHAT_HELP)
    sp.add_argument("--message", required=True, type=int, help="the id of a message you sent")
    sp.set_defaults(fn=cmd_receipts)

    sp = sub.add_parser("watch", help="run the standing rules in watch.json on every incoming message")
    sp.add_argument("--rules", help="rules file (default: watch.json beside the script)")
    sp.add_argument("--check", action="store_true", help="validate the rules and exit")
    sp.add_argument("--dry", action="store_true", help="match and log, but do no action")
    sp.add_argument("--example", action="store_true", help="print an example rules file")
    sp.add_argument("--timeout", type=int, help="stop after this many seconds")
    sp.set_defaults(fn=cmd_watch, cache="watch")

    # --- the AI interface ---------------------------------------------------
    sp = sub.add_parser("mcp", help="serve every command as an MCP tool on stdio (for Claude and other agents)")
    sp.set_defaults(fn=cmd_mcp, cache="mcp")

    sp = sub.add_parser("schema", help="print the tool definitions as JSON")
    sp.add_argument("--format", choices=("mcp", "openai", "anthropic"), default="mcp")
    sp.set_defaults(fn=cmd_schema, offline=True)

    # --json on everything, writes included: a read prints its rows, a write
    # its plan (dry run) or its outcome, and the narration moves to stderr.
    for name, (child, _) in subcommands(p).items():
        if name not in ("mcp", "schema", "setup") and not any(a.dest == "json" for a in child._actions):
            add_json(child)

    return p


async def run(args) -> None:
    global JSON_ERRORS
    JSON_ERRORS = bool(getattr(args, "json", False))
    # `device` only talks to the network when it is asked to show the live
    # session; renaming is a config write.
    if getattr(args, "offline", False):
        await args.fn(None, args)
        return

    cli = client(getattr(args, "cache", None))
    # Deliberately connect() rather than `async with cli`: the context manager
    # calls start(), which prompts for a phone number on stdin. In a
    # non-interactive run that surfaces as an EOFError traceback instead of the
    # one-line "you are not logged in" that actually tells you what to do.
    await cli.connect()
    try:
        secure_session()
        if not await cli.is_user_authorized() and args.fn is not cmd_login:
            die(f"account {ACCOUNT!r} is not logged in yet. Run: {invocation()} login --phone <number>")
        await args.fn(cli, args)
    finally:
        await cli.disconnect()


def main() -> None:
    args = build_parser().parse_args()
    set_account(args.account)
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
