"""Binary Opus audio transport for the satellite WebSocket (C1, D6).

Design: docs/design/voice-identity-wakeword-verification.md §4 C1 — the
satellite replaces base64-PCM-in-JSON with binary WS frames carrying Opus,
and the BACKEND EDGE decodes back to PCM so everything downstream (STT,
speaker resolver, enrollment) keeps its existing PCM contract and the
voice-server API stays frozen.

Wire format of a binary frame (all integers big-endian):

    byte  0        frame type (0x01 = AUDIO_OPUS)
    byte  1        L = length of the session-id
    bytes 2..2+L   session_id (utf-8, ascii in practice)
    bytes 2+L..+4  sequence number (uint32)
    rest           repeated [uint16 packet_len][opus packet]

An 80 ms capture chunk (1280 samples @ 16 kHz) doesn't fit a single Opus
frame (max 60 ms), so the satellite encodes it as two 40 ms packets and
ships both in one binary frame; the length-prefix framing keeps that
flexible. Opus decoders are STATEFUL, so one decoder is kept per session
and must be dropped when the session ends.

opuslib (libopus via ctypes) is an optional dependency: when it is not
importable the backend simply negotiates ``pcm`` at register time and this
module is never asked to decode.
"""

from __future__ import annotations

import struct

from loguru import logger

try:  # pragma: no cover - trivially environment-dependent
    import opuslib

    OPUS_AVAILABLE = True
except Exception:  # ImportError or missing libopus shared object
    opuslib = None  # type: ignore[assignment]
    OPUS_AVAILABLE = False

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


class SessionOpusDecoders:
    """Per-connection registry of stateful Opus decoders, keyed by session.

    One instance lives per satellite WS connection; the handler calls
    ``drop()`` on audio_end and the whole instance is garbage-collected on
    disconnect, so decoder state can never leak across sessions.
    """

    # 120 ms of headroom per decode call — an Opus packet decodes to at most
    # 120 ms of audio, and ours are 40 ms.
    _MAX_FRAME_SAMPLES = SAMPLE_RATE * 120 // 1000
    # Hard cap on decoded PCM per binary frame. The handler size-checks only
    # the COMPRESSED frame (~1 MB), but a frame packed with hundreds of
    # thousands of tiny packets each declaring 120 ms would decode to >1 GB
    # BEFORE the downstream buffer-size check ever runs — a decode-
    # amplification OOM. One 80 ms capture chunk is 2 packets, so a healthy
    # frame is a few packets; 1 s of headroom is ~40x that and still bounds
    # the attack to a fixed allocation.
    _MAX_DECODED_BYTES_PER_FRAME = SAMPLE_RATE * 2 * 1  # 1 s of 16-bit mono

    def __init__(self) -> None:
        if not OPUS_AVAILABLE:
            raise RuntimeError("opuslib not available — negotiate pcm instead")
        self._decoders: dict[str, opuslib.Decoder] = {}

    def has(self, session_id: str) -> bool:
        return session_id in self._decoders

    def decode(self, session_id: str, packets: list[bytes]) -> bytes:
        """Decode packets for a session into 16-bit mono PCM bytes.

        Raises BinaryFrameError if the decoded size exceeds the per-frame cap
        (decode-amplification guard) — the caller drops the session on any
        exception so a poisoned frame can neither OOM the pod nor leave the
        stateful decoder desynced from the buffered audio.
        """
        dec = self._decoders.get(session_id)
        if dec is None:
            dec = opuslib.Decoder(SAMPLE_RATE, CHANNELS)
            self._decoders[session_id] = dec
        pcm = bytearray()
        for packet in packets:
            pcm += dec.decode(packet, self._MAX_FRAME_SAMPLES)
            if len(pcm) > self._MAX_DECODED_BYTES_PER_FRAME:
                raise BinaryFrameError(
                    f"decoded PCM exceeds per-frame cap "
                    f"({len(pcm)} > {self._MAX_DECODED_BYTES_PER_FRAME})"
                )
        return bytes(pcm)

    def drop(self, session_id: str) -> None:
        if self._decoders.pop(session_id, None) is not None:
            logger.debug(f"🎛️ Dropped opus decoder for session {session_id}")
