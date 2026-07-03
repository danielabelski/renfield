"""Route-level tests for /api/command-center (admin-only, read-only).

Covers the two constellation feeds: `GET /roles` (agent roles off
`app.state.agent_router`) and `GET /activity` (content-free role pulse
read from assistant messages' persisted ``message_metadata.agent_role``).
"""
from __future__ import annotations

from datetime import datetime, timedelta, UTC

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import Message


class _FakeUser:
    """Bypasses the ORM role relationship (MissingGreenlet across sessions)."""

    def __init__(self, uid: int, name: str, is_admin: bool):
        self.id = uid
        self.username = name
        self._is_admin = is_admin

    def has_permission(self, perm: str) -> bool:
        return self._is_admin and perm == "admin"


@pytest.fixture
def auth_as_regular(app_with_test_db, monkeypatch):
    from services.auth_service import get_current_user
    fake = _FakeUser(uid=7, name="cc_regular", is_admin=False)
    monkeypatch.setattr("services.auth_service.settings.auth_enabled", True)
    app_with_test_db.dependency_overrides[get_current_user] = lambda: fake
    try:
        yield fake
    finally:
        app_with_test_db.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def auth_as_admin(app_with_test_db, monkeypatch):
    from services.auth_service import get_current_user
    fake = _FakeUser(uid=42, name="cc_admin", is_admin=True)
    monkeypatch.setattr("services.auth_service.settings.auth_enabled", True)
    app_with_test_db.dependency_overrides[get_current_user] = lambda: fake
    try:
        yield fake
    finally:
        app_with_test_db.dependency_overrides.pop(get_current_user, None)


class _FakeRole:
    def __init__(self, name: str, servers: list[str] | None,
                 tools: list[str] | None):
        self.name = name
        self.description = {"de": f"{name} de", "en": f"{name} en"}
        self.mcp_servers = servers
        self.internal_tools = tools
        self.has_agent_loop = True


class _FakeAgentRouter:
    def __init__(self, roles: dict):
        self.roles = roles


@pytest.fixture
def fake_agent_router(app_with_test_db):
    router = _FakeAgentRouter({
        "smart_home": _FakeRole("smart_home", ["homeassistant"],
                                ["internal.device_action"]),
        "general": _FakeRole("general", None, None),
    })
    prior = getattr(app_with_test_db.state, "agent_router", None)
    app_with_test_db.state.agent_router = router
    try:
        yield router
    finally:
        app_with_test_db.state.agent_router = prior


async def _make_message(
    db_session: AsyncSession, *, role: str = "assistant",
    metadata: dict | None = None, age_minutes: int = 0,
) -> Message:
    msg = Message(
        role=role,
        content="",
        timestamp=datetime.now(UTC).replace(tzinfo=None)
        - timedelta(minutes=age_minutes),
        message_metadata=metadata,
    )
    db_session.add(msg)
    await db_session.commit()
    return msg


# ============================================================ AUTH GATE
@pytest.mark.asyncio
class TestAuthGate:
    async def test_roles_requires_admin(
        self, async_client: AsyncClient, auth_as_regular,
    ):
        resp = await async_client.get("/api/command-center/roles")
        assert resp.status_code in (401, 403)

    async def test_activity_requires_admin(
        self, async_client: AsyncClient, auth_as_regular,
    ):
        resp = await async_client.get("/api/command-center/activity")
        assert resp.status_code in (401, 403)


# ============================================================ ROLES
@pytest.mark.asyncio
class TestRoles:
    async def test_roles_from_agent_router(
        self, async_client: AsyncClient, auth_as_admin, fake_agent_router,
    ):
        resp = await async_client.get("/api/command-center/roles")
        assert resp.status_code == 200
        body = resp.json()
        names = {r["name"] for r in body}
        assert names == {"smart_home", "general"}
        smart = next(r for r in body if r["name"] == "smart_home")
        assert smart["mcp_servers"] == ["homeassistant"]
        assert smart["internal_tools"] == ["internal.device_action"]
        assert smart["description"]["de"] == "smart_home de"
        generic = next(r for r in body if r["name"] == "general")
        assert generic["mcp_servers"] is None

    async def test_roles_empty_when_router_missing(
        self, async_client: AsyncClient, auth_as_admin, app_with_test_db,
    ):
        prior = getattr(app_with_test_db.state, "agent_router", None)
        app_with_test_db.state.agent_router = None
        try:
            resp = await async_client.get("/api/command-center/roles")
        finally:
            app_with_test_db.state.agent_router = prior
        assert resp.status_code == 200
        assert resp.json() == []


# ============================================================ ACTIVITY
@pytest.mark.asyncio
class TestActivity:
    async def test_activity_newest_first_and_content_free(
        self, async_client: AsyncClient, auth_as_admin,
        db_session: AsyncSession,
    ):
        await _make_message(
            db_session,
            metadata={"agent_role": "knowledge", "action_success": True},
            age_minutes=10,
        )
        await _make_message(
            db_session,
            metadata={"agent_role": "smart_home", "action_success": False},
            age_minutes=1,
        )
        resp = await async_client.get("/api/command-center/activity")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert body[0]["role"] == "smart_home"
        assert body[0]["ok"] is False
        assert body[1]["role"] == "knowledge"
        assert body[1]["ok"] is True
        # content-free: only these three keys ever leave the endpoint
        assert set(body[0].keys()) == {"role", "at", "ok"}

    async def test_activity_skips_roleless_and_user_messages(
        self, async_client: AsyncClient, auth_as_admin,
        db_session: AsyncSession,
    ):
        # shortcut path: agent_role=None
        await _make_message(db_session, metadata={"agent_role": None})
        # legacy row: no metadata at all
        await _make_message(db_session, metadata=None)
        # user message never counts, even with a role-shaped metadata
        await _make_message(
            db_session, role="user", metadata={"agent_role": "general"},
        )
        await _make_message(db_session, metadata={"agent_role": "media"})
        resp = await async_client.get("/api/command-center/activity")
        assert resp.status_code == 200
        body = resp.json()
        assert [e["role"] for e in body] == ["media"]
        assert body[0]["ok"] is None

    async def test_activity_limit(
        self, async_client: AsyncClient, auth_as_admin,
        db_session: AsyncSession,
    ):
        for i in range(5):
            await _make_message(
                db_session, metadata={"agent_role": "general"}, age_minutes=i,
            )
        resp = await async_client.get("/api/command-center/activity?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    async def test_activity_limit_validation(
        self, async_client: AsyncClient, auth_as_admin,
    ):
        resp = await async_client.get("/api/command-center/activity?limit=0")
        assert resp.status_code == 422
        resp = await async_client.get("/api/command-center/activity?limit=999")
        assert resp.status_code == 422
