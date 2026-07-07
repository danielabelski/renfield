"""Tests for PR 1 of docs/design/voice-identity-wakeword-verification.md.

Covers:
- P0: the fail-loud in-process (SpeechBrain) embedding guard — the backend
  must never extract/compare/store cross-space embeddings unless a dev
  environment explicitly opts in (T1).
- C1: the binary Opus transport wire format + backend-edge decode seams,
  and the CRITICAL regression that the legacy base64-PCM path buffers
  byte-identically through the refactored manager (T2/T4).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.routes.speakers import _require_inprocess_embeddings
from ha_glue.services import opus_transport
from ha_glue.services.satellite_manager import SatelliteManager
from utils.config import settings

# =============================================================================
# C1 wire format (pure functions — no opus dependency needed)
# =============================================================================

class TestBinaryFrameFormat:
    @pytest.mark.unit
    def test_roundtrip(self):
        packets = [b"\x01\x02\x03", b"\xff" * 100]
        frame = opus_transport.build_audio_frame("sat-kitchen-123", 42, packets)
        session_id, sequence, parsed = opus_transport.parse_audio_frame(frame)
        assert session_id == "sat-kitchen-123"
        assert sequence == 42
        assert parsed == packets

    @pytest.mark.unit
    def test_sequence_wraps_uint32(self):
        frame = opus_transport.build_audio_frame("s", 2**32 + 7, [b"x"])
        _, sequence, _ = opus_transport.parse_audio_frame(frame)
        assert sequence == 7

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "data",
        [
            b"",  # empty
            b"\x01",  # too short
            b"\x02\x01aXXXX",  # unknown frame type
            b"\x01\x00\x00\x00\x00\x00",  # zero-length session id
            b"\x01\x05ab",  # truncated header (sid shorter than declared)
            bytes([0x01, 0x01]) + b"s" + b"\x00\x00\x00\x01",  # no packets
            bytes([0x01, 0x01]) + b"s" + b"\x00\x00\x00\x01" + b"\x00\x05ab",  # truncated packet
            bytes([0x01, 0x01]) + b"s" + b"\x00\x00\x00\x01" + b"\x00\x00",  # zero-length packet
        ],
    )
    def test_malformed_frames_rejected(self, data):
        with pytest.raises(opus_transport.BinaryFrameError):
            opus_transport.parse_audio_frame(data)

    @pytest.mark.unit
    def test_build_rejects_bad_inputs(self):
        with pytest.raises(opus_transport.BinaryFrameError):
            opus_transport.build_audio_frame("", 1, [b"x"])
        with pytest.raises(opus_transport.BinaryFrameError):
            opus_transport.build_audio_frame("s" * 256, 1, [b"x"])
        with pytest.raises(opus_transport.BinaryFrameError):
            opus_transport.build_audio_frame("s", 1, [b""])


# =============================================================================
# C1 opus decode (skipped when opuslib/libopus is absent in the test image)
# =============================================================================

@pytest.mark.skipif(not opus_transport.OPUS_AVAILABLE, reason="opuslib not installed")
class TestSessionOpusDecoders:
    @pytest.mark.unit
    def test_decode_roundtrip_silence(self):
        import opuslib

        enc = opuslib.Encoder(
            opus_transport.SAMPLE_RATE, opus_transport.CHANNELS, opuslib.APPLICATION_VOIP
        )
        forty_ms = opus_transport.SAMPLE_RATE * 40 // 1000
        packet = enc.encode(b"\x00\x00" * forty_ms, forty_ms)

        decoders = opus_transport.SessionOpusDecoders()
        pcm = decoders.decode("sess-1", [packet, packet])
        # two 40 ms windows of 16-bit mono
        assert len(pcm) == 2 * forty_ms * 2

    @pytest.mark.unit
    def test_drop_is_idempotent(self):
        decoders = opus_transport.SessionOpusDecoders()
        decoders.drop("never-seen")  # must not raise

    @pytest.mark.unit
    def test_has_tracks_allocation_and_drop(self):
        import opuslib

        enc = opuslib.Encoder(
            opus_transport.SAMPLE_RATE, opus_transport.CHANNELS, opuslib.APPLICATION_VOIP
        )
        forty_ms = opus_transport.SAMPLE_RATE * 40 // 1000
        packet = enc.encode(b"\x00\x00" * forty_ms, forty_ms)

        decoders = opus_transport.SessionOpusDecoders()
        assert not decoders.has("sess-1")
        decoders.decode("sess-1", [packet])
        assert decoders.has("sess-1")
        decoders.drop("sess-1")
        assert not decoders.has("sess-1")

    @pytest.mark.unit
    def test_decode_amplification_capped(self):
        """A frame whose packets decode past the per-frame cap raises
        BinaryFrameError instead of allocating unbounded PCM (DoS guard)."""
        import opuslib

        enc = opuslib.Encoder(
            opus_transport.SAMPLE_RATE, opus_transport.CHANNELS, opuslib.APPLICATION_VOIP
        )
        forty_ms = opus_transport.SAMPLE_RATE * 40 // 1000
        packet = enc.encode(b"\x00\x00" * forty_ms, forty_ms)
        # Each packet decodes to 40 ms; the cap is 1 s → ~25 packets. 200 is
        # comfortably over, so decode must abort partway with the cap error.
        decoders = opus_transport.SessionOpusDecoders()
        with pytest.raises(opus_transport.BinaryFrameError):
            decoders.decode("sess-dos", [packet] * 200)


# =============================================================================
# C1 CRITICAL REGRESSION: legacy base64 path buffers byte-identically
# =============================================================================

class TestBufferAudioRegression:
    @pytest.fixture
    def manager(self):
        return SatelliteManager()

    async def _session(self, manager) -> str:
        await manager.register(
            satellite_id="sat-test",
            room="Testraum",
            websocket=AsyncMock(),
            capabilities={},
            language="de",
        )
        session_id = await manager.start_session(
            satellite_id="sat-test", keyword="hey_renfield", confidence=0.9
        )
        assert session_id
        return session_id

    @pytest.mark.unit
    async def test_b64_and_bytes_paths_buffer_identically(self, manager):
        """The refactor split buffer_audio into a base64 shim + a bytes tail.
        The legacy path's buffered result must be byte-identical to feeding
        the same PCM through the new bytes entry point."""
        import base64

        pcm = bytes(range(256)) * 10
        sid_a = await self._session(manager)
        ok, err = manager.buffer_audio(sid_a, base64.b64encode(pcm).decode(), 1)
        assert ok, err
        legacy_buffer = manager.get_audio_buffer(sid_a)

        manager2 = SatelliteManager()
        sid_b = await self._session(manager2)
        ok, err = manager2.buffer_audio_bytes(sid_b, pcm, 1)
        assert ok, err
        assert manager2.get_audio_buffer(sid_b) == legacy_buffer == pcm

    @pytest.mark.unit
    async def test_bytes_path_unknown_session_and_buffer_full(self, manager):
        ok, err = manager.buffer_audio_bytes("nope", b"\x00\x00", 1)
        assert not ok and "Unknown session" in err

        sid = await self._session(manager)
        big = b"\x00" * (settings.ws_max_audio_buffer_size + 1)
        ok, err = manager.buffer_audio_bytes(sid, big, 1)
        assert not ok and "buffer full" in err.lower()

    @pytest.mark.unit
    async def test_b64_invalid_encoding_still_rejected(self, manager):
        sid = await self._session(manager)
        ok, err = manager.buffer_audio(sid, "!!!not-base64!!!", 1)
        assert not ok and "base64" in err.lower()

    @pytest.mark.unit
    async def test_has_session(self, manager):
        """The C1 binary path validates the session via has_session BEFORE
        allocating an Opus decoder (leak guard)."""
        assert not manager.has_session("ghost")
        sid = await self._session(manager)
        assert manager.has_session(sid)


# =============================================================================
# P0 fail-loud guard (T1)
# =============================================================================

class TestInprocessEmbeddingGuard:
    @pytest.mark.unit
    def test_route_guard_blocks_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "speaker_inprocess_embeddings_enabled", False)
        with pytest.raises(HTTPException) as exc:
            _require_inprocess_embeddings("route_identify")
        assert exc.value.status_code == 503
        assert "voice-server" in exc.value.detail

    @pytest.mark.unit
    def test_route_guard_passes_when_opted_in(self, monkeypatch):
        monkeypatch.setattr(settings, "speaker_inprocess_embeddings_enabled", True)
        assert _require_inprocess_embeddings("route_identify") is None

    @pytest.mark.unit
    async def test_whisper_inprocess_skips_embedding_by_default(self, monkeypatch, tmp_path):
        """Flag off → the in-process path transcribes but NEVER extracts a
        SpeechBrain embedding (the cross-space landmine)."""
        from services.whisper_service import WhisperService

        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        monkeypatch.setattr(settings, "speaker_inprocess_embeddings_enabled", False)

        service = WhisperService.__new__(WhisperService)
        service.model = object()  # skip load_model()
        service.language = "de"
        service.preprocess_enabled = False
        service._transcribe_async = AsyncMock(return_value="hallo welt")
        extract_mock = AsyncMock(return_value=None)
        service._extract_embedding_async = extract_mock

        audio = tmp_path / "turn.wav"
        audio.write_bytes(b"RIFF0000WAVE")

        result = await service.transcribe_with_speaker(str(audio), db_session=MagicMock())

        assert result["text"] == "hallo welt"
        assert result["speaker_id"] is None
        extract_mock.assert_not_awaited()

    @pytest.mark.unit
    async def test_whisper_inprocess_extracts_when_opted_in(self, monkeypatch, tmp_path):
        """Flag on (dev opt-in) → the legacy behavior is preserved: the
        embedding extraction runs alongside STT."""
        from services.whisper_service import WhisperService

        monkeypatch.setattr(settings, "speaker_recognition_enabled", True)
        monkeypatch.setattr(settings, "speaker_inprocess_embeddings_enabled", True)

        service = WhisperService.__new__(WhisperService)
        service.model = object()
        service.language = "de"
        service.preprocess_enabled = False
        service._transcribe_async = AsyncMock(return_value="hallo welt")
        extract_mock = AsyncMock(return_value=None)  # None → early speaker-none return
        service._extract_embedding_async = extract_mock

        audio = tmp_path / "turn.wav"
        audio.write_bytes(b"RIFF0000WAVE")

        result = await service.transcribe_with_speaker(str(audio), db_session=MagicMock())

        assert result["text"] == "hallo welt"
        extract_mock.assert_awaited_once()
