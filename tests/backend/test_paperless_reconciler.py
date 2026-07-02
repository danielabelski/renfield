"""Tests for the async Paperless reconciler (Design Z).

The reconciler files documents the ingest bridge stamped
``paperless_state='pending'`` into Paperless out of band, so the ingest push
never awaits the external Paperless round-trip on a pooled DB connection. These
unit-test the tick control flow at its seams (session, doc query, leg, byte
read) — the real leg is covered by test_folder_ingest_paperless; the wiring by
the .159 E2E.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.paperless_reconciler as pr
from models.database import (
    DOC_STATUS_COMPLETED,
    DOC_STATUS_PROCESSING,
    PAPERLESS_STATE_DONE,
    PAPERLESS_STATE_FAILED,
    PAPERLESS_STATE_PENDING,
)


def _doc(**kw):
    d = MagicMock()
    d.id = kw.get("id", 1)
    d.status = kw.get("status", DOC_STATUS_COMPLETED)
    d.paperless_state = kw.get("paperless_state", PAPERLESS_STATE_PENDING)
    d.filename = kw.get("filename", "invoice.pdf")
    d.file_path = kw.get("file_path", "/uploads/x.pdf")
    return d


def _fake_session(*, ids=None, doc=None):
    """An AsyncSessionLocal() stand-in. The enum tick reads scalars().all();
    the per-doc tick reads scalar_one_or_none()."""
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = ids or []
    result.scalar_one_or_none.return_value = doc
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _cm():
        yield session

    return _cm, session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tick_no_pending_is_noop(monkeypatch):
    cm, _ = _fake_session(ids=[])
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    leg = MagicMock()
    monkeypatch.setattr(pr, "make_paperless_leg", MagicMock(return_value=leg))
    await pr.reconcile_pending_paperless(MagicMock())
    leg.assert_not_called()  # nothing pending → no leg built


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tick_null_mcp_manager_skips(monkeypatch):
    # A tick before the MCP manager is wired must not touch the DB.
    cm, session = _fake_session(ids=[1])
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    await pr.reconcile_pending_paperless(None)
    session.execute.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_files_pending_doc_via_leg(monkeypatch):
    doc = _doc(id=5)
    # enum session returns [5]; the per-doc session returns the doc.
    cm, _ = _fake_session(ids=[5], doc=doc)
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    leg = AsyncMock(return_value=True)
    make = MagicMock(return_value=leg)
    monkeypatch.setattr(pr, "make_paperless_leg", make)

    with patch("aiofiles.open") as aopen:
        handle = AsyncMock()
        handle.read = AsyncMock(return_value=b"%PDF-1.4")
        aopen.return_value.__aenter__ = AsyncMock(return_value=handle)
        aopen.return_value.__aexit__ = AsyncMock(return_value=False)
        await pr.reconcile_pending_paperless(MagicMock())

    leg.assert_awaited_once()
    # user_id=None (owner-scoped extractor examples are a refinement, not needed).
    assert make.call_args.kwargs["user_id"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skips_doc_no_longer_pending(monkeypatch):
    # A concurrent tick / re-push settled it between the enum SELECT and the
    # per-doc re-check → skip without building a leg.
    doc = _doc(id=6, paperless_state=PAPERLESS_STATE_DONE)
    cm, _ = _fake_session(ids=[6], doc=doc)
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    leg = MagicMock()
    monkeypatch.setattr(pr, "make_paperless_leg", MagicMock(return_value=leg))
    await pr.reconcile_pending_paperless(MagicMock())
    leg.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skips_doc_not_completed(monkeypatch):
    doc = _doc(id=7, status=DOC_STATUS_PROCESSING)
    cm, _ = _fake_session(ids=[7], doc=doc)
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    leg = MagicMock()
    monkeypatch.setattr(pr, "make_paperless_leg", MagicMock(return_value=leg))
    await pr.reconcile_pending_paperless(MagicMock())
    leg.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_bytes_marks_failed(monkeypatch):
    # The recovery copy vanished → the doc can never be filed, so mark it terminal
    # FAILED (settled) instead of leaving it 'pending' forever — otherwise it would
    # sit at the low end of the id-ordered batch and starve newer pending docs.
    doc = _doc(id=8)
    cm, _ = _fake_session(ids=[8], doc=doc)
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    leg = MagicMock()
    monkeypatch.setattr(pr, "make_paperless_leg", MagicMock(return_value=leg))
    with patch("aiofiles.open", side_effect=OSError("gone")):
        await pr.reconcile_pending_paperless(MagicMock())
    leg.assert_not_called()
    assert doc.paperless_state == PAPERLESS_STATE_FAILED  # settled, leaves the set


@pytest.mark.unit
@pytest.mark.asyncio
async def test_leg_error_swallowed(monkeypatch):
    # A leg raising must not crash the tick (one bad doc can't stall the rest).
    doc = _doc(id=9)
    cm, _ = _fake_session(ids=[9], doc=doc)
    monkeypatch.setattr(pr, "AsyncSessionLocal", cm)
    leg = AsyncMock(side_effect=RuntimeError("paperless down"))
    monkeypatch.setattr(pr, "make_paperless_leg", MagicMock(return_value=leg))
    with patch("aiofiles.open") as aopen:
        handle = AsyncMock()
        handle.read = AsyncMock(return_value=b"%PDF")
        aopen.return_value.__aenter__ = AsyncMock(return_value=handle)
        aopen.return_value.__aexit__ = AsyncMock(return_value=False)
        # must not raise
        await pr.reconcile_pending_paperless(MagicMock())
    leg.assert_awaited_once()
