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
# Active-subsystem pulse — which subsystem(s) a completed turn touched. Shared by
# BOTH the web-chat path (chat_handler) AND the voice path (satellite_handler) so
# a spoken "turn off the light" lights the same kiosk node a typed one does. It
# lived in chat_handler until the voice path was found to never pulse (the household
# talks to satellites, not the web chat) — moved here so both can emit it.
# ---------------------------------------------------------------------------

# Maps the platform-core / ha_glue ``internal.*`` tools (which have no MCP server,
# hence no natural ring node) onto a kiosk subsystem id. Unknown internal tools
# are intentionally skipped (no pulse). ``homeassistant`` and ``weather`` are REAL
# MCP servers (they already render as tool-ring nodes); ``knowledge`` / ``presence``
# / ``media`` are INTERNAL-ONLY subsystems with no MCP server — the kiosk renders
# synthetic pulse-only nodes for exactly those three, so this map's internal-only
# value set MUST stay in sync with the frontend ``INTERNAL_SUBSYSTEM_NODES``
# (components/kiosk/useKioskModel.ts). Pure Gen-UI formatting tools (render_table /
# render_list) touch no subsystem → omitted.
INTERNAL_SUBSYSTEM_LABELS: dict[str, str] = {
    # knowledge / second brain — RAG, memory, document ingest + maintenance
    "internal.knowledge_search": "knowledge",
    "internal.list_my_memories": "knowledge",
    "internal.forward_attachment_to_paperless": "knowledge",
    "internal.paperless_commit_upload": "knowledge",
    "internal.ingest_file": "knowledge",
    "internal.ingest_status": "knowledge",
    "internal.reindex_documents": "knowledge",
    "internal.list_chunkless_documents": "knowledge",
    # presence
    "internal.presence_map": "presence",
    "internal.presence_history": "presence",
    "internal.get_all_presence": "presence",
    "internal.get_user_location": "presence",
    "internal.bluetooth_scan": "presence",
    # home assistant — device control + spoken announcements via HA speakers
    "internal.device_action": "homeassistant",
    "internal.device_controls": "homeassistant",
    "internal.announce_in_room": "homeassistant",
    "internal.broadcast_announcement": "homeassistant",
    # weather (wraps the weather MCP)
    "internal.weather_widget": "weather",
    # media — DLNA / radio / server playback orchestration
    "internal.media_control": "media",
    "internal.play_radio": "media",
    "internal.play_in_room": "media",
    "internal.play_from_server": "media",
    "internal.play_album_on_dlna": "media",
    "internal.play_video_on_dlna": "media",
    "internal.list_radio_favorites": "media",
    "internal.save_radio_favorite": "media",
    "internal.remove_radio_favorite": "media",
    "internal.resolve_room_player": "media",
}

# A single turn rarely touches many subsystems; cap the pushed/persisted list so
# an orchestrated fan-out can't bloat the event or the row.
_MAX_SUBSYSTEMS_PER_TURN = 5


def extract_subsystems_used(tool_results: list) -> list[str]:
    """Derive the content-free subsystem ids a turn touched, for the kiosk pulse.

    Each entry is a ``(tool_name, data)`` pair (only ``tool_name`` is read).
    ``mcp.<server>.<tool>`` → ``<server>``; ``internal.<tool>`` → the static
    ``INTERNAL_SUBSYSTEM_LABELS`` allowlist (unknown internal tools skipped).
    Deduped, order-preserved, capped at ``_MAX_SUBSYSTEMS_PER_TURN``. Empty when a
    turn ran no tool (direct-LLM / ``general.conversation`` / shortcut paths).
    """
    subsystems: list[str] = []
    seen: set[str] = set()
    for entry in tool_results:
        tool_name = entry[0] if isinstance(entry, (tuple, list)) and entry else None
        if not isinstance(tool_name, str) or not tool_name:
            continue
        if tool_name.startswith("mcp."):
            parts = tool_name.split(".")
            sub = parts[1] if len(parts) >= 3 and parts[1] else None
        elif tool_name.startswith("internal."):
            sub = INTERNAL_SUBSYSTEM_LABELS.get(tool_name)
        else:
            sub = None
        if not sub or sub in seen:
            continue
        seen.add(sub)
        subsystems.append(sub)
        if len(subsystems) >= _MAX_SUBSYSTEMS_PER_TURN:
            break
    return subsystems


async def broadcast_turn_activity(
    role: str | None, subsystems: list[str], ok: bool | None
) -> None:
    """Push ONE content-free ``turn_activity`` pulse to the kiosk hub (role +
    which subsystems this turn touched). No-op when there's nothing to show (no
    role AND no subsystems), so a plain conversation turn pushes nothing.
    Fire-and-forget: a hub failure must never break the turn."""
    if not role and not subsystems:
        return
    try:
        from datetime import UTC, datetime

        from api.websocket.kiosk_handler import broadcast_kiosk_event

        await broadcast_kiosk_event(
            {
                "type": "turn_activity",
                "role": role,
                "subsystems": subsystems,
                "ok": ok,
                "at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception as e:  # noqa: BLE001 — never break a turn on a kiosk push
        logger.debug(f"kiosk turn_activity broadcast failed: {e}")


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
