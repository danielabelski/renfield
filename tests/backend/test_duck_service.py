"""Duck-on-listen (Phase 4) — DuckService unit tests.

Ducks room DLNA media while a satellite listens, restores on turn end. All paths
are best-effort; flag-off is a no-op. docs/design/speaker-enrollment-redesign.md.
"""
from __future__ import annotations

import pytest

from ha_glue.services import duck_service as ds_mod
from ha_glue.services.duck_service import DuckService

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class FakeMcp:
    """Minimal MCPManager stand-in: tracks set_volume, serves get_volume."""

    def __init__(self, volume: int):
        self.volume = volume
        self.sets: list[int] = []

    async def execute_tool(self, name: str, args: dict):
        if name == "mcp.dlna.get_volume":
            return {"volume": self.volume}
        if name == "mcp.dlna.set_volume":
            self.volume = args["volume"]
            self.sets.append(args["volume"])
            return {"ok": True}
        return {}


@pytest.fixture
def _on(monkeypatch):
    from ha_glue.utils.config import ha_glue_settings
    monkeypatch.setattr(ha_glue_settings, "duck_on_listen_enabled", True)
    monkeypatch.setattr(ha_glue_settings, "duck_on_listen_volume", 20)
    monkeypatch.setattr(ha_glue_settings, "duck_on_listen_max_seconds", 30.0)


def _wire(monkeypatch, mcp, renderer):
    monkeypatch.setattr(ds_mod, "_get_mcp_manager", lambda: mcp)
    monkeypatch.setattr(DuckService, "_playing_renderer", staticmethod(lambda room_id: renderer))


class TestDuck:
    async def test_ducks_then_restores(self, monkeypatch, _on):
        mcp = FakeMcp(volume=80)
        _wire(monkeypatch, mcp, "HiFiBerry Wohnzimmer")
        svc = DuckService()

        await svc.duck_room(5, "Wohnzimmer")
        assert mcp.volume == 20            # ducked
        assert mcp.sets == [20]

        await svc.restore_room(5)
        assert mcp.volume == 80            # restored to the stashed original
        assert mcp.sets == [20, 80]

    async def test_noop_when_nothing_playing(self, monkeypatch, _on):
        mcp = FakeMcp(volume=80)
        _wire(monkeypatch, mcp, None)      # no renderer playing → no echo source
        svc = DuckService()
        await svc.duck_room(5, "Wohnzimmer")
        assert mcp.sets == []              # never touched the volume
        await svc.restore_room(5)          # restore of an un-ducked room is a no-op
        assert mcp.sets == []

    async def test_idempotent_double_duck(self, monkeypatch, _on):
        mcp = FakeMcp(volume=80)
        _wire(monkeypatch, mcp, "R")
        svc = DuckService()
        await svc.duck_room(5)
        await svc.duck_room(5)             # second listener / repeat transition
        assert mcp.sets == [20]            # ducked once; original not overwritten
        await svc.restore_room(5)
        assert mcp.volume == 80

    async def test_skip_when_already_below_duck_level(self, monkeypatch, _on):
        mcp = FakeMcp(volume=15)           # already quieter than the duck target 20
        _wire(monkeypatch, mcp, "R")
        svc = DuckService()
        await svc.duck_room(5)
        assert mcp.sets == []              # nothing to do

    async def test_flag_off_is_noop(self, monkeypatch):
        from ha_glue.utils.config import ha_glue_settings
        monkeypatch.setattr(ha_glue_settings, "duck_on_listen_enabled", False)
        mcp = FakeMcp(volume=80)
        _wire(monkeypatch, mcp, "R")
        svc = DuckService()
        await svc.duck_room(5)
        assert mcp.sets == []

    async def test_none_room_id_is_noop(self, monkeypatch, _on):
        mcp = FakeMcp(volume=80)
        _wire(monkeypatch, mcp, "R")
        svc = DuckService()
        await svc.duck_room(None)
        await svc.restore_room(None)
        assert mcp.sets == []

    async def test_safety_timeout_restores(self, monkeypatch, _on):
        # The drop-path backstop: if IDLE never arrives, the safety task must
        # restore. Regression guard for the self-cancel bug (the safety task
        # routing restore through its own .cancel() aborted the set_volume,
        # leaving media stuck ducked).
        import asyncio

        from ha_glue.utils.config import ha_glue_settings
        monkeypatch.setattr(ha_glue_settings, "duck_on_listen_max_seconds", 0.05)
        mcp = FakeMcp(volume=80)
        _wire(monkeypatch, mcp, "R")
        svc = DuckService()
        await svc.duck_room(5)
        assert mcp.volume == 20
        await asyncio.sleep(0.15)      # let the safety task fire
        assert mcp.volume == 80        # restored, not stuck ducked
        assert 5 not in svc._ducked
