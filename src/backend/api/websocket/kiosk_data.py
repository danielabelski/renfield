"""
Kiosk data helpers — the read-only, content-free reads that back the `/ws/kiosk`
snapshot + push deltas (`kiosk_handler`).

This is kiosk-OWNED code: it moved here wholesale from the now-decommissioned
`api/routes/command_center.py` when the admin Command Center was removed and the
kiosk became the surviving wall-display surface (the kiosk sources everything
over the WS hub — no REST poll). It holds two pieces of shared logic:

  * recent_role_activity_entries — newest-first role activations for the pulse
    trail, content-free by construction (role + timestamp + ok only).
  * compute_kiosk_weather / refresh_and_push_kiosk_weather — the process-cached
    home-location weather reading + the backend-internal refresher that PUSHES a
    ``weather_updated`` delta on change (NOT a client poll — the timer refreshes
    an external cache Open-Meteo doesn't push).

Both degrade to an empty/None payload (never an error) when the feature is off or
the source is unavailable, so the kiosk simply hides the tile.
"""

import time
from datetime import datetime

from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import Message
from utils.config import settings


class RoleActivityEntry(BaseModel):
    role: str
    at: datetime
    ok: bool | None


# How many recent assistant messages to scan for role entries. Shortcut paths
# persist agent_role=None, so the window is larger than the returned limit.
_ACTIVITY_SCAN_WINDOW = 400


async def recent_role_activity_entries(
    db: AsyncSession, limit: int = 30
) -> list[RoleActivityEntry]:
    """Newest-first role activations, content-free by construction: only the
    role name, timestamp, and the turn's action_success. Feeds the kiosk WS
    snapshot (``kiosk_handler``).

    The agent_role extraction happens in Python over a bounded recent window
    (JSON, not JSONB, column — portable across the test sqlite shim and prod
    Postgres without dialect-specific JSON operators).
    """
    # Order by the PK, not the unindexed timestamp column: id order is insert
    # order (≈ chronological), and the PK index turns the scan into a bounded
    # backward index scan instead of a full-table sort.
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
# Ambient kiosk weather tile. Read-only, degrades to None (never an error) when
# the feature is off or the source is unavailable, so the kiosk hides the tile.
# ---------------------------------------------------------------------------


class KioskWeather(BaseModel):
    location: str
    temp: float
    unit: str
    code: int
    condition: str
    high: float | None = None
    low: float | None = None


# Weather barely moves; serve a process-local cached reading so the refresher
# never hammers the Open-Meteo MCP.
_WEATHER_TTL_SECONDS = 600
_weather_cache: dict[str, object] = {"at": 0.0, "value": None}


async def compute_kiosk_weather(mcp_manager, force: bool = False) -> "KioskWeather | None":
    """Current conditions for the configured home location (process-cached).

    Feeds the kiosk WS snapshot + ``weather_updated`` delta. ``None`` (never an
    error) when weather is disabled, no location is configured, or the MCP can't
    answer — the tile hides itself.

    ``force=True`` bypasses the TTL cache READ (used by the periodic refresher so
    a tick genuinely re-fetches even if a client snapshot just warmed the cache
    mid-cycle); it still writes the cache on success.
    """
    location = (settings.kiosk_weather_location or "").strip()
    if not settings.weather_enabled or not location:
        return None

    now = time.monotonic()
    if (
        not force
        and _weather_cache["value"] is not None
        and now - float(_weather_cache["at"]) < _WEATHER_TTL_SECONDS
    ):
        return _weather_cache["value"]

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


# Last weather value PUSHED to the kiosk hub, so the periodic refresher only
# broadcasts on an actual change (diff-gate). None until the first push.
_weather_last_pushed: dict | None = None


async def refresh_and_push_kiosk_weather(mcp_manager) -> None:
    """Backend-internal weather refresh → PUSH to the kiosk hub on change.

    NOT a client poll (plan §1.6): the timer refreshes an EXTERNAL cache
    (Open-Meteo has no push of its own), and the moment the reading changes it
    streams a ``weather_updated`` delta to the connected wall displays instead of
    waiting for the next connect/snapshot. Runs at ``_WEATHER_TTL_SECONDS`` so
    each tick actually re-fetches. Diff-gated + fire-and-forget.
    """
    global _weather_last_pushed
    weather = await compute_kiosk_weather(mcp_manager, force=True)
    payload = weather.model_dump() if weather is not None else None
    if payload == _weather_last_pushed:
        return
    _weather_last_pushed = payload
    try:
        from api.websocket.kiosk_handler import broadcast_kiosk_event

        await broadcast_kiosk_event({"type": "weather_updated", "weather": payload})
    except Exception as e:
        logger.debug(f"kiosk weather_updated broadcast failed: {e}")
