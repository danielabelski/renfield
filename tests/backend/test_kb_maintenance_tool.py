"""Tests for the chat-triggerable KB maintenance tools.

`internal.reindex_documents` reindexes completed docs with 0 chunks (enqueues
user_reindex worker tasks); `internal.ingest_status` reports pipeline state. DB +
queue + redis are mocked at their seams; the SQL itself is exercised on .159.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.kb_maintenance_tool as kb

pytestmark = [pytest.mark.unit]


def _scalars_result(ids):
    r = MagicMock()
    r.scalars.return_value.all.return_value = ids
    return r


def _all_result(rows):
    r = MagicMock()
    r.all.return_value = rows
    return r


def _scalar_result(val):
    r = MagicMock()
    r.scalar.return_value = val
    return r


def _session(execute_results):
    """Session whose execute() returns the given result objects in order."""
    session = MagicMock()
    session.execute = AsyncMock(side_effect=list(execute_results))
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _cm():
        yield session

    return _cm, session


def _patch_queue(monkeypatch):
    q = MagicMock()
    q.enqueue = AsyncMock()
    monkeypatch.setattr(
        "services.task_queue.DocumentTaskQueue", MagicMock(return_value=q)
    )
    monkeypatch.setattr("services.redis_client.get_redis", MagicMock(return_value=MagicMock()))
    return q


# --------------------------------------------------------------------------
# reindex_documents
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reindex_enqueues_user_reindex_for_chunkless(monkeypatch):
    # select chunkless ids -> [5, 9]; then the UPDATE result (ignored)
    cm, session = _session([_scalars_result([5, 9]), MagicMock()])
    monkeypatch.setattr(kb, "AsyncSessionLocal", cm)
    monkeypatch.setattr(kb.settings, "auth_enabled", False)
    q = _patch_queue(monkeypatch)

    out = await kb.reindex_documents({}, user_id=7)
    assert out["success"] and out["data"]["reindexed"] == 2
    payloads = [c.args[0] for c in q.enqueue.await_args_list]
    assert {p["document_id"] for p in payloads} == {5, 9}
    assert all(p["trigger"] == "user_reindex" for p in payloads)
    assert all(p["user_id"] == 7 for p in payloads)
    # status flipped to pending before enqueue (UPDATE executed + committed)
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_reindex_noop_when_none_chunkless(monkeypatch):
    cm, session = _session([_scalars_result([])])
    monkeypatch.setattr(kb, "AsyncSessionLocal", cm)
    monkeypatch.setattr(kb.settings, "auth_enabled", False)
    q = _patch_queue(monkeypatch)

    out = await kb.reindex_documents({})
    assert out["success"] and out["data"]["reindexed"] == 0
    q.enqueue.assert_not_awaited()
    session.commit.assert_not_awaited()  # nothing flipped


@pytest.mark.asyncio
async def test_reindex_denied_for_low_priv_user(monkeypatch):
    monkeypatch.setattr(kb.settings, "auth_enabled", True)
    # a session that would blow up if reached — proves we short-circuit on the gate
    monkeypatch.setattr(kb, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("must not query")))
    out = await kb.reindex_documents({}, user_id=3, user_permissions=["rag.use"])
    assert out["success"] is False
    assert out["action_taken"] is False


@pytest.mark.asyncio
async def test_reindex_allowed_with_rag_manage(monkeypatch):
    cm, _ = _session([_scalars_result([11]), MagicMock()])
    monkeypatch.setattr(kb, "AsyncSessionLocal", cm)
    monkeypatch.setattr(kb.settings, "auth_enabled", True)
    q = _patch_queue(monkeypatch)
    out = await kb.reindex_documents({}, user_id=3, user_permissions=["rag.manage"])
    assert out["success"] and out["data"]["reindexed"] == 1


@pytest.mark.asyncio
async def test_reindex_allowed_when_permissions_none(monkeypatch):
    # auth on but user_permissions None (auth-off context / unidentified voice) → allowed
    cm, _ = _session([_scalars_result([1]), MagicMock()])
    monkeypatch.setattr(kb, "AsyncSessionLocal", cm)
    monkeypatch.setattr(kb.settings, "auth_enabled", True)
    _patch_queue(monkeypatch)
    out = await kb.reindex_documents({}, user_id=None, user_permissions=None)
    assert out["success"] and out["data"]["reindexed"] == 1


@pytest.mark.asyncio
async def test_reindex_limit_clamped_and_reported(monkeypatch):
    # exactly cap results → message flags "weitere folgen"
    cm, _ = _session([_scalars_result([1, 2]), MagicMock()])
    monkeypatch.setattr(kb, "AsyncSessionLocal", cm)
    monkeypatch.setattr(kb.settings, "auth_enabled", False)
    _patch_queue(monkeypatch)
    out = await kb.reindex_documents({"limit": 2})
    assert out["data"]["reindexed"] == 2
    assert "weitere folgen" in out["message"]


@pytest.mark.asyncio
async def test_reindex_fails_when_all_enqueues_fail(monkeypatch):
    # Redis/queue outage: docs found but every enqueue raises → report FAILURE,
    # not a misleading success with reindexed=0.
    cm, session = _session([_scalars_result([5, 9])])
    monkeypatch.setattr(kb, "AsyncSessionLocal", cm)
    monkeypatch.setattr(kb.settings, "auth_enabled", False)
    q = MagicMock()
    q.enqueue = AsyncMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr("services.task_queue.DocumentTaskQueue", MagicMock(return_value=q))
    monkeypatch.setattr("services.redis_client.get_redis", MagicMock(return_value=MagicMock()))

    out = await kb.reindex_documents({})
    assert out["success"] is False
    assert out["action_taken"] is False
    assert out["data"]["reindexed"] == 0
    session.commit.assert_not_awaited()  # no status flip on total failure


@pytest.mark.asyncio
async def test_reindex_bad_limit_falls_back(monkeypatch):
    cm, _ = _session([_scalars_result([]), ])
    monkeypatch.setattr(kb, "AsyncSessionLocal", cm)
    monkeypatch.setattr(kb.settings, "auth_enabled", False)
    _patch_queue(monkeypatch)
    out = await kb.reindex_documents({"limit": "not-a-number"})
    assert out["success"] is True  # bad limit ignored, no crash


# --------------------------------------------------------------------------
# ingest_status
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_status_reports_counts(monkeypatch):
    cm, _ = _session([
        _all_result([("completed", 10), ("pending", 5), ("processing", 1)]),  # status group
        _scalar_result(3),                                                     # chunkless
        _all_result([("done", 8), (None, 2), ("pending", 5)]),                 # paperless group
    ])
    monkeypatch.setattr(kb, "AsyncSessionLocal", cm)
    # force the worker/queue probe down the except path (keep the test hermetic)
    monkeypatch.setattr(
        "services.redis_client.get_redis", MagicMock(side_effect=RuntimeError("no redis")))

    out = await kb.ingest_status({})
    assert out["success"] is True
    d = out["data"]
    assert d["documents_by_status"] == {"completed": 10, "pending": 5, "processing": 1}
    assert d["completed_without_chunks"] == 3
    assert d["paperless_state"]["done"] == 8
    assert d["paperless_state"]["unfiled"] == 2   # NULL → unfiled
    assert d["paperless_pending"] == 5
    assert "KB-Verarbeitung" in out["message"]
    assert "3 fertige Dokument(e) haben KEINE Chunks" in out["message"]
