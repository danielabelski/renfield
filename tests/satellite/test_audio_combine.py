"""Tests for the satellite capture stereo→mono combine
(docs/design/satellite-audio-combine-pipeline.md).

The combine step is the one that was silently broken for the XVF3800 (ALSA
downmixed its processed-beam + AEC-residual to near-silence, starving the
wakeword). These tests pin every mode and the back-compat auto-derivation so a
regression is caught here, not by a user reporting "the satellite went deaf".

All mono-only, deterministic, no hardware — AudioCapture.__init__ does not open
a device.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest
from renfield_satellite.audio.capture import AudioCapture


def _interleave(*channels: list[int]) -> bytes:
    """Build an interleaved S16_LE buffer from per-channel sample lists."""
    arr = np.array(channels, dtype=np.int16).T.reshape(-1)
    return arr.tobytes()


class TestEffectiveCombineResolution:
    """combine / select_channel must auto-derive the legacy behavior when not
    given, so un-reprovisioned sats stay byte-identical."""

    @pytest.mark.satellite
    def test_mono_defaults_to_passthrough(self):
        cap = AudioCapture(channels=1)
        assert cap.combine == "passthrough"
        assert cap.select_channel == 0

    @pytest.mark.satellite
    def test_stereo_defaults_to_select_ch0(self):
        cap = AudioCapture(channels=2)
        assert cap.combine == "select"
        assert cap.select_channel == 0

    @pytest.mark.satellite
    def test_ac108_4ch_defaults_to_select_ch1(self):
        # AC108: ch0 is the silent reference, mics on ch1-3 → legacy default ch1
        cap = AudioCapture(channels=4, use_arecord=True)
        assert cap.combine == "select"
        assert cap.select_channel == 1

    @pytest.mark.satellite
    def test_beamforming_maps_to_beamform_and_forces_stereo(self):
        cap = AudioCapture(channels=1, beamforming=True)
        assert cap.combine == "beamform"
        assert cap.channels == 2

    @pytest.mark.satellite
    def test_xvf3800_explicit_select_ch0(self):
        cap = AudioCapture(channels=2, combine="select", select_channel=0)
        assert cap.combine == "select"
        assert cap.select_channel == 0

    @pytest.mark.satellite
    def test_explicit_select_channel_overrides_legacy_default(self):
        cap = AudioCapture(channels=4, use_arecord=True, select_channel=2)
        assert cap.select_channel == 2

    @pytest.mark.satellite
    def test_out_of_range_select_channel_clamped(self):
        cap = AudioCapture(channels=2, combine="select", select_channel=5)
        assert cap.select_channel == 0

    @pytest.mark.satellite
    def test_unknown_combine_falls_back(self):
        cap = AudioCapture(channels=2, combine="bogus")
        assert cap.combine == "select"


class TestStereoToMonoS16:
    """The PyAudio consumer path (S16)."""

    @pytest.mark.satellite
    def test_passthrough_mono_is_identity(self):
        cap = AudioCapture(channels=1)
        buf = np.array([1, 2, 3, 4], dtype=np.int16).tobytes()
        assert cap._stereo_to_mono(buf) == buf

    @pytest.mark.satellite
    def test_select_ch0_keeps_left(self):
        cap = AudioCapture(channels=2, combine="select", select_channel=0)
        buf = _interleave([100, 200, 300], [-1, -2, -3])  # L, R
        out = np.frombuffer(cap._stereo_to_mono(buf), dtype=np.int16)
        assert list(out) == [100, 200, 300]

    @pytest.mark.satellite
    def test_select_ch1_keeps_right(self):
        cap = AudioCapture(channels=2, combine="select", select_channel=1)
        buf = _interleave([100, 200, 300], [-1, -2, -3])
        out = np.frombuffer(cap._stereo_to_mono(buf), dtype=np.int16)
        assert list(out) == [-1, -2, -3]

    @pytest.mark.satellite
    def test_xvf3800_regression_residual_is_dropped_not_mixed(self):
        """The exact bug: ch0=processed beam (loud), ch1=residual (~silent).
        select ch0 must keep the loud beam; a downmix/average would halve it."""
        cap = AudioCapture(channels=2, combine="select", select_channel=0)
        beam = [8000, -8000, 8000, -8000]
        residual = [1, 0, -1, 0]
        buf = _interleave(beam, residual)
        out = np.frombuffer(cap._stereo_to_mono(buf), dtype=np.int16)
        assert list(out) == beam  # full amplitude, not (beam+residual)/2

    @pytest.mark.satellite
    def test_beamform_delegates_to_beamformer(self):
        cap = AudioCapture(channels=2, beamforming=True)
        cap._beamformer = MagicMock()
        cap._beamformer.process_bytes.return_value = b"beamformed"
        buf = _interleave([1, 2], [3, 4])
        assert cap._stereo_to_mono(buf) == b"beamformed"
        cap._beamformer.process_bytes.assert_called_once_with(buf)


class TestSelectMonoSharedByArecord:
    """_select_mono is dtype-preserving and shared with the S32 arecord path,
    so AC108 channel selection stays byte-identical."""

    @pytest.mark.satellite
    def test_s32_ac108_selects_ch1(self):
        cap = AudioCapture(channels=4, use_arecord=True)  # select_channel auto → 1
        # 4ch S32 interleaved: ch0=ref(0), ch1=mic(big), ch2/3 other
        frame = np.array(
            [0, 111, 20, 30, 0, 222, 21, 31, 0, 333, 22, 32], dtype=np.int32
        )
        out = cap._select_mono(frame)
        assert list(out) == [111, 222, 333]
        assert out.dtype == np.int32  # dtype preserved (arecord shifts >>16 after)
