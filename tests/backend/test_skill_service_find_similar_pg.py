"""Postgres-only regression tests for SkillService.find_similar.

These guard the asyncpg "could not determine data type of parameter $3"
bug: the visibility WHERE-clause referenced :asker in a bare ``IS NULL``
arm. SQLAlchemy renders each named-param occurrence as its own
positional placeholder, so that arm carried no type context and Postgres
aborted the Parse before a single row was scanned — find_similar threw on
EVERY agent turn in production. The fix casts :asker to INTEGER at every
site.

This bug is INVISIBLE on the sqlite test harness — sqlite binds the
parameter without a server-side Parse/type-inference step, so the
existing find_similar tests in test_skill_service.py stayed green. Hence:
real-Postgres only, via the ``pg_db_session`` fixture (gated on
RENFIELD_TEST_PG_URL; skipped when unset).

NOTE: ``pg_db_session`` wraps everything in one outer transaction that
rolls back on teardown — tests MUST ``flush()``, never ``commit()``.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import (
    EMBEDDING_DIMENSION,
    SKILL_SOURCE_AUTO_EXTRACTED,
    SKILL_SOURCE_SEED,
    TIER_HOUSEHOLD,
    TIER_PUBLIC,
    TIER_SELF,
    CircleMembership,
    ProceduralSkill,
    Role,
    User,
)
from services.skill_service import SkillService

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


# ---------------------------------------------------------------- helpers
def _vec(seed: int, dim: int = EMBEDDING_DIMENSION) -> list[float]:
    v = [0.0] * dim
    v[seed % dim] = 1.0
    return v


async def _make_user(db: AsyncSession, name: str) -> User:
    role = Role(name=f"{name}_role")
    db.add(role)
    await db.flush()
    user = User(
        username=name, email=f"{name}@ex.test",
        password_hash="x", role_id=role.id, is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _skill(
    db: AsyncSession,
    *,
    user_id: int | None,
    title: str,
    embedding: list[float],
    source: str = SKILL_SOURCE_AUTO_EXTRACTED,
    status: str = "approved",
    tier: int = TIER_SELF,
) -> ProceduralSkill:
    s = ProceduralSkill(
        user_id=user_id, title=title, body_md="- body",
        trigger_examples=["t"], tool_sequence=["mcp.x.y"],
        source=source, embedding=embedding,
        success_count=0, failure_count=0,
        status=status, circle_tier=tier, atom_id=None,
    )
    db.add(s)
    await db.flush()
    return s


async def _membership(
    db: AsyncSession, *, owner_id: int, member_id: int, tier: int
) -> None:
    db.add(CircleMembership(
        circle_owner_id=owner_id, member_user_id=member_id,
        dimension="tier", value=tier, granted_by=owner_id,
    ))
    await db.flush()


def _patch_embed(vec: list[float]):
    """Force SkillService._embed to return a fixed query vector so the SQL
    runs against a known embedding (no Ollama round-trip)."""
    async def _fake(self, _text):
        return vec
    return patch.object(SkillService, "_embed", _fake)


# ---------------------------------------------------------------------------
class TestFindSimilarTypedParam:
    async def test_integer_asker_does_not_raise(self, pg_db_session):
        """The core regression: find_similar with an integer asker_id ran
        the visibility clause with a bare ``:asker IS NULL`` arm and threw
        AmbiguousParameterError on Postgres. It must now execute cleanly
        and return the asker's own skill."""
        user = await _make_user(pg_db_session, "fs_owner")
        await _skill(
            pg_db_session, user_id=user.id, title="Own skill",
            embedding=_vec(3),
        )
        svc = SkillService(pg_db_session)
        with _patch_embed(_vec(3)):
            # Pre-fix: raises asyncpg AmbiguousParameterError here.
            result = await svc.find_similar("do the thing", asker_id=user.id)
        assert "Own skill" in {r["title"] for r in result}

    async def test_owner_isolation(self, pg_db_session):
        owner = await _make_user(pg_db_session, "fs_a")
        other = await _make_user(pg_db_session, "fs_b")
        await _skill(pg_db_session, user_id=owner.id, title="Mine",
                     embedding=_vec(4))
        await _skill(pg_db_session, user_id=other.id, title="Theirs",
                     embedding=_vec(4))
        svc = SkillService(pg_db_session)
        with _patch_embed(_vec(4)):
            result = await svc.find_similar("x", asker_id=owner.id)
        titles = {r["title"] for r in result}
        assert "Mine" in titles
        assert "Theirs" not in titles

    async def test_public_seed_visible_to_any_asker(self, pg_db_session):
        asker = await _make_user(pg_db_session, "fs_seed_asker")
        await _skill(
            pg_db_session, user_id=None, title="Public seed",
            embedding=_vec(6), source=SKILL_SOURCE_SEED, tier=TIER_PUBLIC,
        )
        svc = SkillService(pg_db_session)
        with _patch_embed(_vec(6)):
            result = await svc.find_similar("x", asker_id=asker.id)
        assert "Public seed" in {r["title"] for r in result}

    async def test_circle_reach_exists_arm(self, pg_db_session):
        """Exercises the EXISTS subquery the error log pointed at: a tier-2
        household member reaches the owner's household-tier skill but not
        the owner's self-tier skill."""
        owner = await _make_user(pg_db_session, "fs_owner2")
        member = await _make_user(pg_db_session, "fs_member")
        await _membership(
            pg_db_session, owner_id=owner.id, member_id=member.id,
            tier=TIER_HOUSEHOLD,
        )
        await _skill(
            pg_db_session, user_id=owner.id, title="Household skill",
            embedding=_vec(8), tier=TIER_HOUSEHOLD,
        )
        await _skill(
            pg_db_session, user_id=owner.id, title="Self skill",
            embedding=_vec(8), tier=TIER_SELF,
        )
        svc = SkillService(pg_db_session)
        with _patch_embed(_vec(8)):
            result = await svc.find_similar("x", asker_id=member.id)
        titles = {r["title"] for r in result}
        assert "Household skill" in titles
        assert "Self skill" not in titles
