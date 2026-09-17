"""The pure helpers: no network, no session, no Telegram account.

These are the parts that fail silently in production -- an offset computed in
the wrong unit still sends, it just underlines the wrong half of a sentence --
so they are worth pinning down even though the tool as a whole is I/O.
"""

from types import SimpleNamespace

import pytest
from telethon.tl.types import MessageEntityBlockquote, MessageEntityBold

import topoli_user as t


class TestUtf16Len:
    """Telegram counts entity offsets in UTF-16 code units, not characters."""

    def test_ascii_matches_character_count(self):
        assert t._utf16_len("hello") == 5

    def test_persian_is_still_one_unit_per_character(self):
        assert t._utf16_len("سلام") == 4

    @pytest.mark.parametrize("emoji", ["👋", "😀", "🇮🇷"[:2]])
    def test_astral_emoji_count_as_surrogate_pairs(self, emoji):
        # Anything above U+FFFF is two UTF-16 units, which is exactly the case
        # a len() based implementation gets wrong.
        assert t._utf16_len(emoji) == len(emoji.encode("utf-16-le")) // 2
        assert t._utf16_len(emoji) > len(emoji)


class TestBuildMessage:
    def test_markdown_strips_the_markup_and_emits_entities(self):
        text, entities = t.build_message("**bold** plain")
        assert text == "bold plain"
        assert any(isinstance(e, MessageEntityBold) for e in entities)

    def test_parse_none_leaves_the_text_alone(self):
        raw = "**not bold** <b>nor this</b>"
        text, entities = t.build_message(raw, parse="none")
        assert text == raw
        assert entities == []

    def test_html_mode_reads_tags(self):
        text, entities = t.build_message("<b>bold</b>", parse="html")
        assert text == "bold"
        assert any(isinstance(e, MessageEntityBold) for e in entities)

    def test_quote_wraps_the_whole_message(self):
        text, entities = t.build_message("سلام 👋", parse="none", quote=True)
        quotes = [e for e in entities if isinstance(e, MessageEntityBlockquote)]
        assert len(quotes) == 1
        # The blockquote must span the message in UTF-16 units, or the emoji
        # falls outside it and the quote renders short.
        assert quotes[0].offset == 0
        assert quotes[0].length == t._utf16_len(text)

    def test_expandable_implies_quote_and_collapses(self):
        _, entities = t.build_message("x", expandable=True)
        quote = next(e for e in entities if isinstance(e, MessageEntityBlockquote))
        assert quote.collapsed is True

    def test_unknown_parse_mode_exits(self):
        with pytest.raises(SystemExit):
            t.build_message("x", parse="rtf")


class TestFormatSummary:
    def test_no_entities_reads_as_plain(self):
        assert t.format_summary([]) == "plain"

    def test_repeated_kinds_are_counted(self):
        entities = [MessageEntityBold(0, 1), MessageEntityBold(2, 1), MessageEntityBlockquote(0, 3)]
        summary = t.format_summary(entities)
        assert "Boldx2" in summary
        assert "Blockquote" in summary


class TestQuoteOnAnEmptyMessage:
    def test_an_empty_body_gets_no_blockquote(self):
        # A 0-length entity is rejected by Telegram, so a --quote with nothing
        # to quote must drop the entity rather than build an invalid one.
        text, entities = t.build_message("", parse="none", quote=True)
        assert (text, entities) == ("", [])


class TestMsgRow:
    def _msg(self, **kw):
        return SimpleNamespace(
            id=kw.get("id", 1),
            date=None,
            sender_id=kw.get("sender_id", 5),
            sender=kw.get("sender"),
            text=kw.get("text", "hi"),
            media=None,
            action=None,
        )

    def test_outgoing_needs_a_known_account(self):
        # Without me_id nothing can be called outgoing; the old default
        # compared sender_id against None and called channel posts "mine".
        assert t.msg_row(self._msg(sender_id=None))["outgoing"] is False

    def test_my_own_message_is_outgoing_and_reads_as_me(self):
        row = t.msg_row(self._msg(sender_id=7), me_id=7)
        assert row["outgoing"] is True
        assert row["sender"] == "me"

    def test_an_unknown_sender_falls_back_to_the_id(self):
        assert t.msg_row(self._msg(sender_id=9), me_id=7)["sender"] == "9"
