"""Tests for the document-worker stale-task recovery hardening:
- reclaim_stale stamps each reclaimed entry with its PEL redelivery count.
- _process_entry quarantines an entry redelivered past the poison cap (a doc that
  kept OOM-killing the worker) instead of re-processing it into a crashloop.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit]


# --------------------------------------------------------------------------
# reclaim_stale: delivery-count stamping
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclaim_stale_stamps_delivery_count():
    from services.task_queue import DocumentTaskQueue

    r = MagicMock()
    # XAUTOCLAIM -> (next_cursor, items, deleted); one entry then the "0-0" stop.
    r.xautoclaim = AsyncMock(
        return_value=("0-0", [("5-0", {"payload": json.dumps({"document_id": 9})})], [])
    )
    # XPENDING range -> this entry has been delivered 4 times.
    r.xpending_range = AsyncMock(
        return_value=[{"message_id": "5-0", "times_delivered": 4}]
    )
    r.xack = AsyncMock()

    q = DocumentTaskQueue(redis_client=r)
    claimed = await q.reclaim_stale()

    assert len(claimed) == 1
    assert claimed[0].entry_id == "5-0"
    assert claimed[0].params == {"document_id": 9}
    assert claimed[0].delivery_count == 4  # stamped from XPENDING


@pytest.mark.asyncio
async def test_reclaim_stale_no_entries_is_empty():
    from services.task_queue import DocumentTaskQueue

    r = MagicMock()
    r.xautoclaim = AsyncMock(return_value=("0-0", [], []))
    r.xpending_range = AsyncMock(return_value=[])
    q = DocumentTaskQueue(redis_client=r)
    assert await q.reclaim_stale() == []


# --------------------------------------------------------------------------
# _process_entry: OOM-poison guard
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poison_guard_quarantines_over_cap(monkeypatch):
    import workers.document_processor_worker as w
    from services.task_queue import StreamEntry

    monkeypatch.setattr(w.settings, "worker_max_deliveries", 3)
    marked = []

    async def _fake_mark(doc_id, err):
        marked.append((doc_id, str(err)))
        return True

    monkeypatch.setattr(w, "_mark_document_failed", _fake_mark)
    # Entering the real processing path would touch the DB session — blow up if so.
    monkeypatch.setattr(
        w, "AsyncSessionLocal",
        MagicMock(side_effect=AssertionError("must not process a quarantined entry")),
    )
    q = MagicMock()
    q.ack = AsyncMock()
    entry = StreamEntry(entry_id="1-0", params={"document_id": 241}, delivery_count=4)

    await w._process_entry(MagicMock(), q, entry)

    assert marked and marked[0][0] == 241
    assert "4 delivery attempts" in marked[0][1]
    q.ack.assert_awaited_once_with("1-0")


@pytest.mark.asyncio
async def test_fresh_entry_not_quarantined(monkeypatch):
    # A normal fresh delivery (count=1) must NOT trip the guard — it passes into
    # normal processing. Route it through the light paperless_refile branch so we
    # can prove the guard passed without invoking the full KB pipeline.
    import workers.document_processor_worker as w
    from services.task_queue import StreamEntry

    monkeypatch.setattr(w.settings, "worker_max_deliveries", 3)
    marked = []

    async def _fake_mark(doc_id, err):
        marked.append(doc_id)

    monkeypatch.setattr(w, "_mark_document_failed", _fake_mark)
    refiled = []

    async def _fake_refile(doc_id, user_id=None):
        refiled.append(doc_id)

    monkeypatch.setattr(
        "services.paperless_filing_hook.refile_document_paperless", _fake_refile
    )
    q = MagicMock()
    q.ack = AsyncMock()
    entry = StreamEntry(
        entry_id="2-0",
        params={"document_id": 7, "trigger": "paperless_refile"},
        delivery_count=1,
    )

    await w._process_entry(MagicMock(), q, entry)

    assert marked == []  # guard did not quarantine a fresh entry
    assert refiled == [7]  # proceeded into normal (refile) processing
    q.ack.assert_awaited_once_with("2-0")
