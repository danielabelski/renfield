"""Speaker deletion + merge — FK ON DELETE actions (migration pc20260705).

The three FKs referencing ``speakers.id`` were created NO ACTION, so deleting a
speaker that had ever been used (embeddings, a conversation, or a user link)
raised a foreign-key violation → the DELETE route 500'd. Now:

- ``speaker_embeddings.speaker_id`` CASCADE (embeddings die with the speaker),
- ``conversations.speaker_id`` / ``users.speaker_id`` SET NULL on a plain delete,
- ``/merge`` REASSIGNS conversations + the user link to the target (preserve).

Real Postgres with real commits (``pg_async_engine`` drops the schema on
teardown). Seeding, the route call, and the assertions each use a SEPARATE
session — mirroring prod (FastAPI ``get_db`` hands every request a fresh
session), and so the route never sees identity-map objects left over from
seeding (which would otherwise confound the Core bulk DML).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.routes.speakers import (
    MergeSpeakersRequest,
    delete_speaker,
    merge_speakers,
)
from models.database import Conversation, Role, Speaker, SpeakerEmbedding, User

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

_seq = 0


async def _mk_speaker(session, name="Unbekannter Sprecher #1", *, embeddings=2):
    global _seq
    _seq += 1
    sp = Speaker(name=name, alias=f"sp_{_seq}")
    session.add(sp)
    await session.flush()
    for _ in range(embeddings):
        session.add(SpeakerEmbedding(speaker_id=sp.id, embedding="AAAA"))
    await session.flush()
    return sp.id


async def _mk_user(session, *, speaker_id=None):
    global _seq
    _seq += 1
    role = Role(name=f"r_{_seq}")
    session.add(role)
    await session.flush()
    u = User(username=f"u_{_seq}", email=f"u{_seq}@ex.test", password_hash="x",
             role_id=role.id, is_active=True, speaker_id=speaker_id)
    session.add(u)
    await session.flush()
    return u.id


async def _mk_conv(session, *, speaker_id):
    global _seq
    _seq += 1
    c = Conversation(session_id=f"sess_{_seq}", speaker_id=speaker_id)
    session.add(c)
    await session.flush()
    return c.id


async def _count_embeddings(maker, speaker_id) -> int:
    async with maker() as s:
        return len((await s.execute(
            select(SpeakerEmbedding).where(SpeakerEmbedding.speaker_id == speaker_id)
        )).scalars().all())


@pytest.fixture
def maker(pg_async_engine):
    return async_sessionmaker(pg_async_engine, expire_on_commit=False)


class TestDeleteSpeaker:
    async def test_delete_cascades_embeddings_and_nulls_refs(self, maker):
        async with maker() as s:
            sid = await _mk_speaker(s, embeddings=3)
            cid = await _mk_conv(s, speaker_id=sid)
            uid = await _mk_user(s, speaker_id=sid)
            await s.commit()
        async with maker() as s:  # fresh session, like a real request
            await delete_speaker(sid, db=s, _user=None)

        async with maker() as s:
            assert (await s.execute(
                select(Speaker).where(Speaker.id == sid))).scalar_one_or_none() is None
            assert await _count_embeddings(maker, sid) == 0  # CASCADE
            assert (await s.execute(
                select(Conversation).where(Conversation.id == cid))).scalar_one().speaker_id is None
            assert (await s.execute(
                select(User).where(User.id == uid))).scalar_one().speaker_id is None

    async def test_delete_unused_speaker_ok(self, maker):
        async with maker() as s:
            sid = await _mk_speaker(s, embeddings=0)
            await s.commit()
        async with maker() as s:
            await delete_speaker(sid, db=s, _user=None)
        async with maker() as s:
            assert (await s.execute(
                select(Speaker).where(Speaker.id == sid))).scalar_one_or_none() is None


class TestMergeSpeakers:
    async def test_merge_reassigns_embeddings_conversation_and_user(self, maker):
        async with maker() as s:
            src = await _mk_speaker(s, name="Unbekannter Sprecher #A", embeddings=2)
            tgt = await _mk_speaker(s, name="Eduard", embeddings=1)
            cid = await _mk_conv(s, speaker_id=src)
            uid = await _mk_user(s, speaker_id=src)
            await s.commit()
        async with maker() as s:
            await merge_speakers(
                MergeSpeakersRequest(source_speaker_id=src, target_speaker_id=tgt),
                db=s, _user=None,
            )

        assert await _count_embeddings(maker, tgt) == 3  # 2 moved + 1 original
        async with maker() as s:
            assert (await s.execute(
                select(Speaker).where(Speaker.id == src))).scalar_one_or_none() is None
            assert (await s.execute(
                select(Conversation).where(Conversation.id == cid))).scalar_one().speaker_id == tgt
            assert (await s.execute(
                select(User).where(User.id == uid))).scalar_one().speaker_id == tgt

    async def test_merge_keeps_target_link_when_both_users_linked(self, maker):
        async with maker() as s:
            src = await _mk_speaker(s, embeddings=1)
            tgt = await _mk_speaker(s, embeddings=1)
            src_uid = await _mk_user(s, speaker_id=src)
            tgt_uid = await _mk_user(s, speaker_id=tgt)
            await s.commit()
        async with maker() as s:
            resp = await merge_speakers(
                MergeSpeakersRequest(source_speaker_id=src, target_speaker_id=tgt),
                db=s, _user=None,
            )
        # the severed source-user link is surfaced in the response
        assert "link was removed" in resp.message
        async with maker() as s:
            assert (await s.execute(
                select(User).where(User.id == tgt_uid))).scalar_one().speaker_id == tgt
            assert (await s.execute(
                select(User).where(User.id == src_uid))).scalar_one().speaker_id is None
