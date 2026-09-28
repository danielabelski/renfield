"""
Unit tests for the continuous BLE presence scanner (BLEScanner continuous mode).

Pure logic — exercises the detection-callback EWMA smoothing, known-MAC
filtering, threshold gating and freshness pruning without any BLE hardware.
"""
import time
from types import SimpleNamespace

import pytest
from renfield_satellite.ble.scanner import BLEScanner


def _det(scanner, mac, rssi):
    """Feed one advertisement into the detection callback."""
    scanner._on_detection(SimpleNamespace(address=mac), SimpleNamespace(rssi=rssi))


@pytest.mark.satellite
@pytest.mark.unit
class TestBLEScannerContinuous:
    def test_only_known_macs_tracked(self):
        s = BLEScanner(rssi_threshold=-80)
        s.update_known({"AA:BB:CC:DD:EE:FF"})
        _det(s, "AA:BB:CC:DD:EE:FF", -50)
        _det(s, "11:22:33:44:55:66", -40)  # not in whitelist
        macs = {r["mac"] for r in s.get_readings()}
        assert macs == {"AA:BB:CC:DD:EE:FF"}

    def test_case_insensitive_match(self):
        s = BLEScanner(rssi_threshold=-80)
        s.update_known({"aa:bb:cc:dd:ee:ff"})
        _det(s, "AA:BB:CC:DD:EE:FF", -55)
        assert len(s.get_readings()) == 1

    def test_ewma_smoothing(self):
        s = BLEScanner(rssi_threshold=-100, smoothing_alpha=0.5)
        s.update_known({"AA:BB:CC:DD:EE:FF"})
        _det(s, "AA:BB:CC:DD:EE:FF", -60)
        _det(s, "AA:BB:CC:DD:EE:FF", -70)  # 0.5*-70 + 0.5*-60 = -65
        assert s.get_readings()[0]["rssi"] == -65

    def test_threshold_gates_weak_signal(self):
        s = BLEScanner(rssi_threshold=-70)
        s.update_known({"AA:BB:CC:DD:EE:FF"})
        _det(s, "AA:BB:CC:DD:EE:FF", -85)  # below threshold
        assert s.get_readings() == []

    def test_freshness_prune(self):
        s = BLEScanner(rssi_threshold=-100, freshness_seconds=5.0)
        s.update_known({"AA:BB:CC:DD:EE:FF"})
        _det(s, "AA:BB:CC:DD:EE:FF", -50)
        assert len(s.get_readings()) == 1
        # Age the last-seen timestamp beyond the freshness window
        s._readings["AA:BB:CC:DD:EE:FF"][1] = time.monotonic() - 10.0
        assert s.get_readings() == []

    def test_update_known_drops_removed_device(self):
        s = BLEScanner(rssi_threshold=-100)
        s.update_known({"AA:BB:CC:DD:EE:FF"})
        _det(s, "AA:BB:CC:DD:EE:FF", -50)
        assert len(s.get_readings()) == 1
        s.update_known(set())  # device no longer tracked
        assert s.get_readings() == []

    def test_none_rssi_ignored(self):
        s = BLEScanner(rssi_threshold=-100)
        s.update_known({"AA:BB:CC:DD:EE:FF"})
        _det(s, "AA:BB:CC:DD:EE:FF", None)
        assert s.get_readings() == []
