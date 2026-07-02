"""Retry re-enqueuer for Paperless filing — LIGHT, no Docling in the backend.

The document-worker files each folder/email-ingest document into Paperless during
ingest (``paperless_filing_hook``), reusing the worker's own high-quality Docling
OCR. That is the normal path. If the hook couldn't settle (Paperless was down when
the doc was processed), the doc stays ``paperless_state='pending'`` after it has
``completed``.

This periodic backend scan re-ENQUEUES those stragglers as ``paperless_refile``
worker tasks — the heavy Docling/OCR + MCP work runs in the WORKER (its home);
the backend only enqueues. This is the fix for the OOM outage: the backend used to
run Docling here (concurrent, atop the full app) and exceeded its 6 Gi limit.

A grace window (``paperless_reconciler_refile_grace_seconds``) keeps the scan from
racing an initial filing that is still in flight (the hook runs fire-and-forget
after the doc is marked completed), so a fresh doc isn't refiled while its first
attempt is mid-upload.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select

from models.database import (
    DOC_STATUS_COMPLETED,
    PAPERLESS_STATE_PENDING,
    Document,
)
from services.database import AsyncSessionLocal
from services.redis_client import get_redis
from services.task_queue import DocumentTaskQueue
from utils.config import settings


async def reenqueue_pending_paperless(mcp_manager: Any = None) -> None:
    """Enqueue a ``paperless_refile`` worker task for each straggler pending doc.

    ``mcp_manager`` is accepted but unused — kept so the scheduler wiring stays
    uniform with the other reconcilers; the backend no longer touches the MCP for
    Paperless (the worker does)."""
    grace = timedelta(seconds=settings.paperless_reconciler_refile_grace_seconds)
    cutoff = datetime.now(UTC).replace(tzinfo=None) - grace
    batch = settings.paperless_reconciler_batch

    async with AsyncSessionLocal() as db:
        doc_ids = list(
            (
                await db.execute(
                    select(Document.id)
                    .where(
                        Document.paperless_state == PAPERLESS_STATE_PENDING,
                        Document.status == DOC_STATUS_COMPLETED,
                        Document.processed_at < cutoff,
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

    queue = DocumentTaskQueue(redis_client=get_redis())
    for doc_id in doc_ids:
        await queue.enqueue({"document_id": doc_id, "trigger": "paperless_refile"})
    logger.info(
        f"paperless-reconciler: re-enqueued {len(doc_ids)} pending doc(s) for "
        f"worker refile (grace={grace.total_seconds():.0f}s)"
    )
