"""
Camera Controller for Renfield Satellite

Captures JPEG snapshots via rpicam-still subprocess.
Used for visual queries — snapshot taken at wakeword detection,
sent alongside transcription to backend for Vision-LLM processing.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Optional


class CameraController:
    """
    Camera controller using rpicam-still subprocess.

    Follows the same pattern as DisplayController/LEDController:
    open() -> bool for initialization, close() for cleanup,
    graceful degradation when hardware is not available.
    """

    def __init__(self, resolution: str = "1280x720", quality: int = 85):
        self.resolution = resolution
        self.quality = quality
        self._available = False
        self._tmp_dir: Optional[str] = None

    def open(self) -> bool:
        """Check if rpicam-still is available on this system."""
        if shutil.which("rpicam-still") is None:
            print("Camera: rpicam-still not found")
            return False

        self._tmp_dir = tempfile.mkdtemp(prefix="renfield-cam-")
        self._available = True
        print(f"Camera initialized (resolution={self.resolution}, quality={self.quality})")
        return True

    def close(self):
        """Clean up temporary directory."""
        if self._tmp_dir:
            try:
                import shutil as _shutil
                _shutil.rmtree(self._tmp_dir, ignore_errors=True)
            except Exception:
                pass
            self._tmp_dir = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def capture(self) -> Optional[bytes]:
        """
        Capture a JPEG snapshot.

        Runs rpicam-still in a subprocess (non-blocking).
        Returns JPEG bytes on success, None on failure.
        """
        if not self._available or not self._tmp_dir:
            return None

        output_path = str(Path(self._tmp_dir) / "snapshot.jpg")
        width, height = self.resolution.split("x")

        cmd = [
            "rpicam-still",
            "--immediate",
            "--nopreview",
            "--width", width,
            "--height", height,
            "--quality", str(self.quality),
            "-o", output_path,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

            if proc.returncode != 0:
                err_msg = stderr.decode(errors="replace").strip() if stderr else "unknown error"
                print(f"Camera capture failed (rc={proc.returncode}): {err_msg}")
                return None

            path = Path(output_path)
            if not path.exists():
                print("Camera capture failed: output file not created")
                return None

            jpeg_bytes = path.read_bytes()
            print(f"Camera captured {len(jpeg_bytes)} bytes")
            return jpeg_bytes

        except asyncio.TimeoutError:
            print("Camera capture timed out (10s)")
            return None
        except Exception as e:
            print(f"Camera capture error: {e}")
            return None
