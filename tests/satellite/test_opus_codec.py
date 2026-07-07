"""Tests for the satellite-side C1 Opus transport (audio/opus_codec.py).

The binary frame builder is pure (no opus dependency) and is validated
field-by-field against the wire format shared with the backend
(ha_glue/services/opus_transport.py). Encoder tests skip when
opuslib/libopus is not installed — the satellite degrades to PCM then,
which is itself asserted.
"""

import struct

import pytest
from renfield_satellite.audio import opus_codec


class TestBuildAudioFrame:
    @pytest.mark.satellite
    def test_wire_format_fields(self):
        frame = opus_codec.build_audio_frame("sat-a-1", 7, [b"abc", b"\x00\x01"])

        assert frame[0] == opus_codec.FRAME_AUDIO_OPUS
        sid_len = frame[1]
        assert sid_len == len(b"sat-a-1")
        assert frame[2 : 2 + sid_len] == b"sat-a-1"
        (seq,) = struct.unpack_from(">I", frame, 2 + sid_len)
        assert seq == 7
        offset = 2 + sid_len + 4
        (p1_len,) = struct.unpack_from(">H", frame, offset)
        assert p1_len == 3
        assert frame[offset + 2 : offset + 5] == b"abc"
        offset += 2 + p1_len
        (p2_len,) = struct.unpack_from(">H", frame, offset)
        assert p2_len == 2
        assert frame[offset + 2 : offset + 4] == b"\x00\x01"
        assert offset + 2 + p2_len == len(frame)

    @pytest.mark.satellite
    def test_rejects_bad_inputs(self):
        with pytest.raises(ValueError):
            opus_codec.build_audio_frame("", 1, [b"x"])
        with pytest.raises(ValueError):
            opus_codec.build_audio_frame("s" * 256, 1, [b"x"])
        with pytest.raises(ValueError):
            opus_codec.build_audio_frame("s", 1, [b""])


@pytest.mark.skipif(not opus_codec.OPUS_AVAILABLE, reason="opuslib not installed")
class TestOpusChunkEncoder:
    @pytest.mark.satellite
    def test_80ms_chunk_yields_two_packets_no_remainder(self):
        enc = opus_codec.OpusChunkEncoder()
        chunk = b"\x00\x00" * 1280  # 80 ms @ 16 kHz S16_LE
        packets = enc.encode_chunk(chunk)
        assert len(packets) == 2
        assert enc._remainder == b""

    @pytest.mark.satellite
    def test_partial_chunk_buffers_remainder_until_next(self):
        enc = opus_codec.OpusChunkEncoder()
        half_packet = b"\x00\x00" * (opus_codec.PACKET_SAMPLES // 2)  # 20 ms
        assert enc.encode_chunk(half_packet) == []
        assert len(enc._remainder) == len(half_packet)
        # Next 20 ms completes one 40 ms window
        packets = enc.encode_chunk(half_packet)
        assert len(packets) == 1
        assert enc._remainder == b""

    @pytest.mark.satellite
    def test_flush_padded_emits_tail_once(self):
        enc = opus_codec.OpusChunkEncoder()
        enc.encode_chunk(b"\x00\x00" * 100)  # small tail
        assert len(enc.flush_padded()) == 1
        assert enc.flush_padded() == []  # idempotent
