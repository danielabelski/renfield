"""
Route-level tests for the Wissensgraph backend (/api/wissensbasis/*).

The service logic is covered in test_kg_graph_service.py; this file pins the
HTTP surface the frontend GraphView depends on: the Permission.KG_VIEW gate
(200 vs 403), the non-integer entity_id → 404 branch, the missing-entity 404,
and the response shapes for /graph, /focus, /search.

SQLite-backed (auth bypassed by default) so these run on every test pass. The
auth-on cases toggle settings.auth_enabled to exercise the permission gate;
circle-filter correctness itself is covered against real Postgres in the
service tests.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import TIER_PUBLIC, KGEntity, KGRelation, Role, User
from models.permissions import Permission
from utils.config import settings


@pytest.fixture
async def app(db_session: AsyncSession):
    """FastAPI app with get_db pointed at the sqlite test session."""
    from main import app as fastapi_app
    from services.database import get_db

    async def _override_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = _override_db
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
async def pg_app(pg_db_session: AsyncSession):
    """Same as `app` but on real Postgres — needed for the circle-filtered
    auth-on path (the filter's ::text casts don't run on sqlite)."""
    from main import app as fastapi_app
    from services.database import get_db

    async def _override_db():
        yield pg_db_session

    fastapi_app.dependency_overrides[get_db] = _override_db
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


def _auth_as(app, user: User | None) -> None:
    from services.auth_service import get_current_user

    app.dependency_overrides[get_current_user] = lambda: user


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _entity(
    db: AsyncSession, name: str, *, mention_count: int = 1, circle_tier: int = 0
) -> KGEntity:
    e = KGEntity(
        name=name, entity_type="person",
        mention_count=mention_count, circle_tier=circle_tier,
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


async def _relation(db: AsyncSession, subject_id: int, object_id: int) -> None:
    db.add(KGRelation(
        subject_id=subject_id, predicate="knows", object_id=object_id, is_active=True,
    ))
    await db.commit()


def _user(perms: list[str], uid: int = 1) -> User:
    """In-memory User+Role for the permission gate.

    Deliberately not session-attached: require_permission only reads
    user.has_permission (→ user.role.permissions), and a detached object
    avoids the async lazy-load (MissingGreenlet) that expire-on-commit
    triggers when the relationship is touched after the request starts.
    """
    role = Role(name="gate-role", description="t", permissions=perms)
    return User(id=uid, username="gate-user", password_hash="x", is_active=True, role=role)


# ==========================================================================
# Shapes (auth disabled — default test mode)
# ==========================================================================

class TestShapes:
    @pytest.mark.database
    async def test_graph_200_shape(self, app, db_session):
        a = await _entity(db_session, "Alpha", mention_count=5)
        b = await _entity(db_session, "Beta", mention_count=2)
        await _relation(db_session, a.id, b.id)
        _auth_as(app, None)
        async with await _client(app) as c:
            resp = await c.get("/api/wissensbasis/graph")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"clusters", "total_entities", "total_relations", "truncated"}
        assert body["total_entities"] == 2
        # Tier + intra-cluster structure ride on every cluster (3D scene:
        # tier-token node colour + hub↔hub filaments).
        cluster = body["clusters"][0]
        hub_by_name = {h["name"]: h for h in cluster["hubs"]}
        assert hub_by_name["Alpha"]["circle_tier"] == 0
        assert {"from_entity": str(a.id), "to_entity": str(b.id), "relation": "knows"} in cluster["hub_edges"]

    @pytest.mark.database
    async def test_focus_200_shape(self, app, db_session):
        center = await _entity(db_session, "Center", mention_count=9)
        near = await _entity(db_session, "Near", mention_count=3)
        await _relation(db_session, center.id, near.id)
        _auth_as(app, None)
        async with await _client(app) as c:
            resp = await c.get("/api/wissensbasis/focus", params={"entity_id": str(center.id)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["focus"]["entity_id"] == str(center.id)
        assert [h["entity_id"] for h in body["hop1"]] == [str(near.id)]
        assert body["focus"]["circle_tier"] == 0
        assert body["hop1"][0]["circle_tier"] == 0

    @pytest.mark.database
    async def test_focus_404_non_integer(self, app, db_session):
        _auth_as(app, None)
        async with await _client(app) as c:
            resp = await c.get("/api/wissensbasis/focus", params={"entity_id": "abc"})
        assert resp.status_code == 404

    @pytest.mark.database
    async def test_focus_404_missing_entity(self, app, db_session):
        _auth_as(app, None)
        async with await _client(app) as c:
            resp = await c.get("/api/wissensbasis/focus", params={"entity_id": "999999"})
        assert resp.status_code == 404

    @pytest.mark.database
    async def test_search_200(self, app, db_session):
        await _entity(db_session, "Berlin", mention_count=4)
        _auth_as(app, None)
        async with await _client(app) as c:
            resp = await c.get("/api/wissensbasis/search", params={"q": "ber"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert [i["display_name"] for i in items] == ["Berlin"]

    @pytest.mark.database
    async def test_search_blank_query_empty(self, app, db_session):
        await _entity(db_session, "Anything")
        _auth_as(app, None)
        async with await _client(app) as c:
            resp = await c.get("/api/wissensbasis/search", params={"q": "  "})
        assert resp.status_code == 200
        assert resp.json()["items"] == []


# ==========================================================================
# Permission gate (auth enabled)
# ==========================================================================

class TestPermissionGate:
    @pytest.mark.database
    async def test_403_without_kg_view(self, app, db_session, monkeypatch):
        monkeypatch.setattr(settings, "auth_enabled", True)
        _auth_as(app, _user(perms=[]))
        async with await _client(app) as c:
            resp = await c.get("/api/wissensbasis/graph")
        assert resp.status_code == 403

    @pytest.mark.postgres
    async def test_200_with_kg_view(self, pg_app, pg_db_session, monkeypatch):
        # Postgres-backed: with auth on, the route runs the circle filter,
        # whose ::text casts only execute on PG. A public-tier entity is
        # visible to any KG_VIEW holder via the public branch.
        monkeypatch.setattr(settings, "auth_enabled", True)
        ent = KGEntity(
            name="PublicEnt", entity_type="person",
            mention_count=1, circle_tier=TIER_PUBLIC,
        )
        pg_db_session.add(ent)
        await pg_db_session.flush()
        _auth_as(pg_app, _user(perms=[Permission.KG_VIEW.value], uid=987654))
        async with await _client(pg_app) as c:
            resp = await c.get("/api/wissensbasis/graph")
        assert resp.status_code == 200
        assert resp.json()["total_entities"] >= 1
