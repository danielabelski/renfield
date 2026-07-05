"""Unit tests for the kiosk-owned data helpers (api/websocket/kiosk_data.py).

These moved here from the deleted test_command_center_routes.py when the admin
Command Center was decommissioned: the REST endpoints are gone, but the shared
read logic that now backs the /ws/kiosk snapshot + deltas is exercised directly
(no HTTP) — content-free role-activity extraction and the process-cached weather
reading.
"""
from __future__ import annotations

from datetime import datetime, timedelta, UTC

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import Message
import api.websocket.kiosk_data as kiosk_data
from api.websocket.kiosk_data import (
    compute_kiosk_weather,
    recent_role_activity_entries,
)


async def _make_message(
    db_session: AsyncSession, *, role: str = "assistant",
    metadata: dict | None = None, age_minutes: int = 0,
) -> Message:
    msg = Message(
        role=role,
        content="",
        timestamp=datetime.now(UTC).replace(tzinfo=None)
        - timedelta(minutes=age_minutes),
        message_metadata=metadata,
    )
    db_session.add(msg)
    await db_session.commit()
    return msg


# ============================================================ ROLE ACTIVITY
@pytest.mark.asyncio
class TestRoleActivity:
    async def test_newest_first_and_content_free(self, db_session: AsyncSession):
        await _make_message(
            db_session,
            metadata={"agent_role": "knowledge", "action_success": True},
            age_minutes=10,
        )
        await _make_message(
            db_session,
            metadata={"agent_role": "smart_home", "action_success": False},
            age_minutes=1,
        )
        entries = await recent_role_activity_entries(db_session)
        assert len(entries) == 2
        # newest first
        assert entries[0].role == "smart_home"
        assert entries[0].ok is False
        assert entries[1].role == "knowledge"
        assert entries[1].ok is True
        # content-free by construction: only role / at / ok
        assert set(entries[0].model_dump().keys()) == {"role", "at", "ok"}

    async def test_skips_roleless_and_user_messages(self, db_session: AsyncSession):
        await _make_message(db_session, metadata={"agent_role": None})  # shortcut
        await _make_message(db_session, metadata=None)  # legacy row
        await _make_message(
            db_session, role="user", metadata={"agent_role": "general"},
        )  # user msg never counts
        await _make_message(db_session, metadata={"agent_role": "media"})
        entries = await recent_role_activity_entries(db_session)
        assert [e.role for e in entries] == ["media"]
        assert entries[0].ok is None  # no action_success → None

    async def test_limit_caps_the_result(self, db_session: AsyncSession):
        for i in range(5):
            await _make_message(
                db_session, metadata={"agent_role": "general"}, age_minutes=i,
            )
        entries = await recent_role_activity_entries(db_session, limit=3)
        assert len(entries) == 3


# ============================================================ WEATHER
@pytest.mark.asyncio
class TestKioskWeather:
    async def test_none_when_disabled(self, monkeypatch):
        monkeypatch.setattr(kiosk_data.settings, "weather_enabled", False)
        monkeypatch.setattr(kiosk_data.settings, "kiosk_weather_location", "Testville")
        assert await compute_kiosk_weather(mcp_manager=object()) is None

    async def test_none_when_no_location(self, monkeypatch):
        monkeypatch.setattr(kiosk_data.settings, "weather_enabled", True)
        monkeypatch.setattr(kiosk_data.settings, "kiosk_weather_location", "  ")
        assert await compute_kiosk_weather(mcp_manager=object()) is None

    async def test_reading_from_mcp(self, monkeypatch):
        # Reset the process-local cache so no prior reading leaks across tests.
        kiosk_data._weather_cache["value"] = None
        kiosk_data._weather_cache["at"] = 0.0
        monkeypatch.setattr(kiosk_data.settings, "weather_enabled", True)
        monkeypatch.setattr(kiosk_data.settings, "kiosk_weather_location", "Testville")

        class _MCP:
            async def execute_tool(self, name, args):
                assert name == "mcp.weather.get_weather"
                return {"success": True, "data": {
                    "location": {"name": "Testville"},
                    "current": {"temperature": 18.6, "weather_code": 3,
                                "weather_description": "Bewölkt"},
                    "daily": [{"temp_max": 20, "temp_min": 11}],
                }}

        try:
            weather = await compute_kiosk_weather(_MCP())
        finally:
            kiosk_data._weather_cache["value"] = None
            kiosk_data._weather_cache["at"] = 0.0
        assert weather is not None
        assert weather.location == "Testville"
        assert weather.temp == 18.6
        assert weather.code == 3
        assert weather.condition == "Bewölkt"
        assert weather.high == 20
        assert weather.low == 11
