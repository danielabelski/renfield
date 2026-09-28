"""Security review H4 — KG read endpoints must enforce circle access.

`list_entities` was hardened against `?user_id=<victim>` exfiltration, but the
sibling read methods (`get_entity`, `list_relations`, `get_stats`) were not.
These tests pin the fix: with `enforce_circle=True` (the KG_VIEW read route) a
non-owner with no grant/membership sees only public-tier rows, while the owner
and the admin/internal path (enforce_circle=False) still see everything.

PG-only: the circle clause is raw SQL over the real schema.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import KGEntity, KGRelation, Role, User
from services.knowledge_graph_service import KnowledgeGraphService
from utils.config import settings


async def _make_user(db: AsyncSession, name: str) -> User:
    role = Role(name=f"{name}_role")
    db.add(role)
    await db.flush()
    u = User(username=name, email=f"{name}@ex.test", password_hash="x",
             role_id=role.id, is_active=True)
    db.add(u)
    await db.flush()
    return u


async def _entity(db, owner, name, *, tier=0, etype="person") -> KGEntity:
    e = KGEntity(user_id=owner.id, name=name, entity_type=etype, circle_tier=tier)
    db.add(e)
    await db.flush()
    return e


async def _relation(db, owner, subj, obj, *, tier=0) -> KGRelation:
    r = KGRelation(
        user_id=owner.id, subject_id=subj.id, predicate="kennt",
        object_id=obj.id, circle_tier=tier, confidence=1.0,
    )
    db.add(r)
    await db.flush()
    return r


class TestGetEntityCircleGuard:
    pytestmark = [pytest.mark.postgres, pytest.mark.asyncio, pytest.mark.database]

    async def test_owner_sees_own_private_entity(self, pg_db_session, monkeypatch):
        monkeypatch.setattr(settings, "auth_enabled", True)
        owner = await _make_user(pg_db_session, "h4_own")
        ent = await _entity(pg_db_session, owner, "Geheim", tier=0)
        svc = KnowledgeGraphService(pg_db_session)

        got = await svc.get_entity(ent.id, asker_id=owner.id, enforce_circle=True)
        assert got is not None and got.id == ent.id

    async def test_non_owner_cannot_read_private_entity(self, pg_db_session, monkeypatch):
        monkeypatch.setattr(settings, "auth_enabled", True)
        owner = await _make_user(pg_db_session, "h4_own2")
        attacker = await _make_user(pg_db_session, "h4_atk")
        ent = await _entity(pg_db_session, owner, "Geheim2", tier=0)
        svc = KnowledgeGraphService(pg_db_session)

        # The IDOR: a different KG_VIEW user must NOT resolve the private entity.
        got = await svc.get_entity(ent.id, asker_id=attacker.id, enforce_circle=True)
        assert got is None

    async def test_non_owner_can_read_public_entity(self, pg_db_session, monkeypatch):
        monkeypatch.setattr(settings, "auth_enabled", True)
        owner = await _make_user(pg_db_session, "h4_own3")
        attacker = await _make_user(pg_db_session, "h4_atk2")
        ent = await _entity(pg_db_session, owner, "Öffentlich", tier=4)
        svc = KnowledgeGraphService(pg_db_session)

        got = await svc.get_entity(ent.id, asker_id=attacker.id, enforce_circle=True)
        assert got is not None and got.id == ent.id

    async def test_admin_path_unfiltered(self, pg_db_session, monkeypatch):
        """enforce_circle=False (KG_MANAGE / internal callers) is unchanged."""
        monkeypatch.setattr(settings, "auth_enabled", True)
        owner = await _make_user(pg_db_session, "h4_own4")
        ent = await _entity(pg_db_session, owner, "Geheim3", tier=0)
        svc = KnowledgeGraphService(pg_db_session)

        got = await svc.get_entity(ent.id)  # no asker, no enforcement
        assert got is not None and got.id == ent.id


class TestListRelationsCircleGuard:
    pytestmark = [pytest.mark.postgres, pytest.mark.asyncio, pytest.mark.database]

    async def test_non_owner_excluded_from_private_relations(self, pg_db_session, monkeypatch):
        monkeypatch.setattr(settings, "auth_enabled", True)
        owner = await _make_user(pg_db_session, "h4_rel_own")
        attacker = await _make_user(pg_db_session, "h4_rel_atk")
        a = await _entity(pg_db_session, owner, "A", tier=0)
        b = await _entity(pg_db_session, owner, "B", tier=0)
        await _relation(pg_db_session, owner, a, b, tier=0)
        svc = KnowledgeGraphService(pg_db_session)

        rels, total = await svc.list_relations(asker_id=attacker.id, enforce_circle=True)
        assert total == 0 and rels == []

        # Owner still sees it
        _own_rels, own_total = await svc.list_relations(
            asker_id=owner.id, enforce_circle=True
        )
        assert own_total == 1


class TestStatsCircleGuard:
    pytestmark = [pytest.mark.postgres, pytest.mark.asyncio, pytest.mark.database]

    async def test_stats_exclude_other_users_private_entities(self, pg_db_session, monkeypatch):
        monkeypatch.setattr(settings, "auth_enabled", True)
        owner = await _make_user(pg_db_session, "h4_stat_own")
        attacker = await _make_user(pg_db_session, "h4_stat_atk")
        await _entity(pg_db_session, owner, "Priv", tier=0)
        await _entity(pg_db_session, owner, "Pub", tier=4)
        svc = KnowledgeGraphService(pg_db_session)

        stats = await svc.get_stats(asker_id=attacker.id, enforce_circle=True)
        # Attacker sees only the public entity, not the owner's private one.
        assert stats["entity_count"] == 1
