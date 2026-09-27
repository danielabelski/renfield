"""Security review H1 — per-satellite enrollment credential.

Covers the enrollment service (mint/verify/revoke/rotate), the constant-time
verdict path, the effective-mode state machine (PERMISSIVE vs ENFORCING), the
auto-flip latch, and the IRK-push re-keying. Runs on the sqlite test harness —
all queries are dialect-neutral (select / func.count / column comparisons).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import ha_glue.services.satellite_enrollment_service as svc
from models.database import Satellite, SatelliteFleetState
from utils.config import settings


@pytest.fixture
def _enrollment_on(monkeypatch):
    monkeypatch.setattr(settings, "satellite_enrollment_enabled", True)
    monkeypatch.setattr(settings, "satellite_enrollment_autoflip_enabled", False)


@pytest.mark.backend
@pytest.mark.database
class TestEnrollAndVerify:
    async def test_enroll_returns_token_and_stores_only_hash(self, db_session: AsyncSession):
        token = await svc.enroll_satellite(db_session, "sat-wohnzimmer", room="Wohnzimmer")
        assert token and len(token) > 20

        row = (
            await db_session.execute(select(Satellite).where(Satellite.satellite_id == "sat-wohnzimmer"))
        ).scalar_one()
        # The plaintext is never persisted — only a bcrypt hash.
        assert row.token_hash != token
        assert row.token_hash.startswith("$2")  # bcrypt
        assert row.last_authenticated_at is None

    async def test_verify_ok_stamps_last_authenticated(self, db_session: AsyncSession):
        token = await svc.enroll_satellite(db_session, "sat-x")
        verdict = await svc.evaluate_credential(db_session, "sat-x", token)
        assert verdict == svc.VERDICT_OK

        row = (
            await db_session.execute(select(Satellite).where(Satellite.satellite_id == "sat-x"))
        ).scalar_one()
        assert row.last_authenticated_at is not None

    async def test_verify_wrong_token_is_bad(self, db_session: AsyncSession):
        await svc.enroll_satellite(db_session, "sat-x")
        assert await svc.evaluate_credential(db_session, "sat-x", "not-the-token") == svc.VERDICT_BAD

    async def test_verify_unknown_satellite_is_bad(self, db_session: AsyncSession):
        assert await svc.evaluate_credential(db_session, "sat-ghost", "anything") == svc.VERDICT_BAD

    async def test_no_token_is_no_token(self, db_session: AsyncSession):
        await svc.enroll_satellite(db_session, "sat-x")
        assert await svc.evaluate_credential(db_session, "sat-x", None) == svc.VERDICT_NO_TOKEN
        assert await svc.evaluate_credential(db_session, "sat-x", "") == svc.VERDICT_NO_TOKEN

    async def test_revoked_satellite_cannot_authenticate(self, db_session: AsyncSession):
        token = await svc.enroll_satellite(db_session, "sat-x")
        assert await svc.revoke_satellite(db_session, "sat-x") is True
        # Even with the correct PSK, a revoked row is rejected.
        assert await svc.evaluate_credential(db_session, "sat-x", token) == svc.VERDICT_BAD

    async def test_revoke_missing_returns_false(self, db_session: AsyncSession):
        assert await svc.revoke_satellite(db_session, "nope") is False


@pytest.mark.backend
@pytest.mark.database
class TestEnrollRotation:
    async def test_double_enroll_without_rotate_returns_none(self, db_session: AsyncSession):
        assert await svc.enroll_satellite(db_session, "sat-x") is not None
        assert await svc.enroll_satellite(db_session, "sat-x") is None  # already enrolled

    async def test_rotate_issues_new_token_and_invalidates_old(self, db_session: AsyncSession):
        old = await svc.enroll_satellite(db_session, "sat-x")
        # mark it authenticated so we can prove rotate resets it
        await svc.evaluate_credential(db_session, "sat-x", old)

        new = await svc.enroll_satellite(db_session, "sat-x", rotate=True)
        assert new and new != old
        assert await svc.evaluate_credential(db_session, "sat-x", old) == svc.VERDICT_BAD
        assert await svc.evaluate_credential(db_session, "sat-x", new) == svc.VERDICT_OK

    async def test_rotate_reactivates_revoked(self, db_session: AsyncSession):
        await svc.enroll_satellite(db_session, "sat-x")
        await svc.revoke_satellite(db_session, "sat-x")
        new = await svc.enroll_satellite(db_session, "sat-x", rotate=True)
        assert await svc.evaluate_credential(db_session, "sat-x", new) == svc.VERDICT_OK

    async def test_concurrent_enroll_integrityerror_converges_to_none(
        self, db_session: AsyncSession, monkeypatch
    ):
        # Simulate the unique-index race: a concurrent enroll committed first, so
        # this INSERT commit raises IntegrityError. The service must converge to
        # None (→ the route's 409) instead of letting a 500 escape.
        from sqlalchemy.exc import IntegrityError

        async def boom():
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))

        monkeypatch.setattr(db_session, "commit", boom)
        assert await svc.enroll_satellite(db_session, "sat-race") is None

    async def test_plain_enroll_does_not_resurrect_revoked(self, db_session: AsyncSession):
        # A revoked satellite must NOT be silently re-enabled by a plain enroll;
        # resurrection requires an explicit rotate (review finding).
        await svc.enroll_satellite(db_session, "sat-x")
        await svc.revoke_satellite(db_session, "sat-x")
        assert await svc.enroll_satellite(db_session, "sat-x") is None  # no rotate → refused
        # Still revoked: even the (now-unknown) caller can't authenticate.
        row = (
            await db_session.execute(select(Satellite).where(Satellite.satellite_id == "sat-x"))
        ).scalar_one()
        assert row.is_enabled is False and row.revoked_at is not None


@pytest.mark.backend
@pytest.mark.database
class TestAuthorizeRegisterModes:
    async def test_disabled_is_legacy_passthrough(self, db_session: AsyncSession, monkeypatch):
        monkeypatch.setattr(settings, "satellite_enrollment_enabled", False)
        res = await svc.authorize_register(db_session, "sat-x", None)
        assert res.authenticated is False and res.reject is False

    async def test_permissive_allows_no_token(self, db_session: AsyncSession, _enrollment_on):
        res = await svc.authorize_register(db_session, "sat-x", None)
        assert res.reject is False
        assert res.authenticated is False
        assert res.reason == "unenrolled-permissive"

    async def test_permissive_rejects_bad_token(self, db_session: AsyncSession, _enrollment_on):
        await svc.enroll_satellite(db_session, "sat-x")
        res = await svc.authorize_register(db_session, "sat-x", "wrong")
        assert res.reject is True
        assert res.reason == "invalid-credential"

    async def test_valid_token_authenticates(self, db_session: AsyncSession, _enrollment_on):
        token = await svc.enroll_satellite(db_session, "sat-x")
        res = await svc.authorize_register(db_session, "sat-x", token)
        assert res.authenticated is True and res.reject is False

    async def test_enforcing_rejects_no_token(self, db_session: AsyncSession, monkeypatch):
        monkeypatch.setattr(settings, "satellite_enrollment_enabled", True)
        monkeypatch.setattr(settings, "satellite_enrollment_autoflip_enabled", True)
        token = await svc.enroll_satellite(db_session, "sat-x")
        # Authenticating the only enrolled satellite latches ENFORCING.
        await svc.authorize_register(db_session, "sat-x", token)
        assert await svc.is_enforcing(db_session) is True

        res = await svc.authorize_register(db_session, "sat-y", None)
        assert res.reject is True
        assert res.reason == "enrollment-required"


@pytest.mark.backend
@pytest.mark.database
class TestAutoFlipLatch:
    async def test_no_flip_when_autoflip_disabled(self, db_session: AsyncSession, _enrollment_on):
        token = await svc.enroll_satellite(db_session, "sat-x")
        await svc.evaluate_credential(db_session, "sat-x", token)
        assert await svc.maybe_autoflip(db_session) is False
        assert await svc.is_enforcing(db_session) is False

    async def test_no_flip_while_a_satellite_pending(self, db_session: AsyncSession, monkeypatch):
        monkeypatch.setattr(settings, "satellite_enrollment_enabled", True)
        monkeypatch.setattr(settings, "satellite_enrollment_autoflip_enabled", True)
        t1 = await svc.enroll_satellite(db_session, "sat-1")
        await svc.enroll_satellite(db_session, "sat-2")  # never authenticates
        await svc.evaluate_credential(db_session, "sat-1", t1)
        # sat-2 still pending → no flip.
        assert await svc.maybe_autoflip(db_session) is False
        assert await svc.is_enforcing(db_session) is False

    async def test_flip_when_all_authenticated(self, db_session: AsyncSession, monkeypatch):
        monkeypatch.setattr(settings, "satellite_enrollment_enabled", True)
        monkeypatch.setattr(settings, "satellite_enrollment_autoflip_enabled", True)
        t1 = await svc.enroll_satellite(db_session, "sat-1")
        t2 = await svc.enroll_satellite(db_session, "sat-2")
        await svc.evaluate_credential(db_session, "sat-1", t1)
        await svc.evaluate_credential(db_session, "sat-2", t2)
        assert await svc.maybe_autoflip(db_session) is True
        assert await svc.is_enforcing(db_session) is True

    async def test_latch_not_cleared_by_new_pending_satellite(self, db_session: AsyncSession, monkeypatch):
        monkeypatch.setattr(settings, "satellite_enrollment_enabled", True)
        monkeypatch.setattr(settings, "satellite_enrollment_autoflip_enabled", True)
        t1 = await svc.enroll_satellite(db_session, "sat-1")
        await svc.evaluate_credential(db_session, "sat-1", t1)
        assert await svc.maybe_autoflip(db_session) is True

        # Enroll a NEW satellite that has not authenticated. The latch must hold
        # (a never-connected new row must not re-open the fleet).
        await svc.enroll_satellite(db_session, "sat-new")
        assert await svc.maybe_autoflip(db_session) is False  # no re-flip needed
        assert await svc.is_enforcing(db_session) is True

    async def test_singleton_state_row(self, db_session: AsyncSession, monkeypatch):
        monkeypatch.setattr(settings, "satellite_enrollment_enabled", True)
        monkeypatch.setattr(settings, "satellite_enrollment_autoflip_enabled", True)
        t1 = await svc.enroll_satellite(db_session, "sat-1")
        await svc.evaluate_credential(db_session, "sat-1", t1)
        await svc.maybe_autoflip(db_session)
        rows = (await db_session.execute(select(SatelliteFleetState))).scalars().all()
        assert len(rows) == 1 and rows[0].id == 1


@pytest.mark.backend
class TestIrkPushEnrollmentGate:
    """irks_for_satellite must key on enrollment auth when enrollment is on."""

    def _svc(self):
        from ha_glue.services.presence_service import PresenceService
        s = PresenceService.__new__(PresenceService)
        s._irks_hex = {"evdb": "00112233445566778899aabbccddeeff"}
        return s

    def test_enrollment_on_pushes_only_to_authenticated(self, monkeypatch):
        monkeypatch.setattr(settings, "satellite_enrollment_enabled", True)
        s = self._svc()
        assert s.irks_for_satellite("sat-x", is_enrolled_authenticated=True)
        assert s.irks_for_satellite("sat-x", is_enrolled_authenticated=False) == []

    def test_enrollment_off_falls_back_to_allowlist(self, monkeypatch):
        monkeypatch.setattr(settings, "satellite_enrollment_enabled", False)
        monkeypatch.setattr(settings, "satellite_irk_allowlist", "sat-known")
        s = self._svc()
        # Allowlist governs; the enrollment-auth flag is ignored when disabled.
        assert s.irks_for_satellite("sat-known", is_enrolled_authenticated=False)
        assert s.irks_for_satellite("sat-rogue", is_enrolled_authenticated=True) == []

    async def test_broadcast_push_respects_enrollment_auth(self, monkeypatch):
        """push_macs_to_satellites is the second IRK path — must also gate on
        per-connection enrollment auth when enrollment is on."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        import ha_glue.services.satellite_manager as sm
        monkeypatch.setattr(settings, "satellite_enrollment_enabled", True)

        ws_auth = AsyncMock()
        ws_unauth = AsyncMock()
        fake_mgr = SimpleNamespace(satellites={
            "sat-auth": SimpleNamespace(websocket=ws_auth, authenticated=True),
            "sat-unauth": SimpleNamespace(websocket=ws_unauth, authenticated=False),
        })
        monkeypatch.setattr(sm, "get_satellite_manager", lambda: fake_mgr)

        s = self._svc()
        s._mac_to_method = {}  # no MACs, only IRKs
        await s.push_macs_to_satellites()

        auth_sends = [c.args[0].get("type") for c in ws_auth.send_json.call_args_list]
        unauth_sends = [c.args[0].get("type") for c in ws_unauth.send_json.call_args_list]
        assert "ble_known_irks" in auth_sends
        assert "ble_known_irks" not in unauth_sends
