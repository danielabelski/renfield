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

import asyncio

import pytest

import api.websocket.kiosk_handler as kiosk
from api.websocket.chat_handler import (
    INTERNAL_SUBSYSTEM_LABELS,
    _MAX_SUBSYSTEMS_PER_TURN,
    _extract_subsystems_used,
)
from api.websocket.kiosk_handler import broadcast_kiosk_event, build_kiosk_snapshot


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, msg):
        self.sent.append(msg)


async def _drain_fanout():
    """broadcast_kiosk_event enqueues; a single consumer drains the queue. Wait
    for every queued event to be fully processed before asserting."""
    if kiosk._event_queue is not None:
        await kiosk._event_queue.join()


@pytest.fixture(autouse=True)
def _clear_clients():
    # Reset the lazily-created queue+consumer so each test's event loop gets a
    # fresh pipeline (an asyncio.Queue/Task is bound to the loop it was made in).
    kiosk._kiosk_clients.clear()
    kiosk._event_queue = None
    kiosk._consumer_task = None
    kiosk._consumer_loop = None
    yield
    if kiosk._consumer_task is not None:
        kiosk._consumer_task.cancel()
    kiosk._kiosk_clients.clear()
    kiosk._event_queue = None
    kiosk._consumer_task = None
    kiosk._consumer_loop = None


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
    assert _extract_subsystems_used([("internal.knowledge_search", {})]) == ["knowledge"]
    assert _extract_subsystems_used([("internal.list_my_memories", {})]) == ["knowledge"]
    assert _extract_subsystems_used([("internal.device_controls", {})]) == ["homeassistant"]
    assert _extract_subsystems_used([("internal.announce_in_room", {})]) == ["homeassistant"]
    assert _extract_subsystems_used([("internal.presence_history", {})]) == ["presence"]
    assert _extract_subsystems_used([("internal.play_radio", {})]) == ["media"]
    assert _extract_subsystems_used([("internal.weather_widget", {})]) == ["weather"]


@pytest.mark.backend
@pytest.mark.unit
def test_extract_unknown_internal_tool_skipped():
    assert _extract_subsystems_used([("internal.something_new", {})]) == []
    # Pure Gen-UI formatting tools touch no subsystem → no pulse.
    assert _extract_subsystems_used([("internal.render_table", {})]) == []
    assert _extract_subsystems_used([("internal.render_list", {})]) == []


@pytest.mark.backend
@pytest.mark.unit
def test_internal_only_subsystem_ids_match_frontend_synthetic_nodes():
    """Coupling guard: every subsystem id an internal tool can pulse is EITHER a
    real MCP server (homeassistant / weather) OR one of the three internal-only
    ids the kiosk renders synthetic nodes for. If someone adds a mapping to a new
    internal-only id, they must add a matching node to the frontend
    INTERNAL_SUBSYSTEM_NODES (components/kiosk/useKioskModel.ts) or its pulse
    lights nothing."""
    real_mcp_servers = {"homeassistant", "weather"}
    frontend_synthetic_nodes = {"knowledge", "presence", "media"}
    values = set(INTERNAL_SUBSYSTEM_LABELS.values())
    assert values == real_mcp_servers | frontend_synthetic_nodes
    assert (values - real_mcp_servers) == frontend_synthetic_nodes


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


@pytest.mark.backend
@pytest.mark.asyncio
async def test_broadcast_prunes_stalled_socket(monkeypatch):
    """A socket whose send_json HANGS (backpressure — never raises) is pruned via
    the wait_for timeout, and a healthy peer is still delivered. This is the core
    of the non-blocking fix; without the timeout the consumer would hang here."""
    monkeypatch.setattr(kiosk, "_SEND_TIMEOUT_SECONDS", 0.05)

    class _StallWS(_FakeWS):
        async def send_json(self, msg):
            await asyncio.sleep(10)  # never completes within the timeout

    good, stalled = _FakeWS(), _StallWS()
    kiosk._kiosk_clients.update({good, stalled})
    await broadcast_kiosk_event({"type": "satellite_state"})
    await _drain_fanout()
    assert stalled not in kiosk._kiosk_clients  # pruned on timeout
    assert good in kiosk._kiosk_clients
    assert len(good.sent) == 1


@pytest.mark.backend
@pytest.mark.asyncio
async def test_hydrate_before_register(monkeypatch):
    """The socket must receive its snapshot BEFORE joining _kiosk_clients — else
    the consumer could send a delta on the same socket concurrently with the
    snapshot send, or the client would apply a delta before hydrating."""
    from unittest.mock import AsyncMock

    from fastapi import WebSocketDisconnect

    monkeypatch.setattr(
        kiosk, "authenticate_websocket",
        AsyncMock(return_value={"authenticated": True, "auth_skipped": True}),
    )
    monkeypatch.setattr(
        kiosk, "build_kiosk_snapshot", AsyncMock(return_value={"type": "snapshot"})
    )

    class _RecordingWS:
        def __init__(self):
            self.app = _FakeApp()
            self.sent: list[dict] = []
            self.registered_at_send: bool | None = None

        async def accept(self):
            pass

        async def send_json(self, msg):
            # Capture whether this socket was broadcast-eligible when hydrated.
            self.registered_at_send = self in kiosk._kiosk_clients
            self.sent.append(msg)

        async def receive_text(self):
            raise WebSocketDisconnect()

        async def close(self, **kwargs):
            pass

    ws = _RecordingWS()
    await kiosk.kiosk_live(ws, token=None)

    assert ws.sent == [{"type": "snapshot"}]
    assert ws.registered_at_send is False  # snapshot sent while UNREGISTERED
    assert ws not in kiosk._kiosk_clients  # disconnect cleaned up


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
