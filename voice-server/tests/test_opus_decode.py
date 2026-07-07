"""Tests for raw-Opus-packet decode on the voice-server (C2 Phase 1, design D6).

Decode moved here from the backend; these mirror the former backend
SessionOpusDecoders tests plus the `[uint16 len][packet]` framing the backend
forwards. The framing + guard tests run without libopus (a fake decoder covers
the cap/resilience/availability branches deterministically); one end-to-end
roundtrip skips when libopus is absent.
"""

from __future__ import annotations

import struct
from types import SimpleNamespace

import numpy as np
import pytest

from voice_server.services import opus_decode

try:
    import opuslib

    OPUS_AVAILABLE = True
except Exception:
    OPUS_AVAILABLE = False

_FORTY_MS = opus_decode.SAMPLE_RATE * 40 // 1000  # 640 samples


def _frame(*packets: bytes) -> bytes:
    """Build the `[uint16 len][packet]…` body the backend forwards."""
    out = bytearray()
    for p in packets:
        out += struct.pack(">H", len(p))
        out += p
    return bytes(out)


def _fake_opuslib(poison: bytes | None = None, *, always_fail: bool = False):
    """A stand-in opuslib whose Decoder yields 40 ms of silence per packet, but
    raises on `poison` (or on every packet when always_fail) — lets the
    resilience/cap branches run without a real libopus."""

    class FakeDecoder:
        def __init__(self, sample_rate, channels):
            pass

        def decode(self, packet, frame_size):
            if always_fail or (poison is not None and packet == poison):
                raise ValueError("bad opus packet")
            return b"\x00\x00" * _FORTY_MS

    return SimpleNamespace(Decoder=FakeDecoder)


class TestParseOpusPackets:
    def test_roundtrip(self):
        pkts = [b"\x01\x02\x03", b"\xff" * 40]
        assert opus_decode.parse_opus_packets(_frame(*pkts)) == pkts

    @pytest.mark.parametrize(
        "body",
        [
            b"",  # empty
            b"\x00\x05ab",  # declared len 5, only 2 bytes
            b"\x00\x00",  # zero-length packet
            b"\x00",  # truncated length prefix
        ],
    )
    def test_malformed_rejected(self, body):
        with pytest.raises(opus_decode.OpusDecodeError):
            opus_decode.parse_opus_packets(body)


class TestDecodeGuards:
    """Cap / resilience / availability — deterministic via a fake decoder."""

    def _use_fake(self, monkeypatch, fake):
        monkeypatch.setattr(opus_decode, "OPUSLIB_AVAILABLE", True)
        monkeypatch.setattr(opus_decode, "opuslib", fake, raising=False)

    def test_unavailable_fails_loud(self, monkeypatch):
        # Skewed deploy: image lacks libopus → distinct error, never silent PCM.
        monkeypatch.setattr(opus_decode, "OPUSLIB_AVAILABLE", False)
        with pytest.raises(opus_decode.OpusUnavailableError):
            opus_decode.decode_opus_packets_to_pcm(_frame(b"\x01\x02"))

    def test_corrupt_packet_salvaged(self, monkeypatch):
        # One bad packet must NOT discard a long utterance — it's skipped.
        self._use_fake(monkeypatch, _fake_opuslib(poison=b"POISON"))
        pcm = opus_decode.decode_opus_packets_to_pcm(_frame(b"a", b"POISON", b"b"))
        assert pcm.shape == (2 * _FORTY_MS,)  # 2 salvaged, 1 dropped

    def test_all_packets_corrupt_raises(self, monkeypatch):
        self._use_fake(monkeypatch, _fake_opuslib(always_fail=True))
        with pytest.raises(opus_decode.OpusDecodeError):
            opus_decode.decode_opus_packets_to_pcm(_frame(b"a", b"b"))

    def test_amplification_cap_enforced(self, monkeypatch):
        # Cap is env-tunable; shrink it so a single packet trips it.
        self._use_fake(monkeypatch, _fake_opuslib())
        monkeypatch.setattr(opus_decode.settings, "opus_max_decoded_seconds", 0)
        with pytest.raises(opus_decode.OpusDecodeError):
            opus_decode.decode_opus_packets_to_pcm(_frame(b"a"))

    def test_generous_default_cap_admits_long_audio(self, monkeypatch):
        # A ~2 s utterance must pass under the default (no recording cap).
        self._use_fake(monkeypatch, _fake_opuslib())
        pcm = opus_decode.decode_opus_packets_to_pcm(_frame(*([b"a"] * 50)))
        assert pcm.shape == (50 * _FORTY_MS,)


@pytest.mark.skipif(not OPUS_AVAILABLE, reason="opuslib not installed")
class TestDecodeWithRealOpus:
    def test_decode_roundtrip_shape_and_dtype(self):
        enc = opuslib.Encoder(
            opus_decode.SAMPLE_RATE, opus_decode.CHANNELS, opuslib.APPLICATION_VOIP
        )
        blob = _frame(*[enc.encode(b"\x00\x00" * _FORTY_MS, _FORTY_MS) for _ in range(2)])
        pcm = opus_decode.decode_opus_packets_to_pcm(blob)
        assert pcm.dtype == np.float32
        assert pcm.shape == (2 * _FORTY_MS,)  # two 40 ms windows, mono
        assert np.all(np.abs(pcm) <= 1.0)  # normalized to [-1, 1]
