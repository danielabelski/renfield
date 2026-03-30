"""
Live Knowledge Graph WebSocket endpoint.

Pushes new entities and relations to connected graph viewers in real-time
as KG extraction runs after conversations.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

router = APIRouter()

# Connected graph viewer WebSockets
_viewers: set[WebSocket] = set()


async def broadcast_kg_update(entities: list[dict], relations: list[dict]) -> None:
    """Broadcast new KG entities/relations to all connected graph viewers.

    Fire-and-forget: failures are logged but never propagate.
    """
    if not _viewers or (not entities and not relations):
        return

    message = {
        "type": "kg_update",
        "entities": entities,
        "relations": relations,
    }

    broken: list[WebSocket] = []
    for ws in _viewers:
        try:
            await ws.send_json(message)
        except Exception:
            broken.append(ws)

    for ws in broken:
        _viewers.discard(ws)


@router.websocket("/ws/knowledge-graph")
async def knowledge_graph_live(websocket: WebSocket):
    """WebSocket endpoint for live KG graph updates."""
    await websocket.accept()
    _viewers.add(websocket)
    logger.info(f"📊 KG graph viewer connected ({len(_viewers)} total)")

    try:
        # Keep connection alive; we only push, never receive meaningful messages
        while True:
            # Wait for client messages (ping/pong handled by framework)
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"KG viewer connection error: {e}")
    finally:
        _viewers.discard(websocket)
        logger.info(f"📊 KG graph viewer disconnected ({len(_viewers)} total)")
