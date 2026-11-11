"""The AI interface: tools generated from the parser, and the MCP server.

The tool list is derived, not written, so these tests pin the derivation: every
command an agent may call is a tool, its flags are typed parameters, a tool
call turns back into the exact command line, and a failure inside a call is a
failed call rather than a dead server.
"""

import asyncio
import json

import pytest

import topoli_user as t


@pytest.fixture
def parser():
    return t.build_parser()


@pytest.fixture
def specs(parser):
    return {tool["name"]: tool for tool in t.tool_specs(parser)}


class TestToolSpecs:
    def test_every_command_but_the_plumbing_is_a_tool(self, parser, specs):
        commands = set(t.subcommands(parser)) - t.NOT_TOOLS
        assert set(specs) == {t.tool_name(c) for c in commands}

    def test_login_is_never_a_tool(self, specs):
        assert "login" not in specs and "setup" not in specs

    def test_names_are_identifier_safe(self, specs):
        assert "send_file" in specs and "chat_photo" in specs
        assert all("-" not in name for name in specs)

    def test_flags_become_typed_parameters(self, specs):
        props = specs["send"]["inputSchema"]["properties"]
        assert props["chat"]["type"] == "string"
        assert props["reply_to"]["type"] == "integer"
        assert props["yes"]["type"] == "boolean"
        assert props["parse"]["enum"] == list(t.PARSE_CHOICES)

    def test_repeatable_flags_are_arrays(self, specs):
        message = specs["delete"]["inputSchema"]["properties"]["message"]
        assert message == {**message, "type": "array", "items": {"type": "integer"}}

    def test_required_flags_are_required(self, specs):
        assert set(specs["history"]["inputSchema"]["required"]) == {"chat"}

    def test_json_is_not_a_parameter_because_it_is_always_on(self, specs):
        assert all("json" not in s["inputSchema"]["properties"] for s in specs.values())

    def test_writes_say_they_are_dry_runs(self, specs):
        assert "Dry run unless yes=true" in specs["send"]["description"]
        assert specs["send"]["annotations"]["readOnlyHint"] is False
        assert specs["history"]["annotations"]["readOnlyHint"] is True

    def test_schema_survives_json(self, parser):
        json.dumps(t.tool_specs(parser))


class TestToolArgv:
    def test_a_call_becomes_its_command_line(self, parser):
        argv = t.tool_argv(parser, "send", {"chat": "wife", "text": "hi", "reply_to": 5, "yes": True})
        assert argv == ["send", "--chat", "wife", "--text", "hi", "--reply-to", "5", "--yes", "--json"]

    def test_false_flags_and_nulls_are_left_out(self, parser):
        assert t.tool_argv(parser, "chats", {"unread": False, "limit": None}) == ["chats", "--json"]

    def test_arrays_repeat_the_flag(self, parser):
        argv = t.tool_argv(parser, "delete", {"chat": "me", "message": [1, 2]})
        assert argv[:6] == ["delete", "--chat", "me", "--message", "1", "--message"]

    def test_a_dest_that_differs_from_the_flag_maps_back(self, parser):
        assert t.tool_argv(parser, "mute", {"chat": "me", "for_": "8h"})[3:5] == ["--for", "8h"]

    def test_unknown_parameters_are_refused(self, parser):
        with pytest.raises(ValueError, match="no parameter"):
            t.tool_argv(parser, "send", {"chat": "me", "txt": "typo"})

    def test_unknown_tools_are_refused(self, parser):
        with pytest.raises(ValueError, match="unknown tool"):
            t.tool_argv(parser, "login", {})

    def test_every_argv_parses(self, parser, specs):
        """Round trip: the minimal call of every tool parses back."""
        for name, spec in specs.items():
            args = {}
            for key in spec["inputSchema"]["required"]:
                prop = spec["inputSchema"]["properties"][key]
                args[key] = {"integer": 1, "string": "x", "array": ["a", "b"]}[prop["type"]]
                if prop["type"] == "array" and prop["items"]["type"] == "integer":
                    args[key] = [1]
            if name in ("send", "edit", "remind"):
                args["text"] = "x"
            parser.parse_args(t.tool_argv(parser, name, args))


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "REPO_CONFIG", tmp_path / "api.json")
    monkeypatch.setattr(t, "CONFIG_FILE", tmp_path / "state" / "user.json")
    monkeypatch.setattr(t, "STATE_DIR", tmp_path / "state")
    for env in t.DEVICE_KEYS.values():
        monkeypatch.delenv(env, raising=False)
    return tmp_path


class TestMcp:
    def handle(self, msg, parser):
        return asyncio.run(t.mcp_handle(None, parser, t.tool_specs(parser), msg))

    def test_initialize_names_the_server_and_carries_the_rules(self, parser):
        res = self.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}, parser
        )
        assert res["result"]["protocolVersion"] == "2025-03-26"
        assert res["result"]["serverInfo"]["name"] == "elbot"
        assert "DRY RUN" in res["result"]["instructions"]

    def test_an_unknown_protocol_gets_the_newest(self, parser):
        res = self.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999"}}, parser
        )
        assert res["result"]["protocolVersion"] == t.MCP_PROTOCOLS[0]

    def test_notifications_get_no_answer(self, parser):
        assert self.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, parser) is None

    def test_tools_list(self, parser):
        res = self.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, parser)
        assert {"send", "draft", "catchup"} <= {tool["name"] for tool in res["result"]["tools"]}

    def test_unknown_methods_are_json_rpc_errors(self, parser):
        assert self.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"}, parser)["error"]["code"] == -32601

    def test_a_call_returns_the_json_result(self, parser, config):
        res = asyncio.run(t.call_tool(None, parser, "device", {}))
        assert res["isError"] is False
        assert json.loads(res["content"][0]["text"])["device"]["device_model"] == "ELBoT"

    def test_a_bad_call_is_a_failed_call_not_a_dead_server(self, parser):
        res = asyncio.run(t.call_tool(None, parser, "send", {"chat": "me"}))  # no text: argparse exits 2
        assert res["isError"] is True

    def test_json_errors_are_switched_off_again(self, parser):
        asyncio.run(t.call_tool(None, parser, "send", {"chat": "me"}))
        assert t.JSON_ERRORS is False


class TestDevice:
    def test_default_names_the_tool(self, config):
        assert t.device_identity()["device_model"] == "ELBoT"
        assert t.device_identity()["app_version"] == f"ELBoT {t.VERSION}"

    def test_config_then_environment(self, config, monkeypatch):
        (config / "api.json").write_text(json.dumps({"device_model": "Claude"}))
        assert t.device_identity()["device_model"] == "Claude"
        monkeypatch.setenv("TOPOLI_DEVICE_MODEL", "Override")
        assert t.device_identity()["device_model"] == "Override"

    def test_rename_writes_the_config(self, config, capsys):
        args = t.build_parser().parse_args(["device", "--name", "Claude", "--system", "AI secretary"])
        asyncio.run(t.cmd_device(None, args))
        stored = json.loads((config / "api.json").read_text())
        assert stored == {"device_model": "Claude", "system_version": "AI secretary"}

    def test_rename_keeps_the_api_credentials(self, config):
        (config / "api.json").write_text(json.dumps({"api_id": 1, "api_hash": "h"}))
        asyncio.run(t.cmd_device(None, t.build_parser().parse_args(["device", "--name", "Claude"])))
        assert json.loads((config / "api.json").read_text())["api_id"] == 1

    def test_reset_forgets_it(self, config):
        (config / "api.json").write_text(json.dumps({"api_id": 1, "device_model": "Claude"}))
        asyncio.run(t.cmd_device(None, t.build_parser().parse_args(["device", "--reset"])))
        assert json.loads((config / "api.json").read_text()) == {"api_id": 1}


class TestDurations:
    @pytest.mark.parametrize(
        ("text", "seconds"), [("30m", 1800), ("8h", 28800), ("1d", 86400), ("2w", 1209600), ("1d12h", 129600)]
    )
    def test_parse(self, text, seconds):
        assert t.parse_duration(text).total_seconds() == seconds

    @pytest.mark.parametrize("text", ["", "8", "h", "8x", "8h junk"])
    def test_refuses_anything_else(self, text):
        with pytest.raises(SystemExit):
            t.parse_duration(text)

    def test_mute_forever_and_off(self):
        assert t.mute_until(None, off=False) == t.MUTE_FOREVER
        assert t.mute_until("forever", off=False) == t.MUTE_FOREVER
        assert t.mute_until("8h", off=True) == 0


class TestPoll:
    def test_answers_are_numbered_bytes(self):
        media = t.build_poll("lunch?", ["pizza", "kabab"], multiple=False, public=False, quiz=None)
        assert [a.option for a in media.poll.answers] == [b"\x00", b"\x01"]
        assert media.correct_answers is None

    def test_quiz_marks_the_right_answer(self):
        media = t.build_poll("2+2", ["3", "4"], multiple=False, public=False, quiz=1)
        assert media.poll.quiz and media.correct_answers == [1]

    @pytest.mark.parametrize(
        ("options", "quiz", "multiple"), [(["only"], None, False), (["a", "b"], 2, False), (["a", "b"], 0, True)]
    )
    def test_refuses_bad_polls(self, options, quiz, multiple):
        with pytest.raises(SystemExit):
            t.build_poll("q", options, multiple=multiple, public=False, quiz=quiz)


class TestOutput:
    def test_narration_moves_to_stderr_under_json(self, capsys):
        t.say(type("A", (), {"json": True})(), "about to send")
        out, err = capsys.readouterr()
        assert out == "" and "about to send" in err

    def test_dry_run_is_one_json_object(self, capsys):
        t.dry_run(type("A", (), {"json": True})(), {"text": "hi"}, "DRY RUN")
        assert json.loads(capsys.readouterr().out) == {"dry_run": True, "text": "hi"}

    def test_die_answers_in_json_when_asked(self, capsys, monkeypatch):
        monkeypatch.setattr(t, "JSON_ERRORS", True)
        with pytest.raises(SystemExit):
            t.die("no chat matching 'x'")
        assert json.loads(capsys.readouterr().out) == {"error": "no chat matching 'x'"}

    def test_text_file_wins_and_keeps_every_character(self, tmp_path):
        src = tmp_path / "reply.md"
        src.write_text("قیمت `$PRICE` — **done**\n")
        args = type("A", (), {"text": None, "text_file": str(src)})()
        assert t.message_text(args) == "قیمت `$PRICE` — **done**\n"


class TestMediaRow:
    def test_no_media_is_none(self):
        assert t.media_row(type("M", (), {"media": None})()) is None

    def test_the_most_specific_kind_wins(self):
        msg = type("M", (), {"media": object(), "voice": True, "audio": True, "file": None})()
        row = t.media_row(msg)
        assert row is not None and row["type"] == "voice"

    def test_file_details_are_carried(self):
        f = type("F", (), {"name": "invoice.pdf", "mime_type": "application/pdf", "size": 1024, "duration": None})()
        msg = type("M", (), {"media": object(), "file": f})()
        row = t.media_row(msg)
        assert row is not None
        assert row["name"] == "invoice.pdf" and row["size"] == 1024 and "duration" not in row


class TestCachePath:
    def test_the_named_cache_when_free_and_a_private_one_when_held(self, tmp_path, monkeypatch):
        monkeypatch.setattr(t, "STATE_DIR", tmp_path)
        monkeypatch.setattr(t, "SESSION", tmp_path / "topoli.session")
        monkeypatch.setattr(t, "_HELD", [])
        first = t.cache_path("mcp")
        second = t.cache_path("mcp")  # the first lock is still held by this process's open file
        assert first.name == "topoli-mcp.session"
        assert second != first and "mcp-" in second.name
        for lock in t._HELD:
            lock.close()


class TestAccounts:
    @pytest.fixture(autouse=True)
    def _restore(self, monkeypatch, tmp_path):
        monkeypatch.setattr(t, "SKILL_DIR", tmp_path)
        monkeypatch.setattr(t, "STATE_DIR", tmp_path / "state")
        monkeypatch.delenv("TOPOLI_SESSION", raising=False)
        yield
        t.set_account(None)

    def test_the_default_keeps_the_old_layout(self, tmp_path):
        t.set_account(None)
        assert tmp_path / "topoli.authkey" == t.SESSION_STRING
        assert t.INDEX_FILE.name == "chat-index.json"

    def test_a_named_account_gets_its_own_key_cache_index_and_login(self, tmp_path):
        t.set_account("Parham")
        assert t.ACCOUNT == "parham"
        assert tmp_path / "parham.authkey" == t.SESSION_STRING
        assert t.SESSION.name == "parham.session"
        assert t.INDEX_FILE.name == "chat-index-parham.json"
        assert t.PENDING.name == "login-pending-parham.json"
        assert "--account parham" in t.invocation()

    @pytest.mark.parametrize("name", ["../x", "a b", "", "-x"])
    def test_names_cannot_escape_the_directory(self, name):
        if not name:
            t.set_account(name)  # empty means the default
            assert t.ACCOUNT == t.DEFAULT_ACCOUNT
            return
        with pytest.raises(SystemExit):
            t.set_account(name)

    def test_the_flag_comes_before_the_command(self):
        assert t.build_parser().parse_args(["--account", "parham", "whoami"]).account == "parham"
