"""
Unit tests for the interactive Paperless confirm card.

Covers the structured (clickable-picker) confirm path that replaces the
free-text "1:n, 2:x" mini-syntax:

    - ``_build_confirm_payload`` (chat_upload_tool): produces per-field
      options with a 1-based idx over the FULL proposals list, surfacing
      only fields that need a decision, with safe defaults.
    - ``_decisions_from_structured`` (paperless_commit_tool): maps the
      card's {idx, action, value} entries back onto resolution-anchored
      decisions, dropping out-of-range / unknown-action entries.
    - ``paperless_commit_upload`` structured branch: ``params["decisions"]``
      routes straight to the commit (bypassing the text parser);
      ``params["abort"]`` aborts.

Pure-unit, heavy mocking. Mirrors test_paperless_commit_tool.py helpers.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.chat_upload_tool import _build_confirm_payload
from services.paperless_commit_tool import (
    _decisions_from_structured,
    paperless_commit_upload,
)

# ===========================================================================
# _build_confirm_payload
# ===========================================================================

_SUMMARY = {
    "title": "Stromrechnung",
    "correspondent": "Telekom",
    "document_type": "Rechnung",
    "tags": ["wohnung"],
    "storage_path": "/x",
    "created_date": None,
    "ignored_extra": "drop me",
}

# Mixed bag: fuzzy w/ matches, no-match (needs decision), an EXACT hit that
# must be skipped from the card WITHOUT shifting later indices, and a tag.
_RESOLUTIONS = [
    {"field": "correspondent", "extracted_value": "Telekom",
     "near_matches": ["Telekom AG", "Telekom GmbH"], "status": "fuzzy"},
    {"field": "document_type", "extracted_value": "Rechnung",
     "near_matches": [], "status": "none"},
    {"field": "storage_path", "extracted_value": "/x",
     "near_matches": [], "status": "exact"},   # resolved → not surfaced
    {"field": "tag", "extracted_value": "wohnung",
     "near_matches": ["Wohnung"], "status": "fuzzy"},
]


class TestBuildConfirmPayload:
    @pytest.mark.unit
    def test_summary_carries_only_known_keys(self):
        payload = _build_confirm_payload(_SUMMARY, _RESOLUTIONS)
        assert set(payload["summary"]) == {
            "title", "correspondent", "document_type",
            "tags", "storage_path", "created_date",
        }
        assert payload["summary"]["correspondent"] == "Telekom"
        assert "ignored_extra" not in payload["summary"]

    @pytest.mark.unit
    def test_exact_field_skipped_but_index_preserved(self):
        payload = _build_confirm_payload(_SUMMARY, _RESOLUTIONS)
        idxs = [f["idx"] for f in payload["fields"]]
        # correspondent=1, document_type=2, (storage_path=3 EXACT → hidden), tag=4
        assert idxs == [1, 2, 4]
        assert [f["field"] for f in payload["fields"]] == [
            "correspondent", "document_type", "tag",
        ]

    @pytest.mark.unit
    def test_options_order_and_actions(self):
        payload = _build_confirm_payload(_SUMMARY, _RESOLUTIONS)
        corr = payload["fields"][0]
        actions = [(o["action"], o["value"]) for o in corr["options"]]
        assert actions == [
            ("use", "Telekom AG"),
            ("use", "Telekom GmbH"),
            ("create", "Telekom"),
            ("skip", None),
        ]

    @pytest.mark.unit
    def test_default_picks_top_match_when_present(self):
        payload = _build_confirm_payload(_SUMMARY, _RESOLUTIONS)
        assert payload["fields"][0]["default"] == {"action": "use", "value": "Telekom AG"}

    @pytest.mark.unit
    def test_default_skips_when_no_near_match(self):
        # document_type has no near_matches → default must be skip, never create.
        payload = _build_confirm_payload(_SUMMARY, _RESOLUTIONS)
        doctype = payload["fields"][1]
        assert doctype["default"] == {"action": "skip", "value": None}

    @pytest.mark.unit
    def test_empty_resolutions_yields_no_fields(self):
        payload = _build_confirm_payload(_SUMMARY, [])
        assert payload["fields"] == []


# ===========================================================================
# _decisions_from_structured
# ===========================================================================


class TestDecisionsFromStructured:
    @pytest.mark.unit
    def test_maps_idx_to_resolution(self):
        out = _decisions_from_structured(
            [{"idx": 1, "action": "use", "value": "Telekom AG"}],
            _RESOLUTIONS,
        )
        assert len(out) == 1
        assert out[0]["resolution"] is _RESOLUTIONS[0]
        assert out[0]["action"] == "use"
        assert out[0]["value"] == "Telekom AG"

    @pytest.mark.unit
    def test_create_backfills_extracted_value(self):
        out = _decisions_from_structured(
            [{"idx": 2, "action": "create", "value": None}],
            _RESOLUTIONS,
        )
        assert out[0]["action"] == "create"
        assert out[0]["value"] == "Rechnung"  # backfilled from extracted_value

    @pytest.mark.unit
    def test_skip_forces_empty_value(self):
        out = _decisions_from_structured(
            [{"idx": 4, "action": "skip", "value": "leftover"}],
            _RESOLUTIONS,
        )
        assert out[0]["action"] == "skip"
        assert out[0]["value"] == ""

    @pytest.mark.unit
    def test_non_string_value_coerced_to_empty(self):
        # A malformed payload value (dict/None) must not survive — it would
        # crash _commit_approved's .strip(). "use" with junk → "".
        out = _decisions_from_structured(
            [{"idx": 1, "action": "use", "value": {"x": 1}}],
            _RESOLUTIONS,
        )
        assert out[0]["value"] == ""

    @pytest.mark.unit
    def test_bool_idx_rejected(self):
        # bool is an int subclass — must NOT alias idx 1.
        out = _decisions_from_structured(
            [{"idx": True, "action": "use", "value": "x"}],
            _RESOLUTIONS,
        )
        assert out == []

    @pytest.mark.unit
    def test_out_of_range_and_unknown_action_dropped(self):
        out = _decisions_from_structured(
            [
                {"idx": 99, "action": "use", "value": "x"},   # out of range
                {"idx": 1, "action": "bogus"},                # unknown action
                {"idx": 0, "action": "use"},                  # idx < 1
                "not-a-dict",                                  # junk
                {"idx": 1, "action": "use", "value": "Telekom AG"},  # valid
            ],
            _RESOLUTIONS,
        )
        assert len(out) == 1
        assert out[0]["value"] == "Telekom AG"


# ===========================================================================
# paperless_commit_upload — structured branch
# ===========================================================================


def _make_pending(*, confirm_token, attachment_id, llm_output, post_fuzzy,
                  proposals, edit_rounds=0):
    return SimpleNamespace(
        confirm_token=confirm_token,
        attachment_id=attachment_id,
        session_id="s",
        user_id=1,
        llm_output=llm_output,
        post_fuzzy_output=post_fuzzy,
        proposals=proposals,
        edit_rounds=edit_rounds,
    )


def _make_session_factory(*, pending, upload=None):
    def _factory():
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.add = MagicMock()
        session.commit = AsyncMock()

        def _execute(query):
            result = MagicMock()
            result.scalar_one_or_none = MagicMock(return_value=pending)
            result.rowcount = 1
            return result

        session.execute = AsyncMock(side_effect=lambda q: _execute(q))

        async def _get(model, pk):
            if model.__name__ == "ChatUpload":
                return upload
            if model.__name__ == "PaperlessPendingConfirm":
                return pending
            return None

        session.get = _get
        return session

    return _factory


class TestStructuredCommitBranch:
    @pytest.fixture(autouse=True)
    def _no_background(self, monkeypatch):
        monkeypatch.setattr(
            "services.paperless_commit_tool._spawn_bg", lambda coro: coro.close()
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_structured_decisions_fire_upload(self, tmp_path):
        file = tmp_path / "rechnung.pdf"
        file.write_bytes(b"%PDF-1.4 " + b"x" * 200)

        pending = _make_pending(
            confirm_token="tok-s",
            attachment_id=7,
            llm_output={"title": "T"},
            post_fuzzy={
                "title": "T", "correspondent": "Stadtwerke",
                "document_type": "Rechnung", "tags": ["wohnung"],
                "storage_path": "/x", "created_date": None,
            },
            proposals=[],
        )
        upload = MagicMock(id=7, filename="rechnung.pdf", file_path=str(file))

        mcp = MagicMock()
        mcp.execute_tool = AsyncMock(return_value={
            "success": True,
            "message": json.dumps({"task_id": "t-9", "document_id": 1}),
        })

        with patch(
            "services.database.AsyncSessionLocal",
            _make_session_factory(pending=pending, upload=upload),
        ):
            result = await paperless_commit_upload(
                {"confirm_token": "tok-s", "decisions": []},
                mcp_manager=mcp, session_id="s", user_id=1,
            )

        assert result["success"] is True
        assert result["action_taken"] is True
        # Upload fired (structured path bypassed the text parser entirely).
        upload_call = mcp.execute_tool.await_args_list[0]
        assert upload_call.args[0] == "mcp.paperless.upload_document"
        assert upload_call.args[1]["wait_for_consume"] is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_abort_flag_deletes_pending_without_uploading(self):
        pending = _make_pending(
            confirm_token="tok-x", attachment_id=7,
            llm_output={}, post_fuzzy={"title": "T"}, proposals=[],
        )
        mcp = MagicMock()
        mcp.execute_tool = AsyncMock()

        with patch(
            "services.database.AsyncSessionLocal",
            _make_session_factory(pending=pending),
        ):
            result = await paperless_commit_upload(
                {"confirm_token": "tok-x", "abort": True},
                mcp_manager=mcp, session_id="s", user_id=1,
            )

        assert result["success"] is True
        assert result["data"]["aborted"] is True
        mcp.execute_tool.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_non_list_decisions_errors(self):
        pending = _make_pending(
            confirm_token="tok-y", attachment_id=7,
            llm_output={}, post_fuzzy={"title": "T"}, proposals=[],
        )
        mcp = MagicMock()
        mcp.execute_tool = AsyncMock()

        with patch(
            "services.database.AsyncSessionLocal",
            _make_session_factory(pending=pending),
        ):
            result = await paperless_commit_upload(
                {"confirm_token": "tok-y", "decisions": "1:neu"},
                mcp_manager=mcp, session_id="s", user_id=1,
            )

        assert result["success"] is False
        assert "decisions" in result["message"].lower()
        mcp.execute_tool.assert_not_awaited()
