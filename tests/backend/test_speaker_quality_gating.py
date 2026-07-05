"""Phase-0 speaker quality gating (docs/design/speaker-enrollment-redesign.md).

`speaker_quality_gating_enabled` (dark by default) stops the noisy-turn pollution
loop: too-short turns don't auto-enrol or reinforce, and continuous-learning only
appends on a STRONG match. Flag off = legacy behaviour. The resolver only uses the
pure-numpy service methods (embedding (de)serialise + cosine), so no ECAPA model
loads here.
"""
from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import select

from models.database import Speaker, SpeakerEmbedding
from services.speaker_resolver import resolve_speaker_from_embedding
from services.speaker_service import get_speaker_service

pytestmark = [pytest.mark.database, pytest.mark.asyncio]

_DIM = 192


def _vec(*, axis0: float, axis1: float = 0.0) -> np.ndarray:
    """A 192-dim embedding with mass on axes 0/1 → controllable cosine."""
    v = np.zeros(_DIM, dtype=np.float32)
    v[0] = axis0
    v[1] = axis1
    return v


# Reference speaker embedding (unit along axis 0). Cosine with a query is then
# just the query's normalized axis-0 component.
_REF = _vec(axis0=1.0)
_STRONG = _vec(axis0=1.0)                     # cosine 1.0  → strong match
_WEAK = _vec(axis0=0.30, axis1=0.954)         # cosine 0.30 → matches (>=0.25), weak (<0.45)
_NOMATCH = _vec(axis0=0.0, axis1=1.0)         # cosine 0.0  → no match


async def _seed_speaker(db, name="Anna") -> int:
    svc = get_speaker_service()
    sp = Speaker(name=name, alias=name.lower())
    db.add(sp)
    await db.flush()
    db.add(SpeakerEmbedding(speaker_id=sp.id, embedding=svc.embedding_to_base64(_REF)))
    await db.commit()
    return sp.id


async def _emb_count(db, sid) -> int:
    return len((await db.execute(
        select(SpeakerEmbedding).where(SpeakerEmbedding.speaker_id == sid)
    )).scalars().all())


async def _unknown_count(db) -> int:
    return len((await db.execute(
        select(Speaker).where(Speaker.name.like("Unbekannter%"))
    )).scalars().all())


@pytest.fixture
def _on(monkeypatch):
    from utils.config import settings
    monkeypatch.setattr(settings, "speaker_quality_gating_enabled", True)
    monkeypatch.setattr(settings, "speaker_recognition_min_duration_s", 1.0)
    monkeypatch.setattr(settings, "speaker_continuous_learning_min_confidence", 0.45)
    monkeypatch.setattr(settings, "speaker_auto_enroll", True)
    monkeypatch.setattr(settings, "speaker_continuous_learning", True)


@pytest.fixture
def _off(monkeypatch):
    from utils.config import settings
    monkeypatch.setattr(settings, "speaker_quality_gating_enabled", False)
    monkeypatch.setattr(settings, "speaker_auto_enroll", True)
    monkeypatch.setattr(settings, "speaker_continuous_learning", True)


class TestGatingOffLegacy:
    async def test_no_match_auto_enrolls(self, db_session, _off):
        await _seed_speaker(db_session)
        info = await resolve_speaker_from_embedding(db_session, _NOMATCH)
        assert info["is_new_speaker"] is True
        assert await _unknown_count(db_session) == 1

    async def test_weak_match_still_appends(self, db_session, _off):
        sid = await _seed_speaker(db_session)
        info = await resolve_speaker_from_embedding(db_session, _WEAK)
        assert info["speaker_id"] == sid           # matched (>=0.25)
        assert await _emb_count(db_session, sid) == 2  # legacy CL appends any match


class TestGatingOn:
    async def test_too_short_no_match_does_not_auto_enroll(self, db_session, _on):
        await _seed_speaker(db_session)
        info = await resolve_speaker_from_embedding(
            db_session, _NOMATCH, audio_duration_s=0.5,
        )
        assert info["speaker_id"] is None           # unknown, not enrolled
        assert await _unknown_count(db_session) == 0

    async def test_too_short_match_identifies_but_no_append(self, db_session, _on):
        sid = await _seed_speaker(db_session)
        info = await resolve_speaker_from_embedding(
            db_session, _STRONG, audio_duration_s=0.5,
        )
        assert info["speaker_id"] == sid            # still identified (read-only)
        assert await _emb_count(db_session, sid) == 1   # NOT reinforced

    async def test_weak_match_long_enough_no_append(self, db_session, _on):
        sid = await _seed_speaker(db_session)
        info = await resolve_speaker_from_embedding(
            db_session, _WEAK, audio_duration_s=2.0,
        )
        assert info["speaker_id"] == sid            # matched (0.30 >= 0.25)
        assert await _emb_count(db_session, sid) == 1   # but 0.30 < 0.45 → no reinforce

    async def test_strong_match_long_enough_appends(self, db_session, _on):
        sid = await _seed_speaker(db_session)
        info = await resolve_speaker_from_embedding(
            db_session, _STRONG, audio_duration_s=2.0,
        )
        assert info["speaker_id"] == sid
        assert await _emb_count(db_session, sid) == 2   # strong + long → reinforced

    async def test_long_enough_no_match_still_auto_enrolls(self, db_session, _on):
        # A clean, long-enough turn from a genuinely new speaker SHOULD still enrol.
        await _seed_speaker(db_session)
        info = await resolve_speaker_from_embedding(
            db_session, _NOMATCH, audio_duration_s=2.0,
        )
        assert info["is_new_speaker"] is True
        assert await _unknown_count(db_session) == 1

    async def test_unknown_duration_does_not_gate(self, db_session, _on):
        # audio_duration_s=None (e.g. the WS path) → duration gate can't fire;
        # a strong match still reinforces (confidence gate still applies).
        sid = await _seed_speaker(db_session)
        info = await resolve_speaker_from_embedding(db_session, _STRONG)
        assert info["speaker_id"] == sid
        assert await _emb_count(db_session, sid) == 2
