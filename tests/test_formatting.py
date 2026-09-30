"""The pure helpers: no network, no session, no Telegram account.

These are the parts that fail silently in production -- an offset computed in
the wrong unit still sends, it just underlines the wrong half of a sentence --
so they are worth pinning down even though the tool as a whole is I/O.
"""

from types import SimpleNamespace

import pytest
from telethon.tl.types import (
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityPre,
    MessageEntitySpoiler,
)

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


def spans(text, entities, kind):
    """The substrings an entity type covers, read back in UTF-16 units."""
    s = text.encode("utf-16-le")
    return [s[e.offset * 2 : (e.offset + e.length) * 2].decode("utf-16-le") for e in entities if isinstance(e, kind)]


class TestMarkdownExtensions:
    """What telethon's markdown drops silently, and the desktop client has."""

    def test_spoiler(self):
        text, entities = t.build_message("see ||the answer|| 👋")
        assert text == "see the answer 👋"
        assert spans(text, entities, MessageEntitySpoiler) == ["the answer"]

    def test_fence_language_becomes_the_pre_language(self):
        text, entities = t.build_message("run:\n```bash\nls -la\n```\n**done**")
        # Without this the word "bash" is sent as the first line of code.
        assert text == "run:\nls -la\ndone"
        pre = next(e for e in entities if isinstance(e, MessageEntityPre))
        assert pre.language == "bash"
        assert spans(text, entities, MessageEntityPre) == ["ls -la"]
        assert spans(text, entities, MessageEntityBold) == ["done"]

    def test_a_bare_fence_keeps_its_first_line_as_code(self):
        text, entities = t.build_message("```\nfoo\nbar\n```")
        assert text == "foo\nbar"
        assert next(e for e in entities if isinstance(e, MessageEntityPre)).language == ""

    def test_code_on_the_fence_line_is_not_a_language(self):
        text, entities = t.build_message("```x = 1\ny```")
        assert text == "x = 1\ny"
        assert next(e for e in entities if isinstance(e, MessageEntityPre)).language == ""

    def test_quote_lines_group_into_one_blockquote_each_run(self):
        text, entities = t.build_message("> سلام **👋**\n> دوم\nplain\n>third")
        assert text == "سلام 👋\nدوم\nplain\nthird"
        assert spans(text, entities, MessageEntityBlockquote) == ["سلام 👋\nدوم", "third"]
        # The bold must move left with the stripped `> `, across the emoji.
        assert spans(text, entities, MessageEntityBold) == ["👋"]

    def test_quote_marker_inside_code_is_content(self):
        text, entities = t.build_message("```\n> $ make\n```\n`> x`")
        assert text == "> $ make\n> x"
        assert not any(isinstance(e, MessageEntityBlockquote) for e in entities)
        assert spans(text, entities, MessageEntityCode) == ["> x"]

    def test_quote_flag_replaces_inner_quotes(self):
        # Telegram rejects nested blockquotes.
        text, entities = t.build_message("> a\nb", quote=True)
        quotes = [e for e in entities if isinstance(e, MessageEntityBlockquote)]
        assert len(quotes) == 1
        assert (quotes[0].offset, quotes[0].length) == (0, t._utf16_len(text))


class TestHtmlExtensions:
    @pytest.mark.parametrize("markup", ["<tg-spoiler>x</tg-spoiler>", '<span class="tg-spoiler">x</span>'])
    def test_spoiler_tags(self, markup):
        text, entities = t.build_message(f"<b>a</b> {markup}", parse="html")
        assert text == "a x"
        assert spans(text, entities, MessageEntitySpoiler) == ["x"]

    def test_other_spans_are_ignored(self):
        text, entities = t.build_message('<span class="x">y</span>', parse="html")
        assert (text, entities) == ("y", [])

    def test_pre_language(self):
        _, entities = t.build_message('<pre><code class="language-go">x</code></pre>', parse="html")
        assert entities[0].language == "go"


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
