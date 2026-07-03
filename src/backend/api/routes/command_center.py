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

import time
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from loguru import logger
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


# ---------------------------------------------------------------------------
# Ambient kiosk tiles — weather + now-playing. Both read-only, ADMIN-gated, and
# degrade to an empty payload (never an error) when the feature is off or the
# source is unavailable, so the kiosk simply hides the tile.
# ---------------------------------------------------------------------------


class KioskWeather(BaseModel):
    location: str
    temp: float
    unit: str
    code: int
    condition: str
    high: float | None = None
    low: float | None = None


# Weather barely moves; a kiosk polls every few minutes. Serve a process-local
# cached reading so the poll never hammers the Open-Meteo MCP.
_WEATHER_TTL_SECONDS = 600
_weather_cache: dict[str, object] = {"at": 0.0, "value": None}


@router.get("/weather", response_model=KioskWeather | None)
@limiter.limit(settings.api_rate_limit_admin)
async def kiosk_weather(
    request: Request,
    _: User = Depends(require_permission(Permission.ADMIN)),
):
    """Current conditions for the configured home location, for the kiosk tile.
    ``null`` (not an error) when weather is disabled, no location is configured,
    or the MCP can't answer — the tile hides itself."""
    location = (settings.kiosk_weather_location or "").strip()
    if not settings.weather_enabled or not location:
        return None

    now = time.monotonic()
    if _weather_cache["value"] is not None and now - float(_weather_cache["at"]) < _WEATHER_TTL_SECONDS:
        return _weather_cache["value"]

    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if mcp_manager is None:
        return _weather_cache["value"]  # last good reading, or None

    try:
        from services.widget_tools import _extract_mcp_payload

        res = await mcp_manager.execute_tool(
            "mcp.weather.get_weather",
            {"location": location, "days": 1, "temperature_unit": "celsius"},
        )
        if isinstance(res, dict) and not res.get("success", True):
            return _weather_cache["value"]
        raw = _extract_mcp_payload(res) if isinstance(res, dict) else {}
        cur = raw.get("current") if isinstance(raw, dict) else None
        if not isinstance(cur, dict) or cur.get("temperature") is None:
            return _weather_cache["value"]
        daily = raw.get("daily") if isinstance(raw.get("daily"), list) else []
        today = daily[0] if daily and isinstance(daily[0], dict) else {}
        loc = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        weather = KioskWeather(
            location=loc.get("name") or location,
            temp=float(cur["temperature"]),
            unit="°C",
            code=int(cur.get("weather_code", 0)),
            condition=cur.get("weather_description", ""),
            high=today.get("temp_max"),
            low=today.get("temp_min"),
        )
    except Exception as e:  # noqa: BLE001 — a flaky MCP must never break the tile
        logger.warning(f"kiosk_weather: {e}")
        return _weather_cache["value"]

    _weather_cache["at"] = now
    _weather_cache["value"] = weather
    return weather


class KioskNowPlaying(BaseModel):
    room: str
    kind: str
    title: str
    subtitle: str | None = None
    track: int | None = None
    total: int | None = None


@router.get("/now-playing", response_model=list[KioskNowPlaying])
@limiter.limit(settings.api_rate_limit_admin)
async def kiosk_now_playing(
    request: Request,
    _: User = Depends(require_permission(Permission.ADMIN)),
):
    """Live media-follow sessions, one per room (content-minimal). Empty list
    when media-follow is disabled or nothing is playing."""
    from ha_glue.utils.config import ha_glue_settings

    if not ha_glue_settings.media_follow_enabled:
        return []
    try:
        from ha_glue.services.media_follow_service import get_media_follow_service

        return get_media_follow_service().active_sessions()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"kiosk_now_playing: {e}")
        return []
