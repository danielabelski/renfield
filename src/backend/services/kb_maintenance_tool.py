"""
KB / ingest maintenance tools — platform-owned agent tools.

Two chat-triggerable `internal.*` tools for the document ingest → KB → Paperless
pipeline:

- ``internal.ingest_status`` (read-only): reports the live processing state —
  documents by status, how many completed docs have NO chunks, the worker queue
  depth + liveness, and the Paperless filing state. Backs questions like
  "wie ist der Verarbeitungsstatus?" / "sind alle Dokumente in Paperless?".

- ``internal.reindex_documents`` (write / maintenance): finds ``completed``
  documents with **0 chunks** (indexing finished but produced nothing) and
  enqueues a ``user_reindex`` worker task for each (purge + rebuild) — the same
  path as ``POST /api/knowledge/documents/{id}/reindex``. Gated on
  ``Permission.RAG_MANAGE`` when auth is enabled (an authenticated low-privilege
  user is refused; auth-off / unidentified-voice turns are allowed, matching the
  platform's HA_CONTROL convention).

Mirrors ``services/memory_list_tool.py``: flattened tool definitions registered by
``agent_tools._register_internal_tools`` + async handlers dispatched as special
cases in ``action_executor`` (which injects the authenticated ``user_id`` and,
for reindex, ``user_permissions``).
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy import func, select, update

from models.database import (
    DOC_STATUS_COMPLETED,
    DOC_STATUS_PENDING,
    Document,
    DocumentChunk,
)
from models.permissions import Permission, has_permission
from services.database import AsyncSessionLocal
from utils.config import settings

REINDEX_DEFAULT_CAP = 200
REINDEX_MAX_CAP = 500

# Registered with the agent tool registry by
# `services/agent_tools.py::_register_internal_tools()`.
KB_MAINTENANCE_TOOLS: dict = {
    "internal.ingest_status": {
        "description": (
            "Report the CURRENT processing status of the document pipeline — "
            "knowledge-base indexing AND Paperless filing. Use for questions like "
            "'wie ist der Verarbeitungsstatus?', 'werden noch Dokumente "
            "verarbeitet?', 'gibt es einen Rückstau?', 'sind alle Dokumente in "
            "Paperless abgelegt?'. Returns how many documents are pending / "
            "processing / completed / failed, how many completed documents have "
            "NO chunks (empty index), the worker queue depth and whether the "
            "worker is alive, and the Paperless filing state (filed / pending / "
            "failed / not-filed)."
        ),
        "parameters": {},
    },
    "internal.reindex_documents": {
        "description": (
            "Re-index documents in the knowledge base that have NO chunks — "
            "documents that finished indexing but produced nothing. Enqueues a "
            "background reindex (purge + rebuild) for each and reports how many "
            "were queued. Use when the user asks to 'reindex documents without "
            "chunks', 'Dokumente ohne Chunks neu indexieren', or 'repariere die "
            "leeren Dokumente'. Does nothing to documents that already have chunks "
            "or are currently being processed."
        ),
        "parameters": {
            "limit": (
                "Max documents to reindex in one call (optional; default "
                f"{REINDEX_DEFAULT_CAP}, max {REINDEX_MAX_CAP})"
            ),
        },
    },
}

# paperless_state → human label for the status readout.
_PL_LABELS = {
    "done": "abgelegt",
    "pending": "ausstehend",
    "failed": "fehlgeschlagen",
    "unfiled": "nicht vorgesehen",  # NULL → interactive uploads, never filed
}


async def ingest_status(params: dict, user_id: int | None = None) -> dict:
    """Read-only snapshot of the ingest → KB → Paperless pipeline."""
    try:
        async with AsyncSessionLocal() as db:
            status_counts = {
                r[0]: r[1]
                for r in (
                    await db.execute(
                        select(Document.status, func.count()).group_by(Document.status)
                    )
                ).all()
            }
            # completed docs with zero chunk rows
            chunk_sub = (
                select(DocumentChunk.document_id)
                .group_by(DocumentChunk.document_id)
                .subquery()
            )
            chunkless = (
                await db.execute(
                    select(func.count())
                    .select_from(Document)
                    .outerjoin(chunk_sub, chunk_sub.c.document_id == Document.id)
                    .where(
                        Document.status == DOC_STATUS_COMPLETED,
                        chunk_sub.c.document_id.is_(None),
                    )
                )
            ).scalar()
            pl_counts = {
                (r[0] or "unfiled"): r[1]
                for r in (
                    await db.execute(
                        select(Document.paperless_state, func.count()).group_by(
                            Document.paperless_state
                        )
                    )
                ).all()
            }

        # worker liveness + queue depth (best-effort; never fail the readout)
        worker_alive = None
        queue_depth = None
        try:
            from api.routes.knowledge import _worker_is_alive
            from services.redis_client import get_redis
            from services.task_queue import DocumentTaskQueue

            worker_alive = await _worker_is_alive()
            queue_depth = await DocumentTaskQueue(
                redis_client=get_redis()
            ).stream_length()
        except Exception as e:  # noqa: BLE001 - liveness is a nice-to-have
            logger.warning(f"ingest_status: worker/queue probe failed: {e}")

        pending = status_counts.get("pending", 0)
        processing = status_counts.get("processing", 0)
        completed = status_counts.get("completed", 0)
        failed = status_counts.get("failed", 0)
        pl_pending = pl_counts.get("pending", 0)
        pl_failed = pl_counts.get("failed", 0)

        parts = [
            f"KB-Verarbeitung: {completed} fertig, {pending} in Warteschlange, "
            f"{processing} in Arbeit, {failed} fehlgeschlagen."
        ]
        if chunkless:
            parts.append(
                f"{chunkless} fertige Dokument(e) haben KEINE Chunks "
                f"(leerer Index — mit 'Dokumente ohne Chunks neu indexieren' reparierbar)."
            )
        pl_bits = ", ".join(
            f"{v} {_PL_LABELS.get(k, k)}" for k, v in sorted(pl_counts.items())
        )
        parts.append(f"Paperless: {pl_bits}.")
        if worker_alive is not None:
            parts.append(
                f"Worker: {'aktiv' if worker_alive else 'NICHT erreichbar'}"
                + (f", {queue_depth} Aufgabe(n) in der Queue." if queue_depth is not None else ".")
            )

        return {
            "success": True,
            "message": " ".join(parts),
            "action_taken": True,
            "data": {
                "documents_by_status": status_counts,
                "completed_without_chunks": int(chunkless or 0),
                "paperless_state": pl_counts,
                "paperless_pending": pl_pending,
                "paperless_failed": pl_failed,
                "worker_alive": worker_alive,
                "queue_depth": queue_depth,
            },
        }
    except Exception as e:
        logger.error(f"Error in ingest_status: {e}")
        return {
            "success": False,
            "message": f"Status-Abfrage fehlgeschlagen: {e!s}",
            "action_taken": False,
        }


async def reindex_documents(
    params: dict,
    user_id: int | None = None,
    user_permissions: list[str] | None = None,
) -> dict:
    """Enqueue a reindex (purge + rebuild) for every completed doc with 0 chunks.

    Gated on ``Permission.RAG_MANAGE`` when auth is enabled: ``user_permissions``
    is None (auth-off OR unidentified voice turn) is allowed — matching the
    platform's HA_CONTROL convention — but an authenticated user lacking
    rag.manage is refused (a low-privilege member can't trigger a fleet re-OCR).
    """
    if settings.auth_enabled and user_permissions is not None:
        if not has_permission(user_permissions, Permission.RAG_MANAGE):
            return {
                "success": False,
                "message": (
                    "Zum Neu-Indexieren fehlt die Berechtigung "
                    "(rag.manage / Dokumentenverwaltung)."
                ),
                "action_taken": False,
            }

    cap = REINDEX_DEFAULT_CAP
    if params.get("limit"):
        try:
            cap = max(1, min(REINDEX_MAX_CAP, int(params["limit"])))
        except (ValueError, TypeError):
            pass

    try:
        async with AsyncSessionLocal() as db:
            # completed docs with no chunk rows (excludes pending/processing by
            # construction, so no in-flight double-enqueue — mirrors the route's
            # dedup guard). Capped, oldest first.
            chunk_sub = (
                select(DocumentChunk.document_id)
                .group_by(DocumentChunk.document_id)
                .subquery()
            )
            doc_ids = list(
                (
                    await db.execute(
                        select(Document.id)
                        .select_from(Document)
                        .outerjoin(chunk_sub, chunk_sub.c.document_id == Document.id)
                        .where(
                            Document.status == DOC_STATUS_COMPLETED,
                            chunk_sub.c.document_id.is_(None),
                        )
                        .order_by(Document.id)
                        .limit(cap)
                    )
                )
                .scalars()
                .all()
            )
        if not doc_ids:
            return {
                "success": True,
                "message": "Keine fertigen Dokumente ohne Chunks gefunden — nichts zu tun.",
                "action_taken": True,
                "empty_result": True,
                "data": {"reindexed": 0},
            }

        # Enqueue FIRST, then flip only the successfully-enqueued docs to
        # 'pending'. The worker reprocesses a user_reindex regardless of the
        # doc's status, so enqueue-then-flip avoids the orphan a flip-then-enqueue
        # crash would leave (a doc stuck 'pending' with no task). A failed enqueue
        # simply leaves the doc 'completed' — retried on the next call — instead
        # of stranded.
        from services.redis_client import get_redis
        from services.task_queue import DocumentTaskQueue

        queue = DocumentTaskQueue(redis_client=get_redis())
        enqueued_ids: list[int] = []
        for did in doc_ids:
            try:
                await queue.enqueue(
                    {
                        "document_id": did,
                        "force_ocr": False,
                        "user_id": user_id,
                        "trigger": "user_reindex",
                    }
                )
                enqueued_ids.append(did)
            except Exception as e:  # noqa: BLE001 - one bad enqueue mustn't abort the batch
                logger.warning(f"reindex_documents: enqueue failed for doc {did}: {e}")

        # Had work but nothing could be enqueued → the queue is unreachable.
        # Report failure rather than a success with reindexed=0 (which is
        # indistinguishable from the legitimate "nothing to do" path and would
        # mislead the operator during exactly the outage this tool diagnoses).
        if not enqueued_ids:
            return {
                "success": False,
                "message": "Einreihen fehlgeschlagen — die Aufgaben-Queue ist nicht erreichbar.",
                "action_taken": False,
                "data": {"reindexed": 0},
            }

        # Cosmetic status flip so the KB list/poll shows them queued (the worker
        # sets the real state as it processes). Guard on status=completed so a
        # doc the worker already advanced (fast-worker race across the three
        # separate transactions) isn't dragged back to 'pending' with no task.
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Document)
                .where(
                    Document.id.in_(enqueued_ids),
                    Document.status == DOC_STATUS_COMPLETED,
                )
                .values(status=DOC_STATUS_PENDING, error_message=None)
            )
            await db.commit()

        more = " (weitere folgen beim nächsten Aufruf)" if len(doc_ids) == cap else ""
        return {
            "success": True,
            "message": (
                f"{len(enqueued_ids)} Dokument(e) ohne Chunks zum Neu-Indexieren "
                f"eingereiht{more}. Die Verarbeitung läuft im Hintergrund."
            ),
            "action_taken": True,
            "data": {"reindexed": len(enqueued_ids), "document_ids": enqueued_ids},
        }
    except Exception as e:
        logger.error(f"Error in reindex_documents: {e}")
        return {
            "success": False,
            "message": f"Neu-Indexieren fehlgeschlagen: {e!s}",
            "action_taken": False,
        }
