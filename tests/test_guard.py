"""The key guard, the cache sweep and the code-login report.

A key Telegram sees from two IP addresses at once is revoked for good, so the
guard must refuse a second route while another process holds the key, and
must not mistake a dead process for a live one.
"""

import fcntl
import json
import os

import pytest
from telethon.tl import types

import topoli_user as t


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "STATE_DIR", tmp_path)
    monkeypatch.setattr(t, "ACCOUNT", "spain")
    monkeypatch.setattr(t, "SESSION", tmp_path / "spain.session")
    monkeypatch.setattr(t, "_CLAIMED", set())
    monkeypatch.setattr(t, "_HELD", [])
    for var in ("TOPOLI_PROXY", "all_proxy", "https_proxy", "http_proxy", "TOPOLI_ALLOW_MIXED_ROUTES"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def other_process(state, pid: int, route: str, alive: bool = True):
    """Register a fake process; holding its lock from a second handle makes it live."""
    folder = state / "keys" / "spain"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{pid}.json"
    path.write_text(json.dumps({"pid": pid, "route": route, "command": "mcp", "since": "now"}))
    if not alive:
        return None
    handle = path.open("a")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def test_route_label(monkeypatch):
    monkeypatch.delenv("TOPOLI_PROXY", raising=False)
    assert t.route_label(None) in ("direct", t.route_label())
    assert t.route_label(("http", "px.example", 8080)) == "http://px.example:8080"


def test_live_user_listed_and_dead_one_removed(state):
    held = other_process(state, 111, "direct")
    other_process(state, 222, "direct", alive=False)
    users = t.key_users()
    assert [u["pid"] for u in users] == [111]
    assert not (state / "keys" / "spain" / "222.json").exists()
    held.close()


def test_claim_refuses_a_second_route(state, monkeypatch):
    held = other_process(state, 111, "http://px.example:8080")
    with pytest.raises(SystemExit):
        t.claim_key()  # this process would go direct
    assert not (state / "keys" / "spain" / f"{os.getpid()}.json").exists()
    monkeypatch.setenv("TOPOLI_ALLOW_MIXED_ROUTES", "1")
    t.claim_key()
    assert (state / "keys" / "spain" / f"{os.getpid()}.json").exists()
    held.close()


def test_claim_on_the_same_route_registers_once(state):
    held = other_process(state, 111, "direct")
    t.claim_key()
    t.claim_key()  # a second client in one process must not deadlock on its own lock
    mine = json.loads((state / "keys" / "spain" / f"{os.getpid()}.json").read_text())
    assert mine["route"] == "direct"
    held.close()


def test_mixed_routes():
    users = [{"route": "direct"}, {"route": "socks5://h:1"}]
    assert t.mixed_routes("direct", users) == [{"route": "socks5://h:1"}]


def test_sweep_keeps_live_and_named_caches(state):
    dead = state / "spain-mcp-123.session"
    live = state / "spain-mcp-456.session"
    named = state / "spain-mcp.session"
    for path in (dead, live, named):
        path.write_text("x")
        path.with_suffix(".lock").write_text("")
    handle = live.with_suffix(".lock").open("a")
    fcntl.flock(handle, fcntl.LOCK_EX)

    found = t.sweep_caches(dry_run=True)
    assert dead in found and live not in found and named not in found
    assert dead.exists()  # a dry run deletes nothing

    t.sweep_caches()
    assert not dead.exists() and not dead.with_suffix(".lock").exists()
    assert live.exists() and named.exists()
    handle.close()


def test_describe_code_says_where_it_went():
    app = t.describe_code(types.auth.SentCodeTypeApp(length=5))
    assert "5-digit" in app and "not by SMS" in app and "--qr" in app
    assert "SMS" in t.describe_code(types.auth.SentCodeTypeSms(length=5))
    mail = t.describe_code(types.auth.SentCodeTypeEmailCode(email_pattern="p***@gmail.com", length=6))
    assert "p***@gmail.com" in mail


def test_doctor_and_resend_flags():
    args = t.build_parser().parse_args(["doctor", "--fix", "--offline", "--timeout", "5"])
    assert args.fix and args.files_only and args.timeout == 5
    assert t.build_parser().parse_args(["login", "--resend"]).resend
