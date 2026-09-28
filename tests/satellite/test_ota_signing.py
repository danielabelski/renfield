"""Security review H6 — signed OTA release manifest (satellite side).

A release is authenticated by an Ed25519-signed manifest of per-file source
hashes. The satellite verifies it against pinned public keys AFTER extract,
BEFORE install. Requires `cryptography` (already a satellite dep for IRK).
"""
import base64
import tempfile
from pathlib import Path

import pytest
from renfield_satellite.update.release_manifest import (
    build_manifest,
    canonical_bytes,
    verify_extracted,
    verify_signature,
)
from renfield_satellite.update.update_manager import (
    UpdateError,
    UpdateManager,
    UpdateRequest,
)

ed25519 = pytest.importorskip(
    "cryptography.hazmat.primitives.asymmetric.ed25519",
)


def _keypair():
    priv = ed25519.Ed25519PrivateKey.generate()
    return priv, priv.public_key().public_bytes_raw().hex()


def _make_source(root: Path, version="9.9.9"):
    """A clean, bytecode-free source tree (as the backend's stripped tarball)."""
    (root / "renfield_satellite").mkdir(parents=True)
    (root / "renfield_satellite" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (root / "renfield_satellite" / "foo.py").write_text("x = 1\n")
    (root / "requirements.txt").write_text("websockets\n")


def _add_pyc(root: Path):
    """Inject compiled bytecode (the .pyc-shadowing attack / a stray build)."""
    pc = root / "renfield_satellite" / "__pycache__"
    pc.mkdir(exist_ok=True)
    (pc / "foo.cpython-311.pyc").write_bytes(b"\x00malicious-bytecode")


@pytest.mark.satellite
class TestManifestAndSignature:
    def test_manifest_excludes_pyc_includes_source(self):
        root = Path(tempfile.mkdtemp())
        _make_source(root)
        _add_pyc(root)  # bytecode present in the tree...
        m = build_manifest(root, "9.9.9")
        assert "renfield_satellite/__init__.py" in m["files"]
        assert "renfield_satellite/foo.py" in m["files"]
        assert "requirements.txt" in m["files"]
        # ...but the manifest never lists it.
        assert not any("pyc" in k or "__pycache__" in k for k in m["files"])

    def test_signature_roundtrip_and_rotation(self):
        root = Path(tempfile.mkdtemp())
        _make_source(root)
        body = canonical_bytes(build_manifest(root, "9.9.9"))
        priv, pub = _keypair()
        sig = base64.b64encode(priv.sign(body)).decode()
        _, other = _keypair()

        assert verify_signature(body, sig, [pub]) is True
        assert verify_signature(body, sig, [other]) is False
        # N-key rotation: accept if ANY pinned key matches.
        assert verify_signature(body, sig, [other, pub]) is True
        # Tampered body fails.
        assert verify_signature(body + b" ", sig, [pub]) is False

    def test_verify_extracted_detects_tamper_and_injection(self):
        root = Path(tempfile.mkdtemp())
        _make_source(root)
        m = build_manifest(root, "9.9.9")
        assert verify_extracted(root, m) == []

        (root / "renfield_satellite" / "foo.py").write_text("x = 666\n")
        assert any("foo.py" in p for p in verify_extracted(root, m))

        (root / "renfield_satellite" / "foo.py").write_text("x = 1\n")  # restore
        (root / "renfield_satellite" / "evil.py").write_text("backdoor()\n")
        probs = verify_extracted(root, m)
        assert any("evil.py" in p and "unexpected" in p for p in probs)

    def test_verify_extracted_flags_injected_pyc(self):
        # P0 guard: a forged/injected .pyc (which the manifest never lists, but
        # CPython could load over a trusted .py) must surface as unexpected.
        root = Path(tempfile.mkdtemp())
        _make_source(root)
        m = build_manifest(root, "9.9.9")  # clean, pyc-free → signed
        _add_pyc(root)                      # attacker smuggles bytecode in the tarball
        probs = verify_extracted(root, m)
        assert any(".pyc" in p and "unexpected" in p for p in probs)


@pytest.mark.satellite
class TestUpdateManagerVerify:
    def _mgr(self, pubkeys, require):
        # Bypass __init__ path detection — only the verify fields matter here.
        m = UpdateManager.__new__(UpdateManager)
        m.release_pubkeys = pubkeys
        m.require_signature = require
        m._current_stage = None
        m._progress = 0
        m._on_progress = None
        return m

    def _signed(self, root, version="9.9.9"):
        body = canonical_bytes(build_manifest(root, version))
        priv, pub = _keypair()
        sig = base64.b64encode(priv.sign(body)).decode()
        return body.decode("utf-8"), sig, pub

    def test_unsigned_allowed_when_not_required(self):
        m = self._mgr([], require=False)
        req = UpdateRequest("9.9.9", "u", "sha256:x", 1, manifest=None, signature=None)
        m._verify_signature(Path(tempfile.mkdtemp()), req)  # no raise

    def test_unsigned_rejected_when_required(self):
        m = self._mgr(["aa"], require=True)
        req = UpdateRequest("9.9.9", "u", "sha256:x", 1, manifest=None, signature=None)
        with pytest.raises(UpdateError):
            m._verify_signature(Path(tempfile.mkdtemp()), req)

    def test_valid_signature_passes(self):
        root = Path(tempfile.mkdtemp())
        _make_source(root)
        manifest, sig, pub = self._signed(root)
        m = self._mgr([pub], require=True)
        req = UpdateRequest("9.9.9", "u", "sha256:x", 1, manifest=manifest, signature=sig)
        m._verify_signature(root, req)  # no raise

    def test_signed_but_no_pinned_keys_rejected(self):
        root = Path(tempfile.mkdtemp())
        _make_source(root)
        manifest, sig, _pub = self._signed(root)
        m = self._mgr([], require=False)  # signature present → must verify anyway
        req = UpdateRequest("9.9.9", "u", "sha256:x", 1, manifest=manifest, signature=sig)
        with pytest.raises(UpdateError):
            m._verify_signature(root, req)

    def test_bad_signature_rejected(self):
        root = Path(tempfile.mkdtemp())
        _make_source(root)
        manifest, _, pub = self._signed(root)
        m = self._mgr([pub], require=False)
        req = UpdateRequest("9.9.9", "u", "sha256:x", 1, manifest=manifest, signature="bm90")
        with pytest.raises(UpdateError):
            m._verify_signature(root, req)

    def test_version_mismatch_rejected(self):
        root = Path(tempfile.mkdtemp())
        _make_source(root, version="9.9.9")
        manifest, sig, pub = self._signed(root, version="9.9.9")
        m = self._mgr([pub], require=True)
        # target says 8.8.8 but the signed manifest says 9.9.9
        req = UpdateRequest("8.8.8", "u", "sha256:x", 1, manifest=manifest, signature=sig)
        with pytest.raises(UpdateError):
            m._verify_signature(root, req)

    def test_tampered_tree_rejected(self):
        root = Path(tempfile.mkdtemp())
        _make_source(root)
        manifest, sig, pub = self._signed(root)
        # tamper AFTER signing
        (root / "renfield_satellite" / "foo.py").write_text("x = 666\n")
        m = self._mgr([pub], require=True)
        req = UpdateRequest("9.9.9", "u", "sha256:x", 1, manifest=manifest, signature=sig)
        with pytest.raises(UpdateError):
            m._verify_signature(root, req)

    def test_injected_pyc_rejected(self):
        # P0: genuine signed manifest forwarded, but a malicious .pyc smuggled
        # into the extracted tree → must abort (every .py matches, the manifest
        # has no .pyc, so the injected bytecode is the only discrepancy).
        root = Path(tempfile.mkdtemp())
        _make_source(root)
        manifest, sig, pub = self._signed(root)
        _add_pyc(root)
        m = self._mgr([pub], require=True)
        req = UpdateRequest("9.9.9", "u", "sha256:x", 1, manifest=manifest, signature=sig)
        with pytest.raises(UpdateError):
            m._verify_signature(root, req)


@pytest.mark.satellite
class TestUpdateConfig:
    def test_yaml_loads_pubkeys_and_require(self, tmp_path):
        from renfield_satellite.config import load_config

        cfg = tmp_path / "satellite.yaml"
        cfg.write_text(
            "update:\n"
            "  release_pubkeys:\n"
            "    - aabbcc\n"
            "    - ddeeff\n"
            "  require_signature: true\n"
        )
        c = load_config(str(cfg))
        assert c.update.release_pubkeys == ["aabbcc", "ddeeff"]
        assert c.update.require_signature is True

    def test_defaults_are_safe(self):
        from renfield_satellite.config import UpdateConfig

        u = UpdateConfig()
        assert u.release_pubkeys == [] and u.require_signature is False
