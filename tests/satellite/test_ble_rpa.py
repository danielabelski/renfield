"""
Unit tests for RPA (Resolvable Private Address) resolution + scanner routing.

The core `ah` resolution is checked against the Bluetooth Core Spec sample
(Vol 3, Part H, Appendix D.7): IRK ec0234a357c8ad05341010a60a397d9b resolves
the address 70:81:94:0D:FB:AA.
"""
from types import SimpleNamespace

import pytest
from renfield_satellite.ble import rpa
from renfield_satellite.ble.scanner import BLEScanner

SPEC_IRK = bytes.fromhex("ec0234a357c8ad05341010a60a397d9b")
SPEC_RPA = "70:81:94:0D:FB:AA"          # resolves against SPEC_IRK
PUBLIC_ADDR = "C0:81:94:0D:FB:AA"       # top bits 0b11 → not an RPA


def _det(scanner, mac, rssi):
    scanner._on_detection(SimpleNamespace(address=mac), SimpleNamespace(rssi=rssi))


@pytest.mark.satellite
@pytest.mark.unit
class TestRPAResolution:
    def test_is_rpa(self):
        assert rpa.is_resolvable_private_address(SPEC_RPA) is True
        assert rpa.is_resolvable_private_address(PUBLIC_ADDR) is False
        assert rpa.is_resolvable_private_address("garbage") is False

    def test_spec_vector_resolves(self):
        assert rpa.resolve_rpa(SPEC_IRK, SPEC_RPA) is True

    def test_wrong_irk_does_not_resolve(self):
        assert rpa.resolve_rpa(bytes(16), SPEC_RPA) is False

    def test_bad_irk_length(self):
        assert rpa.resolve_rpa(b"\x00" * 15, SPEC_RPA) is False

    def test_public_address_never_resolves(self):
        assert rpa.resolve_rpa(SPEC_IRK, PUBLIC_ADDR) is False

    def test_resolve_identity_picks_matching_irk(self):
        irks = {"bob": bytes(16), "alice": SPEC_IRK}
        assert rpa.resolve_identity(SPEC_RPA, irks) == "alice"
        assert rpa.resolve_identity(PUBLIC_ADDR, irks) is None
        assert rpa.resolve_identity(SPEC_RPA, {}) is None


@pytest.mark.satellite
@pytest.mark.unit
class TestScannerIRKRouting:
    def test_rotating_rpa_resolves_to_identity(self):
        s = BLEScanner(rssi_threshold=-100)
        s.update_irks({"alice": SPEC_IRK})
        _det(s, SPEC_RPA, -55)
        readings = s.get_readings()
        assert len(readings) == 1
        assert readings[0]["identity"] == "alice"
        assert readings[0]["mac"] == SPEC_RPA

    def test_unresolvable_rpa_ignored(self):
        s = BLEScanner(rssi_threshold=-100)
        s.update_irks({"bob": bytes(16)})  # won't match SPEC_RPA
        _det(s, SPEC_RPA, -50)
        assert s.get_readings() == []

    def test_known_mac_still_tracked_without_identity(self):
        s = BLEScanner(rssi_threshold=-100)
        s.update_known({"AA:BB:CC:DD:EE:FF"})
        _det(s, "AA:BB:CC:DD:EE:FF", -50)
        r = s.get_readings()
        assert len(r) == 1 and "identity" not in r[0]

    def test_update_irks_drops_removed_identity(self):
        s = BLEScanner(rssi_threshold=-100)
        s.update_irks({"alice": SPEC_IRK})
        _det(s, SPEC_RPA, -50)
        assert len(s.get_readings()) == 1
        s.update_irks({})  # alice no longer tracked
        assert s.get_readings() == []

    def test_invalid_irk_length_rejected_by_update(self):
        s = BLEScanner(rssi_threshold=-100)
        s.update_irks({"alice": b"\x00" * 10})  # wrong length → dropped
        _det(s, SPEC_RPA, -50)
        assert s.get_readings() == []
