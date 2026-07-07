"""Tests for raw-Opus-packet decode on the voice-server (C2 Phase 1, design D6).

Decode moved here from the backend; these mirror the former backend
SessionOpusDecoders tests plus the `[uint16 len][packet]` framing the backend
forwards. opuslib-dependent tests skip when libopus is absent.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

from voice_server.services import opus_decode

try:
    import opuslib

    OPUS_AVAILABLE = True
except Exception:
    OPUS_AVAILABLE = False


def _frame(*packets: bytes) -> bytes:
    """Build the `[uint16 len][packet]…` body the backend forwards."""
    out = bytearray()
    for p in packets:
        out += struct.pack(">H", len(p))
        out += p
    return bytes(out)


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


@pytest.mark.skipif(not OPUS_AVAILABLE, reason="opuslib not installed")
class TestDecodeOpusPackets:
    def _encode(self, n_windows: int) -> bytes:
        enc = opuslib.Encoder(opus_decode.SAMPLE_RATE, opus_decode.CHANNELS, opuslib.APPLICATION_VOIP)
        forty_ms = opus_decode.SAMPLE_RATE * 40 // 1000
        return _frame(*[enc.encode(b"\x00\x00" * forty_ms, forty_ms) for _ in range(n_windows)])

    def test_decode_roundtrip_shape_and_dtype(self):
        pcm = opus_decode.decode_opus_packets_to_pcm(self._encode(2))
        forty_ms = opus_decode.SAMPLE_RATE * 40 // 1000
        assert pcm.dtype == np.float32
        assert pcm.shape == (2 * forty_ms,)  # two 40 ms windows, mono
        assert np.all(np.abs(pcm) <= 1.0)  # normalized to [-1, 1]

    def test_amplification_capped(self):
        # 60 s cap = 1500 x 40 ms windows; 2000 comfortably exceeds it.
        with pytest.raises(opus_decode.OpusDecodeError):
            opus_decode.decode_opus_packets_to_pcm(self._encode(2000))
