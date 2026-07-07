"""Raw-Opus-packet decode for the satellite C1 transport (voice-identity C2
Phase 1 — decode moved off the backend onto the voice-server, the media layer).

Satellites ship **bare libopus packets** (no Ogg/WebM container), length-prefixed
`[uint16 packet_len][opus packet]…`. ffmpeg (the voice-server's container decoder,
audio_oneshot.py) cannot parse that, so this module owns the raw-packet path with
`opuslib`. The backend forwards a whole utterance's packets in one POST; we decode
them one-shot with a single stateless-per-call decoder → float32 mono 16 kHz PCM,
the same shape `decode_audio_to_pcm` returns, so it feeds the existing STT + embed
path unchanged.

This is the former backend `ha_glue/services/opus_transport.py::SessionOpusDecoders`
decode, relocated here (the media layer) per design D6.
"""

from __future__ import annotations

import logging
import struct

import numpy as np

from voice_server.config import settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
# An Opus packet decodes to at most 120 ms; ours are 40 ms.
_MAX_FRAME_SAMPLES = SAMPLE_RATE * 120 // 1000

# opuslib is a ctypes wrapper over libopus; both must be present in the image.
# Compute availability once at import so the app can fail LOUD at startup on a
# skewed deploy (opus negotiated by the backend, but this image lacks the codec)
# instead of silently 500-ing the first satellite utterance.
try:
    import opuslib  # noqa: F401

    OPUSLIB_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only on a broken image
    OPUSLIB_AVAILABLE = False


class OpusDecodeError(ValueError):
    """Malformed packet framing or a decode failure / cap breach."""


class OpusUnavailableError(RuntimeError):
    """opuslib/libopus is not installed in this image (skewed deploy)."""


def _max_decoded_bytes() -> int:
    """Decode-amplification ceiling in bytes of 16-bit mono PCM. Read from
    settings each call (not a module constant) so it is env-tunable and a test
    can shrink it. NOT a recording cap — see settings.opus_max_decoded_seconds."""
    return SAMPLE_RATE * 2 * settings.opus_max_decoded_seconds


def parse_opus_packets(blob: bytes) -> list[bytes]:
    """Parse the `[uint16 len][packet]…` framing into a list of Opus packets."""
    packets: list[bytes] = []
    offset = 0
    n = len(blob)
    while offset < n:
        if offset + 2 > n:
            raise OpusDecodeError("truncated packet length")
        (plen,) = struct.unpack_from(">H", blob, offset)
        offset += 2
        if plen == 0 or offset + plen > n:
            raise OpusDecodeError("truncated or empty opus packet")
        packets.append(blob[offset : offset + plen])
        offset += plen
    if not packets:
        raise OpusDecodeError("no opus packets in body")
    return packets


def decode_opus_packets_to_pcm(blob: bytes) -> np.ndarray:
    """Decode the C1 opus packet framing to mono 16 kHz **float32** PCM in
    [-1, 1] — the same dtype/shape ffmpeg's `decode_audio_to_pcm` returns, so
    downstream STT + speaker embed are identical whether the source was a
    container (ffmpeg) or raw opus (here).

    A single corrupt packet does NOT discard the whole utterance: it is skipped
    with a warning (opus's own packet-loss concealment territory) so one bad
    frame can't wipe a long diary entry; only an utterance where EVERY packet
    fails raises. Also raises OpusDecodeError on malformed framing or if the
    decoded audio exceeds the amplification cap, and OpusUnavailableError if the
    image lacks libopus (fail-loud on a skewed deploy, not a silent empty text).
    """
    if not OPUSLIB_AVAILABLE:
        raise OpusUnavailableError(
            "opuslib/libopus not installed — cannot decode satellite opus"
        )

    packets = parse_opus_packets(blob)
    dec = opuslib.Decoder(SAMPLE_RATE, CHANNELS)
    cap = _max_decoded_bytes()
    pcm = bytearray()
    decoded, skipped = 0, 0
    for packet in packets:
        try:
            pcm += dec.decode(packet, _MAX_FRAME_SAMPLES)
        except Exception as e:  # opuslib.OpusError on a corrupt packet
            skipped += 1
            logger.warning("skipping undecodable opus packet: %s", e)
            continue
        decoded += 1
        if len(pcm) > cap:
            raise OpusDecodeError(f"decoded PCM exceeds cap ({len(pcm)} > {cap})")
    if decoded == 0:
        raise OpusDecodeError(f"no opus packet decoded ({skipped} corrupt)")
    if skipped:
        logger.warning("opus decode salvaged %d packet(s), dropped %d", decoded, skipped)
    return np.frombuffer(bytes(pcm), dtype=np.int16).astype(np.float32) / 32768.0
