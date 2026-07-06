#!/usr/bin/env python3
"""Recommend the speaker recognition threshold + margin from REAL enrolled data.

Phase 3b of the speaker-enrollment redesign (docs/design/speaker-enrollment-
redesign.md). Controlled recognition (`speaker_controlled_enrollment_enabled`)
identifies a turn against enrolled reference profiles and requires the best match
to clear `speaker_recognition_threshold` AND beat the runner-up by
`speaker_match_min_margin`. Those two numbers should come from the household's
OWN enrolled voices, not a guess — this script measures the separation and
recommends them.

It reads the ENROLLED speakers' stored embeddings (voice-server ONNX space, the
same the resolver matches against), L2-normalizes each (mirroring the resolver's
`quality_active` centroid), and computes two distributions:

  * same-speaker: pairwise cosine WITHIN each enrolled profile (how tightly one
    person's own samples cohere),
  * different-speaker: cosine between each PAIR of enrolled profile centroids
    (how far apart two people are).

A clean separation means `same` sits well above `different`; the recommended
threshold falls in the gap and the margin is a fraction of it. Needs >= 2
enrolled speakers to measure the different-speaker side; with one it reports the
same-speaker cohesion only and cannot recommend a threshold.

Read-only. Run:

    python bin/calibrate_speaker_threshold.py
    python bin/calibrate_speaker_threshold.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np

_BACKEND = Path(__file__).resolve().parent.parent / "src" / "backend"
sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from models.database import Speaker  # noqa: E402
from services.database import AsyncSessionLocal  # noqa: E402
from services.speaker_service import get_speaker_service  # noqa: E402


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(_l2(a), _l2(b)))


def _pct(xs: list[float], p: float) -> float:
    return float(np.percentile(xs, p)) if xs else float("nan")


async def _collect() -> dict:
    svc = get_speaker_service()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Speaker).where(Speaker.enrolled.is_(True))
            .options(selectinload(Speaker.embeddings))
        )).scalars().all()

    profiles: list[dict] = []
    for sp in rows:
        embs = [
            np.asarray(svc.embedding_from_base64(e.embedding), dtype=np.float32)
            for e in sp.embeddings
        ]
        embs = [e for e in embs if e.size and np.all(np.isfinite(e))]
        if not embs:
            continue
        centroid = np.mean([_l2(e) for e in embs], axis=0)
        profiles.append({"id": sp.id, "name": sp.name, "embs": embs, "centroid": centroid})
    return {"profiles": profiles}


def _analyze(profiles: list[dict]) -> dict:
    # same-speaker: pairwise cosine within each profile (>=2 samples)
    same: list[float] = []
    for p in profiles:
        same.extend(_cos(a, b) for a, b in combinations(p["embs"], 2))
    # different-speaker: centroid-vs-centroid across profile pairs
    diff: list[float] = []
    for a, b in combinations(profiles, 2):
        diff.append(_cos(a["centroid"], b["centroid"]))

    out: dict = {
        "enrolled_speakers": len(profiles),
        "names": [p["name"] for p in profiles],
        "same_speaker": _summary(same),
        "different_speaker": _summary(diff),
    }

    # Recommendation needs both sides.
    if len(profiles) < 2 or not same:
        out["recommendation"] = None
        out["note"] = (
            "Need >= 2 enrolled speakers (each with >= 2 samples) to recommend a "
            "threshold. Enroll the household via /speakers, then re-run."
        )
        return out

    same_low = _pct(same, 5)     # a genuine match should clear this
    diff_high = _pct(diff, 95)   # an impostor should stay below this
    gap = same_low - diff_high
    if gap > 0:
        threshold = round(diff_high + gap / 2, 3)          # middle of the clean gap
        margin = round(min(gap / 2, 0.15), 3)              # half the gap, capped
        verdict = "clean separation"
    else:
        # overlap: profiles aren't separable at these samples — fall back to a
        # conservative point above the different-speaker mass and flag it.
        threshold = round(diff_high, 3)
        margin = 0.1
        verdict = (
            "OVERLAP — enrolled profiles are not cleanly separable (same-speaker "
            "p5 <= different-speaker p95). Re-enroll noisy profiles or add samples; "
            "the recommended threshold is a floor, expect misses/confusions."
        )
    out["recommendation"] = {
        "speaker_recognition_threshold": threshold,
        "speaker_match_min_margin": margin,
        "separation_gap": round(gap, 3),
        "verdict": verdict,
    }
    return out


def _summary(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "min": round(float(np.min(xs)), 3),
        "p5": round(_pct(xs, 5), 3),
        "mean": round(float(np.mean(xs)), 3),
        "p95": round(_pct(xs, 95), 3),
        "max": round(float(np.max(xs)), 3),
    }


def _print(report: dict) -> None:
    print("🎚️  Speaker threshold calibration (enrolled profiles)\n")
    print(f"  enrolled speakers: {report['enrolled_speakers']} "
          f"({', '.join(report['names']) or '—'})")
    ss, ds = report["same_speaker"], report["different_speaker"]
    print(f"  same-speaker cosine (within a profile): {_fmt(ss)}")
    print(f"  different-speaker cosine (across profiles): {_fmt(ds)}")
    rec = report.get("recommendation")
    if not rec:
        print(f"\n  ⚠️  {report.get('note')}")
        return
    print(f"\n  → {rec['verdict']} (gap = {rec['separation_gap']})")
    print(f"  → recommended  speaker_recognition_threshold = {rec['speaker_recognition_threshold']}")
    print(f"  → recommended  speaker_match_min_margin      = {rec['speaker_match_min_margin']}")


def _fmt(s: dict) -> str:
    if not s.get("n"):
        return "n=0 (need >= 2 samples / speakers)"
    return (f"n={s['n']}  min={s['min']}  p5={s['p5']}  mean={s['mean']}  "
            f"p95={s['p95']}  max={s['max']}")


async def main(as_json: bool) -> int:
    data = await _collect()
    report = _analyze(data["profiles"])
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    raise SystemExit(asyncio.run(main(ap.parse_args().json)))
