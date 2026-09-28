"""Tests for Structured Memory Phase 2, T6 — KG provenance + multi-type + prompt.

PG: save_relation provenance (stated_by) and resolve_entity multi-type absorb.
Unit: the extraction prompt carries the multi-type + taste-as-relation guidance.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import KGEntity, Role, User
from services.knowledge_graph_service import KnowledgeGraphService


async def _make_user(db: AsyncSession, name: str) -> User:
    role = Role(name=f"{name}_role")
    db.add(role)
    await db.flush()
    u = User(username=name, email=f"{name}@ex.test", password_hash="x",
             role_id=role.id, is_active=True)
    db.add(u)
    await db.flush()
    return u


async def _entity(db, owner, name, *, etype="person") -> KGEntity:
    e = KGEntity(user_id=owner.id, name=name, entity_type=etype, circle_tier=0)
    db.add(e)
    await db.flush()
    return e


class TestSaveRelationProvenance:
    pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

    async def test_stated_by_recorded(self, pg_db_session):
        owner = await _make_user(pg_db_session, "t6_owner")
        speaker = await _make_user(pg_db_session, "t6_speaker")
        subj = await _entity(pg_db_session, owner, "Alice")
        obj = await _entity(pg_db_session, owner, "Sam Star")
        svc = KnowledgeGraphService(pg_db_session)

        rel = await svc.save_relation(
            subject_id=subj.id, predicate="mag_musik_von", object_id=obj.id,
            user_id=owner.id, stated_by_user_id=speaker.id,
        )
        await pg_db_session.refresh(rel)
        assert rel.stated_by_user_id == speaker.id
        assert rel.user_id == owner.id  # owner distinct from asserter


class TestResolveMultiType:
    pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

    async def test_new_entity_absorbs_extra_types(self, pg_db_session, monkeypatch):
        owner = await _make_user(pg_db_session, "t6_mt")
        svc = KnowledgeGraphService(pg_db_session)
        monkeypatch.setattr(svc, "_get_embedding", AsyncMock(return_value=None))

        ent = await svc.resolve_entity("Sam Star", "person", owner.id,
                                       extra_types=["musician"])
        assert ent.entity_type == "person"               # scalar primary
        assert ent.entity_types == ["person", "musician"]  # multi-type superset

    async def test_existing_entity_merges_new_type(self, pg_db_session, monkeypatch):
        owner = await _make_user(pg_db_session, "t6_mt2")
        svc = KnowledgeGraphService(pg_db_session)
        monkeypatch.setattr(svc, "_get_embedding", AsyncMock(return_value=None))

        first = await svc.resolve_entity("Sam Star", "person", owner.id)
        assert first.entity_types == ["person"]
        # second mention adds a new role -> folded into the same entity
        again = await svc.resolve_entity("Sam Star", "person", owner.id,
                                         extra_types=["musician"])
        assert again.id == first.id
        assert again.entity_types == ["person", "musician"]


class TestExtractionPromptContract:
    @pytest.mark.unit
    def test_de_prompt_has_multitype_and_taste_guidance(self):
        from services.prompt_manager import prompt_manager
        p = prompt_manager.get(
            "knowledge_graph", "extraction_prompt", lang="de",
            user_message="x", assistant_response="y", speaker_clause="",
        )
        assert '"types"' in p
        assert "MEHRFACH-TYP" in p
        assert "mag_musik_von" in p  # taste-as-relation example

    @pytest.mark.unit
    def test_en_prompt_has_multitype_and_taste_guidance(self):
        from services.prompt_manager import prompt_manager
        p = prompt_manager.get(
            "knowledge_graph", "extraction_prompt", lang="en",
            user_message="x", assistant_response="y", speaker_clause="",
        )
        assert '"types"' in p
        assert "MULTI-TYPE" in p
        assert "likes_music_by" in p
