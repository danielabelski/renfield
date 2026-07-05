"""Controlled speaker enrollment (Phase 1) — real PG.

The service computes embeddings via the voice-server ONNX model (mocked here) and
enrolls a named, user-linked reference profile only when the samples pass the
duration + count + cohesion gates. docs/design/speaker-enrollment-redesign.md.
"""
from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from unittest.mock import AsyncMock, patch

from models.database import Role, Speaker, SpeakerEmbedding, User
from services.speaker_enrollment_service import enroll_speaker_controlled

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

_DIM = 192
_seq = 0


def _axis(i: int) -> list[float]:
    v = np.zeros(_DIM, dtype=np.float32)
    v[i] = 1.0
    return v.tolist()


def _stt(embedding, duration=2.5):
    return {"text": "x", "language": "de", "speaker_embedding": embedding,
            "audio_duration_s": duration}


def _samples(n):
    return [(b"audio", f"s{i}.wav") for i in range(n)]


@pytest.fixture
def maker(pg_async_engine):
    return async_sessionmaker(pg_async_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    from utils.config import settings
    monkeypatch.setattr(settings, "voice_server_url", "http://vs")
    monkeypatch.setattr(settings, "speaker_enroll_min_samples", 3)
    monkeypatch.setattr(settings, "speaker_enroll_min_duration_s", 2.0)
    monkeypatch.setattr(settings, "speaker_enroll_min_cohesion", 0.5)
    monkeypatch.setattr(
        "services.speaker_enrollment_service._service_token", lambda: "tok")


async def _mk_user(session):
    global _seq
    _seq += 1
    role = Role(name=f"r_{_seq}")
    session.add(role)
    await session.flush()
    u = User(username=f"u_{_seq}", email=f"u{_seq}@ex.test", password_hash="x",
             role_id=role.id, is_active=True)
    session.add(u)
    await session.flush()
    return u.id


def _mock_stt(returns):
    return patch("services.voice_server_client.stt", AsyncMock(side_effect=returns))


class TestEnrollAccept:
    async def test_coherent_samples_enroll_and_link_user(self, maker):
        async with maker() as s:
            uid = await _mk_user(s)
            await s.commit()
        # 3 near-identical embeddings → cohesion ~1.0
        returns = [_stt(_axis(0)) for _ in range(3)]
        with _mock_stt(returns):
            async with maker() as s:
                res = await enroll_speaker_controlled(
                    s, name="Anna", samples=_samples(3), user_id=uid)
        assert res["ok"] is True
        assert res["accepted"] == 3
        async with maker() as s:
            sp = (await s.execute(
                select(Speaker).where(Speaker.id == res["speaker_id"]))).scalar_one()
            assert sp.enrolled is True and sp.name == "Anna"
            n = (await s.execute(select(SpeakerEmbedding)
                 .where(SpeakerEmbedding.speaker_id == sp.id))).scalars().all()
            assert len(n) == 3
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            assert u.speaker_id == sp.id

    async def test_reenroll_replaces_embeddings(self, maker):
        async with maker() as s:
            sp = Speaker(name="Old", alias="old", enrolled=True)
            s.add(sp)
            await s.flush()
            s.add(SpeakerEmbedding(speaker_id=sp.id, embedding="AAAA"))
            await s.commit()
            sid = sp.id
        returns = [_stt(_axis(0)) for _ in range(3)]
        with _mock_stt(returns):
            async with maker() as s:
                res = await enroll_speaker_controlled(
                    s, name="Anna", samples=_samples(3), speaker_id=sid)
        assert res["ok"] is True and res["speaker_id"] == sid
        async with maker() as s:
            embs = (await s.execute(select(SpeakerEmbedding)
                    .where(SpeakerEmbedding.speaker_id == sid))).scalars().all()
            assert len(embs) == 3  # old one replaced, 3 new
            assert (await s.execute(
                select(Speaker).where(Speaker.id == sid))).scalar_one().name == "Anna"


class TestEnrollReject:
    async def test_too_few_usable_samples(self, maker):
        returns = [_stt(_axis(0)) for _ in range(2)]
        with _mock_stt(returns):
            async with maker() as s:
                res = await enroll_speaker_controlled(s, name="Anna", samples=_samples(2))
        assert res["ok"] is False and "usable sample" in res["reason"]
        async with maker() as s:
            assert (await s.execute(select(Speaker))).scalars().all() == []

    async def test_too_short_samples_rejected(self, maker):
        returns = [_stt(_axis(0), duration=1.0) for _ in range(3)]  # < 2.0s
        with _mock_stt(returns):
            async with maker() as s:
                res = await enroll_speaker_controlled(s, name="Anna", samples=_samples(3))
        assert res["ok"] is False
        assert res["accepted"] == 0
        assert any("too short" in r for r in res["sample_reasons"])

    async def test_incoherent_samples_rejected(self, maker):
        # three MUTUALLY ORTHOGONAL embeddings → cohesion ~0 < 0.5
        returns = [_stt(_axis(0)), _stt(_axis(1)), _stt(_axis(2))]
        with _mock_stt(returns):
            async with maker() as s:
                res = await enroll_speaker_controlled(s, name="Anna", samples=_samples(3))
        assert res["ok"] is False and "cohere" in res["reason"]
        assert res["cohesion"] < 0.5
        async with maker() as s:
            assert (await s.execute(select(Speaker))).scalars().all() == []

    async def test_no_voice_server_configured(self, maker, monkeypatch):
        from utils.config import settings
        monkeypatch.setattr(settings, "voice_server_url", "")
        async with maker() as s:
            res = await enroll_speaker_controlled(s, name="Anna", samples=_samples(3))
        assert res["ok"] is False and "voice-server" in res["reason"]

    async def test_invalid_user_id_rejected(self, maker):
        # nonexistent user → reject up front (never a "successful" unlinked enroll)
        async with maker() as s:
            res = await enroll_speaker_controlled(
                s, name="Anna", samples=_samples(3), user_id=999999)
        assert res["ok"] is False and "not found" in res["reason"]

    async def test_nonfinite_embedding_rejected(self, maker):
        # NaN embeddings must not slip past the cohesion gate (NaN < x is False)
        nan = [float("nan")] * _DIM
        returns = [_stt(nan) for _ in range(3)]
        with _mock_stt(returns):
            async with maker() as s:
                res = await enroll_speaker_controlled(s, name="Anna", samples=_samples(3))
        assert res["ok"] is False
        assert res["accepted"] == 0
        assert any("non-finite" in r for r in res["sample_reasons"])
        async with maker() as s:
            assert (await s.execute(select(Speaker))).scalars().all() == []


class TestUserLinkMove:
    async def test_link_moves_off_prior_speaker(self, maker):
        async with maker() as s:
            uid = await _mk_user(s)
            old = Speaker(name="OldLink", alias="oldlink", enrolled=True)
            s.add(old)
            await s.flush()
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            u.speaker_id = old.id
            await s.commit()
            old_id = old.id
        returns = [_stt(_axis(0)) for _ in range(3)]
        with _mock_stt(returns):
            async with maker() as s:
                res = await enroll_speaker_controlled(
                    s, name="Anna", samples=_samples(3), user_id=uid)
        async with maker() as s:
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            assert u.speaker_id == res["speaker_id"] != old_id

    async def test_reenroll_reports_displaced_user(self, maker):
        # speaker linked to user A; re-enroll it for user B → A displaced + reported
        async with maker() as s:
            a = await _mk_user(s)
            b = await _mk_user(s)
            sp = Speaker(name="Shared", alias="shared", enrolled=True)
            s.add(sp)
            await s.flush()
            (await s.execute(select(User).where(User.id == a))).scalar_one().speaker_id = sp.id
            await s.commit()
            sid = sp.id
        returns = [_stt(_axis(0)) for _ in range(3)]
        with _mock_stt(returns):
            async with maker() as s:
                res = await enroll_speaker_controlled(
                    s, name="Bob", samples=_samples(3), user_id=b, speaker_id=sid)
        assert res["ok"] is True
        assert res.get("displaced_user_ids") == [a]
        async with maker() as s:
            assert (await s.execute(
                select(User).where(User.id == a))).scalar_one().speaker_id is None
            assert (await s.execute(
                select(User).where(User.id == b))).scalar_one().speaker_id == sid
