"""Backend unit tests for the kiosk push hub (Phase 1a).

Covers:
  * ``_extract_subsystems_used`` — mcp-vs-internal mapping, the internal
    allowlist, dedup, order-preservation, the cap, and the empty case.
  * ``broadcast_kiosk_event`` — fire-and-forget: an empty registry is a no-op,
    a broken socket is pruned (not raised), a good socket receives the event.
  * ``build_kiosk_snapshot`` — the snapshot dict shape / content-free contract
    with all sources stubbed out (no DB / no app state required).

Run on the .159 build box (CI is non-functional): see
``memory/reference_test_runner_159.md``.
"""
from __future__ import annotations

import pytest

import api.websocket.kiosk_handler as kiosk
from api.websocket.chat_handler import (
    INTERNAL_SUBSYSTEM_LABELS,
    _MAX_SUBSYSTEMS_PER_TURN,
    _extract_subsystems_used,
)
from api.websocket.kiosk_handler import broadcast_kiosk_event, build_kiosk_snapshot


import asyncio


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, msg):
        self.sent.append(msg)


async def _drain_fanout():
    """broadcast_kiosk_event now schedules the fan-out on a background task (so a
    slow socket can't block the caller); await those tasks before asserting."""
    while kiosk._fanout_tasks:
        await asyncio.gather(*list(kiosk._fanout_tasks), return_exceptions=True)


@pytest.fixture(autouse=True)
def _clear_clients():
    kiosk._kiosk_clients.clear()
    yield
    kiosk._kiosk_clients.clear()


# --------------------------------------------------------------------------
# _extract_subsystems_used
# --------------------------------------------------------------------------


@pytest.mark.backend
@pytest.mark.unit
def test_extract_mcp_tool_maps_to_server():
    assert _extract_subsystems_used(
        [("mcp.homeassistant.turn_on", {})]
    ) == ["homeassistant"]


@pytest.mark.backend
@pytest.mark.unit
def test_extract_internal_tool_uses_allowlist():
    assert _extract_subsystems_used(
        [("internal.knowledge_search", {})]
    ) == ["knowledge"]
    assert _extract_subsystems_used(
        [("internal.device_controls", {})]
    ) == ["homeassistant"]


@pytest.mark.backend
@pytest.mark.unit
def test_extract_unknown_internal_tool_skipped():
    assert _extract_subsystems_used([("internal.something_new", {})]) == []


@pytest.mark.backend
@pytest.mark.unit
def test_extract_dedup_preserves_first_seen_order():
    results = [
        ("mcp.homeassistant.turn_on", {}),
        ("internal.device_action", {}),  # also -> homeassistant (dupe)
        ("mcp.weather.get_weather", {}),
        ("internal.knowledge_search", {}),  # -> knowledge
    ]
    assert _extract_subsystems_used(results) == [
        "homeassistant",
        "weather",
        "knowledge",
    ]


@pytest.mark.backend
@pytest.mark.unit
def test_extract_caps_at_five_distinct_subsystems():
    results = [(f"mcp.server{i}.tool", {}) for i in range(8)]
    out = _extract_subsystems_used(results)
    assert len(out) == _MAX_SUBSYSTEMS_PER_TURN == 5
    assert out == ["server0", "server1", "server2", "server3", "server4"]


@pytest.mark.backend
@pytest.mark.unit
def test_extract_empty_and_malformed_are_safe():
    assert _extract_subsystems_used([]) == []
    # Malformed / non-tool entries are ignored, not raised.
    assert _extract_subsystems_used(
        [(), ("", {}), ("plain_intent", {}), ("mcp.", {}), ("mcp.only", {})]
    ) == []


@pytest.mark.backend
@pytest.mark.unit
def test_allowlist_values_are_known_subsystem_ids():
    # Guard: every allowlisted internal tool maps to a non-empty id.
    assert all(v for v in INTERNAL_SUBSYSTEM_LABELS.values())


# --------------------------------------------------------------------------
# broadcast_kiosk_event
# --------------------------------------------------------------------------


@pytest.mark.backend
@pytest.mark.asyncio
async def test_broadcast_empty_registry_is_noop():
    # No clients registered — must return without error.
    await broadcast_kiosk_event({"type": "satellite_state"})
    assert kiosk._kiosk_clients == set()


@pytest.mark.backend
@pytest.mark.asyncio
async def test_broadcast_delivers_to_connected_clients():
    a, b = _FakeWS(), _FakeWS()
    kiosk._kiosk_clients.update({a, b})
    event = {"type": "turn_activity", "role": "smart_home", "subsystems": ["homeassistant"]}
    await broadcast_kiosk_event(event)
    await _drain_fanout()
    assert a.sent == [event]
    assert b.sent == [event]


@pytest.mark.backend
@pytest.mark.asyncio
async def test_broadcast_prunes_broken_socket_not_raises():
    class _BrokenWS(_FakeWS):
        async def send_json(self, msg):
            raise RuntimeError("closed")

    good, bad = _FakeWS(), _BrokenWS()
    kiosk._kiosk_clients.update({good, bad})
    await broadcast_kiosk_event({"type": "satellite_state"})
    await _drain_fanout()
    assert bad not in kiosk._kiosk_clients  # pruned
    assert good in kiosk._kiosk_clients
    assert len(good.sent) == 1  # good socket still delivered


# --------------------------------------------------------------------------
# build_kiosk_snapshot (shape + content-free)
# --------------------------------------------------------------------------


class _FakeState:
    def __init__(self):
        self.mcp_manager = None
        self.agent_router = None


class _FakeApp:
    def __init__(self):
        self.state = _FakeState()


@pytest.mark.backend
@pytest.mark.asyncio
async def test_snapshot_shape_with_all_sources_down(monkeypatch):
    """Every source failing must still yield a fully-shaped, content-free
    snapshot (a fresh tab always hydrates)."""
    # Neutralize the DB-backed sections so no database is required.
    async def _boom(*a, **k):
        raise RuntimeError("no db in unit test")

    # Force the tool-health / activity / peers / weather / now-playing sources
    # to their degraded empty branches by making their imports/queries fail.
    monkeypatch.setattr(kiosk, "AsyncSessionLocal", _boom, raising=True)

    snap = await build_kiosk_snapshot(_FakeApp())

    assert snap["type"] == "snapshot"
    assert isinstance(snap["at"], str)
    # All expected top-level keys present with safe empty defaults.
    for key in (
        "satellites",
        "presence",
        "mcp",
        "tool_health",
        "roles",
        "activity",
        "peers",
        "weather",
        "now_playing",
    ):
        assert key in snap
    assert snap["presence"] == {"rooms": [], "people_present": 0, "occupied_rooms": 0}
    assert snap["tool_health"] == []
    assert snap["activity"] == []
    assert snap["peers"] == []
    assert snap["weather"] is None
    assert snap["now_playing"] == []


@pytest.mark.backend
@pytest.mark.asyncio
async def test_snapshot_roles_content_free(monkeypatch):
    """Roles ride straight off the router; the shape is names + reach lists
    only (no message content)."""
    async def _boom(*a, **k):
        raise RuntimeError("no db")

    monkeypatch.setattr(kiosk, "AsyncSessionLocal", _boom, raising=True)

    class _Role:
        name = "smart_home"
        description = {"de": "Haussteuerung", "en": "Smart home"}
        mcp_servers = ["homeassistant"]
        internal_tools = ["internal.device_action"]
        has_agent_loop = True

    class _Router:
        roles = {"smart_home": _Role()}

    app = _FakeApp()
    app.state.agent_router = _Router()

    snap = await build_kiosk_snapshot(app)
    assert snap["roles"] == [
        {
            "name": "smart_home",
            "description": {"de": "Haussteuerung", "en": "Smart home"},
            "mcp_servers": ["homeassistant"],
            "internal_tools": ["internal.device_action"],
            "has_agent_loop": True,
        }
    ]
