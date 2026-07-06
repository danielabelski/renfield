"""Phase-3 review bucket → promote-to-enrolled — real PG.

Under controlled recognition, an unknown voice is captured as a
``SpeakerCandidate`` (real voice-server ONNX embedding). The admin promotes a
coherent set of candidates to a named enrolled speaker; the same cohesion +
count gates as a fresh enrollment apply, and the promoted candidates are
consumed. docs/design/speaker-enrollment-redesign.md.
"""
from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from models.database import Role, Speaker, SpeakerCandidate, SpeakerEmbedding, User
from services.speaker_enrollment_service import promote_candidates
from services.speaker_service import get_speaker_service

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

_DIM = 192
_seq = 0


def _axis(i: int) -> np.ndarray:
    v = np.zeros(_DIM, dtype=np.float32)
    v[i] = 1.0
    return v


@pytest.fixture
def maker(pg_async_engine):
    return async_sessionmaker(pg_async_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    from utils.config import settings
    monkeypatch.setattr(settings, "speaker_enroll_min_samples", 3)
    monkeypatch.setattr(settings, "speaker_enroll_min_cohesion", 0.5)


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


async def _seed_candidates(session, vectors):
    svc = get_speaker_service()
    ids = []
    for v in vectors:
        c = SpeakerCandidate(embedding=svc.embedding_to_base64(v), best_score=0.4)
        session.add(c)
        await session.flush()
        ids.append(c.id)
    return ids


class TestPromoteAccept:
    async def test_coherent_candidates_promote_and_consume(self, maker):
        async with maker() as s:
            uid = await _mk_user(s)
            ids = await _seed_candidates(s, [_axis(0)] * 3)  # cohesion ~1.0
            await s.commit()
        async with maker() as s:
            res = await promote_candidates(s, candidate_ids=ids, name="Anna", user_id=uid)
        assert res["ok"] is True
        assert res["accepted"] == 3
        async with maker() as s:
            sp = (await s.execute(
                select(Speaker).where(Speaker.id == res["speaker_id"]))).scalar_one()
            assert sp.enrolled is True and sp.name == "Anna"
            embs = (await s.execute(select(SpeakerEmbedding)
                    .where(SpeakerEmbedding.speaker_id == sp.id))).scalars().all()
            assert len(embs) == 3
            # promoted candidates consumed
            left = (await s.execute(
                select(SpeakerCandidate).where(SpeakerCandidate.id.in_(ids)))).scalars().all()
            assert left == []
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            assert u.speaker_id == sp.id


class TestPromoteReject:
    async def test_too_few_candidates(self, maker):
        async with maker() as s:
            ids = await _seed_candidates(s, [_axis(0)] * 2)
            await s.commit()
        async with maker() as s:
            res = await promote_candidates(s, candidate_ids=ids, name="Anna")
        assert res["ok"] is False and "candidates" in res["reason"]
        async with maker() as s:
            # nothing enrolled, candidates untouched
            assert (await s.execute(select(Speaker))).scalars().all() == []
            assert len((await s.execute(
                select(SpeakerCandidate))).scalars().all()) == 2

    async def test_incoherent_candidates_rejected(self, maker):
        async with maker() as s:
            ids = await _seed_candidates(s, [_axis(0), _axis(1), _axis(2)])  # orthogonal
            await s.commit()
        async with maker() as s:
            res = await promote_candidates(s, candidate_ids=ids, name="Anna")
        assert res["ok"] is False and "cohere" in res["reason"]
        async with maker() as s:
            assert (await s.execute(select(Speaker))).scalars().all() == []
            # candidates NOT consumed on a gate failure
            assert len((await s.execute(
                select(SpeakerCandidate))).scalars().all()) == 3

    async def test_invalid_user_id_rejected(self, maker):
        async with maker() as s:
            ids = await _seed_candidates(s, [_axis(0)] * 3)
            await s.commit()
        async with maker() as s:
            res = await promote_candidates(
                s, candidate_ids=ids, name="Anna", user_id=999999)
        assert res["ok"] is False and "not found" in res["reason"]
