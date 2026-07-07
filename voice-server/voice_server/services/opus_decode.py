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

import struct

import numpy as np

SAMPLE_RATE = 16000
CHANNELS = 1
# An Opus packet decodes to at most 120 ms; ours are 40 ms.
_MAX_FRAME_SAMPLES = SAMPLE_RATE * 120 // 1000
# Decode-amplification guard: a body of tiny packets each declaring 120 ms could
# decode to gigabytes. Bound the whole utterance to 60 s of 16-bit mono (~1.9 MB)
# — far above any real turn (max_recording_seconds ~20 s), fixed allocation.
_MAX_DECODED_BYTES = SAMPLE_RATE * 2 * 60


class OpusDecodeError(ValueError):
    """Malformed packet framing or a decode failure / cap breach."""


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

    Raises OpusDecodeError on malformed framing, a decode failure, or if the
    decoded audio exceeds the amplification cap.
    """
    import opuslib  # local import: keeps module import-safe if libopus is absent

    packets = parse_opus_packets(blob)
    dec = opuslib.Decoder(SAMPLE_RATE, CHANNELS)
    pcm = bytearray()
    for packet in packets:
        pcm += dec.decode(packet, _MAX_FRAME_SAMPLES)
        if len(pcm) > _MAX_DECODED_BYTES:
            raise OpusDecodeError(
                f"decoded PCM exceeds cap ({len(pcm)} > {_MAX_DECODED_BYTES})"
            )
    return np.frombuffer(bytes(pcm), dtype=np.int16).astype(np.float32) / 32768.0
