"""
Unit test for the BlueZ info-file IRK parser used by the UI pairing-capture flow
(Satellite._read_bonded_irks). Verifies it extracts the IdentityResolvingKey,
reverses BlueZ's least-significant-octet-first byte order to the most-significant-
octet-first form the resolver/backend expect, and ignores devices/sections
without an IRK.
"""

import pytest
from renfield_satellite.satellite import Satellite

_WITH_IRK = """[General]
Name=Karnak

[IdentityResolvingKey]
Key=3A66FE43118690229991659536EF9A4B

[LinkKey]
Key=0123456789ABCDEF0123456789ABCDEF
"""

_NO_IRK = """[General]
Name=Speaker

[LinkKey]
Key=FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
"""


@pytest.mark.satellite
@pytest.mark.unit
def test_read_bonded_irks_extracts_only_irk(tmp_path):
    adapter = tmp_path / "10:11:12:13:14:15"
    # device with an IRK
    d1 = adapter / "4C:E6:C0:27:52:93"
    d1.mkdir(parents=True)
    (d1 / "info").write_text(_WITH_IRK)
    # device without an IRK (e.g. a Classic speaker) — must be ignored
    d2 = adapter / "AA:BB:CC:DD:EE:FF"
    d2.mkdir(parents=True)
    (d2 / "info").write_text(_NO_IRK)

    result = Satellite._read_bonded_irks(str(tmp_path))

    # BlueZ stores the key LSO-first (3A66…9A4B); the reader returns it
    # MSO-first (4B9A…663A) so the software RPA resolver actually matches.
    assert result == {"4C:E6:C0:27:52:93": "4B9AEF36956591992290861143FE663A"}


@pytest.mark.satellite
@pytest.mark.unit
def test_read_bonded_irks_reverses_byte_order(tmp_path):
    """The returned IRK is BlueZ's stored key with the bytes reversed."""
    adapter = tmp_path / "10:11:12:13:14:15"
    dev = adapter / "4C:E6:C0:27:52:93"
    dev.mkdir(parents=True)
    (dev / "info").write_text(_WITH_IRK)

    bluez_stored = "3A66FE43118690229991659536EF9A4B"
    expected = bytes.fromhex(bluez_stored)[::-1].hex().upper()

    result = Satellite._read_bonded_irks(str(tmp_path))

    assert result["4C:E6:C0:27:52:93"] == expected


@pytest.mark.satellite
@pytest.mark.unit
def test_read_bonded_irks_missing_root(tmp_path):
    assert Satellite._read_bonded_irks(str(tmp_path / "nope")) == {}
