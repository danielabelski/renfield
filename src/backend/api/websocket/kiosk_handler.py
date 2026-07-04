"""
Live Kiosk WebSocket endpoint (the /kiosk wall-display push hub).

Precedent: this is the second instance of the ``kg_live_handler`` shape — a
module-level client registry + a fire-and-forget ``broadcast_*`` called from
wherever the source event happens + a ``/ws/...`` endpoint that accepts,
registers, hydrates, and cleans up on disconnect. The only structural
differences from ``kg_live_handler`` are:

  * the registry is a plain ``set`` — kiosk content is **household-wide** by
    design (like the Command Center it replaces), so there is no per-owner
    scoping to carry; and
  * the connect gate requires ``Permission.ADMIN`` (mirroring ``<AdminRoute>``
    on the page), not merely authentication; and
  * on connect the server sends one ``snapshot`` message (hydrate) before the
    idle receive-loop (subscribe) — the standard hydrate-then-subscribe pattern
    so a fresh / reconnecting tab has all current state immediately.

Privacy bar (non-negotiable): every message this hub emits is CONTENT-FREE —
ids, names, counts, health, and state strings only. Never an utterance, never
an entity name, never a user id. See ``tasks/kiosk-active-subsystem-plan.md`` §5.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from models.permissions import Permission
from services.auth_service import get_user_by_id
from services.database import AsyncSessionLocal
from services.websocket_auth import WSAuthError, authenticate_websocket

router = APIRouter()

# Connected kiosk wall displays. No per-viewer scoping: the kiosk projection is
# household-wide (ADMIN-gated at connect), so every client sees every event.
_kiosk_clients: set[WebSocket] = set()


async def broadcast_kiosk_event(event: dict) -> None:
    """Broadcast one content-free kiosk delta to every connected wall display.

    Fire-and-forget, same broken-socket cleanup as ``broadcast_kg_update``: a
    send that raises prunes that socket, never propagates. No-op when no client
    is connected (the common case — cost of the publish points stays ~zero when
    nobody is watching the kiosk).
    """
    if not _kiosk_clients:
        return

    broken: list[WebSocket] = []
    for ws in _kiosk_clients:
        try:
            await ws.send_json(event)
        except Exception:  # noqa: BLE001 — a dead socket must not break the caller
            broken.append(ws)

    for ws in broken:
        _kiosk_clients.discard(ws)


# ---------------------------------------------------------------------------
# Snapshot builder — the one-time hydrate compute sent on connect. Reuses the
# exact source calls the REST layer already reads; no new query logic.
# ---------------------------------------------------------------------------

# Tools whose per-(user,tool) success rate is below this are "degraded" in the
# aggregated, user-id-free kiosk health view.
_TOOL_HEALTH_DEGRADED_BELOW = 0.5

# A federation peer is shown "reachable" if it was seen within this window. The
# snapshot has no live heartbeat probe (peer reachability transitions are a
# later delta phase, see the plan §1.6/§9); last-seen recency is the honest
# best-effort signal at hydrate time.
_PEER_REACHABLE_WITHIN_SECONDS = 300


async def build_kiosk_snapshot(app) -> dict:
    """Compute the full current kiosk state ONCE, for the connect ``snapshot``.

    Every section is best-effort: a failing source degrades to an empty/None
    value rather than aborting the whole snapshot (a fresh kiosk tab must always
    hydrate). CONTENT-FREE by construction — see the module docstring.
    """
    snapshot: dict = {
        "type": "snapshot",
        "at": datetime.now(UTC).isoformat(),
        "satellites": [],
        "presence": {"rooms": [], "people_present": 0, "occupied_rooms": 0},
        "mcp": {"enabled": False, "total_tools": 0, "servers": []},
        "tool_health": [],
        "roles": [],
        "activity": [],
        "peers": [],
        "weather": None,
        "now_playing": [],
    }

    # --- Satellites (roster + live state) --------------------------------
    try:
        from ha_glue.services.satellite_manager import get_satellite_manager

        snapshot["satellites"] = get_satellite_manager().get_all_satellites()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"kiosk snapshot: satellites unavailable: {e}")

    # --- Presence (rooms → occupant counts; no user ids) -----------------
    try:
        from ha_glue.services.presence_service import get_presence_service

        presence = get_presence_service()
        rooms: dict[int | None, dict] = {}
        for pres in presence.get_all_presence().values():
            key = pres.room_id
            if key is None:
                continue
            room = rooms.setdefault(
                key, {"room_id": key, "room_name": pres.room_name, "occupants": 0}
            )
            room["occupants"] += 1
        room_list = list(rooms.values())
        snapshot["presence"] = {
            "rooms": room_list,
            "people_present": sum(r["occupants"] for r in room_list),
            "occupied_rooms": len(room_list),
        }
    except Exception as e:  # noqa: BLE001
        logger.debug(f"kiosk snapshot: presence unavailable: {e}")

    # --- MCP connection status + tool counts -----------------------------
    mcp_manager = getattr(app.state, "mcp_manager", None)
    if mcp_manager is not None:
        try:
            snapshot["mcp"] = mcp_manager.get_status()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"kiosk snapshot: mcp status unavailable: {e}")

    # --- Tool-health classification (aggregated, user-id-free) -----------
    try:
        from services.tool_outcome_service import ToolOutcomeService

        async with AsyncSessionLocal() as db:
            stats = await ToolOutcomeService(db).list_stats(limit=500)
        agg: dict[str, dict] = {}
        for st in stats:
            row = agg.setdefault(
                st.tool_name, {"tool_name": st.tool_name, "success": 0, "failure": 0}
            )
            row["success"] += st.success_count
            row["failure"] += st.failure_count
        tool_health: list[dict] = []
        for row in agg.values():
            total = row["success"] + row["failure"]
            rate = (row["success"] / total) if total else 1.0
            tool_health.append(
                {
                    "tool_name": row["tool_name"],
                    "total": total,
                    "success_rate": round(rate, 3),
                    "degraded": total > 0 and rate < _TOOL_HEALTH_DEGRADED_BELOW,
                }
            )
        snapshot["tool_health"] = tool_health
    except Exception as e:  # noqa: BLE001
        logger.debug(f"kiosk snapshot: tool health unavailable: {e}")

    # --- Agent roles (availability-filtered, as the router sees them) ----
    try:
        agent_router = getattr(app.state, "agent_router", None)
        if agent_router is not None and getattr(agent_router, "roles", None):
            snapshot["roles"] = [
                {
                    "name": role.name,
                    "description": role.description,
                    "mcp_servers": role.mcp_servers,
                    "internal_tools": role.internal_tools,
                    "has_agent_loop": role.has_agent_loop,
                }
                for role in agent_router.roles.values()
            ]
    except Exception as e:  # noqa: BLE001
        logger.debug(f"kiosk snapshot: roles unavailable: {e}")

    # --- Recent role activations (content-free pulse history) ------------
    try:
        from api.routes.command_center import recent_role_activity_entries

        async with AsyncSessionLocal() as db:
            entries = await recent_role_activity_entries(db, limit=30)
        snapshot["activity"] = [
            {"role": e.role, "at": e.at.isoformat() if e.at else None, "ok": e.ok}
            for e in entries
        ]
    except Exception as e:  # noqa: BLE001
        logger.debug(f"kiosk snapshot: activity unavailable: {e}")

    # --- Federation peers (reachability, no message content) -------------
    try:
        from sqlalchemy import select

        from models.database import PeerUser

        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(PeerUser).where(PeerUser.revoked_at.is_(None))
                )
            ).scalars().all()
        peers: list[dict] = []
        seen_peers: set = set()
        for peer in rows:
            # Different circle owners can pair with the same remote node; the
            # kiosk shows one node per remote identity.
            if peer.remote_pubkey in seen_peers:
                continue
            seen_peers.add(peer.remote_pubkey)
            last_seen = peer.last_seen_at
            reachable = False
            if last_seen is not None:
                ls = last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=UTC)
                reachable = (now - ls).total_seconds() < _PEER_REACHABLE_WITHIN_SECONDS
            peers.append(
                {
                    "id": peer.id,
                    "name": peer.remote_display_name,
                    "last_seen_at": last_seen.isoformat() if last_seen else None,
                    "reachable": reachable,
                }
            )
        snapshot["peers"] = peers
    except Exception as e:  # noqa: BLE001
        logger.debug(f"kiosk snapshot: peers unavailable: {e}")

    # --- Weather tile (process-cached; None hides the tile) --------------
    try:
        from api.routes.command_center import compute_kiosk_weather

        weather = await compute_kiosk_weather(mcp_manager)
        if weather is not None:
            snapshot["weather"] = weather.model_dump()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"kiosk snapshot: weather unavailable: {e}")

    # --- Now-playing tile (one per room, PLAYING only, no user ids) ------
    try:
        from ha_glue.utils.config import ha_glue_settings

        if ha_glue_settings.media_follow_enabled:
            from ha_glue.services.media_follow_service import get_media_follow_service

            snapshot["now_playing"] = get_media_follow_service().active_sessions()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"kiosk snapshot: now-playing unavailable: {e}")

    return snapshot


@router.websocket("/ws/kiosk")
async def kiosk_live(
    websocket: WebSocket,
    token: str = Query(None, description="Authentication token"),
):
    """WebSocket endpoint for the live kiosk projection (ADMIN-gated).

    Gate: authenticate, then require ``Permission.ADMIN`` (mirroring
    ``<AdminRoute>``) UNLESS auth is disabled (single-user/household mode, where
    ``authenticate_websocket`` returns ``auth_skipped`` and the household is
    trusted). An unauthenticated or non-admin client must not open this socket
    and harvest the household-wide snapshot.
    """
    auth_result = await authenticate_websocket(websocket, token)
    if not auth_result:
        await websocket.close(
            code=WSAuthError.UNAUTHORIZED, reason="Authentication required"
        )
        return

    # Auth disabled → single-user/household mode, no per-user permission model.
    if not auth_result.get("auth_skipped"):
        user_id = auth_result.get("user_id") if isinstance(auth_result, dict) else None
        is_admin = False
        if user_id is not None:
            try:
                async with AsyncSessionLocal() as db:
                    user = await get_user_by_id(db, user_id)
                is_admin = bool(user and user.has_permission(Permission.ADMIN))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"kiosk WS admin check failed: {e}")
                is_admin = False
        if not is_admin:
            await websocket.close(
                code=WSAuthError.UNAUTHORIZED, reason="Admin permission required"
            )
            return

    await websocket.accept()
    _kiosk_clients.add(websocket)
    logger.info(f"🖥️ Kiosk display connected ({len(_kiosk_clients)} total)")

    try:
        # Hydrate: one full snapshot, then subscribe to deltas.
        snapshot = await build_kiosk_snapshot(websocket.app)
        await websocket.send_json(snapshot)

        # Push-only channel: idle on receive (ping/pong handled by framework).
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Kiosk display connection error: {e}")
    finally:
        _kiosk_clients.discard(websocket)
        logger.info(f"🖥️ Kiosk display disconnected ({len(_kiosk_clients)} total)")
