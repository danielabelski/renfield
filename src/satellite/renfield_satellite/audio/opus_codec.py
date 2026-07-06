"""Opus encoding for the C1 binary satellite audio transport.

Design: docs/design/voice-identity-wakeword-verification.md §4 C1. The
satellite replaces base64-PCM-in-JSON with binary WS frames carrying Opus
(16 kHz mono, VOIP profile); the backend edge decodes back to PCM. Codec
choice is negotiated at register time (capabilities.audio_codec → the
register_ack answers "opus" or "pcm"), so a backend without the feature —
or this module without opuslib — always degrades to the legacy JSON path.

Wire format of a binary frame (mirror of the backend's
`ha_glue/services/opus_transport.py`, all integers big-endian):

    byte  0        frame type (0x01 = AUDIO_OPUS)
    byte  1        L = length of the session-id
    bytes 2..2+L   session_id (utf-8)
    bytes 2+L..+4  sequence number (uint32)
    rest           repeated [uint16 packet_len][opus packet]

Opus frames max out at 60 ms, so the 80 ms capture chunk (1280 samples)
is encoded as 40 ms packets; PCM that doesn't fill a whole packet is kept
in an internal remainder buffer until the next chunk arrives.

opuslib is optional: `OPUS_AVAILABLE` is False when it (or libopus) is
missing and the caller must stay on PCM.
"""

from __future__ import annotations

import logging
import struct

logger = logging.getLogger(__name__)

try:  # pragma: no cover - environment-dependent
    import opuslib

    OPUS_AVAILABLE = True
except Exception:  # ImportError or missing libopus shared object
    opuslib = None  # type: ignore[assignment]
    OPUS_AVAILABLE = False

FRAME_AUDIO_OPUS = 0x01

SAMPLE_RATE = 16000
CHANNELS = 1
# 40 ms @ 16 kHz mono S16_LE
PACKET_SAMPLES = SAMPLE_RATE * 40 // 1000
PACKET_BYTES = PACKET_SAMPLES * 2


def build_audio_frame(session_id: str, sequence: int, packets: list[bytes]) -> bytes:
    """Build a FRAME_AUDIO_OPUS binary frame."""
    sid = session_id.encode("utf-8")
    if not sid or len(sid) > 255:
        raise ValueError("session id length must be 1..255 bytes")
    out = bytearray()
    out.append(FRAME_AUDIO_OPUS)
    out.append(len(sid))
    out += sid
    out += struct.pack(">I", sequence & 0xFFFFFFFF)
    for p in packets:
        if not p or len(p) > 0xFFFF:
            raise ValueError("opus packet length must be 1..65535 bytes")
        out += struct.pack(">H", len(p))
        out += p
    return bytes(out)


class OpusChunkEncoder:
    """Stateful chunk→packets encoder for ONE audio session.

    Feed arbitrary-sized S16_LE mono PCM chunks; get back a list of Opus
    packets covering every complete 40 ms window (a trailing partial window
    stays buffered for the next call). Create a fresh instance per session —
    the Opus encoder is stateful and must not bleed across sessions.
    """

    def __init__(self) -> None:
        if not OPUS_AVAILABLE:
            raise RuntimeError("opuslib not available")
        self._encoder = opuslib.Encoder(
            SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_VOIP
        )
        self._remainder = b""

    def encode_chunk(self, pcm_bytes: bytes) -> list[bytes]:
        data = self._remainder + pcm_bytes
        packets: list[bytes] = []
        offset = 0
        while offset + PACKET_BYTES <= len(data):
            packets.append(
                self._encoder.encode(data[offset : offset + PACKET_BYTES], PACKET_SAMPLES)
            )
            offset += PACKET_BYTES
        self._remainder = data[offset:]
        return packets

    def flush_padded(self) -> list[bytes]:
        """Encode any buffered remainder by zero-padding to a full packet.

        Called at end-of-utterance so the final partial window isn't lost.
        """
        if not self._remainder:
            return []
        padded = self._remainder.ljust(PACKET_BYTES, b"\x00")
        self._remainder = b""
        return [self._encoder.encode(padded, PACKET_SAMPLES)]


def make_encoder() -> OpusChunkEncoder | None:
    """Create an encoder, or None (with a warning) when opuslib is missing."""
    if not OPUS_AVAILABLE:
        logger.warning(
            "Opus requested but opuslib/libopus is not installed — "
            "falling back to PCM transport"
        )
        return None
    return OpusChunkEncoder()
