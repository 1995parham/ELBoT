"""Chat resolution: ids, aliases, and the title cache.

Everything here is the code that decides *who* a message goes to, which is the
one class of bug in this tool that cannot be taken back after the fact.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from telethon.tl.types import Chat, ChatPhotoEmpty

import topoli_user as t


def chat(id_: int, title: str) -> Chat:
    """A minimal basic group, which is what `label`/`kind` read."""
    return Chat(id=id_, title=title, photo=ChatPhotoEmpty(), participants_count=0, date=None, version=0)


class TestBotApiVariants:
    """Bot API ids and MTProto ids are different numbers for the same chat."""

    def test_a_plain_positive_id_is_offered_as_is(self):
        assert t.bot_api_variants(123) == [123]

    def test_supergroup_prefix_is_stripped(self):
        # -100<id> is how the Bot API writes a supergroup.
        assert 5446268647 in t.bot_api_variants(-1005446268647)

    def test_negative_group_id_is_offered_positive(self):
        assert t.bot_api_variants(-5446268647) == [-5446268647, 5446268647]

    def test_the_raw_value_always_comes_first(self):
        # The caller tries these in order, so the id as given must win when it
        # happens to be valid on its own.
        for raw in (-1005446268647, -5446268647, 42):
            assert t.bot_api_variants(raw)[0] == raw


class TestIndexLookup:
    @pytest.fixture(autouse=True)
    def _cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(t, "INDEX_FILE", tmp_path / "chat-index.json")
        monkeypatch.setattr(t, "STATE_DIR", tmp_path)
        t.save_index(
            {
                "1": {
                    "id": 1,
                    "title": "Elaheh Dastan (@elaheh_dastan)",
                    "username": "elaheh_dastan",
                    "kind": "private",
                },
                "2": {"id": 2, "title": "Elaheh Dastan (@elahe_dastan)", "username": "elahe_dastan", "kind": "private"},
                "3": {"id": 3, "title": "Life Planning", "username": None, "kind": "group"},
            }
        )

    def test_exact_username_wins_over_substring(self):
        assert t.index_lookup("@elaheh_dastan") == [1]

    def test_a_shared_display_name_returns_every_candidate(self):
        # Two contacts, one name: the caller must ask rather than pick.
        assert sorted(t.index_lookup("Elaheh Dastan")) == [1, 2]

    def test_lookup_is_case_insensitive(self):
        assert t.index_lookup("life planning") == [3]

    def test_a_miss_is_empty(self):
        assert t.index_lookup("nobody") == []


class TestAliases:
    @pytest.fixture(autouse=True)
    def _books(self, tmp_path, monkeypatch):
        monkeypatch.setattr(t, "ALIAS_REPO", tmp_path / "aliases.json")
        monkeypatch.setattr(t, "ALIAS_LOCAL", tmp_path / "state" / "aliases.json")
        monkeypatch.setattr(t, "STATE_DIR", tmp_path / "state")

    def test_absent_book_is_empty_not_an_error(self):
        assert t.load_aliases() == {}

    def test_corrupt_book_is_ignored_rather_than_fatal(self):
        t.ALIAS_REPO.write_text("{ this is not json")
        assert t.load_aliases() == {}

    def test_keys_are_lowercased_so_lookups_are_case_insensitive(self):
        t.save_aliases({"Wife": {"id": 962896850}}, local=False)
        assert t.load_aliases()["wife"]["id"] == 962896850

    def test_the_local_book_overrides_the_shared_one(self):
        t.save_aliases({"wife": {"id": 1}}, local=False)
        t.save_aliases({"wife": {"id": 2}}, local=True)
        assert t.load_aliases()["wife"]["id"] == 2

    def test_removing_clears_both_books_not_just_the_first(self):
        # The local book shadows the shared one, so stopping at the first hit
        # left the alias apparently still set.
        t.save_aliases({"wife": {"id": 1}}, local=False)
        t.save_aliases({"wife": {"id": 2}}, local=True)
        asyncio.run(t.cmd_alias(None, SimpleNamespace(remove="wife", set=None, local=False, json=False)))
        assert t.load_aliases() == {}

    def test_saving_local_creates_the_state_directory(self):
        t.save_aliases({"mom": {"id": 7}}, local=True)
        assert json.loads(t.ALIAS_LOCAL.read_text())["mom"]["id"] == 7


class TestParseWhen:
    def test_none_stays_none(self):
        assert t.parse_when(None, "--since") is None

    def test_a_bare_date_is_accepted(self):
        assert t.parse_when("2026-08-01", "--since").year == 2026

    def test_the_result_is_always_tz_aware(self):
        # A naive value would compare-explode against Telegram's aware dates.
        assert t.parse_when("2026-08-01T14:30", "--since").tzinfo is not None

    def test_an_explicit_offset_is_honoured(self):
        assert t.parse_when("2026-08-01T00:00+00:00", "--until").hour == 0

    def test_garbage_exits_with_a_message(self):
        with pytest.raises(SystemExit):
            t.parse_when("last tuesday", "--since")


class TestProxy:
    def test_no_proxy_env_means_no_proxy(self, monkeypatch):
        for var in ("TOPOLI_PROXY", "all_proxy", "https_proxy", "http_proxy"):
            monkeypatch.delenv(var, raising=False)
        assert t.proxy() is None

    def test_socks5h_is_normalised_for_telethon(self, monkeypatch):
        monkeypatch.setenv("TOPOLI_PROXY", "socks5h://127.0.0.1:1080")
        assert t.proxy() == ("socks5", "127.0.0.1", 1080)

    def test_a_bare_host_port_is_assumed_http(self, monkeypatch):
        monkeypatch.setenv("TOPOLI_PROXY", "gpx.caffex.ir:8084")
        assert t.proxy() == ("http", "gpx.caffex.ir", 8084)

    def test_topoli_proxy_beats_the_ambient_vars(self, monkeypatch):
        monkeypatch.setenv("all_proxy", "http://wrong:1")
        monkeypatch.setenv("TOPOLI_PROXY", "http://right:2")
        assert t.proxy() == ("http", "right", 2)

    def test_an_unsupported_scheme_exits(self, monkeypatch):
        monkeypatch.setenv("TOPOLI_PROXY", "ftp://host:21")
        with pytest.raises(SystemExit):
            t.proxy()

    def test_a_port_less_proxy_exits(self, monkeypatch):
        monkeypatch.setenv("TOPOLI_PROXY", "http://host")
        with pytest.raises(SystemExit):
            t.proxy()


class TestIndexLookupWithUsernames:
    """`label` appends " (@username)", so the stored title is never what you type."""

    @pytest.fixture(autouse=True)
    def _cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(t, "INDEX_FILE", tmp_path / "chat-index.json")
        monkeypatch.setattr(t, "STATE_DIR", tmp_path)
        t.save_index(
            {
                "1": {"id": 1, "title": "Ops Team (@ops_chat)", "username": "ops_chat", "kind": "supergroup"},
                "2": {"id": 2, "title": "Ops Team Archive", "username": None, "kind": "group"},
            }
        )

    def test_the_bare_title_still_counts_as_an_exact_match(self):
        # The full title is what anyone types; the "(@ops_chat)" suffix is
        # something `label` added. Without stripping it the exact pass finds
        # nothing, the substring pass returns both chats, and an unambiguous
        # name is refused as ambiguous.
        assert t.index_lookup("Ops Team") == [1]

    def test_the_username_is_still_an_exact_match_of_its_own(self):
        assert t.index_lookup("@ops_chat") == [1]

    def test_a_genuine_substring_still_returns_every_candidate(self):
        assert sorted(t.index_lookup("ops")) == [1, 2]


class TestResolveByTitle:
    """The live-dialog fallback, which runs on a cache miss."""

    @pytest.fixture(autouse=True)
    def _empty_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(t, "INDEX_FILE", tmp_path / "chat-index.json")
        monkeypatch.setattr(t, "STATE_DIR", tmp_path)
        monkeypatch.setattr(t, "ALIAS_REPO", tmp_path / "aliases.json")
        monkeypatch.setattr(t, "ALIAS_LOCAL", tmp_path / "state" / "aliases.json")

    @staticmethod
    def _client(*chats):
        class FakeClient:
            async def get_entity(self, ref):
                for c in chats:
                    if c.id == ref:
                        return c
                raise ValueError(ref)

            def iter_dialogs(self, limit=None):
                async def gen():
                    for c in chats:
                        yield SimpleNamespace(entity=c)

                return gen()

        return FakeClient()

    def test_a_single_match_resolves_after_one_scan(self):
        cli = self._client(chat(1, "Ops Team"), chat(2, "Life Planning"))
        assert asyncio.run(t.resolve(cli, "Life")).id == 2

    def test_an_ambiguous_title_asks_instead_of_taking_the_first(self):
        # This is the one that cannot be taken back: the old fallback broke on
        # the first dialog whose name merely contained the text.
        cli = self._client(chat(1, "Ops Team"), chat(2, "Ops Team Archive"))
        with pytest.raises(SystemExit):
            asyncio.run(t.resolve(cli, "Ops"))

    def test_no_match_at_all_exits(self):
        cli = self._client(chat(1, "Ops Team"))
        with pytest.raises(SystemExit):
            asyncio.run(t.resolve(cli, "nobody here"))
