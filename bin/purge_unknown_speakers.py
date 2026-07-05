#!/usr/bin/env python3
"""Purge ambient auto-enrolled "Unbekannter Sprecher" profiles.

Speaker recognition was polluting itself (see docs/design/speaker-enrollment-
redesign.md): far-field auto-enroll minted dozens of noise-polluted, unusable
"Unbekannter Sprecher #N" rows. Before re-enrolling the household with the
controlled flow, wipe those. NEVER touches an ``enrolled=True`` reference
profile — the delete is scoped to ``enrolled=False`` (and, by default, only rows
named "Unbekannter …"). Embeddings CASCADE-delete; conversation/user links are
SET NULL (migration pc20260705), so the delete is safe.

Dry-run by default; pass --commit to actually delete.

    python bin/purge_unknown_speakers.py                 # dry-run
    python bin/purge_unknown_speakers.py --commit
    python bin/purge_unknown_speakers.py --all-unenrolled --commit  # also unenrolled named
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "src" / "backend"
sys.path.insert(0, str(_BACKEND))

from sqlalchemy import delete, func, select  # noqa: E402

from models.database import Speaker, SpeakerEmbedding, User  # noqa: E402
from services.database import AsyncSessionLocal  # noqa: E402


async def main(commit: bool, all_unenrolled: bool) -> int:
    async with AsyncSessionLocal() as db:
        # NEVER delete an enrolled reference profile, and NEVER delete a speaker a
        # user is linked to (a renamed-but-not-formally-enrolled speaker could be
        # a real, curated identity — deleting it would sever that user's voice
        # link via the FK SET NULL). The name filter is the default guard.
        linked = select(User.speaker_id).where(User.speaker_id.isnot(None))
        conds = [Speaker.enrolled.is_(False), Speaker.id.notin_(linked)]
        if not all_unenrolled:
            conds.append(Speaker.name.like("Unbekannter%"))

        rows = (await db.execute(
            select(Speaker.id, Speaker.name).where(*conds).order_by(Speaker.id)
        )).all()
        if not rows:
            print("Nothing to purge (no matching unenrolled speakers).")
            return 0

        ids = [r.id for r in rows]
        emb = (await db.execute(
            select(func.count()).select_from(SpeakerEmbedding)
            .where(SpeakerEmbedding.speaker_id.in_(ids))
        )).scalar() or 0
        enrolled_kept = (await db.execute(
            select(func.count()).select_from(Speaker).where(Speaker.enrolled.is_(True))
        )).scalar() or 0

        print(f"{'DELETING' if commit else 'WOULD DELETE'} {len(ids)} speaker(s) "
              f"+ {emb} embedding(s). Keeping {enrolled_kept} enrolled profile(s).")
        for r in rows[:40]:
            print(f"  - #{r.id} {r.name}")
        if len(rows) > 40:
            print(f"  … and {len(rows) - 40} more")

        if not commit:
            print("\nDry-run. Re-run with --commit to delete.")
            return 0

        await db.execute(
            delete(Speaker).where(Speaker.id.in_(ids))
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        print(f"\n✅ Deleted {len(ids)} speaker(s).")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Purge unenrolled 'Unbekannter' speakers.")
    ap.add_argument("--commit", action="store_true", help="actually delete (default: dry-run)")
    ap.add_argument("--all-unenrolled", action="store_true",
                    help="also delete unenrolled speakers NOT named 'Unbekannter'")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.commit, args.all_unenrolled)))
