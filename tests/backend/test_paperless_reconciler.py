"""Tests for the Paperless refile RE-ENQUEUER (light backend path).

After the OOM fix, the backend no longer runs Docling / the Paperless leg. It only
re-enqueues stragglers (docs that stayed paperless_state='pending' after completing)
as 'paperless_refile' worker tasks — the worker does the heavy filing. These verify
the enqueue behaviour + the grace window, with the DB + queue mocked at their seams.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.paperless_reconciler as pr


def _fake_session(ids):
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = ids
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _cm():
        yield session

    return _cm, session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_stragglers_is_noop(monkeypatch):
    cm, _ = _fake_session([])
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    q = MagicMock()
    q.enqueue = AsyncMock()
    monkeypatch.setattr(pr, "DocumentTaskQueue", MagicMock(return_value=q))
    monkeypatch.setattr(pr, "get_redis", MagicMock(return_value=MagicMock()))
    await pr.reenqueue_pending_paperless()
    q.enqueue.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reenqueues_pending_stragglers(monkeypatch):
    cm, _ = _fake_session([5, 9])
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    q = MagicMock()
    q.enqueue = AsyncMock()
    monkeypatch.setattr(pr, "DocumentTaskQueue", MagicMock(return_value=q))
    monkeypatch.setattr(pr, "get_redis", MagicMock(return_value=MagicMock()))
    await pr.reenqueue_pending_paperless()
    assert q.enqueue.await_count == 2
    # each task is a paperless_refile trigger for its doc (no Docling in backend)
    payloads = [c.args[0] for c in q.enqueue.await_args_list]
    assert {p["document_id"] for p in payloads} == {5, 9}
    assert all(p["trigger"] == "paperless_refile" for p in payloads)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_uses_grace_cutoff(monkeypatch):
    # The scan must filter on processed_at < now - grace (don't race the initial
    # fire-and-forget filing). Assert a WHERE clause was built with the cutoff by
    # capturing the executed statement.
    cm, session = _fake_session([])
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    monkeypatch.setattr(pr, "DocumentTaskQueue", MagicMock())
    monkeypatch.setattr(pr, "get_redis", MagicMock(return_value=MagicMock()))
    await pr.reenqueue_pending_paperless()
    # one SELECT was executed with a compiled where-clause referencing processed_at
    assert session.execute.await_count == 1
    stmt = session.execute.await_args.args[0]
    compiled = str(stmt)
    assert "processed_at" in compiled and "paperless_state" in compiled
