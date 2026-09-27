"""Security review H1 (PR-B, satellite side) — enrollment PSK plumbing.

The satellite loads a per-device enrollment token (YAML / env) and presents it
in the register frame so the backend can verify it against the `satellites`
table. A non-enrolled satellite omits the field entirely (legacy frame shape).
"""
import json
import os
from unittest.mock import patch

import pytest
from renfield_satellite.config import ServerConfig, load_config
from renfield_satellite.network.websocket_client import WebSocketClient


class _AckWS:
    """Captures sent frames; replies with a register_ack."""
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)

    async def recv(self):
        return json.dumps({
            "type": "register_ack", "success": True,
            "config": {"wake_words": ["hey_renfield"], "threshold": 0.6},
            "protocol_version": "1.0",
        })


def _client(**kw):
    return WebSocketClient(satellite_id="sat-test", room="TestRoom",
                           server_url="wss://x/ws/satellite", **kw)


class TestEnrollmentConfig:
    @pytest.mark.satellite
    def test_default_is_none(self):
        assert ServerConfig().enrollment_token is None

    @pytest.mark.satellite
    def test_yaml_loads_enrollment_token(self, tmp_path):
        cfg = tmp_path / "satellite.yaml"
        cfg.write_text(
            "satellite:\n  id: sat-x\nserver:\n  enrollment_token: \"psk-abc123\"\n"
        )
        config = load_config(str(cfg))
        assert config.server.enrollment_token == "psk-abc123"

    @pytest.mark.satellite
    def test_blank_yaml_token_is_none(self, tmp_path):
        # An un-provisioned host renders enrollment_token: "" from the template;
        # that must become None so the register frame omits the field.
        cfg = tmp_path / "satellite.yaml"
        cfg.write_text("server:\n  enrollment_token: \"\"\n")
        config = load_config(str(cfg))
        assert config.server.enrollment_token is None

    @pytest.mark.satellite
    def test_env_override(self, tmp_path):
        cfg = tmp_path / "satellite.yaml"
        cfg.write_text("server:\n  enrollment_token: \"from-yaml\"\n")
        with patch.dict(os.environ, {"RENFIELD_ENROLLMENT_TOKEN": "from-env"}, clear=False):
            config = load_config(str(cfg))
        assert config.server.enrollment_token == "from-env"


class TestRegisterFrameToken:
    @pytest.mark.satellite
    async def test_register_includes_token_when_set(self):
        c = _client(enrollment_token="psk-xyz")
        c._ws = _AckWS()
        await c._register()
        frame = json.loads(c._ws.sent[0])
        assert frame["type"] == "register"
        assert frame["token"] == "psk-xyz"

    @pytest.mark.satellite
    async def test_register_omits_token_when_unset(self):
        c = _client()  # no enrollment token
        c._ws = _AckWS()
        await c._register()
        frame = json.loads(c._ws.sent[0])
        assert "token" not in frame
