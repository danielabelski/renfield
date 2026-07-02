"""Minimal single-server Paperless MCP client for the document-worker.

The worker deliberately does NOT run the full MCP lifecycle (10 servers +
Whisper + Speechbrain) — see ``workers/document_processor_worker.py``. But the
Paperless-filing ``post_document_ingest`` hook needs the Paperless MCP to
upload + set metadata + write back the OCR content. This spins up **only** the
``paperless`` stdio server (one lightweight subprocess), lazily and once, so the
worker keeps its memory budget while still filing to Paperless.

Requires ``PAPERLESS_ENABLED=true`` + ``PAPERLESS_API_URL`` + ``PAPERLESS_API_TOKEN``
in the worker's environment (the same vars the backend passes to the stdio server
via mcp_servers.yaml). Returns None when Paperless is disabled/unconfigured or the
subprocess can't connect — callers then skip filing (leaving the doc `pending` for
a later retry) rather than crash the ingest.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from utils.config import settings

_manager: Any = None
_lock = asyncio.Lock()


async def get_paperless_mcp_manager() -> Any:
    """Lazily create + connect a single-server (paperless-only) MCP manager.

    Cached on success; a failed connect is NOT cached, so a later call retries
    (e.g. Paperless came back up)."""
    global _manager
    if _manager is not None:
        return _manager
    async with _lock:
        if _manager is not None:
            return _manager
        from services.mcp_client import MCPManager

        mgr = MCPManager()
        mgr.load_config(settings.mcp_config_path, only={"paperless"})
        # Reach into the loaded set: with only={"paperless"} it holds 0 or 1
        # servers. 0 → paperless disabled/unconfigured (enabled gate off).
        state = mgr._servers.get("paperless")
        if state is None:
            logger.warning(
                "paperless-worker-client: paperless MCP not configured/enabled "
                "(PAPERLESS_ENABLED/API_URL/TOKEN) — worker Paperless filing disabled"
            )
            return None
        await mgr.connect_all()
        if not state.connected:
            logger.warning(
                "paperless-worker-client: paperless MCP failed to connect in the "
                "worker — leaving docs pending for a later retry"
            )
            return None
        _manager = mgr
        logger.info("paperless-worker-client: connected (single-server paperless MCP)")
    return _manager
