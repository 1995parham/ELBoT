"""`read --until` and `read --topic`: acknowledge what was handled, nothing after.

Marking a whole chat read after answering one message also swallows whatever
arrived while the answer was being written, and in a forum it clears every
topic, not the one that was read. These pin which request goes out, with which
bound, and that nothing past the bound is touched — all against a fake client.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.tl.functions.channels import ReadMessageContentsRequest as ChannelReadContentsRequest
from telethon.tl.functions.messages import (
    GetPeerDialogsRequest,
    GetUnreadMentionsRequest,
    MarkDialogUnreadRequest,
    ReadDiscussionRequest,
    ReadMessageContentsRequest,
)
from telethon.tl.types import Channel, ChatPhotoEmpty, User

import topoli_user as t


def forum() -> Channel:
    return Channel(
        id=7,
        title="Pod venture",
        photo=ChatPhotoEmpty(),
        date=datetime(2026, 9, 20, tzinfo=UTC),
        megagroup=True,
        forum=True,
    )


def person() -> User:
    return User(id=42, first_name="Alireza")


def topic(id_: int, title: str, last: int, unread: int = 0) -> dict:
    return {
        "id": id_,
        "title": title,
        "unread": unread,
        "closed": False,
        "pinned": False,
        "general": id_ == t.GENERAL_TOPIC_ID,
        "last": last,
    }


class FakeClient:
    """Records every request; answers the three reads `read` makes."""

    def __init__(self, mentions=(), unread_left: int = 0):
        self.calls: list = []
        self.acks: list[dict] = []
        self.mentions = list(mentions)
        self.unread_left = unread_left

    async def get_input_entity(self, entity):
        return entity

    async def send_read_acknowledge(self, entity, message=None, *, max_id=None, clear_mentions=False, **kw):
        self.acks.append({"entity": entity, "max_id": max_id, "clear_mentions": clear_mentions})

    async def __call__(self, request):
        self.calls.append(request)
        if isinstance(request, GetUnreadMentionsRequest):
            below = [m for m in self.mentions if not request.max_id or m < request.max_id]
            return SimpleNamespace(messages=[SimpleNamespace(id=m) for m in below])
        if isinstance(request, GetPeerDialogsRequest):
            return SimpleNamespace(dialogs=[SimpleNamespace(unread_count=self.unread_left)])
        return True

    def sent(self, cls) -> list:
        return [c for c in self.calls if isinstance(c, cls)]


@pytest.fixture
def chats(monkeypatch):
    """`resolve` and `fetch_topics` without Telegram: a forum, a person, Saved Messages."""
    known = {"pod": forum(), "alireza": person()}
    topics = [topic(1, "General", last=500), topic(12, "Bayat Group", last=900, unread=3), topic(34, "Platform", 700)]

    async def resolve(cli, ref):
        return known[ref]

    async def fetch_topics(cli, entity):
        return topics

    monkeypatch.setattr(t, "resolve", resolve)
    monkeypatch.setattr(t, "fetch_topics", fetch_topics)
    return topics


def args(*chat: str, until=None, topic=None, unread=False, json_=False):
    return argparse.Namespace(chat=list(chat), until=until, topic=topic, unread=unread, json=json_)


def run(cli, a):
    asyncio.run(t.cmd_read(cli, a))


class TestScope:
    def test_plain_read_has_nothing_to_refuse(self):
        assert t.read_scope_error(["a", "b"], False, None, None) is None

    def test_until_needs_exactly_one_chat(self):
        assert "exactly one --chat" in (t.read_scope_error(["a", "b"], False, 5, None) or "")

    def test_topic_needs_exactly_one_chat(self):
        assert "exactly one --chat" in (t.read_scope_error(["a", "b"], False, None, "deploy") or "")

    def test_unread_with_until_is_refused(self):
        assert "whole dialog" in (t.read_scope_error(["a"], True, 5, None) or "")

    def test_unread_with_topic_is_refused(self):
        assert "whole dialog" in (t.read_scope_error(["a"], True, None, "deploy") or "")

    def test_until_must_be_a_message_id(self):
        assert "message id" in (t.read_scope_error(["a"], False, 0, None) or "")

    def test_refusals_exit_before_any_request(self, chats):
        cli = FakeClient()
        with pytest.raises(SystemExit):
            run(cli, args("pod", "alireza", until=10))
        with pytest.raises(SystemExit):
            run(cli, args("pod", until=10, unread=True))
        assert cli.calls == [] and cli.acks == []


class TestUntil:
    def test_reads_history_only_up_to_the_message(self, chats):
        cli = FakeClient(unread_left=2)
        run(cli, args("alireza", until=120))
        (ack,) = cli.acks
        assert (ack["entity"].id, ack["max_id"], ack["clear_mentions"]) == (42, 120, False)

    def test_never_clears_every_mention(self, chats):
        """readMentions has no bound: a mention after --until would be lost."""
        cli = FakeClient(mentions=[100, 130])
        run(cli, args("pod", until=120))
        assert cli.acks[0]["clear_mentions"] is False
        assert [c.id for c in cli.sent(ChannelReadContentsRequest)] == [[100]]

    def test_a_private_chat_clears_mentions_through_messages(self, chats):
        cli = FakeClient(mentions=[110])
        run(cli, args("alireza", until=120))
        assert [c.id for c in cli.sent(ReadMessageContentsRequest)] == [[110]]

    def test_no_pending_mentions_sends_nothing_extra(self, chats):
        cli = FakeClient()
        run(cli, args("pod", until=120))
        assert not cli.sent(ChannelReadContentsRequest)

    def test_reports_what_is_still_unread(self, chats, capsys):
        run(FakeClient(unread_left=4), args("alireza", until=120))
        assert "4 still unread" in capsys.readouterr().out

    def test_json_carries_the_bound_and_the_rest(self, chats, capsys):
        run(FakeClient(mentions=[100], unread_left=4), args("alireza", until=120, json_=True))
        out = json.loads(capsys.readouterr().out)
        assert (out["until"], out["unread_left"], out["mentions_cleared"], out["topic"]) == (120, 4, 1, None)
        assert out["chats"][0]["id"] == 42


class TestTopic:
    def test_reads_the_one_thread_to_its_latest(self, chats):
        cli = FakeClient()
        run(cli, args("pod", topic="Bayat Group"))
        (req,) = cli.sent(ReadDiscussionRequest)
        assert (req.msg_id, req.read_max_id) == (12, 900)
        assert cli.acks == [], "the whole forum must not be read"

    def test_until_bounds_the_thread(self, chats):
        cli = FakeClient()
        run(cli, args("pod", topic="12", until=850))
        (req,) = cli.sent(ReadDiscussionRequest)
        assert (req.msg_id, req.read_max_id) == (12, 850)

    def test_mentions_are_looked_up_in_that_topic_only(self, chats):
        cli = FakeClient(mentions=[800])
        run(cli, args("pod", topic="Bayat", until=850))
        (lookup,) = cli.sent(GetUnreadMentionsRequest)
        assert lookup.top_msg_id == 12
        assert [c.id for c in cli.sent(ChannelReadContentsRequest)] == [[800]]

    def test_unread_left_is_the_topic_count(self, chats, capsys):
        run(FakeClient(unread_left=99), args("pod", topic="Bayat Group", json_=True))
        out = json.loads(capsys.readouterr().out)
        assert out["unread_left"] == 3
        assert out["topic"]["id"] == 12

    def test_a_chat_that_is_no_forum_is_refused(self, chats):
        cli = FakeClient()
        with pytest.raises(SystemExit):
            run(cli, args("alireza", topic="deploy"))
        assert not cli.sent(ReadDiscussionRequest)


class TestWholeChatUnchanged:
    def test_plain_read_still_clears_everything(self, chats):
        cli = FakeClient()
        run(cli, args("pod", "alireza"))
        assert [a["clear_mentions"] for a in cli.acks] == [True, True]
        assert all(a["max_id"] is None for a in cli.acks)

    def test_unread_still_flags_the_dialog(self, chats):
        cli = FakeClient()
        run(cli, args("pod", unread=True))
        assert len(cli.sent(MarkDialogUnreadRequest)) == 1


class TestParserAndTool:
    def test_read_takes_until_topic_and_json(self):
        a = t.build_parser().parse_args(["read", "--chat", "me", "--until", "77", "--topic", "deploy", "--json"])
        assert (a.until, a.topic, a.json) == (77, "deploy", True)

    def test_the_mcp_tool_exposes_both(self):
        (spec,) = [s for s in t.tool_specs() if s["name"] == "read"]
        props = spec["inputSchema"]["properties"]
        assert props["until"]["type"] == "integer"
        assert "topic" in props

    def test_a_tool_call_becomes_the_same_command_line(self):
        argv = t.tool_argv(t.build_parser(), "read", {"chat": ["pod"], "until": 9, "topic": "Bayat"})
        assert argv == ["read", "--chat", "pod", "--until", "9", "--topic", "Bayat", "--json"]
