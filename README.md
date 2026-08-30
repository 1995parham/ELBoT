# elbot

A personal Telegram secretary in two small Python tools that share nothing but a phone number: a **bot-mode** client for live, revocable notification work, and a **user-mode** MTProto client that can read history and act as the account itself.

## The two tools

`topoli.py` drives a Telegram **Business chatbot** over the Bot API. It can poll for new messages, print an inbox, list chats, send messages and files, and react — but, like every bot, it cannot read a chat backwards and only ever sees a message that arrived while it was listening. Its rights are narrow and the owner can revoke them in the Telegram app at any moment, which is exactly what makes it safe for automated notification work. It is stdlib-only and runs anywhere `python3` does.

`topoli_user.py` logs in as the **account itself** over MTProto (via [Telethon](https://github.com/LonamiWebs/Telethon)). That removes every Bot-API limit: it reads any chat backwards, searches the whole archive, sees every group and channel, sends as the user with full markdown/HTML/quote formatting, uploads files, reacts, marks chats read, and manages folders. Reach for it whenever the task is about the past or a chat a bot cannot see.

## Running

Both tools declare their dependencies inline (PEP 723), so [uv](https://docs.astral.sh/uv/) runs them with no venv to manage:

```bash
uv run topoli_user.py chats
uv run topoli_user.py send --chat me --text "**hello**"
uv run topoli_user.py send --chat "Some Group" --quote --text "$(cat note.txt)"
python3 topoli.py poll --timeout 60
```

`send` and the `send-file` caption honour `--parse {markdown,html,none}`, and `--quote` / `--expandable` wrap a message in a real Telegram blockquote — a leading `>` is chat-app shorthand, not Telegram markup, and would send literally.

## Credentials — never in this repo

This repository is public and holds **code only**. Every credential lives outside it and is loaded at runtime:

- `TOPOLI_BOT_TOKEN` (or a local `token` file) — the bot token for `topoli.py`.
- `TOPOLI_SESSION_STRING` (or a local `*.authkey`) — the Telethon session for `topoli_user.py`. This is a full-account bearer credential: whoever holds it is logged in as the user, with no 2FA re-prompt.
- `api.json` / `TOPOLI_API_ID` + `TOPOLI_API_HASH` — the app credentials from <https://my.telegram.org>.

`.gitignore` blocks `api.json`, `token`, `*.authkey`, and `*.session` so none of them can be committed by accident. Keep them in a private store.

## Development

```bash
uv sync            # create the environment from pyproject
uv run ruff check .
uv run ty check .
```

`ruff` lints and `ty` type-checks; both run in CI on every push and pull request. The project targets Python 3.14.
