"""Binary Opus audio transport for the satellite WebSocket (C1) — wire format.

Design: docs/design/voice-identity-wakeword-verification.md §4 C1. The satellite
replaces base64-PCM-in-JSON with binary WS frames carrying Opus. The backend
PARSES these frames and buffers the raw packets, then forwards them to the
voice-server, which DECODES (design D6 — decode is media processing and lives on
the voice-server, not the backend orchestration layer). This module is therefore
just the frame wire format (parse/build); it has no opuslib dependency.

Wire format of a binary frame (all integers big-endian):

    byte  0        frame type (0x01 = AUDIO_OPUS)
    byte  1        L = length of the session-id
    bytes 2..2+L   session_id (utf-8, ascii in practice)
    bytes 2+L..+4  sequence number (uint32)
    rest           repeated [uint16 packet_len][opus packet]

An 80 ms capture chunk (1280 samples @ 16 kHz) doesn't fit a single Opus frame
(max 60 ms), so the satellite encodes it as two 40 ms packets and ships both in
one binary frame; the length-prefix framing keeps that flexible.
"""

from __future__ import annotations

import struct

# Frame types
FRAME_AUDIO_OPUS = 0x01

SAMPLE_RATE = 16000
CHANNELS = 1


class BinaryFrameError(ValueError):
    """A binary frame violated the wire format above."""


def parse_audio_frame(data: bytes) -> tuple[str, int, list[bytes]]:
    """Parse a FRAME_AUDIO_OPUS binary frame.

    Returns (session_id, sequence, opus_packets). Raises BinaryFrameError
    on any structural violation — the caller answers with a WS error frame
    rather than guessing.
    """
    if len(data) < 2:
        raise BinaryFrameError("frame too short")
    if data[0] != FRAME_AUDIO_OPUS:
        raise BinaryFrameError(f"unknown binary frame type 0x{data[0]:02x}")

    sid_len = data[1]
    header_end = 2 + sid_len + 4
    if sid_len == 0 or len(data) < header_end:
        raise BinaryFrameError("truncated header")

    try:
        session_id = data[2 : 2 + sid_len].decode("utf-8")
    except UnicodeDecodeError as e:
        raise BinaryFrameError("session id not utf-8") from e
    (sequence,) = struct.unpack_from(">I", data, 2 + sid_len)

    packets: list[bytes] = []
    offset = header_end
    while offset < len(data):
        if offset + 2 > len(data):
            raise BinaryFrameError("truncated packet length")
        (plen,) = struct.unpack_from(">H", data, offset)
        offset += 2
        if plen == 0 or offset + plen > len(data):
            raise BinaryFrameError("truncated opus packet")
        packets.append(data[offset : offset + plen])
        offset += plen

    if not packets:
        raise BinaryFrameError("frame carries no packets")
    return session_id, sequence, packets


def build_audio_frame(session_id: str, sequence: int, packets: list[bytes]) -> bytes:
    """Build a FRAME_AUDIO_OPUS frame (shared with tests; the satellite has
    its own mirror implementation to stay dependency-free)."""
    sid = session_id.encode("utf-8")
    if not sid or len(sid) > 255:
        raise BinaryFrameError("session id length must be 1..255 bytes")
    out = bytearray()
    out.append(FRAME_AUDIO_OPUS)
    out.append(len(sid))
    out += sid
    out += struct.pack(">I", sequence & 0xFFFFFFFF)
    for p in packets:
        if not p or len(p) > 0xFFFF:
            raise BinaryFrameError("opus packet length must be 1..65535 bytes")
        out += struct.pack(">H", len(p))
        out += p
    return bytes(out)

