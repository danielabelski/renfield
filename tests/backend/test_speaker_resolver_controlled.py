"""Phase-3 controlled recognition (docs/design/speaker-enrollment-redesign.md).

`speaker_controlled_enrollment_enabled` (dark by default) flips the resolver from
"auto-enrol every unknown voice" to a disciplined mode:
  - identify against ENROLLED reference profiles ONLY,
  - a match must clear the threshold AND beat the runner-up by a margin,
  - reference profiles are IMMUTABLE (no passive reinforcement),
  - a quality-passing unknown goes to the review bucket (SpeakerCandidate), never
    an auto-enrolled "Unbekannter Sprecher"; a too-short one is dropped.
The resolver only uses pure-numpy service methods, so no ECAPA model loads here.
"""
from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import select

from models.database import Speaker, SpeakerCandidate, SpeakerEmbedding
from services.speaker_resolver import resolve_speaker_from_embedding
from services.speaker_service import get_speaker_service

pytestmark = [pytest.mark.database, pytest.mark.asyncio]

_DIM = 192


def _vec(*coords: tuple[int, float]) -> np.ndarray:
    v = np.zeros(_DIM, dtype=np.float32)
    for i, val in coords:
        v[i] = val
    return v


_A = _vec((0, 1.0))                       # enrolled profile A (axis 0)
_B = _vec((1, 1.0))                       # enrolled profile B (axis 1)
_MATCH_A = _vec((0, 1.0))                 # cosine 1.0 with A, 0.0 with B → clean match
_NEAR_TIE = _vec((0, 1.0), (1, 0.97))     # ~equal cosine to A and B → margin fails
_UNKNOWN = _vec((2, 1.0))                 # orthogonal to both → miss


async def _seed(db, name, vec, *, enrolled=True) -> int:
    svc = get_speaker_service()
    sp = Speaker(name=name, alias=name.lower(), enrolled=enrolled)
    db.add(sp)
    await db.flush()
    db.add(SpeakerEmbedding(speaker_id=sp.id, embedding=svc.embedding_to_base64(vec)))
    await db.commit()
    return sp.id


async def _emb_count(db, sid) -> int:
    return len((await db.execute(
        select(SpeakerEmbedding).where(SpeakerEmbedding.speaker_id == sid)
    )).scalars().all())


async def _candidates(db):
    return (await db.execute(select(SpeakerCandidate))).scalars().all()


async def _unknown_count(db) -> int:
    return len((await db.execute(
        select(Speaker).where(Speaker.name.like("Unbekannter%"))
    )).scalars().all())


@pytest.fixture
def _on(monkeypatch):
    from utils.config import settings
    monkeypatch.setattr(settings, "speaker_controlled_enrollment_enabled", True)
    monkeypatch.setattr(settings, "speaker_recognition_threshold", 0.25)
    monkeypatch.setattr(settings, "speaker_match_min_margin", 0.1)
    monkeypatch.setattr(settings, "speaker_recognition_min_duration_s", 1.0)
    monkeypatch.setattr(settings, "speaker_review_bucket_cap", 200)
    # These MUST be ignored under controlled mode:
    monkeypatch.setattr(settings, "speaker_auto_enroll", True)
    monkeypatch.setattr(settings, "speaker_continuous_learning", True)


class TestControlledIdentify:
    async def test_clean_match_identifies_without_reinforcing(self, db_session, _on):
        sid = await _seed(db_session, "Anna", _A)
        info = await resolve_speaker_from_embedding(
            db_session, _MATCH_A, audio_duration_s=3.0)
        assert info["speaker_id"] == sid
        assert await _emb_count(db_session, sid) == 1   # reference profile immutable
        assert await _candidates(db_session) == []      # matched → no review candidate

    async def test_unenrolled_profile_is_ignored(self, db_session, _on):
        # An un-enrolled (legacy "Unbekannter") profile must NOT be matched against.
        await _seed(db_session, "Unbekannter Sprecher #1", _A, enrolled=False)
        info = await resolve_speaker_from_embedding(
            db_session, _MATCH_A, audio_duration_s=3.0)
        assert info["speaker_id"] is None               # no enrolled profile → miss
        assert len(await _candidates(db_session)) == 1   # routed to review bucket

    async def test_near_tie_fails_margin(self, db_session, _on):
        await _seed(db_session, "Anna", _A)
        await _seed(db_session, "Bob", _B)
        info = await resolve_speaker_from_embedding(
            db_session, _NEAR_TIE, audio_duration_s=3.0)
        assert info["speaker_id"] is None               # ambiguous → no identification
        assert len(await _candidates(db_session)) == 1


class TestControlledMiss:
    async def test_miss_captures_candidate_not_auto_enroll(self, db_session, _on):
        await _seed(db_session, "Anna", _A)
        info = await resolve_speaker_from_embedding(
            db_session, _UNKNOWN, audio_duration_s=3.0)
        assert info["speaker_id"] is None
        assert info["is_new_speaker"] is False
        assert await _unknown_count(db_session) == 0     # NO auto-enroll
        cands = await _candidates(db_session)
        assert len(cands) == 1
        assert cands[0].audio_duration_s == 3.0

    async def test_too_short_miss_dropped(self, db_session, _on):
        await _seed(db_session, "Anna", _A)
        info = await resolve_speaker_from_embedding(
            db_session, _UNKNOWN, audio_duration_s=0.5)  # < 1.0s
        assert info["speaker_id"] is None
        assert await _candidates(db_session) == []       # too short → not captured
        assert await _unknown_count(db_session) == 0

    async def test_nonfinite_embedding_rejected(self, db_session, _on):
        # A NaN wire embedding must not land a NaN best_score in the bucket
        # (which would serialize as invalid JSON from GET /candidates).
        await _seed(db_session, "Anna", _A)
        nan = np.full(_DIM, np.nan, dtype=np.float32)
        info = await resolve_speaker_from_embedding(
            db_session, nan, audio_duration_s=3.0)
        assert info["speaker_id"] is None
        assert await _candidates(db_session) == []

    async def test_review_bucket_capped(self, db_session, _on, monkeypatch):
        from utils.config import settings
        monkeypatch.setattr(settings, "speaker_review_bucket_cap", 3)
        await _seed(db_session, "Anna", _A)
        for _ in range(5):
            await resolve_speaker_from_embedding(
                db_session, _UNKNOWN, audio_duration_s=3.0)
        assert len(await _candidates(db_session)) == 3   # capped, oldest evicted
