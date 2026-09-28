"""
Unit tests for `internal.list_my_memories` — the threshold-free memory
enumeration tool that backs broad "what do you know about me" queries.

Guarantees: it lists the asker's OWN memories (user_id resolved, never from
LLM params), formats them as `[category] content` lines, reports total +
truncation, filters/clamps inputs, and degrades cleanly on empty / no-user.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.memory_list_tool import (
    _VALID_CATEGORIES,
    MEMORY_LIST_MAX_LIMIT,
    list_my_memories,
)

pytestmark = pytest.mark.unit


def test_valid_categories_match_canonical_set():
    """Guard against drift: the allowlist must equal the canonical category set.

    A hardcoded subset previously omitted 'procedural', silently turning a
    `category=procedural` filter into an unfiltered all-memories read.
    """
    from models.database import MEMORY_CATEGORIES

    assert _VALID_CATEGORIES == set(MEMORY_CATEGORIES)


def _patches(memories, total, owner=1):
    """Patch the service + session the tool imports at call time."""
    svc = MagicMock()
    svc._resolve_owner_user_id = AsyncMock(return_value=owner)
    svc.list_for_user = AsyncMock(return_value=memories)
    svc.get_count = AsyncMock(return_value=total)

    @asynccontextmanager
    async def _session(*_a, **_k):
        yield MagicMock()

    return (
        svc,
        patch("services.database.AsyncSessionLocal", lambda *a, **k: _session()),
        patch(
            "services.conversation_memory_service.ConversationMemoryService",
            return_value=svc,
        ),
    )


@pytest.mark.asyncio
async def test_lists_memories_as_category_lines():
    mems = [
        {"category": "preference", "content": "Mein Lieblingsobst ist Mango"},
        {"category": "fact", "content": "Wohnt in Korschenbroich"},
    ]
    svc, p_sess, p_svc = _patches(mems, total=2)
    with p_sess, p_svc:
        out = await list_my_memories({}, user_id=7)

    assert out["success"] is True
    assert out["data"]["results_count"] == 2
    assert out["data"]["truncated"] is False
    assert "[preference] Mein Lieblingsobst ist Mango" in out["data"]["context"]
    assert "[fact] Wohnt in Korschenbroich" in out["data"]["context"]
    # user_id is resolved from the injected context, not params.
    svc._resolve_owner_user_id.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_truncation_reported_when_total_exceeds_returned():
    mems = [{"category": "fact", "content": f"f{i}"} for i in range(3)]
    _svc, p_sess, p_svc = _patches(mems, total=42)
    with p_sess, p_svc:
        out = await list_my_memories({"limit": "3"}, user_id=1)
    assert out["data"]["truncated"] is True
    assert out["data"]["total"] == 42
    assert "of 42" in out["message"]


@pytest.mark.asyncio
async def test_empty_store_returns_empty_result():
    _svc, p_sess, p_svc = _patches([], total=0)
    with p_sess, p_svc:
        out = await list_my_memories({}, user_id=1)
    assert out["success"] is True
    assert out["empty_result"] is True
    assert out["data"]["results_count"] == 0


@pytest.mark.asyncio
async def test_invalid_category_is_ignored_not_errored():
    svc, p_sess, p_svc = _patches([], total=0)
    with p_sess, p_svc:
        await list_my_memories({"category": "bogus"}, user_id=1)
    # invalid category dropped → list_for_user called with category=None
    assert svc.list_for_user.await_args.kwargs["category"] is None


@pytest.mark.asyncio
async def test_procedural_category_reaches_service():
    """A valid non-default category ('procedural') must reach the service."""
    svc, p_sess, p_svc = _patches([], total=0)
    with p_sess, p_svc:
        await list_my_memories({"category": "procedural"}, user_id=1)
    assert svc.list_for_user.await_args.kwargs["category"] == "procedural"


@pytest.mark.asyncio
async def test_limit_clamped_to_max():
    svc, p_sess, p_svc = _patches([], total=0)
    with p_sess, p_svc:
        await list_my_memories({"limit": "9999"}, user_id=1)
    assert svc.list_for_user.await_args.kwargs["limit"] == MEMORY_LIST_MAX_LIMIT


@pytest.mark.asyncio
async def test_no_user_context_degrades_cleanly():
    svc, p_sess, p_svc = _patches([], total=0, owner=None)
    with p_sess, p_svc:
        out = await list_my_memories({}, user_id=None)
    assert out["success"] is True
    assert out["empty_result"] is True
    svc.list_for_user.assert_not_awaited()
