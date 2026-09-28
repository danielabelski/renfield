"""Tests for the D9 subject-attribution fix (Structured Memory Phase 2, T7).

The visible cross-person conflation bug lives in the FLAT memory path: facts
about different people share an embedding neighborhood and get conflated. The
fix gives each memory a structured ``subject`` (stored as subject_name) and
surfaces it in the injected context so the LLM never merges subjects.

Three layers:
- _format_memory_line: the context-line renderer (D9 surfacing). [unit]
- ConversationMemoryService.save(subject=...): stores subject_name. [sqlite]
- MemoryRetrieval.retrieve: carries subject_name in the result dict. [real PG]
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from models.database import EMBEDDING_DIMENSION, ConversationMemory, Role, User
from services.conversation_memory_service import ConversationMemoryService


class TestFormatMemoryLine:
    @pytest.mark.unit
    def test_subject_surfaced_in_tag(self):
        from api.websocket.chat_handler import _format_memory_line
        line = _format_memory_line({"category": "fact", "content": "mag Jazz", "subject_name": "Alice"})
        assert line == "- [FACT · Alice] mag Jazz"

    @pytest.mark.unit
    def test_no_subject_falls_back_to_category(self):
        from api.websocket.chat_handler import _format_memory_line
        line = _format_memory_line({"category": "preference", "content": "kein Kaffee", "subject_name": None})
        assert line == "- [PREFERENCE] kein Kaffee"

    @pytest.mark.unit
    def test_two_subjects_never_collapse(self):
        from api.websocket.chat_handler import _format_memory_line
        a = _format_memory_line({"category": "fact", "content": "X", "subject_name": "Alice"})
        b = _format_memory_line({"category": "fact", "content": "Y", "subject_name": "Carol"})
        # the structural disambiguation the bug was missing
        assert "Alice" in a and "Carol" in b and a != b


class TestSaveStoresSubject:
    pytestmark = pytest.mark.asyncio

    async def test_save_sets_subject_name(self, db_session, monkeypatch):
        svc = ConversationMemoryService(db_session)
        # No embedding -> skip dedup/pgvector (keeps this on the sqlite shim).
        monkeypatch.setattr(svc, "_get_embedding", AsyncMock(return_value=None))

        mem = await svc.save(content="mag Jazz", category="fact", user_id=None, subject="Alice")
        assert mem is not None
        assert mem.subject_name == "Alice"

    async def test_save_without_subject_is_null(self, db_session, monkeypatch):
        svc = ConversationMemoryService(db_session)
        monkeypatch.setattr(svc, "_get_embedding", AsyncMock(return_value=None))

        mem = await svc.save(content="Es regnet", category="context", user_id=None)
        assert mem is not None
        assert mem.subject_name is None


class TestRetrieveCarriesSubject:
    pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

    async def test_subject_name_in_retrieved_dict(self, pg_db_session, monkeypatch):
        from services.memory_retrieval import MemoryRetrieval

        role = Role(name="t7_role")
        pg_db_session.add(role)
        await pg_db_session.flush()
        owner = User(username="t7_owner", email="t7@ex.test", password_hash="x",
                     role_id=role.id, is_active=True)
        pg_db_session.add(owner)
        await pg_db_session.flush()

        vec = [0.0] * EMBEDDING_DIMENSION
        vec[5] = 1.0
        mem = ConversationMemory(
            user_id=owner.id, content="mag Jazz", category="fact",
            importance=0.9, circle_tier=0, embedding=vec, subject_name="Alice",
        )
        pg_db_session.add(mem)
        await pg_db_session.flush()

        retr = MemoryRetrieval(pg_db_session)
        monkeypatch.setattr(retr, "_get_embedding", AsyncMock(return_value=vec))

        hits = await retr.retrieve("was mag Alice", user_id=owner.id, threshold=0.1)
        assert any(h["id"] == mem.id and h.get("subject_name") == "Alice" for h in hits)
