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

Two accepted tradeoffs, both bounded and documented rather than over-engineered:
  - **Held connection:** each leg holds its pooled DB connection across the
    external upload+consume round-trip. This is the same *shape* as the outage it
    replaces, but BOUNDED to ``paperless_reconciler_concurrency`` (default 3) in a
    pod whose pool is 30 — categorically different from the unbounded request-path
    flood. Keep the concurrency small (see the config comment).
  - **Single-replica assumption:** unlike the per-user reconcilers here, this one
    takes no pg advisory lock. The backend runs ``replicas: 1`` (k8s/backend.yaml),
    so ticks never overlap across processes; if the backend is ever scaled out,
    add an advisory lock here (Paperless's own duplicate detection is the only
    backstop until then).
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiofiles
from loguru import logger
from sqlalchemy import select

from models.database import (
    DOC_STATUS_COMPLETED,
    PAPERLESS_STATE_FAILED,
    PAPERLESS_STATE_PENDING,
    Document,
)
from services.database import AsyncSessionLocal
from services.folder_ingest import IngestMeta
from services.folder_ingest_paperless import make_paperless_leg
from utils.config import settings

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
            # The persisted recovery copy is gone (deleted out of band / lost
            # volume). Without the bytes the doc can NEVER be filed, so mark the
            # leg terminally FAILED (settled) rather than leaving it 'pending'
            # forever — an unfilable doc stuck at the low end of the id-ordered
            # batch would be re-selected every tick and starve newer pending docs
            # (poison-pill). 'failed' takes it out of the working set; the doc is
            # still in the KB, only Paperless filing is skipped (observable).
            doc.paperless_state = PAPERLESS_STATE_FAILED
            await db.commit()
            logger.warning(
                f"paperless-reconciler: doc {doc_id} recovery bytes unreadable "
                f"({exc}); marked paperless_state=failed (cannot file without bytes)"
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

    sem = asyncio.Semaphore(settings.paperless_reconciler_concurrency)
    results = await asyncio.gather(
        *(_file_one(mcp_manager, doc_id, sem) for doc_id in doc_ids)
    )
    settled = sum(1 for r in results if r == "settled")
    logger.info(
        f"paperless-reconciler: filed {settled}/{len(doc_ids)} pending "
        f"document(s) (batch={batch})"
    )
