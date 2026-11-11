# ELBoT

A personal Telegram secretary that logs in as the account itself over MTProto (via [Telethon](https://codeberg.org/Lonami/Telethon)) — so it can read any chat backwards, search the whole archive, see every group and channel, send as the user with full markdown/HTML/quote formatting, upload files, react, mark chats read, and manage folders.

It is built to be driven by an AI agent as much as by a person: every command is also an [MCP](https://modelcontextprotocol.io) tool (`mcp`), every command answers in JSON (`--json`), every write is a dry run until it is confirmed, and the session announces itself to Telegram under a name you choose, so it shows up in Settings → Devices as what it is — the AI secretary — rather than as a stray laptop.

## Getting started

`topoli_user.py` is one file that declares its dependencies inline (PEP 723), so [uv](https://docs.astral.sh/uv/) runs it with no venv to manage. It needs Python 3.14, which uv fetches on its own.

1. Create an app at <https://my.telegram.org> → API development tools, and store its id and hash:

    ```bash
    uv run topoli_user.py setup --api-id 123456 --api-hash 0123456789abcdef0123456789abcdef --phone +34XXXXXXXXX
    ```

2. Log in. Telegram sends the code to your other devices (or by SMS/email); the code and the 2FA password are two separate steps, so it works without a terminal prompt:

    ```bash
    uv run topoli_user.py login                       # sends the code
    uv run topoli_user.py login --code 12345          # add --password … if the account has 2FA
    uv run topoli_user.py whoami
    ```

3. Where Telegram is blocked, point it at a proxy: `TOPOLI_PROXY=socks5://127.0.0.1:1080` (or `http://…`). Telethon ignores the usual proxy variables, so ELBoT reads `TOPOLI_PROXY` first and falls back to `all_proxy`, `https_proxy` and `http_proxy`.

The login leaves `topoli.authkey` beside the script — the whole account in one line; see [Credentials](#credentials--never-in-this-repo). Caches go to `~/.local/state/automated-topoli/` (`TOPOLI_STATE_DIR` moves them).

## Running

```bash
uv run topoli_user.py catchup                                   # everything unread
uv run topoli_user.py contacts --query elaheh                   # the address book, in one RPC
uv run topoli_user.py alias --set wife @someone                 # name a chat once
uv run topoli_user.py history --chat wife --since 2026-08-01
uv run topoli_user.py search --query "invoice" --since 2026-01-01
uv run topoli_user.py send --chat wife --text "**hello**"
uv run topoli_user.py send --chat "Some Group" --quote --text "$(cat note.txt)"
```

### Commands at a glance

| Area | Commands |
| --- | --- |
| Reading | `catchup`, `mentions`, `chats`, `contacts`, `history`, `get`, `search`, `topics`, `members`, `info`, `receipts`, `transcribe`, `download`, `export`, `listen` |
| Writing as you (dry run until `--yes`) | `send`, `send-file`, `poll`, `edit`, `delete`, `forward`, `react`, `pin`, `scheduled` |
| Writing only you see | `draft`, `remind`, `tag`, `read` (`--unread`), `mute`, `archive`, `folder`, `folders`, `tags`, `alias` |
| Presence and automation | `typing`, `watch` |
| Groups and profile | `create-group`, `chat-photo`, `join`, `leave`, `invite`, `profile` |
| The session | `accounts`, `whoami`, `device`, `sessions`, `setup`, `login` |
| AI interface | `mcp`, `schema` |

### For AI agents: MCP, JSON and dry runs

`mcp` serves every command as a tool over the Model Context Protocol on stdio. The tool list is generated from the argument parser, so each flag is a typed parameter and nothing is described twice:

```bash
claude mcp add elbot -- uv run /path/to/topoli_user.py mcp                       # Claude Code
claude mcp add elbot-work -- uv run /path/to/topoli_user.py --account work mcp   # a second account
```

One connection stays open for the whole session, so a tool call is one round trip to Telegram rather than a fresh handshake. The server hands the agent its rules in the MCP `instructions`: call a write without `yes` first, show the person the recipient and the text, and only then call it again with `yes: true` — or, when a reply has not been approved, put it in the chat's input box with `draft` for the person to send themselves.

`schema` prints the same tool list for frameworks that are not MCP: `--format openai` for OpenAI-style function definitions, `--format anthropic` for the Messages API `tools` array.

Every command takes `--json`. A read prints its rows; a write prints its plan (`"dry_run": true`) or its outcome (the new `message_id`), and the human narration moves to stderr so stdout is exactly one JSON document. Errors under `--json` are `{"error": "…"}`. Messages in JSON carry `reply_to`, `topic_id`, `forwarded`, `edited` and a `media` object (type, file name, MIME type, size, duration), so an agent can tell a 12-second voice note worth `transcribe` from a 40 MB PDF without downloading either. `--text-file` (or `--text-file -` for stdin) passes a message without shell quoting, which matters for text full of backticks, `$` and Persian.

`listen` looks forward instead of back: it prints each new message as it arrives (one JSON object per line under `--json`) and stops after `--count` messages or `--timeout` seconds — the way for an agent to wait for the answer to a question it just asked. Under `mcp` it never waits longer than a minute unless told to.

**Prompts and resources.** Besides the tools, the server offers MCP *prompts* — `triage` (everything unread and the mentions, sorted by what needs you, with replies proposed as drafts), `reply` (read a thread, show typing, draft an answer in your voice for approval), `summarize`, `waiting` (who is waiting on you) and `remind` — which clients show as slash commands. Its *resources* are what an agent can read without calling a tool: `telegram://unread`, `telegram://mentions`, `telegram://drafts`, `telegram://tags`, `telegram://aliases`, `telegram://reminders`, `telegram://me`, and the templates `telegram://chat/{chat}`, `telegram://chat/{chat}/info` and `telegram://tag/{tag}/unread`.

**Typing while it thinks.** `typing --chat X` shows "typing…" in a chat. Under `mcp` it runs in the background and returns at once, so an agent calls it when it starts on a reply and the other side sees it typing while the agent reads the thread and writes; a `send`, `send-file`, `poll` or `draft` to that chat stops it, as do `--stop` and `--seconds` (30 by default). `--action` shows recording a voice note, choosing a sticker and so on instead.

### Tags of your own

Telegram's chat tags are folders shown as chips, and the chips need Premium. `tag` gives every chat labels of your own instead — free, private, kept in `tags.json` beside the script:

```bash
uv run topoli_user.py tag --chat @acme --chat "Beta Ltd" --add client
uv run topoli_user.py tag                      # every tag and how many chats carry it
uv run topoli_user.py tag --name client        # the chats tagged #client
uv run topoli_user.py catchup --tag client     # also: chats, mentions, listen
```

### Reminders, scheduled messages and read receipts

`remind --in 2h --text "call the bank"` (or `--at 2026-11-12T09:00`) schedules a message to Saved Messages, which Telegram delivers as a reminder with a notification on every device — no process has to stay up. `scheduled` lists what is queued in a chat (Saved Messages by default), and `--cancel ID` or `--now ID` take it back or send it early. `receipts --chat X --message N` says whether one of your messages was read: in a private chat, read or not and when (if their privacy shows it); in a group of up to 100, who read it and when, for 7 days.

### The watcher

`watch` runs standing rules from `watch.json` beside the script against every incoming message, for as long as it runs. A rule matches on any of `chats`, `tags`, `from`, `keywords`, `regex`, `mentions`, `private` and `accounts` (all that are named must hold) and does any of `notify` (a desktop notification), `react`, `log` (a JSON line), `forward`, `mark_read` and `exec` (a shell command with the message as JSON on stdin):

```bash
uv run topoli_user.py watch --example > watch.json   # a starting point
uv run topoli_user.py watch --check                  # validate, resolve chats and tags, show what each rule does
uv run topoli_user.py watch --dry --timeout 600      # match and print, act on nothing
uv run topoli_user.py --account work watch           # for real; run it under systemd or launchd
```

Writing a rule is the authorisation for its actions, so a rule that reacts or forwards does it unattended — read `--check` before leaving it running. To keep it running, give it a service of its own, one per account, for example a systemd user unit:

```ini
[Service]
ExecStart=%h/.local/bin/uv run --quiet /path/to/topoli_user.py --account work watch
Restart=on-failure
RestartSec=30
```

or a launchd agent with the same command and `KeepAlive` on macOS.

### The device name

Every connection tells Telegram a device model, a system version and an app version, and Telegram shows them for the session under Settings → Devices, which is also where the account owner terminates a session they do not recognise. ELBoT announces itself as `ELBoT`; name it whatever should be recognisable:

```bash
uv run topoli_user.py device --name "Claude" --system "AI secretary"   # stored beside the script
uv run topoli_user.py sessions                                         # every logged-in device; * is this one
uv run topoli_user.py sessions --terminate <hash> --yes                # log a device out
```

The name goes out with the next connection, which is when Telegram records it, so `sessions` on the next run shows it. `TOPOLI_DEVICE_MODEL`, `TOPOLI_SYSTEM_VERSION` and `TOPOLI_APP_VERSION` override the stored values.

### Several accounts

`--account NAME` (or `TOPOLI_ACCOUNT=NAME`) picks which login to act as. Each account is `<name>.authkey` beside the script, with its own cache, title index and pending login in the state dir; `accounts` lists them. The one used without `--account` is `"account"` in `api.json`, or `topoli` — the layout from before accounts existed — when it names none.

```bash
uv run topoli_user.py --account work login --phone +34XXXXXXXXX
uv run topoli_user.py --account work login --code 12345 [--password <2fa>]
uv run topoli_user.py --account work catchup
```

An account can carry files of its own next to the shared ones: `api-<name>.json` is merged over `api.json` (its phone, or a device name of its own with `device --this-account`), and `aliases-<name>.json` and `tags-<name>.json` *replace* the shared books for that account — the way to keep a support account that talks to clients from ever resolving a personal alias. `TOPOLI_SESSION_STRING` applies to the default account only, so an exported key can never log another account in as the wrong person.

Long-running processes (`mcp`, `listen`) keep a cache of their own and lock it, so they never collide with a one-off command or with each other on "database is locked"; a second server on the same account falls back to a private cache that is deleted when it exits.

### Finding the right chat

A chat resolves, in this order, by **alias**, then `@username`, a `t.me/…` link, a numeric id, the shortcut `me` (your Saved Messages), or its **title**. Titles come from a cache that `chats` and `contacts` refresh, so repeat lookups skip the live dialog scan.

Prefer `contacts` over `chats` for finding a person: `contacts.GetContacts` returns the whole address book in one round trip, where `chats` pages every conversation the account has ever had — seconds against minutes. Both seed the same cache.

An alias exists for the case the others cannot settle: "wife" is not a Telegram concept, and two contacts can share a display name, so a title lookup would stop and ask every time. `alias --set wife @someone` resolves the reference once and stores the numeric **id** — a display name can be shared and a username can be given up and re-registered by somebody else, but the id is the account. The book lives in `aliases.json` beside the script, with a machine-local override in the state dir.

### Reading

`catchup` is the "what did I miss" command: Telegram already tracks how many messages are unread per chat, so it reads exactly that many rather than making you guess a `--limit`. Nothing is marked read unless you pass `--read` — seeing a message and acknowledging it are different acts, and the second is visible to the other side.

`history` and `search` take a date window (`--since` / `--until`, any ISO date), and `history` additionally takes `--from-user` to follow one person through a group. `history` prints media as `[document]` / `[photo]` placeholders; `download --chat X --message N` pulls the real file onto disk.

A group's own photo is not a message, so `download` cannot reach it — `chat-photo` does. With only `--chat` it reports whether there is one and `--out DIR` saves it; `--set FILE` replaces it and `--remove` clears it, both dry runs until `--yes`. Save the current one before replacing it: everyone in the chat sees the change and Telegram offers no undo. Note a basic group and a supergroup take different RPCs behind this (`messages.EditChatPhoto` against a `Chat`, `channels.EditPhoto` against a `Channel`), which the tool picks for you — sending the wrong one fails as a type error that reads like a broken tool rather than a wrong call.

`mentions` reads only the unread messages that name you or reply to you, across every chat — in a busy group that is the one message out of two hundred that matters. `get --chat X --message N --context 3` reads exactly where a reply or a search hit points, with the messages around it. `info` shows a person's bio, phone, last-seen and the number of groups you share, or a group's description and member count; `members` lists a group with roles. `transcribe` turns a voice or video note into text with Telegram's own recogniser (Premium). `export` saves a whole conversation to JSON Lines or Markdown, with `--media DIR` to keep the files.

### Writing

`send`, `edit` and the `send-file` caption honour `--parse {markdown,html,none}`. Markdown is Telegram Desktop's dialect: `**bold**`, `__italic__`, `~~strike~~`, `||spoiler||`, `` `code` ``, a ```` ```lang ```` fence for a highlighted block, `[label](url)`, and `>`-prefixed lines as a blockquote. HTML adds `<u>` underline, `<tg-spoiler>` and `<blockquote expandable>`. Single `*` and `_` are *not* markup, so `__init__` and `a || b` outside backticks are eaten — use `--parse none` for text full of them. `--quote` / `--expandable` wrap the whole message in a blockquote in any mode. `--schedule 2026-09-01T09:00` hands delivery to Telegram rather than keeping a process alive.

`draft` writes a reply into a chat's input box on every device you are logged in to, for you to read and send yourself; nobody else sees it, and `draft` with no `--chat` lists the drafts waiting. `send` also takes `--silent` (no notification sound) and `--no-preview`; `poll` sends a poll or, with `--quiz N`, a quiz. `pin`, `mute --for 8h`, `archive`, `join` and `leave` cover the rest of a chat's housekeeping, `invite --chat G --user @someone` adds people to a group (it needs the right to add members, and names the admins to ask when it is missing), and `profile` shows or changes your name, bio and username.

Every write is a **dry run** until you add `--yes`; it prints the resolved recipient and the exact text first. `edit`, `delete` and `forward` follow the same rule — `delete` removes for everyone by default, and `--only-me` clears just your own view.

## Credentials — never in this repo

This repository is public and holds **code only**. Every credential lives outside it and is loaded at runtime:

- `<account>.authkey` (or `TOPOLI_SESSION_STRING` for the default account) — the Telethon session. This is a full-account bearer credential: whoever holds it is logged in as the user, with no 2FA re-prompt. Revoke it from Telegram → Settings → Devices, or with `sessions --terminate`.
- `api.json`, `api-<account>.json` / `TOPOLI_API_ID` + `TOPOLI_API_HASH` — the app credentials from <https://my.telegram.org>, phones and device names.
- `aliases*.json`, `tags*.json`, `watch.json` — not secrets, but they name the real people you talk to.

`.gitignore` blocks all of them, and the `*.session` caches, so none can be committed by accident. Keep them in a private repository beside the script: copy the code in from here (`git ls-files` lists exactly what to copy), never the other way.

## Development

```bash
uv sync                    # create the environment from pyproject
uv run ruff check .
uv run ruff format --check .
uv run ty check .
uv run pytest
```

`ruff` lints and checks formatting, `ty` type-checks, and `pytest` needs no network: it covers the code whose bugs would be silent or would reach the wrong person — message formatting (UTF-16 entity offsets, which fail silently across emoji and Persian), chat resolution and the alias book, account selection and key isolation, the MCP tool schema and the JSON-RPC round trip, prompts and resources, the watcher's rule matching, tags, durations and polls. All of it runs in CI on every push and pull request. The project targets Python 3.14, so it leans on what that release added — annotations are lazy by default (PEP 649), and `except` takes an unparenthesised list of exception types (PEP 758).

Telethon's development moved off GitHub in February 2026: <https://github.com/LonamiWebs/Telethon> is archived and read-only, and the live repository is now <https://codeberg.org/Lonami/Telethon>. The PyPI package name is unchanged, so nothing here depends on the move beyond the link.
