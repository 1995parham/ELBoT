"""Forum topics: which thread a message is read from, and sent to.

A supergroup with topics is several conversations wearing one chat id. Getting
this wrong is not a formatting slip — the message is delivered, to people who
are not reading that thread, and it cannot be taken back. So the parts that
decide *which* thread are pinned here, all of them pure.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.tl.types import Channel, ChatPhotoEmpty

import topoli_user as t


def topic(id_: int, title: str, **kw) -> dict:
    return {
        "id": id_,
        "title": title,
        "unread": kw.get("unread", 0),
        "closed": kw.get("closed", False),
        "pinned": kw.get("pinned", False),
        "general": id_ == t.GENERAL_TOPIC_ID,
    }


TOPICS = [
    topic(1, "General"),
    topic(12, "deploy"),
    topic(34, "deploy v2"),
    topic(56, "Design review"),
]


class TestIsForum:
    def test_a_group_without_the_flag_is_not_a_forum(self):
        assert t.is_forum(SimpleNamespace(forum=False)) is False

    def test_a_missing_flag_is_not_a_forum(self):
        """Basic groups and private chats have no such attribute at all."""
        assert t.is_forum(SimpleNamespace()) is False

    def test_the_flag_is_what_makes_it_one(self):
        assert t.is_forum(SimpleNamespace(forum=True)) is True


class TestMatchTopic:
    def test_an_id_selects_that_topic(self):
        assert t.match_topic(TOPICS, "34")["title"] == "deploy v2"

    def test_general_is_selectable_by_name(self):
        assert t.match_topic(TOPICS, "general")["id"] == t.GENERAL_TOPIC_ID

    def test_general_is_case_insensitive(self):
        assert t.match_topic(TOPICS, "General")["id"] == t.GENERAL_TOPIC_ID

    def test_an_exact_title_beats_a_substring(self):
        """'deploy' is also a substring of 'deploy v2' — exact must win.

        Otherwise naming a thread precisely is the one thing that cannot
        address it.
        """
        assert t.match_topic(TOPICS, "deploy")["id"] == 12

    def test_a_unique_substring_resolves(self):
        assert t.match_topic(TOPICS, "design")["id"] == 56

    def test_an_ambiguous_substring_refuses_and_lists_the_matches(self):
        with pytest.raises(LookupError) as exc:
            t.match_topic(TOPICS, "v")
        assert "deploy v2" in str(exc.value)

    def test_an_unknown_title_refuses(self):
        with pytest.raises(LookupError, match="no topic matching"):
            t.match_topic(TOPICS, "nowhere")

    def test_an_unknown_id_refuses(self):
        with pytest.raises(LookupError, match="no topic with id 999"):
            t.match_topic(TOPICS, "999")

    def test_general_refuses_when_the_group_has_none(self):
        with pytest.raises(LookupError, match="no General topic"):
            t.match_topic([topic(12, "deploy")], "general")


def message(**kw):
    """A message with only the reply header the topic is read from."""
    return SimpleNamespace(reply_to=kw.get("reply_to"))


def header(forum_topic=True, top=None, msg=None):
    return SimpleNamespace(forum_topic=forum_topic, reply_to_top_id=top, reply_to_msg_id=msg)


class TestMessageTopicId:
    def test_a_reply_inside_a_topic_reports_the_root(self):
        assert t.message_topic_id(message(reply_to=header(top=12, msg=900))) == 12

    def test_the_first_reply_to_the_root_reports_the_root_itself(self):
        """There is no top id yet — the message replied to *is* the topic."""
        assert t.message_topic_id(message(reply_to=header(top=None, msg=12))) == 12

    def test_a_message_with_no_header_is_not_in_a_named_topic(self):
        assert t.message_topic_id(message(reply_to=None)) is None

    def test_an_ordinary_reply_outside_a_forum_is_not_a_topic(self):
        """A plain reply in a normal group carries a header but no forum flag."""
        assert t.message_topic_id(message(reply_to=header(forum_topic=False, msg=900))) is None


class TestSendReplyTo:
    """Telethon takes one integer, so the two flags have to collapse to one."""

    def test_nothing_given_is_nothing_passed(self):
        assert t.send_reply_to(None, None) is None

    def test_a_topic_alone_addresses_the_topic(self):
        assert t.send_reply_to(None, 12) == 12

    def test_a_reply_alone_addresses_the_message(self):
        assert t.send_reply_to(900, None) == 900

    def test_a_reply_wins_over_a_topic(self):
        """A reply already lands in its parent's topic, so it is the stronger
        of the two. The mismatched case is refused earlier, by
        `check_topic_matches` — this only pins which one is passed."""
        assert t.send_reply_to(900, 12) == 900


class TestTopicRow:
    def test_general_is_flagged_by_its_id(self):
        row = t.topic_row(SimpleNamespace(id=1, title="General", unread_count=0))
        assert row["general"] is True

    def test_counts_and_flags_survive(self):
        row = t.topic_row(SimpleNamespace(id=12, title="deploy", unread_count=4, closed=True, pinned=True))
        assert (row["unread"], row["closed"], row["pinned"], row["general"]) == (4, True, True, False)

    def test_missing_optional_fields_default_rather_than_raise(self):
        row = t.topic_row(SimpleNamespace(id=12, title="deploy"))
        assert (row["unread"], row["closed"], row["pinned"]) == (0, False, False)


class TestMsgRowCarriesTheThread:
    """Without these a group reads as one flat conversation."""

    def test_a_topic_reply_reports_both(self):
        msg = SimpleNamespace(
            id=5,
            date=None,
            sender_id=1,
            sender=None,
            message="hi",
            media=None,
            action=None,
            reply_to=header(top=12, msg=900),
            text="hi",
        )
        row = t.msg_row(msg)
        assert (row["reply_to"], row["topic_id"]) == (900, 12)

    def test_a_plain_message_reports_neither(self):
        msg = SimpleNamespace(
            id=5,
            date=None,
            sender_id=1,
            sender=None,
            message="hi",
            media=None,
            action=None,
            reply_to=None,
            text="hi",
        )
        row = t.msg_row(msg)
        assert (row["reply_to"], row["topic_id"]) == (None, None)


def channel(forum: bool) -> Channel:
    """A supergroup, which is what `kind` and `is_forum` actually read."""
    return Channel(
        id=7,
        title="Pod venture",
        photo=ChatPhotoEmpty(),
        date=datetime(2026, 9, 20, tzinfo=UTC),
        megagroup=True,
        forum=forum,
    )


class TestEntityRow:
    def test_a_forum_is_marked_as_one(self):
        assert t.entity_row(channel(forum=True))["forum"] is True

    def test_a_plain_supergroup_is_not(self):
        assert t.entity_row(channel(forum=False))["forum"] is False

    def test_kind_still_says_supergroup(self):
        """A forum *is* a supergroup; the flag is extra, not a replacement.

        Changing `kind` instead would have quietly invalidated every cached
        chat-index entry written before this.
        """
        assert t.entity_row(channel(forum=True))["kind"] == "supergroup"
