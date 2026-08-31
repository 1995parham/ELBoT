# elbot

A personal Telegram secretary that logs in as the account itself over MTProto (via [Telethon](https://codeberg.org/Lonami/Telethon)) — so it can read any chat backwards, search the whole archive, see every group and channel, send as the user with full markdown/HTML/quote formatting, upload files, react, mark chats read, and manage folders.

## Running

`topoli_user.py` declares its dependencies inline (PEP 723), so [uv](https://docs.astral.sh/uv/) runs it with no venv to manage:

```bash
uv run topoli_user.py chats
uv run topoli_user.py history --chat @someone --limit 50
uv run topoli_user.py search --query "invoice"
uv run topoli_user.py send --chat me --text "**hello**"
uv run topoli_user.py send --chat "Some Group" --quote --text "$(cat note.txt)"
```

A chat resolves by `@username`, a `t.me/…` link, a numeric id, the shortcut `me` (your Saved Messages), or its title — titles resolve from a cache that `chats` refreshes, so repeat lookups skip the live dialog scan.

`send` (and the `send-file` caption) honour `--parse {markdown,html,none}`, and `--quote` / `--expandable` wrap a message in a real Telegram blockquote — a leading `>` is chat-app shorthand, not Telegram markup, and would send literally.

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
```

`ruff` lints and `ty` type-checks; both run in CI on every push and pull request. The project targets Python 3.14.

Telethon's development moved off GitHub in February 2026: <https://github.com/LonamiWebs/Telethon> is archived and read-only, and the live repository is now <https://codeberg.org/Lonami/Telethon>. The PyPI package name is unchanged, so nothing here depends on the move beyond the link.
