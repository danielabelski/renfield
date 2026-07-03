"""
Command Center API — read-only data for the /admin/command-center constellation.

Two small admin-gated endpoints backing docs/design/command-center.md:

  GET /roles     — the agent roles loaded from agent_roles.yaml (the router's
                   live, availability-filtered set). /api/roles is RBAC system
                   roles; the agent roles had no REST surface until this.
  GET /activity  — the recent role activations (role name + timestamp + ok),
                   read from the assistant messages' persisted
                   ``message_metadata.agent_role`` (role-surfacing). This is the
                   board's live pulse. Deliberately CONTENT-FREE: no message
                   text, no user ids — safe for the future kiosk projection.

Both are ADMIN because they expose household-wide operational state.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import Message, User
from models.permissions import Permission
from services.api_rate_limiter import limiter
from services.auth_service import require_permission
from services.database import get_db
from utils.config import settings

router = APIRouter()


class AgentRoleResponse(BaseModel):
    name: str
    description: dict[str, str]
    # None = the role may use ALL servers / internal tools (agent_router
    # semantics). The frontend uses these lists for the hover reach-edges.
    mcp_servers: list[str] | None
    internal_tools: list[str] | None
    has_agent_loop: bool


class RoleActivityEntry(BaseModel):
    role: str
    at: datetime
    ok: bool | None


@router.get("/roles", response_model=list[AgentRoleResponse])
@limiter.limit(settings.api_rate_limit_admin)
async def list_agent_roles(
    request: Request,
    _: User = Depends(require_permission(Permission.ADMIN)),
):
    """Agent roles as the router currently sees them (availability-filtered)."""
    agent_router = getattr(request.app.state, "agent_router", None)
    if agent_router is None or not getattr(agent_router, "roles", None):
        return []
    return [
        AgentRoleResponse(
            name=role.name,
            description=role.description,
            mcp_servers=role.mcp_servers,
            internal_tools=role.internal_tools,
            has_agent_loop=role.has_agent_loop,
        )
        for role in agent_router.roles.values()
    ]


# How many recent assistant messages to scan for role entries. Shortcut paths
# persist agent_role=None, so the window is larger than the returned limit.
_ACTIVITY_SCAN_WINDOW = 400


@router.get("/activity", response_model=list[RoleActivityEntry])
@limiter.limit(settings.api_rate_limit_admin)
async def recent_role_activity(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.ADMIN)),
):
    """Newest-first role activations. Content-free by construction: only the
    role name, timestamp, and the turn's action_success leave this endpoint.

    The agent_role extraction happens in Python over a bounded recent window
    (JSON, not JSONB, column — portable across the test sqlite shim and prod
    Postgres without dialect-specific JSON operators).
    """
    # Order by the PK, not the unindexed timestamp column: id order is insert
    # order (≈ chronological), and the PK index turns the 3s poll into a
    # bounded backward index scan instead of a full-table sort.
    result = await db.execute(
        select(Message.timestamp, Message.message_metadata)
        .where(Message.role == "assistant")
        .order_by(Message.id.desc())
        .limit(_ACTIVITY_SCAN_WINDOW)
    )
    entries: list[RoleActivityEntry] = []
    for timestamp, metadata in result.all():
        if not isinstance(metadata, dict):
            continue
        role = metadata.get("agent_role")
        if not role or not isinstance(role, str):
            continue
        ok = metadata.get("action_success")
        entries.append(
            RoleActivityEntry(
                role=role,
                at=timestamp,
                ok=ok if isinstance(ok, bool) else None,
            )
        )
        if len(entries) >= limit:
            break
    return entries
