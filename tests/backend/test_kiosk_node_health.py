"""Kiosk node health = connectivity AND functionality.

A tool/MCP node must read *degraded* (not green *healthy*) when it is reachable
but not fully functional — the concrete case: an MCP server whose backing
startup plugin failed to load (the twin_adapter regression). Covers the backend
`MCPManager.get_status()` health synthesis, the `_impaired_servers`
binding, and the `api.lifecycle` plugin-status recording that makes a failed
load observable.
"""

from unittest.mock import patch

import pytest

from services.mcp_client import (
    MCPManager,
    MCPServerConfig,
    MCPServerState,
    MCPTransportType,
)


def _state(
    connected: bool,
    n_tools: int = 1,
    n_discovered: int | None = None,
    transport=MCPTransportType.STREAMABLE_HTTP,
):
    # n_discovered defaults to n_tools; set it higher than n_tools to model a
    # server that DISCOVERED tools but had them all filtered out (prompt_tools /
    # override) — that must stay healthy, not falsely degraded.
    if n_discovered is None:
        n_discovered = n_tools
    return MCPServerState(
        config=MCPServerConfig(name="x", transport=transport),
        connected=connected,
        tools=[object()] * n_tools,
        all_discovered_tools=[object()] * n_discovered,
    )


def _mgr(states: dict[str, MCPServerState]) -> MCPManager:
    m = MCPManager.__new__(MCPManager)  # bypass heavy __init__
    m._servers = states
    m._tool_index = {}
    return m


def _health(status: dict, name: str) -> dict:
    return next(s for s in status["servers"] if s["name"] == name)


class TestGetStatusHealth:
    @pytest.mark.unit
    def test_connected_with_tools_is_healthy(self):
        st = _mgr({"weather": _state(True, 3)}).get_status()
        assert _health(st, "weather")["health"] == "healthy"

    @pytest.mark.unit
    def test_disconnected_is_down(self):
        st = _mgr({"weather": _state(False, 3)}).get_status()
        assert _health(st, "weather")["health"] == "down"

    @pytest.mark.unit
    def test_discovered_zero_tools_is_degraded(self):
        info = _health(_mgr({"empty": _state(True, 0)}).get_status(), "empty")
        assert info["health"] == "degraded"
        assert info["impaired_code"] == "no_tools"

    @pytest.mark.unit
    def test_filtered_to_zero_tools_stays_healthy(self):
        # Discovered 3 tools but the active-tool filter matched none → the
        # transport is fine, so NOT degraded (the false-alarm F2 guards against).
        info = _health(_mgr({"srv": _state(True, n_tools=0, n_discovered=3)}).get_status(), "srv")
        assert info["health"] == "healthy"
        assert "impaired_code" not in info

    @pytest.mark.unit
    def test_federation_zero_tools_exempt(self):
        # Federation servers manage their single tool out of band → not degraded.
        st = _mgr({"peer": _state(True, 0, transport=MCPTransportType.FEDERATION)}).get_status()
        assert _health(st, "peer")["health"] == "healthy"

    @pytest.mark.unit
    def test_failed_backing_plugin_marks_server_degraded(self):
        mgr = _mgr({"twin": _state(True, 2)})  # MCP up, 2 tools → would be healthy
        with (
            patch("services.mcp_client.settings.plugin_mcp_bindings", "twin_adapter=twin"),
            patch("api.lifecycle.failed_plugins", return_value=["twin_adapter.plugin:register"]),
        ):
            info = _health(mgr.get_status(), "twin")
        assert info["health"] == "degraded"  # connected, but backing plugin dead
        assert info["impaired_code"] == "plugin_failed"

    @pytest.mark.unit
    def test_full_spec_as_binding_prefix_also_matches(self):
        # `=` delimiter is robust: the operator may paste the full plugin spec
        # (which contains a colon) as the left side — startswith still matches.
        mgr = _mgr({"twin": _state(True, 2)})
        with (
            patch(
                "services.mcp_client.settings.plugin_mcp_bindings",
                "twin_adapter.plugin:register=twin",
            ),
            patch("api.lifecycle.failed_plugins", return_value=["twin_adapter.plugin:register"]),
        ):
            assert _health(mgr.get_status(), "twin")["health"] == "degraded"

    @pytest.mark.unit
    def test_binding_no_failure_stays_healthy(self):
        mgr = _mgr({"twin": _state(True, 2)})
        with (
            patch("services.mcp_client.settings.plugin_mcp_bindings", "twin_adapter=twin"),
            patch("api.lifecycle.failed_plugins", return_value=[]),  # plugin loaded fine
        ):
            assert _health(mgr.get_status(), "twin")["health"] == "healthy"

    @pytest.mark.unit
    def test_empty_bindings_is_noop(self):
        # Public default (no bindings) → the plugin path never degrades a server.
        mgr = _mgr({"twin": _state(True, 2)})
        with patch("services.mcp_client.settings.plugin_mcp_bindings", ""):
            assert _health(mgr.get_status(), "twin")["health"] == "healthy"


class TestPluginStatusRecording:
    @pytest.mark.unit
    async def test_failed_plugin_recorded_and_listed(self):
        import api.lifecycle as lc

        lc._plugin_status.clear()
        await lc._load_one_plugin("no_such_module_xyz:register")
        assert lc._plugin_status["no_such_module_xyz:register"]["ok"] is False
        assert "no_such_module_xyz:register" in lc.failed_plugins()

    @pytest.mark.unit
    async def test_successful_plugin_recorded_ok(self):
        import api.lifecycle as lc

        lc._plugin_status.clear()
        await lc._load_one_plugin("os:getcwd")  # real, no-arg callable
        assert lc._plugin_status["os:getcwd"]["ok"] is True
        assert "os:getcwd" not in lc.failed_plugins()
