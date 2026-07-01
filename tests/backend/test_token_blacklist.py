"""Tests for the JWT revocation blacklist — fail-closed behavior (#698)."""
import pytest

from services.token_blacklist import TokenBlacklist


class TestTokenBlacklistFailClosed:
    """The revocation check must FAIL CLOSED when the store is unreachable."""

    @pytest.mark.unit
    async def test_is_blacklisted_fails_closed_on_store_error(self, monkeypatch):
        """#698: if Redis is unreachable we cannot prove the token is NOT
        revoked, so treat it as revoked (deny) rather than honor an
        unverifiable token."""
        tb = TokenBlacklist()

        def _boom():
            raise ConnectionError("redis unreachable")

        monkeypatch.setattr(tb, "_get_redis", _boom)
        assert await tb.is_blacklisted("any-jti") is True

    @pytest.mark.unit
    async def test_is_blacklisted_error_on_exists_fails_closed(self, monkeypatch):
        """Same fail-closed guarantee when the connection is created but the
        query itself raises (e.g. mid-request Redis drop)."""

        class _Redis:
            async def exists(self, *_a, **_k):
                raise ConnectionError("dropped mid-query")

        tb = TokenBlacklist()
        monkeypatch.setattr(tb, "_get_redis", lambda: _Redis())
        assert await tb.is_blacklisted("any-jti") is True
