"""
Satellite Camera Tests

Tests for the camera controller and visual queries integration:
- CameraController open/close/capture
- CameraConfig loading
- Wakeword snapshot trigger
- audio_end image attachment
"""

import os
import sys

# Add satellite source to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "satellite"))

import asyncio
import base64
import json
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ============================================================================
# CameraController Tests
# ============================================================================

class TestCameraController:
    """Tests for CameraController hardware abstraction"""

    @pytest.mark.satellite
    def test_camera_init_defaults(self):
        """Test: CameraController initializes with defaults"""
        from renfield_satellite.hardware.camera import CameraController

        cam = CameraController()
        assert cam.resolution == "1280x720"
        assert cam.quality == 85
        assert not cam.available

    @pytest.mark.satellite
    def test_camera_init_custom(self):
        """Test: CameraController accepts custom settings"""
        from renfield_satellite.hardware.camera import CameraController

        cam = CameraController(resolution="1920x1080", quality=95)
        assert cam.resolution == "1920x1080"
        assert cam.quality == 95

    @pytest.mark.satellite
    def test_camera_open_no_rpicam(self):
        """Test: open() returns False if rpicam-still not found"""
        from renfield_satellite.hardware.camera import CameraController

        cam = CameraController()
        with patch("shutil.which", return_value=None):
            assert cam.open() is False
        assert not cam.available

    @pytest.mark.satellite
    def test_camera_open_with_rpicam(self):
        """Test: open() returns True if rpicam-still is available"""
        from renfield_satellite.hardware.camera import CameraController

        cam = CameraController()
        with patch("shutil.which", return_value="/usr/bin/rpicam-still"):
            assert cam.open() is True
        assert cam.available
        assert cam._tmp_dir is not None
        cam.close()

    @pytest.mark.satellite
    def test_camera_close_cleanup(self):
        """Test: close() cleans up temp directory"""
        from renfield_satellite.hardware.camera import CameraController

        cam = CameraController()
        with patch("shutil.which", return_value="/usr/bin/rpicam-still"):
            cam.open()
        tmp = cam._tmp_dir
        assert os.path.isdir(tmp)
        cam.close()
        assert not cam.available
        assert cam._tmp_dir is None

    @pytest.mark.satellite
    @pytest.mark.asyncio
    async def test_camera_capture_not_available(self):
        """Test: capture() returns None when camera not available"""
        from renfield_satellite.hardware.camera import CameraController

        cam = CameraController()
        result = await cam.capture()
        assert result is None

    @pytest.mark.satellite
    @pytest.mark.asyncio
    async def test_camera_capture_success(self):
        """Test: capture() returns JPEG bytes on success"""
        from renfield_satellite.hardware.camera import CameraController

        cam = CameraController()
        with patch("shutil.which", return_value="/usr/bin/rpicam-still"):
            cam.open()

        # Create a fake JPEG file that rpicam-still would create
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 1000  # JPEG magic + data
        output_path = os.path.join(cam._tmp_dir, "snapshot.jpg")

        async def fake_create_subprocess_exec(*args, **kwargs):
            # Write fake JPEG to the expected output path
            with open(output_path, "wb") as f:
                f.write(fake_jpeg)
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            result = await cam.capture()

        assert result is not None
        assert result == fake_jpeg
        cam.close()

    @pytest.mark.satellite
    @pytest.mark.asyncio
    async def test_camera_capture_failure(self):
        """Test: capture() returns None on rpicam-still error"""
        from renfield_satellite.hardware.camera import CameraController

        cam = CameraController()
        with patch("shutil.which", return_value="/usr/bin/rpicam-still"):
            cam.open()

        async def fake_create_subprocess_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b"camera error"))
            proc.returncode = 1
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            result = await cam.capture()

        assert result is None
        cam.close()

    @pytest.mark.satellite
    @pytest.mark.asyncio
    async def test_camera_capture_timeout(self):
        """Test: capture() returns None on timeout"""
        from renfield_satellite.hardware.camera import CameraController

        cam = CameraController()
        with patch("shutil.which", return_value="/usr/bin/rpicam-still"):
            cam.open()

        async def slow_communicate():
            await asyncio.sleep(20)
            return (b"", b"")

        async def fake_create_subprocess_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.communicate = slow_communicate
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            result = await cam.capture()

        assert result is None
        cam.close()


# ============================================================================
# CameraConfig Tests
# ============================================================================

class TestCameraConfig:
    """Tests for CameraConfig loading"""

    @pytest.mark.satellite
    def test_camera_config_defaults(self):
        """Test: CameraConfig has correct defaults"""
        from renfield_satellite.config import CameraConfig

        cfg = CameraConfig()
        assert cfg.enabled is False
        assert cfg.resolution == "1280x720"
        assert cfg.quality == 85

    @pytest.mark.satellite
    def test_config_has_camera_field(self):
        """Test: Config includes camera field"""
        from renfield_satellite.config import Config

        cfg = Config()
        assert hasattr(cfg, "camera")
        assert cfg.camera.enabled is False

    @pytest.mark.satellite
    def test_load_config_camera_section(self, tmp_path):
        """Test: load_config() parses camera section"""
        from renfield_satellite.config import load_config

        config_file = tmp_path / "satellite.yaml"
        config_file.write_text(
            "camera:\n"
            "  enabled: true\n"
            "  resolution: '1920x1080'\n"
            "  quality: 90\n"
        )

        cfg = load_config(str(config_file))
        assert cfg.camera.enabled is True
        assert cfg.camera.resolution == "1920x1080"
        assert cfg.camera.quality == 90

    @pytest.mark.satellite
    def test_load_config_no_camera_section(self, tmp_path):
        """Test: load_config() works without camera section"""
        from renfield_satellite.config import load_config

        config_file = tmp_path / "satellite.yaml"
        config_file.write_text("satellite:\n  id: test\n")

        cfg = load_config(str(config_file))
        assert cfg.camera.enabled is False  # Default


# ============================================================================
# WebSocket send_audio_end with image Tests
# ============================================================================

class TestWebSocketAudioEndImage:
    """Tests for image parameter in send_audio_end"""

    @pytest.mark.satellite
    @pytest.mark.asyncio
    async def test_send_audio_end_without_image(self):
        """Test: send_audio_end works without image (backward compatible)"""
        from renfield_satellite.network.websocket_client import WebSocketClient

        client = WebSocketClient(satellite_id="test", room="Test")
        client._ws = AsyncMock()
        client._state = type("", (), {"value": "connected"})()

        # Override is_connected
        with patch.object(type(client), "is_connected", new_callable=lambda: property(lambda self: True)):
            await client.send_audio_end("session-1", "silence")

        # Verify the message was sent
        sent_data = json.loads(client._ws.send.call_args[0][0])
        assert sent_data["type"] == "audio_end"
        assert sent_data["session_id"] == "session-1"
        assert sent_data["reason"] == "silence"
        assert "image" not in sent_data

    @pytest.mark.satellite
    @pytest.mark.asyncio
    async def test_send_audio_end_with_image(self):
        """Test: send_audio_end includes image when provided"""
        from renfield_satellite.network.websocket_client import WebSocketClient

        client = WebSocketClient(satellite_id="test", room="Test")
        client._ws = AsyncMock()
        client._state = type("", (), {"value": "connected"})()

        test_image = base64.b64encode(b"fake-jpeg").decode()

        with patch.object(type(client), "is_connected", new_callable=lambda: property(lambda self: True)):
            await client.send_audio_end("session-1", "silence", image_b64=test_image)

        sent_data = json.loads(client._ws.send.call_args[0][0])
        assert sent_data["type"] == "audio_end"
        assert sent_data["image"] == test_image

    @pytest.mark.satellite
    @pytest.mark.asyncio
    async def test_send_audio_end_none_image_excluded(self):
        """Test: image field excluded when None"""
        from renfield_satellite.network.websocket_client import WebSocketClient

        client = WebSocketClient(satellite_id="test", room="Test")
        client._ws = AsyncMock()
        client._state = type("", (), {"value": "connected"})()

        with patch.object(type(client), "is_connected", new_callable=lambda: property(lambda self: True)):
            await client.send_audio_end("session-1", "silence", image_b64=None)

        sent_data = json.loads(client._ws.send.call_args[0][0])
        assert "image" not in sent_data
