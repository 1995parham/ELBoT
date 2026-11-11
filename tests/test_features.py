"""Tags, the watcher's rules, reminders and the MCP prompts and resources.

None of this needs the network: tags are a file, a rule is matched against a
plain message row, and prompts and resources are lookups that end in a tool
call — the tool call itself is covered in test_ai.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

import topoli_user as t


@pytest.fixture
def tags_file(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "TAGS_FILE", tmp_path / "tags.json")
    return tmp_path / "tags.json"


class TestTags:
    def test_names_are_one_word_lowercase(self):
        assert t.tag_name("#Client") == "client"
        with pytest.raises(SystemExit):
            t.tag_name("two words")

    def test_round_trip_and_lookup(self, tags_file):
        t.save_tags({"client": {"1": "Acme", "2": "Beta"}, "family": {"3": "Wife"}})
        assert t.tagged_ids(["client"]) == {1, 2}
        assert t.tagged_ids(["client", "family"]) == {1, 2, 3}
        assert t.tags_of(3) == ["family"]

    def test_no_tag_asked_means_no_filter(self, tags_file):
        assert t.tagged_ids(None) is None and t.tagged_ids([]) is None

    def test_an_unknown_tag_stops_rather_than_matching_nothing(self, tags_file):
        t.save_tags({"client": {"1": "Acme"}})
        with pytest.raises(SystemExit):
            t.tagged_ids(["clinet"])

    def test_empty_tags_are_dropped_on_save(self, tags_file):
        t.save_tags({"client": {}, "family": {"3": "Wife"}})
        assert json.loads(tags_file.read_text()) == {"family": {"3": "Wife"}}

    def test_tag_and_untag_need_no_network_for_the_listing(self, tags_file, capsys):
        t.save_tags({"client": {"1": "Acme"}})
        args = t.build_parser().parse_args(["tag", "--json"])
        asyncio.run(t.cmd_tag(None, args))
        assert json.loads(capsys.readouterr().out) == [{"tag": "client", "chats": 1}]


def watched(text="hello", outgoing=False, chat_id=10, private=True, mentioned=False, sender="Ali (@ali)"):
    return t.Watched(
        {"text": text, "outgoing": outgoing, "sender": sender}, chat_id, "Chat", private, mentioned, [sender]
    )


class TestRuleMatches:
    def test_an_empty_rule_matches_every_incoming_message(self):
        assert t.rule_matches({}, watched(), None, None)

    def test_outgoing_is_ignored_unless_asked(self):
        assert not t.rule_matches({}, watched(outgoing=True), None, None)
        assert t.rule_matches({"incoming": False}, watched(outgoing=True), None, None)

    def test_chats_and_tags(self):
        assert t.rule_matches({}, watched(chat_id=10), {10, 11}, None)
        assert not t.rule_matches({}, watched(chat_id=12), {10, 11}, None)
        assert not t.rule_matches({}, watched(chat_id=12), None, {10})

    def test_keywords_any_case_and_persian(self):
        rule = {"keywords": ["URGENT", "فوری"]}
        assert t.rule_matches(rule, watched("this is urgent"), None, None)
        assert t.rule_matches(rule, watched("لطفا فوری"), None, None)
        assert not t.rule_matches(rule, watched("whenever"), None, None)

    def test_every_condition_must_hold(self):
        rule = {"keywords": ["invoice"], "private": False}
        assert not t.rule_matches(rule, watched("invoice", private=True), None, None)
        assert t.rule_matches(rule, watched("invoice", private=False), None, None)

    def test_mentions_and_senders(self):
        assert not t.rule_matches({"mentions": True}, watched(), None, None)
        assert t.rule_matches({"mentions": True}, watched(mentioned=True), None, None)
        assert t.rule_matches({"from": ["@ali"]}, watched(), None, None)
        assert not t.rule_matches({"from": ["reza"]}, watched(), None, None)

    def test_regex(self):
        assert t.rule_matches({"regex": r"INV-\d+"}, watched("see inv-42"), None, None)


class TestLoadRules:
    def write(self, tmp_path, rules):
        path = tmp_path / "watch.json"
        path.write_text(json.dumps({"rules": rules}))
        return path

    def test_the_example_is_valid(self, tmp_path):
        path = tmp_path / "watch.json"
        path.write_text(json.dumps(t.WATCH_EXAMPLE))
        t.set_account("parham")
        try:
            names = [r["name"] for r in t.load_rules(path)]
        finally:
            t.set_account(None)
        assert "family" in names and "iranian account to main" not in names

    @pytest.mark.parametrize(
        "rule",
        [
            {"name": "x", "actions": []},
            {"name": "x", "chat": ["typo"], "actions": [{"notify": True}]},
            {"name": "x", "actions": [{"email": "me"}]},
            {"name": "x", "regex": "(", "actions": [{"notify": True}]},
        ],
    )
    def test_mistakes_stop_before_anything_runs(self, tmp_path, rule):
        with pytest.raises(SystemExit):
            t.load_rules(self.write(tmp_path, [rule]))


class TestReminders:
    def test_in_is_from_now(self):
        when = t.parse_at(None, "2h")
        assert timedelta(hours=1, minutes=59) < when - datetime.now(UTC) <= timedelta(hours=2)

    def test_exactly_one_of_at_and_in(self):
        with pytest.raises(SystemExit):
            t.parse_at(None, None)
        with pytest.raises(SystemExit):
            t.parse_at("2030-01-01T09:00", "2h")

    def test_the_past_is_refused(self):
        with pytest.raises(SystemExit):
            t.parse_at("2020-01-01T09:00", None)


class TestPromptsAndResources:
    def test_every_prompt_renders_with_its_required_arguments(self):
        for spec in t.prompt_specs():
            args = {a["name"]: "x" for a in spec["arguments"] if a["required"]}
            text = t.render_prompt(spec["name"], args)["messages"][0]["content"]["text"]
            assert "{" not in text, spec["name"]

    def test_a_missing_required_argument_is_refused(self):
        with pytest.raises(ValueError, match="needs"):
            t.render_prompt("reply", {})

    def test_reply_starts_by_typing(self):
        text = t.render_prompt("reply", {"chat": "wife"})["messages"][0]["content"]["text"]
        assert "`typing`" in text and "wife" in text

    def test_tag_flows_into_the_prompt(self):
        text = t.render_prompt("triage", {"tag": "client"})["messages"][0]["content"]["text"]
        assert "#client" in text

    def test_static_resources_map_to_tools(self):
        assert t.resource_call("telegram://unread") == ("catchup", {})
        assert t.resource_call("telegram://reminders") == ("scheduled", {"chat": "me"})

    def test_templates_extract_and_unquote(self):
        assert t.resource_call("telegram://chat/Pod%20venture") == ("history", {"chat": "Pod venture"})
        assert t.resource_call("telegram://tag/client/unread") == ("catchup", {"tag": ["client"]})

    def test_every_resource_is_a_real_tool_call(self):
        parser = t.build_parser()
        for uri in t.MCP_RESOURCES:
            tool, arguments = t.resource_call(uri)
            parser.parse_args(t.tool_argv(parser, tool, arguments))

    def test_unknown_resources_are_refused(self):
        with pytest.raises(ValueError):
            t.resource_call("telegram://nope")

    def test_mcp_lists_prompts_and_resources(self):
        parser = t.build_parser()

        def ask(method):
            return asyncio.run(t.mcp_handle(None, parser, [], {"jsonrpc": "2.0", "id": 1, "method": method}))

        assert {p["name"] for p in ask("prompts/list")["result"]["prompts"]} >= {"triage", "reply", "remind"}
        assert any(r["uri"] == "telegram://unread" for r in ask("resources/list")["result"]["resources"])
        assert ask("resources/templates/list")["result"]["resourceTemplates"]
        init = asyncio.run(t.mcp_handle(None, parser, [], {"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
        assert init is not None
        assert set(init["result"]["capabilities"]) == {"tools", "prompts", "resources"}


def test_typing_stops_quietly_when_nothing_runs():
    assert t.stop_typing(123) is False
