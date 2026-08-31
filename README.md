# elbot

A personal Telegram secretary that logs in as the account itself over MTProto (via [Telethon](https://codeberg.org/Lonami/Telethon)) — so it can read any chat backwards, search the whole archive, see every group and channel, send as the user with full markdown/HTML/quote formatting, upload files, react, mark chats read, and manage folders.

## Running

`topoli_user.py` declares its dependencies inline (PEP 723), so [uv](https://docs.astral.sh/uv/) runs it with no venv to manage:

```bash
uv run topoli_user.py catchup                                   # everything unread
uv run topoli_user.py contacts --query elaheh                   # the address book, in one RPC
uv run topoli_user.py alias --set wife @someone                 # name a chat once
uv run topoli_user.py history --chat wife --since 2026-08-01
uv run topoli_user.py search --query "invoice" --since 2026-01-01
uv run topoli_user.py send --chat wife --text "**hello**"
uv run topoli_user.py send --chat "Some Group" --quote --text "$(cat note.txt)"
```

### Finding the right chat

A chat resolves, in this order, by **alias**, then `@username`, a `t.me/…` link, a numeric id, the shortcut `me` (your Saved Messages), or its **title**. Titles come from a cache that `chats` and `contacts` refresh, so repeat lookups skip the live dialog scan.

Prefer `contacts` over `chats` for finding a person: `contacts.GetContacts` returns the whole address book in one round trip, where `chats` pages every conversation the account has ever had — seconds against minutes. Both seed the same cache.

An alias exists for the case the others cannot settle: "wife" is not a Telegram concept, and two contacts can share a display name, so a title lookup would stop and ask every time. `alias --set wife @someone` resolves the reference once and stores the numeric **id** — a display name can be shared and a username can be given up and re-registered by somebody else, but the id is the account. The book lives in `aliases.json` beside the script, with a machine-local override in the state dir.

### Reading

`catchup` is the "what did I miss" command: Telegram already tracks how many messages are unread per chat, so it reads exactly that many rather than making you guess a `--limit`. Nothing is marked read unless you pass `--read` — seeing a message and acknowledging it are different acts, and the second is visible to the other side.

`history` and `search` take a date window (`--since` / `--until`, any ISO date), and `history` additionally takes `--from-user` to follow one person through a group. `history` prints media as `[document]` / `[photo]` placeholders; `download --chat X --message N` pulls the real file onto disk.

Every read command takes `--json` for machine-readable output.

### Writing

`send` (and the `send-file` caption) honour `--parse {markdown,html,none}`, and `--quote` / `--expandable` wrap a message in a real Telegram blockquote — a leading `>` is chat-app shorthand, not Telegram markup, and would send literally. `--schedule 2026-09-01T09:00` hands delivery to Telegram rather than keeping a process alive.

Every write is a **dry run** until you add `--yes`; it prints the resolved recipient and the exact text first. `edit`, `delete` and `forward` follow the same rule — `delete` removes for everyone by default, and `--only-me` clears just your own view.

## Credentials — never in this repo

This repository is public and holds **code only**. Every credential lives outside it and is loaded at runtime:

- `TOPOLI_SESSION_STRING` (or a local `*.authkey`) — the Telethon session. This is a full-account bearer credential: whoever holds it is logged in as the user, with no 2FA re-prompt.
- `api.json` / `TOPOLI_API_ID` + `TOPOLI_API_HASH` — the app credentials from <https://my.telegram.org>.

`.gitignore` blocks `api.json`, `token`, `*.authkey`, and `*.session` so none of them can be committed by accident. Keep them in a private store.

## Development

```bash
uv sync            # create the environment from pyproject
uv run ruff check .
uv run ty check .
uv run pytest
```

`ruff` lints, `ty` type-checks, and `pytest` covers the pure helpers — message formatting (UTF-16 entity offsets, which fail silently across emoji and Persian), chat resolution, the alias book, date parsing and proxy handling. All three run in CI on every push and pull request. The project targets Python 3.14, so it leans on what that release added — annotations are lazy by default (PEP 649), and `except` takes an unparenthesised list of exception types (PEP 758).

Telethon's development moved off GitHub in February 2026: <https://github.com/LonamiWebs/Telethon> is archived and read-only, and the live repository is now <https://codeberg.org/Lonami/Telethon>. The PyPI package name is unchanged, so nothing here depends on the move beyond the link.
