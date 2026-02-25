"""
JWT Token Blacklist Service

Redis-based token blacklist for logout/revocation.
Stores JTI (JWT ID) with TTL matching the token's remaining lifetime.
"""
import redis.asyncio as aioredis
from loguru import logger

from utils.config import settings

BLACKLIST_PREFIX = "blacklist:"


class TokenBlacklist:
    """Redis-backed JWT token blacklist."""

    def __init__(self):
        self._redis: aioredis.Redis | None = None

    def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                settings.redis_url, decode_responses=True
            )
        return self._redis

    async def add(self, jti: str, ttl_seconds: int) -> None:
        """
        Blacklist a token JTI.

        Args:
            jti: The JWT ID to blacklist
            ttl_seconds: Time-to-live in seconds (token's remaining lifetime)
        """
        if ttl_seconds <= 0:
            return
        try:
            redis = self._get_redis()
            await redis.setex(f"{BLACKLIST_PREFIX}{jti}", ttl_seconds, "1")
        except Exception as e:
            logger.error(f"Failed to blacklist token: {e}")

    async def is_blacklisted(self, jti: str) -> bool:
        """
        Check if a token JTI is blacklisted.

        Args:
            jti: The JWT ID to check

        Returns:
            True if the token has been revoked
        """
        try:
            redis = self._get_redis()
            return await redis.exists(f"{BLACKLIST_PREFIX}{jti}") > 0
        except Exception as e:
            logger.error(f"Failed to check token blacklist: {e}")
            # Fail open — if Redis is down, don't block all requests
            return False


# Singleton instance
token_blacklist = TokenBlacklist()
