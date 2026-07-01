"""#657 — WS push-registration ownership boundary.

A client may only register a session for server-push delivery if it owns that
conversation (or the session is new/unowned). Enforced only when auth is on and
a JWT caller identity exists. Tested against the helper's logic with a faked DB
session so it stays a fast, deterministic unit test.
"""
import pytest

import api.websocket.chat_handler as ch


class _FakeResult:
    def __init__(self, val):
        self._val = val

    def scalar_one_or_none(self):
        return self._val


class _FakeSession:
    def __init__(self, owner):
        self._owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_k):
        return _FakeResult(self._owner)


def _patch(monkeypatch, *, auth_enabled: bool, owner):
    monkeypatch.setattr("utils.config.settings.auth_enabled", auth_enabled)
    monkeypatch.setattr(ch, "AsyncSessionLocal", lambda: _FakeSession(owner))


class TestSessionRegisterableBy:
    @pytest.mark.unit
    async def test_auth_disabled_always_allows(self, monkeypatch):
        # owner mismatch is irrelevant when auth is off (single-user mode)
        _patch(monkeypatch, auth_enabled=False, owner=999)
        assert await ch._session_registerable_by("s", 1) is True

    @pytest.mark.unit
    async def test_no_caller_identity_allows(self, monkeypatch):
        # device/satellite path (user_id=None) keeps legacy behavior
        _patch(monkeypatch, auth_enabled=True, owner=999)
        assert await ch._session_registerable_by("s", None) is True

    @pytest.mark.unit
    async def test_new_unowned_session_allowed(self, monkeypatch):
        _patch(monkeypatch, auth_enabled=True, owner=None)
        assert await ch._session_registerable_by("brand-new", 1) is True

    @pytest.mark.unit
    async def test_owner_match_allowed(self, monkeypatch):
        _patch(monkeypatch, auth_enabled=True, owner=1)
        assert await ch._session_registerable_by("s", 1) is True

    @pytest.mark.unit
    async def test_owner_mismatch_denied(self, monkeypatch):
        # the core fix: user 2 cannot register user 1's session for pushes
        _patch(monkeypatch, auth_enabled=True, owner=1)
        assert await ch._session_registerable_by("s", 2) is False
