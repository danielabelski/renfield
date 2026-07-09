"""Unit tests for the kiosk internal-subsystem health verdicts
(api/websocket/kiosk_data.py: compute_internal_subsystem_health + the
diff-gated push refresher).

The kiosk draws three synthetic pseudo-nodes (knowledge / presence / media) that
have no MCP server; these functions give each a real healthy/degraded/down
verdict from live backend state so a wall board surfaces an impaired subsystem
instead of a silent gray diamond.
"""
from __future__ import annotations

import pytest

import api.websocket.kiosk_data as kiosk_data
from api.websocket.kiosk_data import (
    _knowledge_health,
    _media_health,
    _presence_health,
    compute_internal_subsystem_health,
    refresh_and_push_internal_health,
)

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------ helpers
class _Sat:
    def __init__(self, authenticated: bool):
        self.authenticated = authenticated


class _Mgr:
    def __init__(self, sats: dict):
        self.satellites = sats


def _patch_presence(monkeypatch, *, enabled: bool, enrollment: bool, sats: dict):
    import ha_glue.services.satellite_manager as sm
    import ha_glue.utils.config as hcfg

    monkeypatch.setattr(hcfg.ha_glue_settings, "presence_enabled", enabled)
    monkeypatch.setattr(kiosk_data.settings, "satellite_enrollment_enabled", enrollment)
    monkeypatch.setattr(sm, "get_satellite_manager", lambda: _Mgr(sats))


def _patch_knowledge(monkeypatch, *, alive, depth, raises: bool = False):
    # _knowledge_health delegates to kb_maintenance_tool.ingest_worker_and_backlog,
    # which uses _worker_is_alive + DocumentTaskQueue.pending_count() (the LIVE
    # backlog, not the ever-growing stream length).
    import api.routes.knowledge as kn
    import services.redis_client as rc
    import services.task_queue as tq

    async def _alive():
        if raises:
            raise RuntimeError("redis down")
        return alive

    monkeypatch.setattr(kn, "_worker_is_alive", _alive)
    monkeypatch.setattr(rc, "get_redis", lambda: object())

    class _Q:
        def __init__(self, *a, **k):
            pass

        async def pending_count(self):
            return depth

    monkeypatch.setattr(tq, "DocumentTaskQueue", _Q)


# ------------------------------------------------------------------ presence
class TestPresenceHealth:
    async def test_off_when_disabled(self, monkeypatch):
        # Disabled-by-config is 'off' (muted), NOT 'down' (red/outage).
        _patch_presence(monkeypatch, enabled=False, enrollment=True, sats={})
        v = await _presence_health()
        assert v.health == "off"
        assert v.impaired_code == "presence_disabled"

    async def test_degraded_when_no_satellite(self, monkeypatch):
        _patch_presence(monkeypatch, enabled=True, enrollment=True, sats={})
        v = await _presence_health()
        assert v.health == "degraded"
        assert v.impaired_code == "presence_no_satellite"

    async def test_degraded_when_a_satellite_is_unauthenticated(self, monkeypatch):
        # The 2026-07-09 failure: an enrolled-but-unauthenticated satellite gets
        # no IRK push and silently reports nothing.
        _patch_presence(
            monkeypatch,
            enabled=True,
            enrollment=True,
            sats={"a": _Sat(True), "b": _Sat(False)},
        )
        v = await _presence_health()
        assert v.health == "degraded"
        assert v.impaired_code == "presence_satellite_unauthenticated"

    async def test_healthy_when_all_authenticated(self, monkeypatch):
        _patch_presence(
            monkeypatch,
            enabled=True,
            enrollment=True,
            sats={"a": _Sat(True), "b": _Sat(True)},
        )
        v = await _presence_health()
        assert v.health == "healthy"
        assert v.impaired_code is None

    async def test_healthy_when_enrollment_off_ignores_auth(self, monkeypatch):
        # With enrollment disabled, `authenticated` is always False in the fleet —
        # it must NOT be read as degraded (that flag is only meaningful when
        # enrollment gates the IRK push).
        _patch_presence(
            monkeypatch,
            enabled=True,
            enrollment=False,
            sats={"a": _Sat(False)},
        )
        v = await _presence_health()
        assert v.health == "healthy"


# ------------------------------------------------------------------ knowledge
class TestKnowledgeHealth:
    async def test_healthy_when_worker_alive_and_queue_shallow(self, monkeypatch):
        _patch_knowledge(monkeypatch, alive=True, depth=3)
        v = await _knowledge_health()
        assert v.health == "healthy"
        assert v.impaired_code is None

    async def test_degraded_when_worker_dead(self, monkeypatch):
        _patch_knowledge(monkeypatch, alive=False, depth=0)
        v = await _knowledge_health()
        assert v.health == "degraded"
        assert v.impaired_code == "knowledge_worker_down"

    async def test_degraded_when_queue_backed_up(self, monkeypatch):
        _patch_knowledge(
            monkeypatch,
            alive=True,
            depth=kiosk_data._KNOWLEDGE_QUEUE_DEGRADED_ABOVE + 1,
        )
        v = await _knowledge_health()
        assert v.health == "degraded"
        assert v.impaired_code == "knowledge_queue_backed_up"

    async def test_degraded_when_probe_raises(self, monkeypatch):
        _patch_knowledge(monkeypatch, alive=True, depth=0, raises=True)
        v = await _knowledge_health()
        assert v.health == "degraded"
        assert v.impaired_code == "knowledge_worker_down"


# ------------------------------------------------------------------ media
class TestMediaHealth:
    async def test_off_when_disabled(self, monkeypatch):
        import ha_glue.utils.config as hcfg

        monkeypatch.setattr(hcfg.ha_glue_settings, "media_follow_enabled", False)
        v = await _media_health()
        assert v.health == "off"
        assert v.impaired_code == "media_disabled"

    async def test_healthy_when_enabled(self, monkeypatch):
        import ha_glue.utils.config as hcfg

        monkeypatch.setattr(hcfg.ha_glue_settings, "media_follow_enabled", True)
        v = await _media_health()
        assert v.health == "healthy"


# ------------------------------------------------------------------ aggregate
class TestComputeAndPush:
    async def test_compute_returns_all_three_content_free(self, monkeypatch):
        _patch_presence(
            monkeypatch, enabled=True, enrollment=True, sats={"a": _Sat(True)}
        )
        _patch_knowledge(monkeypatch, alive=True, depth=0)
        import ha_glue.utils.config as hcfg

        monkeypatch.setattr(hcfg.ha_glue_settings, "media_follow_enabled", True)

        out = await compute_internal_subsystem_health()
        ids = {row["id"] for row in out}
        assert ids == {"presence", "knowledge", "media"}
        for row in out:
            assert set(row.keys()) == {"id", "health", "impaired_code"}
            assert row["health"] in {"healthy", "degraded", "down", "off"}

    async def test_refresh_push_is_diff_gated(self, monkeypatch):
        import api.websocket.kiosk_handler as handler

        pushed: list[dict] = []

        async def _capture(event):
            pushed.append(event)

        monkeypatch.setattr(handler, "broadcast_kiosk_event", _capture)
        kiosk_data._internal_health_last_pushed = None

        fixed = [{"id": "presence", "health": "healthy", "impaired_code": None}]

        async def _compute():
            return list(fixed)

        monkeypatch.setattr(kiosk_data, "compute_internal_subsystem_health", _compute)

        await refresh_and_push_internal_health()
        await refresh_and_push_internal_health()  # unchanged → no second push
        assert len(pushed) == 1
        assert pushed[0]["type"] == "internal_health_changed"
        assert pushed[0]["subsystems"] == fixed

        # A changed verdict pushes again.
        fixed[:] = [{"id": "presence", "health": "degraded", "impaired_code": "x"}]
        await refresh_and_push_internal_health()
        assert len(pushed) == 2

    async def test_broadcast_failure_does_not_advance_gate(self, monkeypatch):
        # A failed broadcast must NOT advance the gate, or the delta is lost
        # forever (the next tick would see "no change" and stay silent).
        import api.websocket.kiosk_handler as handler

        calls = {"n": 0}

        async def _flaky(event):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("hub down")

        monkeypatch.setattr(handler, "broadcast_kiosk_event", _flaky)
        kiosk_data._internal_health_last_pushed = None

        async def _compute():
            return [{"id": "media", "health": "off", "impaired_code": "media_disabled"}]

        monkeypatch.setattr(kiosk_data, "compute_internal_subsystem_health", _compute)

        await refresh_and_push_internal_health()  # broadcast raises → gate NOT advanced
        assert kiosk_data._internal_health_last_pushed is None
        await refresh_and_push_internal_health()  # retries, succeeds
        assert calls["n"] == 2
        assert kiosk_data._internal_health_last_pushed is not None

    async def test_reset_gate_forces_a_repush(self, monkeypatch):
        from api.websocket.kiosk_data import reset_internal_health_gate

        kiosk_data._internal_health_last_pushed = [
            {"id": "presence", "health": "healthy", "impaired_code": None}
        ]
        reset_internal_health_gate()
        assert kiosk_data._internal_health_last_pushed is None
