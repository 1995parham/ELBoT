"""The argument parser.

Cheap insurance: every subcommand is wired to a handler, the write commands all
default to a dry run, and the read commands all offer --json. A typo in
build_parser otherwise only shows up when someone runs that one command.
"""

import pytest

import topoli_user as t

WRITES = ["send", "send-file", "edit", "delete", "forward", "react", "create-group", "folder"]
READS = ["chats", "contacts", "catchup", "alias", "history", "search", "folders", "topics"]

MINIMAL = {
    "setup": ["--api-id", "1", "--api-hash", "0" * 32],
    "history": ["--chat", "me"],
    "topics": ["--chat", "me"],
    "search": ["--query", "x"],
    "send": ["--chat", "me", "--text", "hi"],
    "send-file": ["--chat", "me", "--file", "a.txt"],
    "edit": ["--chat", "me", "--message", "1", "--text", "hi"],
    "delete": ["--chat", "me", "--message", "1"],
    "forward": ["--chat", "me", "--to", "me", "--message", "1"],
    "download": ["--chat", "me", "--message", "1"],
    "react": ["--chat", "me", "--message", "1"],
    "read": ["--chat", "me"],
    "folder": ["--name", "x"],
    "create-group": ["--title", "x"],
}


def parse(argv):
    return t.build_parser().parse_args(argv)


def all_commands() -> list[str]:
    """Every subcommand the parser accepts, straight from the parser itself."""
    for action in t.build_parser()._actions:
        choices = getattr(action, "choices", None)
        if choices and "send" in choices:
            return sorted(choices)
    raise AssertionError("build_parser grew no subcommands")


@pytest.mark.parametrize("cmd", all_commands())
def test_every_command_has_a_handler(cmd):
    args = parse([cmd, *MINIMAL.get(cmd, [])])
    assert callable(args.fn)


@pytest.mark.parametrize("cmd", WRITES)
def test_write_commands_are_dry_by_default(cmd):
    assert parse([cmd, *MINIMAL.get(cmd, [])]).yes is False


@pytest.mark.parametrize("cmd", WRITES)
def test_write_commands_accept_yes(cmd):
    assert parse([cmd, *MINIMAL.get(cmd, []), "--yes"]).yes is True


@pytest.mark.parametrize("cmd", READS)
def test_read_commands_offer_json(cmd):
    assert parse([cmd, *MINIMAL.get(cmd, []), "--json"]).json is True


def test_read_has_no_dry_run_because_there_is_no_un_read():
    assert not hasattr(parse(["read", "--chat", "me"]), "yes")


def test_message_ids_accumulate():
    args = parse(["delete", "--chat", "me", "--message", "1", "--message", "2"])
    assert args.message == [1, 2]


def test_setup_never_reaches_the_network():
    assert parse(["setup", *MINIMAL["setup"]]).offline is True


def test_history_takes_a_date_window_and_a_sender():
    args = parse(["history", "--chat", "me", "--since", "2026-08-01", "--until", "2026-08-31", "--from-user", "@x"])
    assert (args.since, args.until, args.from_user) == ("2026-08-01", "2026-08-31", "@x")


def test_send_can_be_scheduled_and_can_reply():
    args = parse(["send", "--chat", "me", "--text", "hi", "--schedule", "2026-09-01T09:00", "--reply-to", "5"])
    assert args.schedule == "2026-09-01T09:00"
    assert args.reply_to == 5


def test_send_file_can_reply_too():
    assert parse([*["send-file"], *MINIMAL["send-file"], "--reply-to", "9"]).reply_to == 9
