"""Async Paperless filing for folder/email auto-ingest (Design Z).

The folder/email-ingest push used to file into Paperless *inline*, awaiting the
external ``mcp.paperless.upload_document`` + ``await_consume_result`` round-trip
on the request-scoped DB session. Under a burst (a large watch-folder backlog),
that pinned one pooled DB connection per push across a multi-second external wait
— during which the connection did no DB work — and exhausted the pool, so every
interactive request timed out and the backend liveness-restarted (the 2026-07-01
outage). The fix decouples filing from the request:

  - the ingest bridge stamps ``Document.paperless_state='pending'`` and returns
    immediately (no external wait on the request);
  - this periodic, stateless reconciler files every ``pending`` + ``completed``
    document out of band via the shared backend MCP manager, with its OWN
    short-lived session per document and bounded concurrency.

Properties:
  - **Durable + timely**: a document stays ``pending`` until the leg settles it
    to ``done``/``failed``. A crashed or half-finished tick is simply retried on
    the next interval — no state to lose (contrast an in-process queue, which
    would drop in-flight work on a restart).
  - **Idempotent**: the leg no-ops a re-run of an already-settled document, and
    each tick re-checks the row's state under its own session before filing.
  - **Provenance-safe**: only folder/email-ingest set ``pending``; interactive KB
    uploads stay NULL and are never filed to Paperless.

``user_id`` is passed ``None`` to the leg (the Paperless extractor's owner-scoped
learned-examples are a refinement the correspondent resolve-or-create does not
depend on) — matching the email-ingest leg, whose owner isn't threaded either.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiofiles
from loguru import logger
from sqlalchemy import select

from models.database import (
    DOC_STATUS_COMPLETED,
    PAPERLESS_STATE_PENDING,
    Document,
)
from services.database import AsyncSessionLocal
from services.folder_ingest import IngestMeta
from services.folder_ingest_paperless import make_paperless_leg
from utils.config import settings

# Bounded concurrency: the leg blocks on the external Paperless upload +
# consume-await, so a handful in flight is plenty and keeps the reconciler from
# becoming its own load spike (the very thing it exists to prevent).
_CONCURRENCY = 3


async def _file_one(mcp_manager: Any, doc_id: int, sem: asyncio.Semaphore) -> str:
    """File a single pending document into Paperless with its own session.

    Returns a short outcome tag for tick-level aggregation. Never raises — a leg
    error leaves the row ``pending`` for the next tick (durable retry)."""
    async with sem, AsyncSessionLocal() as db:
        doc = (
            await db.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one_or_none()
        if doc is None:
            return "gone"
        # Re-check under this session: a concurrent tick / re-push may have
        # already settled it, or the doc may have slipped out of the filing set.
        if (
            doc.paperless_state != PAPERLESS_STATE_PENDING
            or doc.status != DOC_STATUS_COMPLETED
        ):
            return "skip"
        try:
            async with aiofiles.open(doc.file_path, "rb") as f:
                file_bytes = await f.read()
        except OSError as exc:
            # The persisted recovery copy is gone (e.g. doc deleted mid-flight).
            # Leave pending — a future tick self-heals if the row/file returns,
            # else it stays visible as an un-filed pending doc rather than lying.
            logger.warning(
                f"paperless-reconciler: doc {doc_id} bytes unreadable "
                f"({exc}); leaving pending"
            )
            return "no_bytes"

        leg = make_paperless_leg(mcp_manager, user_id=None)
        try:
            settled = await leg(db, doc, file_bytes, IngestMeta(filename=doc.filename))
        except Exception as exc:  # noqa: BLE001 - never fail the tick on one doc
            logger.warning(
                f"paperless-reconciler: doc {doc_id} leg error ({exc}); will retry"
            )
            return "error"
        return "settled" if settled else "pending"


async def reconcile_pending_paperless(mcp_manager: Any) -> None:
    """Scheduler entry point: file a bounded batch of pending documents.

    Batched (``paperless_reconciler_batch``) so a large first-run backlog is
    drained across several ticks instead of one giant burst; the next tick picks
    up the remainder."""
    if mcp_manager is None:
        logger.debug("paperless-reconciler: mcp_manager not ready; skipping tick")
        return

    batch = settings.paperless_reconciler_batch
    async with AsyncSessionLocal() as enum_session:
        doc_ids = list(
            (
                await enum_session.execute(
                    select(Document.id)
                    .where(
                        Document.paperless_state == PAPERLESS_STATE_PENDING,
                        Document.status == DOC_STATUS_COMPLETED,
                    )
                    .order_by(Document.id)
                    .limit(batch)
                )
            )
            .scalars()
            .all()
        )
    if not doc_ids:
        return

    sem = asyncio.Semaphore(_CONCURRENCY)
    results = await asyncio.gather(
        *(_file_one(mcp_manager, doc_id, sem) for doc_id in doc_ids)
    )
    settled = sum(1 for r in results if r == "settled")
    logger.info(
        f"paperless-reconciler: filed {settled}/{len(doc_ids)} pending "
        f"document(s) (batch={batch})"
    )
