"""Tests for the Paperless refile RE-ENQUEUER (light backend path).

After the OOM fix, the backend no longer runs Docling / the Paperless leg. It only
re-enqueues stragglers (docs that stayed paperless_state='pending' after completing)
as 'paperless_refile' worker tasks — the worker does the heavy filing. These verify
the enqueue behaviour + the grace window + the per-doc refile lease + owner
threading, with the DB + queue + redis mocked at their seams.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.paperless_reconciler as pr


def _fake_session(rows):
    """``rows`` is a list of ``(doc_id, owner_user_id)`` tuples — the reconciler
    now selects the owner via an atoms left-join, so the result is ``.all()``
    rows, not ``.scalars().all()``."""
    session = MagicMock()
    result = MagicMock()
    result.all.return_value = rows
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _cm():
        yield session

    return _cm, session


def _fake_redis(acquired=True):
    """Redis mock whose lease ``set(... nx=True)`` returns True (acquired) or
    None (already leased → skip)."""
    r = MagicMock()
    r.set = AsyncMock(return_value=(True if acquired else None))
    return r


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_stragglers_is_noop(monkeypatch):
    cm, _ = _fake_session([])
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    q = MagicMock()
    q.enqueue = AsyncMock()
    monkeypatch.setattr(pr, "DocumentTaskQueue", MagicMock(return_value=q))
    monkeypatch.setattr(pr, "get_redis", MagicMock(return_value=_fake_redis()))
    await pr.reenqueue_pending_paperless()
    q.enqueue.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reenqueues_pending_stragglers_with_owner(monkeypatch):
    cm, _ = _fake_session([(5, 100), (9, 200)])
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    q = MagicMock()
    q.enqueue = AsyncMock()
    monkeypatch.setattr(pr, "DocumentTaskQueue", MagicMock(return_value=q))
    monkeypatch.setattr(pr, "get_redis", MagicMock(return_value=_fake_redis()))
    await pr.reenqueue_pending_paperless()
    assert q.enqueue.await_count == 2
    payloads = [c.args[0] for c in q.enqueue.await_args_list]
    assert {p["document_id"] for p in payloads} == {5, 9}
    assert all(p["trigger"] == "paperless_refile" for p in payloads)
    # Owner is threaded so the worker refile files with owner-scoped metadata.
    by_id = {p["document_id"]: p["user_id"] for p in payloads}
    assert by_id == {5: 100, 9: 200}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lease_skips_already_enqueued(monkeypatch):
    # A doc whose refile is still in flight (lease held) must NOT be re-enqueued.
    cm, _ = _fake_session([(5, 100)])
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    q = MagicMock()
    q.enqueue = AsyncMock()
    monkeypatch.setattr(pr, "DocumentTaskQueue", MagicMock(return_value=q))
    monkeypatch.setattr(pr, "get_redis", MagicMock(return_value=_fake_redis(acquired=False)))
    await pr.reenqueue_pending_paperless()
    q.enqueue.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_null_owner_threads_none(monkeypatch):
    # atoms left-join miss (dev/SQLite doc with atom_id NULL) → user_id None,
    # still enqueued (owner is a metadata-quality refinement, not required).
    cm, _ = _fake_session([(7, None)])
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    q = MagicMock()
    q.enqueue = AsyncMock()
    monkeypatch.setattr(pr, "DocumentTaskQueue", MagicMock(return_value=q))
    monkeypatch.setattr(pr, "get_redis", MagicMock(return_value=_fake_redis()))
    await pr.reenqueue_pending_paperless()
    assert q.enqueue.await_args.args[0] == {
        "document_id": 7,
        "trigger": "paperless_refile",
        "user_id": None,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_filters_pending_completed_and_null_processed_at(monkeypatch):
    # The scan must filter on paperless_state=pending, status=completed, and
    # (processed_at < cutoff OR processed_at IS NULL) — the last so an anomalous
    # NULL-processed_at completed row isn't dropped forever by SQL NULL semantics.
    cm, session = _fake_session([])
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    monkeypatch.setattr(pr, "DocumentTaskQueue", MagicMock())
    monkeypatch.setattr(pr, "get_redis", MagicMock(return_value=_fake_redis()))
    await pr.reenqueue_pending_paperless()
    assert session.execute.await_count == 1
    compiled = str(session.execute.await_args.args[0])
    assert "processed_at" in compiled
    assert "paperless_state" in compiled
    assert "status" in compiled
    assert "IS NULL" in compiled
