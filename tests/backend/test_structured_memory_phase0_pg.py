"""Postgres-only schema tests for Structured Memory Phase 0.

Covers the additive columns from pc20260604_struct_mem against REAL Postgres
via ``pg_db_session`` (gated on RENFIELD_TEST_PG_URL; skipped when unset).
sqlite would not exercise JSONB semantics or the jsonb_build_array backfill,
so these are PG-only by design (see memory: test-against-real-postgres).

Scope here is the ORM/schema contract (columns exist, defaults, FKs, the
canonical self-pointer, the memory subject link) plus the migration's
entity_types backfill LOGIC. The full ``alembic upgrade head`` run — including
the CONCURRENTLY GIN build, which cannot run inside the fixture's outer
transaction — is verified on the .159 build box, not here.

NOTE: ``pg_db_session`` wraps everything in one outer transaction that rolls
back on teardown — tests MUST ``flush()``, never ``commit()``. To re-read a row
from the DB inside the async session use ``await session.refresh(obj)`` (NOT
``expire()`` + attribute access, which triggers a SYNC lazy-load and raises
MissingGreenlet under asyncpg).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.database import (
    ConversationMemory,
    KGEntity,
    KGRelation,
    Role,
    User,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


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


async def _make_entity(db: AsyncSession, owner: User, name: str, etype: str = "person", **kw) -> KGEntity:
    ent = KGEntity(user_id=owner.id, name=name, entity_type=etype, circle_tier=0, **kw)
    db.add(ent)
    await db.flush()
    return ent


class TestKGEntityColumns:
    async def test_surface_forms_and_entity_types_default_empty(self, pg_db_session):
        owner = await _make_user(pg_db_session, "sm_owner1")
        ent = await _make_entity(pg_db_session, owner, "Alice")
        # Python-side default=list + server_default '[]' => empty list, not None.
        assert ent.surface_forms == []
        assert ent.entity_types == []
        assert ent.external_id is None

    async def test_jsonb_roundtrip(self, pg_db_session):
        owner = await _make_user(pg_db_session, "sm_owner2")
        ent = await _make_entity(
            pg_db_session, owner, "Sam Star",
            surface_forms=["Sam S.", "SS"],
            entity_types=["person", "musician"],
            external_id="Q2831",
        )
        await pg_db_session.refresh(ent)  # round-trip through Postgres (async)
        assert ent.surface_forms == ["Sam S.", "SS"]
        assert ent.entity_types == ["person", "musician"]
        assert ent.external_id == "Q2831"

    async def test_canonical_self_pointer_tombstone(self, pg_db_session):
        owner = await _make_user(pg_db_session, "sm_owner3")
        winner = await _make_entity(pg_db_session, owner, "Alice")
        loser = await _make_entity(pg_db_session, owner, "Alice Brown")
        # Mark loser as a merge tombstone pointing at the winner.
        loser.canonical_id = winner.id
        loser.is_active = False
        await pg_db_session.flush()
        reloaded = (await pg_db_session.execute(
            select(KGEntity)
            .options(selectinload(KGEntity.canonical))  # eager-load (async-safe)
            .where(KGEntity.id == loser.id)
        )).scalar_one()
        assert reloaded.canonical_id == winner.id
        assert reloaded.is_active is False
        # The self-relationship resolves to the surviving entity.
        assert reloaded.canonical is not None
        assert reloaded.canonical.id == winner.id

    async def test_live_rows_have_null_canonical_id(self, pg_db_session):
        owner = await _make_user(pg_db_session, "sm_owner4")
        ent = await _make_entity(pg_db_session, owner, "Carol")
        assert ent.canonical_id is None  # canonical/live by default


class TestKGRelationProvenance:
    async def test_stated_by_and_source_message_columns(self, pg_db_session):
        owner = await _make_user(pg_db_session, "sm_rel_owner")
        speaker = await _make_user(pg_db_session, "sm_speaker")
        subj = await _make_entity(pg_db_session, owner, "Alice")
        obj = await _make_entity(pg_db_session, owner, "Sam Star", etype="person")
        rel = KGRelation(
            user_id=owner.id, subject_id=subj.id, predicate="mag_musik_von",
            object_id=obj.id, circle_tier=0,
            stated_by_user_id=speaker.id, source_message_id=None,
        )
        pg_db_session.add(rel)
        await pg_db_session.flush()
        await pg_db_session.refresh(rel)
        # Provenance (who asserted) is distinct from the owner.
        assert rel.stated_by_user_id == speaker.id
        assert rel.user_id == owner.id
        assert rel.source_message_id is None


class TestConversationMemorySubject:
    async def test_subject_entity_link_and_name(self, pg_db_session):
        owner = await _make_user(pg_db_session, "sm_mem_owner")
        alice = await _make_entity(pg_db_session, owner, "Alice")
        mem = ConversationMemory(
            user_id=owner.id,
            content="Alice mag Musik von Sam Star",
            category="fact",
            circle_tier=0,
            subject_entity_id=alice.id,
            subject_name="Alice",
        )
        pg_db_session.add(mem)
        await pg_db_session.flush()
        await pg_db_session.refresh(mem)
        assert mem.subject_entity_id == alice.id
        assert mem.subject_name == "Alice"

    async def test_subject_defaults_null(self, pg_db_session):
        owner = await _make_user(pg_db_session, "sm_mem_owner2")
        mem = ConversationMemory(
            user_id=owner.id, content="Es regnet.", category="context", circle_tier=0,
        )
        pg_db_session.add(mem)
        await pg_db_session.flush()
        assert mem.subject_entity_id is None
        assert mem.subject_name is None


class TestEntityTypesBackfill:
    """Validates the migration's backfill body (entity_types = [entity_type])
    by running the same UPDATE the migration runs. create_all defaults new
    rows to [], so we simulate a pre-migration row and assert the backfill."""

    async def test_backfill_sets_entity_types_from_scalar(self, pg_db_session):
        owner = await _make_user(pg_db_session, "sm_bf_owner")
        ent = await _make_entity(pg_db_session, owner, "Wikipedia", etype="organization")
        # Simulate a pre-migration row: empty multi-type array.
        ent.entity_types = []
        await pg_db_session.flush()

        await pg_db_session.execute(text(
            "UPDATE kg_entities SET entity_types = jsonb_build_array(entity_type) "
            "WHERE entity_types = '[]'::jsonb AND id = :id"
        ), {"id": ent.id})
        await pg_db_session.refresh(ent)  # raw UPDATE bypassed the ORM identity map
        assert ent.entity_types == ["organization"]

    async def test_backfill_is_idempotent(self, pg_db_session):
        owner = await _make_user(pg_db_session, "sm_bf_owner2")
        ent = await _make_entity(pg_db_session, owner, "Alice")
        ent.entity_types = ["person", "musician"]  # already populated
        await pg_db_session.flush()

        await pg_db_session.execute(text(
            "UPDATE kg_entities SET entity_types = jsonb_build_array(entity_type) "
            "WHERE entity_types = '[]'::jsonb AND id = :id"
        ), {"id": ent.id})
        await pg_db_session.refresh(ent)
        # Untouched — backfill only fills empty arrays.
        assert ent.entity_types == ["person", "musician"]
