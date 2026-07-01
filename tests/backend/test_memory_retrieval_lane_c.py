"""
Tests for Lane C two-stage retrieval with recency-aware rerank.

Validates the SQL-routing contract on MemoryRetrieval.retrieve():
- ranker="default" emits the legacy single-stage ORDER BY
- ranker="recency_aware" emits a two-stage CTE that pulls
  memory_retrieval_recall_k candidates by similarity, then reranks
  with a recency factor on last_accessed_at

We mock the embedding client and the database session, so this is
SQL-shape regression rather than live-postgres behavior. The
end-to-end signal lives in the 150-turn corpus run with --use-v2
on .159, where the cross_session_stale metric is the empirical gauge.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.memory_retrieval import MemoryRetrieval


def _fake_embedding(_text: str) -> list[float]:
    return [0.1] * 1024


class _Capture:
    """Captures the (sql, params) pair passed to db.execute()."""
    def __init__(self):
        self.sql_text: str | None = None
        self.params: dict | None = None

    async def execute(self, sql, params=None):
        # SQLAlchemy passes a TextClause; .text contains the raw SQL string.
        self.sql_text = getattr(sql, "text", str(sql))
        self.params = params or {}
        result = MagicMock()
        result.fetchall = MagicMock(return_value=[])
        return result

    async def commit(self):
        pass


@pytest.fixture
def captured_db():
    return _Capture()


@pytest.fixture
def mr(captured_db):
    instance = MemoryRetrieval(captured_db)
    instance._get_embedding = AsyncMock(side_effect=_fake_embedding)
    return instance


class TestRetrieveRankerRouting:
    """ranker="default" vs ranker="recency_aware" emit different SQL."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_default_ranker_uses_single_stage_sql(self, mr, captured_db):
        with patch("services.memory_retrieval.settings") as s:
            s.auth_enabled = False
            # Phase 3c Memory→KG bridge is dark by default; without this the
            # patched MagicMock settings make it truthy, so retrieve() issues a
            # trailing kg_entities resolve query and _Capture (last-write) holds
            # THAT instead of the ranker query under test. Pin it off.
            s.memory_kg_bridge_enabled = False
            s.memory_retrieval_limit = 5
            s.memory_retrieval_threshold = 0.0
            await mr.retrieve("hello", user_id=None)

        assert "WITH candidates" not in captured_db.sql_text
        assert "ORDER BY (1 - (embedding <=>" in captured_db.sql_text
        assert "recency_weight" not in captured_db.params
        assert "recall_k" not in captured_db.params

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_recency_aware_uses_two_stage_cte(self, mr, captured_db):
        with patch("services.memory_retrieval.settings") as s:
            s.auth_enabled = False
            # Phase 3c Memory→KG bridge is dark by default; without this the
            # patched MagicMock settings make it truthy, so retrieve() issues a
            # trailing kg_entities resolve query and _Capture (last-write) holds
            # THAT instead of the ranker query under test. Pin it off.
            s.memory_kg_bridge_enabled = False
            s.memory_retrieval_limit = 5
            s.memory_retrieval_threshold = 0.0
            s.memory_retrieval_recall_k = 50
            s.memory_retrieval_recency_weight = 0.2
            s.memory_retrieval_recency_half_life_days = 30
            await mr.retrieve("hello", user_id=None, ranker="recency_aware")

        assert "WITH candidates AS" in captured_db.sql_text
        # Stage-1: HNSW recall ordered by raw cosine distance
        assert "ORDER BY (embedding <=> CAST(:embedding AS vector)) ASC" in captured_db.sql_text
        # Stage-2: rerank with recency factor on last_accessed_at
        assert "last_accessed_at" in captured_db.sql_text
        assert "recency_weight" in captured_db.params
        assert "recall_k" in captured_db.params
        assert "half_life_days" in captured_db.params
        assert captured_db.params["recency_weight"] == 0.2
        assert captured_db.params["recall_k"] == 50

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_recall_k_never_drops_below_limit(self, mr, captured_db):
        """If a caller passes limit > recall_k, recall_k must widen.
        Otherwise Stage-1 would return fewer rows than the LIMIT clause asks for."""
        with patch("services.memory_retrieval.settings") as s:
            s.auth_enabled = False
            # Phase 3c Memory→KG bridge is dark by default; without this the
            # patched MagicMock settings make it truthy, so retrieve() issues a
            # trailing kg_entities resolve query and _Capture (last-write) holds
            # THAT instead of the ranker query under test. Pin it off.
            s.memory_kg_bridge_enabled = False
            s.memory_retrieval_limit = 5
            s.memory_retrieval_threshold = 0.0
            s.memory_retrieval_recall_k = 10  # smaller than limit
            s.memory_retrieval_recency_weight = 0.2
            s.memory_retrieval_recency_half_life_days = 30
            await mr.retrieve("hello", user_id=None, limit=20, ranker="recency_aware")

        assert captured_db.params["recall_k"] >= 20

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_recency_weight_zero_collapses_to_pure_similarity_rerank(self, mr, captured_db):
        """recency_weight=0 still emits the two-stage SQL, but the recency
        multiplier collapses to 1.0 — equivalent to a pure
        similarity*importance*confidence rerank over the bigger recall window."""
        with patch("services.memory_retrieval.settings") as s:
            s.auth_enabled = False
            # Phase 3c Memory→KG bridge is dark by default; without this the
            # patched MagicMock settings make it truthy, so retrieve() issues a
            # trailing kg_entities resolve query and _Capture (last-write) holds
            # THAT instead of the ranker query under test. Pin it off.
            s.memory_kg_bridge_enabled = False
            s.memory_retrieval_limit = 5
            s.memory_retrieval_threshold = 0.0
            s.memory_retrieval_recall_k = 50
            s.memory_retrieval_recency_weight = 0.0
            s.memory_retrieval_recency_half_life_days = 30
            await mr.retrieve("hello", user_id=None, ranker="recency_aware")

        assert captured_db.params["recency_weight"] == 0.0
        # Two-stage SQL still emitted; recency_weight=0 neutralizes the
        # multiplier (1.0 + 0.0 * EXP(...) == 1.0).
        assert "WITH candidates AS" in captured_db.sql_text

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_unknown_ranker_falls_back_to_default(self, mr, captured_db):
        """ranker="garbage" must NOT crash and must NOT silently emit the
        two-stage SQL. Strict equality on the string spelling."""
        with patch("services.memory_retrieval.settings") as s:
            s.auth_enabled = False
            # Phase 3c Memory→KG bridge is dark by default; without this the
            # patched MagicMock settings make it truthy, so retrieve() issues a
            # trailing kg_entities resolve query and _Capture (last-write) holds
            # THAT instead of the ranker query under test. Pin it off.
            s.memory_kg_bridge_enabled = False
            s.memory_retrieval_limit = 5
            s.memory_retrieval_threshold = 0.0
            await mr.retrieve("hello", user_id=None, ranker="garbage")

        # Falls through to default branch (no two-stage CTE).
        assert "WITH candidates AS" not in captured_db.sql_text
