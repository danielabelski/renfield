"""Tests for the worker-side Paperless filing hook (post_document_ingest).

Files a folder/email-ingest doc into Paperless from the worker, reusing the
worker's Docling field_text (best OCR, no re-run) and transporting it into
Paperless content. These verify the gating + delegation at the seams (session,
worker MCP client, leg), mocking the heavy pieces.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

import services.paperless_filing_hook as pfh
from models.database import PAPERLESS_STATE_DONE, PAPERLESS_STATE_FAILED, PAPERLESS_STATE_PENDING


def _doc(**kw):
    d = MagicMock()
    d.id = kw.get("id", 1)
    d.filename = kw.get("filename", "invoice.pdf")
    d.file_path = kw.get("file_path", "/uploads/x.pdf")
    d.paperless_state = kw.get("paperless_state", PAPERLESS_STATE_PENDING)
    return d


def _patch_session(monkeypatch, doc):
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = doc
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _cm():
        yield session

    monkeypatch.setattr(pfh, "AsyncSessionLocal", _cm)
    return session


def _patch_mgr(monkeypatch, mgr):
    monkeypatch.setattr(
        "services.paperless_worker_client.get_paperless_mcp_manager",
        AsyncMock(return_value=mgr),
    )


def _patch_leg(monkeypatch):
    leg = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "services.folder_ingest_paperless.make_paperless_leg",
        MagicMock(return_value=leg),
    )
    return leg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skips_non_pending_doc(monkeypatch):
    # interactive KB upload (paperless_state NULL) → never filed
    _patch_session(monkeypatch, _doc(paperless_state=None))
    leg = _patch_leg(monkeypatch)
    mgr_getter = AsyncMock()
    monkeypatch.setattr(
        "services.paperless_worker_client.get_paperless_mcp_manager", mgr_getter
    )
    await pfh.paperless_filing_post_ingest_hook(document_id=1, field_text="text")
    mgr_getter.assert_not_awaited()  # bailed before touching the MCP
    leg.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_files_pending_doc_with_field_text(monkeypatch):
    _patch_session(monkeypatch, _doc(id=7))
    _patch_mgr(monkeypatch, MagicMock())
    leg = _patch_leg(monkeypatch)
    with patch("builtins.open", mock_open(read_data=b"%PDF-1.4")):
        await pfh.paperless_filing_post_ingest_hook(
            document_id=7, field_text="high quality OCR text", lang="de"
        )
    leg.assert_awaited_once()
    # field_text is passed as the leg's doc_text (5th positional arg) so it's
    # reused for extraction AND transported into Paperless content.
    assert leg.await_args.args[4] == "high quality OCR text"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_unavailable_leaves_pending(monkeypatch):
    doc = _doc(id=8)
    session = _patch_session(monkeypatch, doc)
    _patch_mgr(monkeypatch, None)  # Paperless down
    leg = _patch_leg(monkeypatch)
    await pfh.paperless_filing_post_ingest_hook(document_id=8, field_text="t")
    leg.assert_not_awaited()
    session.commit.assert_not_awaited()  # untouched → stays pending
    assert doc.paperless_state == PAPERLESS_STATE_PENDING


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_bytes_marks_failed(monkeypatch):
    doc = _doc(id=9)
    _patch_session(monkeypatch, doc)
    _patch_mgr(monkeypatch, MagicMock())
    leg = _patch_leg(monkeypatch)
    with patch("builtins.open", side_effect=OSError("gone")):
        await pfh.paperless_filing_post_ingest_hook(document_id=9, field_text="t")
    leg.assert_not_awaited()
    assert doc.paperless_state == PAPERLESS_STATE_FAILED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refile_uses_fresh_docling_no_field_text(monkeypatch):
    # The retry path passes doc_text=None → the leg re-OCRs via Docling.
    doc = _doc(id=10)
    _patch_session(monkeypatch, doc)
    _patch_mgr(monkeypatch, MagicMock())
    leg = _patch_leg(monkeypatch)
    with patch("builtins.open", mock_open(read_data=b"%PDF")):
        await pfh.refile_document_paperless(10)
    leg.assert_awaited_once()
    assert leg.await_args.args[4] is None  # doc_text None → fresh extract_from_file
