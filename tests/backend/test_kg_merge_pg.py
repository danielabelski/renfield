"""Postgres-only tests for KnowledgeGraphService.merge_entities (Phase 1, T2).

Real PG via ``pg_db_session`` (skipped when RENFIELD_TEST_PG_URL unset) — the
merge uses PG-only SQL (json_build_object, LEAST, jsonb columns) that the sqlite
shim can't run.

``merge_entities`` calls ``self.db.commit()`` / ``rollback()`` internally; under
the rollback-isolated ``pg_db_session`` those would break test isolation, so we
monkeypatch both to ``flush`` (writes stay visible inside the outer txn, which is
rolled back on teardown). ``_get_embedding`` is mocked to avoid an Ollama call.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import EMBEDDING_DIMENSION, ConversationMemory, KGEntity, KGRelation, Role, User
from services.knowledge_graph_service import KnowledgeGraphService

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def _make_user(db: AsyncSession, name: str) -> User:
    role = Role(name=f"{name}_role")
    db.add(role)
    await db.flush()
    user = User(username=name, email=f"{name}@ex.test", password_hash="x",
                role_id=role.id, is_active=True)
    db.add(user)
    await db.flush()
    return user


async def _entity(db, owner, name, *, tier=0, etype="person", **kw) -> KGEntity:
    e = KGEntity(user_id=owner.id, name=name, entity_type=etype, circle_tier=tier, **kw)
    db.add(e)
    await db.flush()
    return e


async def _relation(db, owner, subj, pred, obj, *, tier=0) -> KGRelation:
    r = KGRelation(user_id=owner.id, subject_id=subj.id, predicate=pred,
                   object_id=obj.id, circle_tier=tier, is_active=True)
    db.add(r)
    await db.flush()
    return r


def _svc(db, monkeypatch) -> KnowledgeGraphService:
    # commit/rollback -> flush so the merge's internal txn control doesn't break
    # the fixture's rollback isolation.
    monkeypatch.setattr(db, "commit", db.flush)
    monkeypatch.setattr(db, "rollback", db.flush)
    svc = KnowledgeGraphService(db)
    monkeypatch.setattr(svc, "_get_embedding",
                        AsyncMock(return_value=[0.01] * EMBEDDING_DIMENSION))
    return svc


async def _active_relations(db, **where) -> list:
    sql = "SELECT id, subject_id, object_id, predicate, circle_tier, confidence FROM kg_relations WHERE is_active = true"
    params = {}
    for k, v in where.items():
        sql += f" AND {k} = :{k}"
        params[k] = v
    return (await db.execute(text(sql), params)).fetchall()


class TestAbsorbAndTombstone:
    async def test_basic_absorb(self, pg_db_session, monkeypatch):
        owner = await _make_user(pg_db_session, "mg_a")
        winner = await _entity(pg_db_session, owner, "Alice", mention_count=12)
        loser = await _entity(pg_db_session, owner, "Alice Brown",
                              surface_forms=["A.B."], entity_types=["person"], mention_count=3)
        svc = _svc(pg_db_session, monkeypatch)

        assert await svc.merge_entities(loser.id, winner.id) is not None

        assert loser.is_active is False
        assert loser.canonical_id == winner.id
        # loser.name + loser's surface forms folded into the survivor
        assert "Alice Brown" in winner.surface_forms
        assert "A.B." in winner.surface_forms
        assert "Alice" not in winner.surface_forms  # own canonical name excluded
        assert winner.entity_types == ["person"]
        assert winner.mention_count == 15

    async def test_idpair_noop(self, pg_db_session, monkeypatch):
        owner = await _make_user(pg_db_session, "mg_noop")
        e = await _entity(pg_db_session, owner, "Alice")
        svc = _svc(pg_db_session, monkeypatch)
        assert await svc.merge_entities(e.id, e.id) is None


class TestReparentRelations:
    async def test_relation_follows_to_winner(self, pg_db_session, monkeypatch):
        owner = await _make_user(pg_db_session, "mg_rel")
        winner = await _entity(pg_db_session, owner, "Alice")
        loser = await _entity(pg_db_session, owner, "Alice B.")
        mj = await _entity(pg_db_session, owner, "Sam Star")
        await _relation(pg_db_session, owner, loser, "mag_musik_von", mj)
        svc = _svc(pg_db_session, monkeypatch)

        assert await svc.merge_entities(loser.id, winner.id) is not None

        assert await _active_relations(pg_db_session, subject_id=loser.id) == []
        surv = await _active_relations(pg_db_session, subject_id=winner.id)
        assert len(surv) == 1 and surv[0].object_id == mj.id

    async def test_duplicate_relations_deduped(self, pg_db_session, monkeypatch):
        owner = await _make_user(pg_db_session, "mg_dup")
        winner = await _entity(pg_db_session, owner, "Alice")
        loser = await _entity(pg_db_session, owner, "Alice B.")
        mj = await _entity(pg_db_session, owner, "Sam Star")
        await _relation(pg_db_session, owner, winner, "mag_musik_von", mj)
        await _relation(pg_db_session, owner, loser, "mag_musik_von", mj)
        svc = _svc(pg_db_session, monkeypatch)

        assert await svc.merge_entities(loser.id, winner.id) is not None

        surv = await _active_relations(pg_db_session, subject_id=winner.id, object_id=mj.id)
        assert len(surv) == 1  # the duplicate triple collapsed to one active row

    async def test_self_loop_removed(self, pg_db_session, monkeypatch):
        owner = await _make_user(pg_db_session, "mg_loop")
        winner = await _entity(pg_db_session, owner, "Alice")
        loser = await _entity(pg_db_session, owner, "Alice B.")
        # an edge winner -> loser becomes a self-loop after the merge
        await _relation(pg_db_session, owner, winner, "ist", loser)
        svc = _svc(pg_db_session, monkeypatch)

        assert await svc.merge_entities(loser.id, winner.id) is not None

        loops = await _active_relations(pg_db_session, subject_id=winner.id, object_id=winner.id)
        assert loops == []


class TestTierInvariant:
    async def test_survivor_tier_is_min_and_cascades(self, pg_db_session, monkeypatch):
        owner = await _make_user(pg_db_session, "mg_tier")
        winner = await _entity(pg_db_session, owner, "Alice", tier=2)   # household
        loser = await _entity(pg_db_session, owner, "Alice B.", tier=0)  # self
        other = await _entity(pg_db_session, owner, "Bonn", tier=2, etype="place")
        await _relation(pg_db_session, owner, loser, "wohnt_in", other, tier=0)
        svc = _svc(pg_db_session, monkeypatch)

        assert await svc.merge_entities(loser.id, winner.id) is not None

        # merge never raises visibility: survivor takes the more-restrictive tier
        assert winner.circle_tier == 0
        rel = (await _active_relations(pg_db_session, subject_id=winner.id))[0]
        # LEAST(winner=0, other=2) == 0
        assert rel.circle_tier == 0


class TestAtomPolicySync:
    async def test_winner_atom_policy_follows_merged_tier(self, pg_db_session, monkeypatch):
        # Regression: the survivor's kg_node atom policy is updated via
        # json_build_object('tier', CAST(:t AS INTEGER)). Without the cast asyncpg
        # raised IndeterminateDatatypeError ($1) → 500 on POST /entities/merge.
        # The other fixtures create bare entities (no atom_id), so this branch
        # never ran and the bug shipped. Here the winner HAS an atom.
        from models.database import ATOM_TYPE_KG_NODE, Atom
        owner = await _make_user(pg_db_session, "mg_atom")
        # entity first (no atom_id), then the atom it points at — the
        # kg_entities.atom_id FK requires the atoms row to exist before linking.
        winner = await _entity(pg_db_session, owner, "Alice", tier=2)
        loser = await _entity(pg_db_session, owner, "Alice B.", tier=0)
        aid = "11111111-1111-1111-1111-111111111111"
        pg_db_session.add(Atom(
            atom_id=aid, atom_type=ATOM_TYPE_KG_NODE,
            source_table="kg_entities", source_id=str(winner.id),
            owner_user_id=owner.id, policy={"tier": 2},
        ))
        await pg_db_session.flush()
        winner.atom_id = aid
        await pg_db_session.flush()
        svc = _svc(pg_db_session, monkeypatch)

        # would raise asyncpg IndeterminateDatatypeError before the CAST fix
        assert await svc.merge_entities(loser.id, winner.id) is not None

        assert winner.circle_tier == 0  # MIN(2, 0)
        pol = (await pg_db_session.execute(
            select(Atom.policy).where(Atom.atom_id == winner.atom_id)
        )).scalar_one()
        assert pol == {"tier": 0}  # atom policy followed the merged tier


class TestMemorySubjectFollows:
    async def test_subject_entity_repointed(self, pg_db_session, monkeypatch):
        owner = await _make_user(pg_db_session, "mg_mem")
        winner = await _entity(pg_db_session, owner, "Alice")
        loser = await _entity(pg_db_session, owner, "Alice B.")
        mem = ConversationMemory(user_id=owner.id, content="Alice mag X",
                                 category="fact", circle_tier=0, subject_entity_id=loser.id)
        pg_db_session.add(mem)
        await pg_db_session.flush()
        svc = _svc(pg_db_session, monkeypatch)

        assert await svc.merge_entities(loser.id, winner.id) is not None

        row = (await pg_db_session.execute(text(
            "SELECT subject_entity_id FROM conversation_memories WHERE id = :i"
        ), {"i": mem.id})).scalar_one()
        assert row == winner.id


class TestGuardsAndIsolation:
    async def test_second_merge_is_skipped(self, pg_db_session, monkeypatch):
        owner = await _make_user(pg_db_session, "mg_guard")
        winner = await _entity(pg_db_session, owner, "Alice", mention_count=10)
        loser = await _entity(pg_db_session, owner, "Alice B.", mention_count=5)
        svc = _svc(pg_db_session, monkeypatch)

        assert await svc.merge_entities(loser.id, winner.id) is not None
        assert winner.mention_count == 15
        # already-merged guard: second call is a no-op, no double-count
        assert await svc.merge_entities(loser.id, winner.id) is None
        assert winner.mention_count == 15

    async def test_cross_user_refused(self, pg_db_session, monkeypatch):
        a = await _make_user(pg_db_session, "mg_u_a")
        b = await _make_user(pg_db_session, "mg_u_b")
        winner = await _entity(pg_db_session, a, "Alice")
        loser = await _entity(pg_db_session, b, "Alice")
        svc = _svc(pg_db_session, monkeypatch)

        assert await svc.merge_entities(loser.id, winner.id) is None
        assert loser.is_active is True
        assert loser.canonical_id is None
