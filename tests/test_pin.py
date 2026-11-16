"""The exit-address pin.

A key Telegram sees from two addresses at once is revoked for good, and a VPN
that fails over changes the address under a route label that stays the same.
So the key is pinned to one exit address: nothing connects from another one
until a person moves the pin, and the check runs before Telegram sees a packet.
"""

import argparse
import asyncio
import json
from types import SimpleNamespace

import pytest

import topoli_user as t


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(t, "SESSION_STRING", tmp_path / "spain.authkey")
    monkeypatch.setattr(t, "ACCOUNT", "spain")
    monkeypatch.setattr(t, "_CLAIMED", set())
    monkeypatch.setattr(t, "_HELD", [])
    monkeypatch.delenv("TOPOLI_IP_GUARD", raising=False)
    monkeypatch.delenv("TOPOLI_PROXY", raising=False)
    for var in ("all_proxy", "https_proxy", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    here = {"ip": "203.0.113.7"}
    monkeypatch.setattr(t, "echo_once", lambda host, path, timeout: here["ip"])
    return here


class FakeClient:
    def __init__(self, location: str | None = "London, United Kingdom", authorized: bool = True):
        self.connected = False
        self.connects = 0
        self.location = location
        self.authorized = authorized

    async def connect(self):
        self.connects += 1
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def __call__(self, request):
        region, _, country = (self.location or "").partition(", ")
        here = SimpleNamespace(current=True, region=region, country=country)
        return SimpleNamespace(authorizations=[SimpleNamespace(current=False), here])


def the_pin() -> dict:
    pin = t.read_pin()
    assert pin is not None
    return pin


def args(**kw):
    return argparse.Namespace(json=False, accept=False, force=False, **kw)


def test_the_pin_sits_beside_the_key(pinned, tmp_path):
    assert t.exit_ip_file() == tmp_path / "spain.exitip"


def test_first_connection_pins_address_and_location(pinned):
    cli = FakeClient()
    asyncio.run(t.gated_connect(cli))
    pin = the_pin()
    assert pin["ip"] == "203.0.113.7"
    assert pin["location"] == "London, United Kingdom"


def test_same_address_connects(pinned):
    t.write_pin("203.0.113.7", "London, United Kingdom")
    cli = FakeClient()
    asyncio.run(t.gated_connect(cli))
    assert cli.connects == 1


def test_a_changed_address_stops_before_connecting(pinned):
    t.write_pin("198.51.100.1", "London, United Kingdom")
    cli = FakeClient()
    with pytest.raises(SystemExit):
        asyncio.run(t.gated_connect(cli))
    assert cli.connects == 0  # Telegram never saw the new address
    assert the_pin()["ip"] == "198.51.100.1"  # and the pin did not move by itself


def test_a_different_location_disconnects(pinned):
    t.write_pin("203.0.113.7", "Vienna, Austria")
    cli = FakeClient("London, United Kingdom")
    with pytest.raises(SystemExit):
        asyncio.run(t.gated_connect(cli))
    assert not cli.is_connected()


def test_a_pin_without_location_learns_it_and_keeps_its_time(pinned):
    t.write_pin("203.0.113.7", pinned="2026-01-01T00:00:00+00:00")
    asyncio.run(t.gated_connect(FakeClient("London, United Kingdom")))
    assert the_pin()["location"] == "London, United Kingdom"
    assert the_pin()["pinned"] == "2026-01-01T00:00:00+00:00"


def test_an_unknown_location_does_not_block(pinned):
    t.write_pin("203.0.113.7", "London, United Kingdom")
    cli = FakeClient(location=None)
    asyncio.run(t.gated_connect(cli))
    assert cli.is_connected()


def test_a_logged_out_key_is_not_pinned_on_connect(pinned):
    asyncio.run(t.gated_connect(FakeClient(authorized=False)))
    assert t.read_pin() is None


def test_guard_off_skips_the_probe(pinned, monkeypatch):
    monkeypatch.setenv("TOPOLI_IP_GUARD", "off")
    monkeypatch.setattr(t, "echo_once", lambda *a: pytest.fail("probed with the guard off"))
    t.write_pin("198.51.100.1", "London, United Kingdom")
    cli = FakeClient()
    asyncio.run(t.gated_connect(cli))
    assert cli.connects == 1


def test_the_probe_falls_back_and_then_gives_up(pinned, monkeypatch):
    tried = []

    def flaky(host, path, timeout):
        tried.append(host)
        if host != t.IP_ECHOES[-1][0]:
            raise OSError("refused")
        return "203.0.113.9"

    monkeypatch.setattr(t, "echo_once", flaky)
    assert t.probe_exit_ip() == "203.0.113.9"
    assert tried == [h for h, _ in t.IP_ECHOES]

    monkeypatch.setattr(t, "echo_once", lambda *a: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(SystemExit):
        t.probe_exit_ip()


def test_a_garbled_pin_counts_as_none(pinned):
    t.exit_ip_file().write_text("{nope")
    assert t.read_pin() is None


def test_pin_ip_shows_without_moving(pinned, capsys):
    t.write_pin("198.51.100.1", "London, United Kingdom")
    asyncio.run(t.cmd_pin_ip(None, args()))
    assert "DIFFERS" in capsys.readouterr().out
    assert the_pin()["ip"] == "198.51.100.1"


def test_pin_ip_accept_moves_the_pin(pinned):
    t.write_pin("198.51.100.1", "London, United Kingdom")
    asyncio.run(t.cmd_pin_ip(None, _accept()))
    pin = the_pin()
    assert pin["ip"] == "203.0.113.7"
    assert pin["location"] is None  # relearnt on the next connection


def _accept(force: bool = False):
    a = args()
    a.accept, a.force = True, force
    return a


def test_pin_ip_accept_refuses_while_the_key_is_in_use(pinned, monkeypatch):
    t.write_pin("198.51.100.1")
    monkeypatch.setattr(t, "key_users", lambda account=None: [{"pid": 4242, "route": "direct", "command": "mcp"}])
    with pytest.raises(SystemExit):
        asyncio.run(t.cmd_pin_ip(None, _accept()))
    assert the_pin()["ip"] == "198.51.100.1"
    asyncio.run(t.cmd_pin_ip(None, _accept(force=True)))
    assert the_pin()["ip"] == "203.0.113.7"


def test_pin_ip_json(pinned, capsys):
    t.write_pin("203.0.113.7", "London, United Kingdom")
    a = args()
    a.json = True
    asyncio.run(t.cmd_pin_ip(None, a))
    assert json.loads(capsys.readouterr().out)["matches"] is True


def test_a_dropped_listener_reconnects_through_the_gate(pinned, monkeypatch):
    monkeypatch.setattr(t, "RECONNECT_CHECK", 0.01)
    t.write_pin("203.0.113.7", "London, United Kingdom")
    cli = FakeClient()  # starts disconnected, as after a drop
    with pytest.raises(TimeoutError):
        asyncio.run(t.next_message(cli, asyncio.Queue(), 0.05))
    assert cli.connects >= 1


def test_a_dropped_listener_does_not_reconnect_from_a_new_address(pinned, monkeypatch):
    monkeypatch.setattr(t, "RECONNECT_CHECK", 0.01)
    t.write_pin("198.51.100.1", "London, United Kingdom")
    cli = FakeClient()
    with pytest.raises(SystemExit):
        asyncio.run(t.next_message(cli, asyncio.Queue(), 1))
    assert cli.connects == 0


def test_pin_ip_is_not_an_agent_tool():
    assert "pin_ip" not in {spec["name"] for spec in t.tool_specs()}
    assert "pin-ip" in t.NOT_TOOLS
