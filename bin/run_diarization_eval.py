#!/usr/bin/env python3
"""Diarization spike / eval harness — meeting transcription §2, spike T1.

Persistent eval harness (eng-review 2026-07-06, D9): the spike IS this script,
and it stays as the regression anchor for every later whisper/pyannote/threshold
change. Gates live in tests/eval/diarization/gates.yaml and were FIXED BEFORE
the first measurement — do not tune them to fit a run.

Subcommands
-----------
generate-fixture   Synthesize a multi-speaker meeting wav + reference.json from
                   a script file (macOS `say` or piper voices). Privacy-clean:
                   synthetic voices, placeholder names — committable.
run                Diarize + transcribe + align + (optionally) embed one
                   audio/reference pair. Writes a metrics JSON per run.
probe-live-stt     Latency probe against a running voice-server. Run once for a
                   baseline, once DURING a `run`, feed both to `report`.
report             Evaluate metrics JSON(s) against the gates -> PASS/FAIL.

Typical spike sequence (on a CUDA host, e.g. inside the voice-server image —
see tests/eval/diarization/README.md):

  python bin/run_diarization_eval.py run \
      --audio tests/eval/diarization/fixtures/meeting_synthetic_de.wav \
      --reference tests/eval/diarization/fixtures/meeting_synthetic_de.reference.json \
      --whisper-model large-v3 --ecapa-onnx /models/ecapa.onnx \
      --out /tmp/metrics-large-v3.json
  python bin/run_diarization_eval.py report \
      --gates tests/eval/diarization/gates.yaml /tmp/metrics-*.json

Heavy deps (pyannote.audio, faster-whisper, torch, speechbrain, onnxruntime)
are imported lazily inside `run` so that fixture generation and reporting work
on any machine.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# Shared data model
#
#   reference.json / hypothesis segments:
#     {"sample_rate": 16000,
#      "segments": [{"speaker": "S1", "start": 0.0, "end": 3.2, "text": "..."}]}
# --------------------------------------------------------------------------

FRAME_S = 0.010  # frame size for the frame-level diarization scoring


@dataclass
class Segment:
    speaker: str
    start: float
    end: float
    text: str = ""


def load_segments(path: Path) -> list[Segment]:
    data = json.loads(path.read_text())
    return [Segment(s["speaker"], float(s["start"]), float(s["end"]), s.get("text", ""))
            for s in data["segments"]]


def total_speech(segments: list[Segment]) -> float:
    return sum(s.end - s.start for s in segments)


# --------------------------------------------------------------------------
# generate-fixture
# --------------------------------------------------------------------------

def cmd_generate_fixture(args: argparse.Namespace) -> int:
    script_path = Path(args.script)
    out_wav = Path(args.out)
    out_ref = out_wav.with_suffix("").with_suffix("")  # strip .wav
    out_ref = out_wav.parent / (out_wav.stem + ".reference.json")

    voice_map: dict[str, str] = {}
    for pair in (args.voices or "").split(","):
        if "=" in pair:
            name, voice = pair.split("=", 1)
            voice_map[name.strip()] = voice.strip()

    lines: list[tuple[str, str]] = []
    for raw in script_path.read_text().splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        if ":" not in raw:
            print(f"skip malformed line (no 'Speaker:'): {raw!r}", file=sys.stderr)
            continue
        speaker, text = raw.split(":", 1)
        lines.append((speaker.strip(), text.strip()))

    speakers = sorted({s for s, _ in lines})
    missing = [s for s in speakers if s not in voice_map]
    if missing:
        print(f"ERROR: no voice mapped for speaker(s) {missing}. "
              f"Pass --voices 'Name=Voice,...'", file=sys.stderr)
        return 2

    sr = args.sample_rate
    audio: list[bytes] = []
    segments: list[Segment] = []
    cursor = 0.0
    gap = args.gap_s

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, (speaker, text) in enumerate(lines):
            seg_wav = tmp / f"seg{i:03d}.wav"
            if args.engine == "say":
                aiff = tmp / f"seg{i:03d}.aiff"
                subprocess.run(["say", "-v", voice_map[speaker], "-o", str(aiff), text],
                               check=True)
                subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{sr}",
                                "-c", "1", str(aiff), str(seg_wav)], check=True)
            else:  # piper: voice_map value = path to .onnx voice model
                raw_wav = tmp / f"seg{i:03d}.raw.wav"
                subprocess.run(["piper", "--model", voice_map[speaker],
                                "--output_file", str(raw_wav)],
                               input=text.encode(), check=True)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw_wav),
                                "-ar", str(sr), "-ac", "1", str(seg_wav)], check=True)

            pcm, dur = _read_wav_pcm16(seg_wav, sr)
            audio.append(b"\x00" * int(gap * sr) * 2)
            cursor += gap
            audio.append(pcm)
            segments.append(Segment(speaker, round(cursor, 3), round(cursor + dur, 3), text))
            cursor += dur

    _write_wav_pcm16(out_wav, b"".join(audio), sr)
    out_ref.write_text(json.dumps(
        {"sample_rate": sr, "engine": args.engine,
         "segments": [asdict(s) for s in segments]}, ensure_ascii=False, indent=2))
    print(f"fixture: {out_wav}  ({cursor:.1f}s, {len(speakers)} speakers, "
          f"{len(segments)} turns)\nreference: {out_ref}")
    return 0


def _read_wav_pcm16(path: Path, expect_sr: int) -> tuple[bytes, float]:
    import wave
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != expect_sr or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(f"{path}: expected mono PCM16 @{expect_sr}, "
                             f"got {w.getnchannels()}ch {w.getsampwidth() * 8}bit "
                             f"@{w.getframerate()}")
        frames = w.readframes(w.getnframes())
        return frames, w.getnframes() / expect_sr


def _write_wav_pcm16(path: Path, pcm: bytes, sr: int) -> None:
    import wave
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


# --------------------------------------------------------------------------
# run — the actual pipeline under test
# --------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    audio_path = Path(args.audio)
    ref_segments = load_segments(Path(args.reference)) if args.reference else []

    import numpy as np  # noqa: PLC0415

    waveform, sr = _load_audio_mono16k(audio_path)
    audio_len_s = len(waveform) / sr
    metrics: dict = {
        "audio": str(audio_path), "audio_seconds": round(audio_len_s, 1),
        "whisper_model": args.whisper_model, "device": args.device,
        "diarization_model": args.diarization_model,
    }

    gpu_peak = _GpuPeak(args.device)

    # -- diarization ---------------------------------------------------------
    t0 = time.monotonic()
    diar_segments = _run_pyannote(args, waveform, sr)
    t_diar = time.monotonic() - t0
    metrics["t_diarization_s"] = round(t_diar, 1)
    metrics["hyp_speaker_count"] = len({s.speaker for s in diar_segments})
    if ref_segments:
        metrics["ref_speaker_count"] = len({s.speaker for s in ref_segments})

    # -- ASR with word timestamps --------------------------------------------
    t0 = time.monotonic()
    words = _run_whisper(args, audio_path)
    t_asr = time.monotonic() - t0
    metrics["t_asr_s"] = round(t_asr, 1)

    # -- alignment: word -> speaker ------------------------------------------
    hyp_segments = _align_words(words, diar_segments)
    metrics["hyp_segments"] = [asdict(s) for s in hyp_segments]

    # -- scoring vs reference --------------------------------------------------
    if ref_segments:
        metrics["diarization_scores"] = _score_frames(ref_segments, hyp_segments)
        if any(s.text for s in ref_segments):
            wer = _try_wer(" ".join(s.text for s in ref_segments),
                           " ".join(w[2] for w in words))
            if wer is not None:
                metrics["wer_sample"] = round(wer, 3)

    # -- per-cluster ECAPA separation (auto-match gate) ------------------------
    if args.ecapa_onnx:
        metrics["embedding_separation"] = _cluster_separation(
            args, waveform, sr, diar_segments)

    metrics["gpu_seconds_per_audio_minute"] = round(
        (t_diar + t_asr) / max(audio_len_s / 60.0, 1e-9), 1)
    metrics["gpu_peak_vram_mb"] = gpu_peak.read()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in metrics.items() if k != "hyp_segments"},
                     ensure_ascii=False, indent=2))
    print(f"\nmetrics written: {out}")
    return 0


def _load_audio_mono16k(path: Path):
    """Load any audio ffmpeg understands as float32 mono 16 kHz."""
    import numpy as np  # noqa: PLC0415
    if shutil.which("ffmpeg"):
        raw = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(path),
             "-f", "f32le", "-ac", "1", "-ar", "16000", "pipe:1"],
            check=True, capture_output=True).stdout
        return np.frombuffer(raw, dtype=np.float32).copy(), 16000
    pcm, _dur = _read_wav_pcm16(path, 16000)  # strict fallback: already-16k wav
    return (np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0), 16000


def _run_pyannote(args, waveform, sr) -> list[Segment]:
    import torch  # noqa: PLC0415
    from pyannote.audio import Pipeline  # noqa: PLC0415

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    pipeline = Pipeline.from_pretrained(args.diarization_model, use_auth_token=token)
    if args.device == "cuda":
        pipeline.to(torch.device("cuda"))
    tensor = torch.from_numpy(waveform).unsqueeze(0)
    kwargs = {}
    if args.num_speakers:
        kwargs["num_speakers"] = args.num_speakers
    annotation = pipeline({"waveform": tensor, "sample_rate": sr}, **kwargs)
    return [Segment(str(label), float(turn.start), float(turn.end))
            for turn, _track, label in annotation.itertracks(yield_label=True)]


def _run_whisper(args, audio_path: Path) -> list[tuple[float, float, str]]:
    from faster_whisper import WhisperModel  # noqa: PLC0415

    compute = "float16" if args.device == "cuda" else "int8"
    model = WhisperModel(args.whisper_model, device=args.device, compute_type=compute)
    seg_iter, _info = model.transcribe(str(audio_path), language=args.language,
                                       word_timestamps=True, vad_filter=True)
    words: list[tuple[float, float, str]] = []
    for seg in seg_iter:
        for w in seg.words or []:
            words.append((float(w.start), float(w.end), w.word.strip()))
    del model
    return words


def _align_words(words, diar_segments: list[Segment]) -> list[Segment]:
    """Assign each ASR word to the diarization turn with maximal time overlap.

    Overlapping turns: max-overlap wins. Uncovered words snap to the nearest
    turn within 0.5 s, otherwise speaker 'UNK'. Consecutive same-speaker words
    merge into hypothesis segments.
    """
    def overlap(a0, a1, b0, b1):
        return max(0.0, min(a1, b1) - max(a0, b0))

    hyp: list[Segment] = []
    for w0, w1, text in words:
        best, best_ov = None, 0.0
        for d in diar_segments:
            ov = overlap(w0, w1, d.start, d.end)
            if ov > best_ov:
                best, best_ov = d, ov
        if best is None:
            near = min(diar_segments,
                       key=lambda d: min(abs(d.start - w1), abs(w0 - d.end)),
                       default=None)
            if near is not None and min(abs(near.start - w1), abs(w0 - near.end)) <= 0.5:
                best = near
        speaker = best.speaker if best else "UNK"
        if hyp and hyp[-1].speaker == speaker and w0 - hyp[-1].end <= 1.0:
            hyp[-1].end = w1
            hyp[-1].text = f"{hyp[-1].text} {text}".strip()
        else:
            hyp.append(Segment(speaker, w0, w1, text))
    return hyp


def _score_frames(ref: list[Segment], hyp: list[Segment]) -> dict:
    """Frame-level DER-style scoring with optimal (Hungarian) speaker mapping."""
    import numpy as np  # noqa: PLC0415
    from scipy.optimize import linear_sum_assignment  # noqa: PLC0415

    end = max(max(s.end for s in ref), max((s.end for s in hyp), default=0.0))
    n = int(math.ceil(end / FRAME_S)) + 1
    ref_speakers = sorted({s.speaker for s in ref})
    hyp_speakers = sorted({s.speaker for s in hyp})
    ref_idx = {s: i for i, s in enumerate(ref_speakers)}
    hyp_idx = {s: i for i, s in enumerate(hyp_speakers)}

    ref_f = np.full(n, -1, dtype=np.int16)
    hyp_f = np.full(n, -1, dtype=np.int16)
    for s in ref:
        ref_f[int(s.start / FRAME_S):int(s.end / FRAME_S)] = ref_idx[s.speaker]
    for s in hyp:
        if s.speaker == "UNK":
            continue
        hyp_f[int(s.start / FRAME_S):int(s.end / FRAME_S)] = hyp_idx[s.speaker]

    # confusion matrix over frames where both streams see speech
    both = (ref_f >= 0) & (hyp_f >= 0)
    conf = np.zeros((len(ref_speakers), len(hyp_speakers)), dtype=np.int64)
    np.add.at(conf, (ref_f[both], hyp_f[both]), 1)
    rows, cols = linear_sum_assignment(-conf)
    mapping = dict(zip(cols.tolist(), rows.tolist()))  # hyp -> ref

    correct = sum(int(conf[r, c]) for r, c in zip(rows, cols))
    confused = int(both.sum()) - correct
    missed = int(((ref_f >= 0) & (hyp_f < 0)).sum())
    false_alarm = int(((ref_f < 0) & (hyp_f >= 0)).sum())
    ref_speech = int((ref_f >= 0).sum())

    return {
        "attribution_error_rate": round(confused / max(both.sum(), 1), 4),
        "der_like": round((missed + false_alarm + confused) / max(ref_speech, 1), 4),
        "missed_rate": round(missed / max(ref_speech, 1), 4),
        "false_alarm_rate": round(false_alarm / max(ref_speech, 1), 4),
        "speaker_mapping": {hyp_speakers[c]: ref_speakers[r]
                            for c, r in mapping.items()},
    }


def _try_wer(ref_text: str, hyp_text: str):
    try:
        import jiwer  # noqa: PLC0415
    except ImportError:
        return None
    return float(jiwer.wer(ref_text.lower(), hyp_text.lower()))


def _cluster_separation(args, waveform, sr, diar_segments: list[Segment]) -> dict:
    """Per-cluster ECAPA embeddings in the voice-server ONNX space (D4/D12).

    Splits every cluster's speech into chunks, embeds each chunk, then reports
    same-cluster vs cross-cluster cosine statistics. The auto-match gate is
    separation = same_mean - cross_p95  (gates.yaml: auto_match_separation_min).
    Requires the speechbrain feature pipeline — run inside the voice-server
    image (same preprocessing as production, see voice-server speaker_service).
    """
    import numpy as np  # noqa: PLC0415
    import onnxruntime as ort  # noqa: PLC0415
    import torch  # noqa: PLC0415
    from speechbrain.lobes.features import Fbank  # noqa: PLC0415
    from speechbrain.processing.features import InputNormalization  # noqa: PLC0415

    fbank = Fbank(n_mels=80)
    norm = InputNormalization(norm_type="sentence", std_norm=False)
    sess = ort.InferenceSession(args.ecapa_onnx, providers=["CUDAExecutionProvider",
                                                            "CPUExecutionProvider"])
    input_name = sess.get_inputs()[0].name

    def embed(chunk: "np.ndarray") -> "np.ndarray":
        wav = torch.from_numpy(chunk).unsqueeze(0)
        feats = norm(fbank(wav), torch.ones(1))
        out = sess.run(None, {input_name: feats.numpy()})[0]
        vec = np.squeeze(out).astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-9)

    chunk_s, min_s = args.embed_chunk_s, args.embed_min_s
    by_cluster: dict[str, list] = {}
    for seg in diar_segments:
        a, b = int(seg.start * sr), int(seg.end * sr)
        by_cluster.setdefault(seg.speaker, []).append(waveform[a:b])

    cluster_embs: dict[str, list] = {}
    for speaker, pieces in by_cluster.items():
        speech = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]
        n_chunks = int(len(speech) / (chunk_s * sr))
        embs = [embed(speech[i * chunk_s * sr:(i + 1) * chunk_s * sr])
                for i in range(n_chunks)]
        rest = speech[n_chunks * chunk_s * sr:]
        if len(rest) >= min_s * sr:
            embs.append(embed(rest))
        if embs:
            cluster_embs[speaker] = embs

    same, cross = [], []
    speakers = list(cluster_embs)
    for i, si in enumerate(speakers):
        ei = cluster_embs[si]
        same += [float(a @ b) for x, a in enumerate(ei) for b in ei[x + 1:]]
        for sj in speakers[i + 1:]:
            cross += [float(a @ b) for a in ei for b in cluster_embs[sj]]

    def p95(vals):
        return sorted(vals)[max(0, int(0.95 * len(vals)) - 1)] if vals else None

    same_mean = round(statistics.fmean(same), 4) if same else None
    cross_p95 = round(p95(cross), 4) if cross else None
    separation = (round(same_mean - cross_p95, 4)
                  if same_mean is not None and cross_p95 is not None else None)
    return {
        "chunks_per_cluster": {s: len(e) for s, e in cluster_embs.items()},
        "same_cluster_cosine_mean": same_mean,
        "cross_cluster_cosine_p95": cross_p95,
        "separation": separation,
    }


class _GpuPeak:
    """Peak-VRAM tracker: torch counter when on CUDA, else nvidia-smi snapshot."""

    def __init__(self, device: str):
        self.device = device
        if device == "cuda":
            try:
                import torch  # noqa: PLC0415
                torch.cuda.reset_peak_memory_stats()
                self._torch = torch
            except Exception:
                self._torch = None

    def read(self):
        if self.device == "cuda" and getattr(self, "_torch", None):
            return round(self._torch.cuda.max_memory_allocated() / 2**20)
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
            return int(out[0]) if out else None
        except Exception:
            return None


# --------------------------------------------------------------------------
# probe-live-stt — measure live STT latency (baseline vs during a batch run)
# --------------------------------------------------------------------------

def cmd_probe_live_stt(args: argparse.Namespace) -> int:
    import urllib.request  # noqa: PLC0415
    import uuid  # noqa: PLC0415

    sample = Path(args.sample).read_bytes()
    latencies: list[float] = []
    deadline = time.monotonic() + args.duration_s
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; "
            f"filename=\"probe.wav\"\r\nContent-Type: audio/wav\r\n\r\n"
            ).encode() + sample + f"\r\n--{boundary}--\r\n".encode()

    while time.monotonic() < deadline:
        req = urllib.request.Request(
            args.url.rstrip("/") + "/api/voice/stt", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     **({"Authorization": f"Bearer {args.token}"} if args.token else {})})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=60):
                pass
            latencies.append(time.monotonic() - t0)
        except Exception as exc:  # noqa: BLE001 — a probe records failures, never aborts
            print(f"probe error: {exc}", file=sys.stderr)
        time.sleep(args.interval_s)

    if not latencies:
        print("no successful probes", file=sys.stderr)
        return 1
    latencies.sort()
    result = {
        "samples": len(latencies),
        "p50_s": round(latencies[len(latencies) // 2], 3),
        "p95_s": round(latencies[max(0, int(0.95 * len(latencies)) - 1)], 3),
        "mean_s": round(statistics.fmean(latencies), 3),
    }
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0


# --------------------------------------------------------------------------
# report — gates
# --------------------------------------------------------------------------

def cmd_report(args: argparse.Namespace) -> int:
    gates = _load_gates(Path(args.gates))
    rows: list[tuple[str, str, str, str, bool | None]] = []

    for metrics_path in args.metrics:
        m = json.loads(Path(metrics_path).read_text())
        label = m.get("whisper_model", Path(metrics_path).stem)

        aer = (m.get("diarization_scores") or {}).get("attribution_error_rate")
        rows.append(_gate(f"[{label}] attribution_error_rate", aer,
                          gates["attribution_error_rate_max"], "<="))
        gpm = m.get("gpu_seconds_per_audio_minute")
        rows.append(_gate(f"[{label}] gpu_s_per_audio_min", gpm,
                          gates["gpu_seconds_per_audio_minute_max"], "<="))
        sep = (m.get("embedding_separation") or {}).get("separation")
        rows.append(_gate(f"[{label}] auto_match_separation", sep,
                          gates["auto_match_separation_min"], ">=",
                          note="gate for BUILDING auto-match, not for shipping §2"))

    if args.live_baseline and args.live_during:
        base = json.loads(Path(args.live_baseline).read_text())["p95_s"]
        during = json.loads(Path(args.live_during).read_text())["p95_s"]
        factor = round(during / base, 2) if base else None
        rows.append(_gate("live_stt_p95_factor", factor,
                          gates["live_stt_p95_factor_max"], "<="))

    print(f"{'gate':55} {'value':>10} {'threshold':>10}  verdict")
    print("-" * 90)
    hard_fail = False
    for name, val, thr, note, ok in rows:
        verdict = "PASS" if ok else ("n/a" if ok is None else "FAIL")
        print(f"{name:55} {val:>10} {thr:>10}  {verdict}{'  # ' + note if note else ''}")
        if ok is False and "auto_match" not in name:
            hard_fail = True
    print("-" * 90)
    print("OVERALL:", "FAIL — do not start the §2 build" if hard_fail
          else "PASS — §2 build unblocked (auto-match only if its gate passed)")
    return 1 if hard_fail else 0


def _load_gates(path: Path) -> dict:
    """Load gates.yaml via PyYAML when present, else a minimal flat parser
    (the file is a single `gates:` mapping of numeric thresholds)."""
    try:
        import yaml  # noqa: PLC0415
        return yaml.safe_load(path.read_text())["gates"]
    except ImportError:
        gates: dict[str, float] = {}
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.endswith(":"):
                continue
            key, _, value = line.partition(":")
            gates[key.strip()] = float(value)
        return gates


def _gate(name, value, threshold, op, note=""):
    if value is None:
        return (name, "—", str(threshold), note or "metric missing", None)
    ok = value <= threshold if op == "<=" else value >= threshold
    return (name, str(value), f"{op}{threshold}", note, ok)


# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate-fixture", help="synthesize meeting wav + reference")
    g.add_argument("--script", required=True, help="text file: 'Speaker: sentence' lines")
    g.add_argument("--out", required=True, help="output wav path")
    g.add_argument("--voices", required=True,
                   help="'Speaker=Voice,...' (say voice names / piper model paths)")
    g.add_argument("--engine", choices=["say", "piper"], default="say")
    g.add_argument("--sample-rate", type=int, default=16000)
    g.add_argument("--gap-s", type=float, default=0.6)
    g.set_defaults(func=cmd_generate_fixture)

    r = sub.add_parser("run", help="run diarization+ASR+alignment on one fixture")
    r.add_argument("--audio", required=True)
    r.add_argument("--reference", help="reference.json (omit for exploratory runs)")
    r.add_argument("--out", required=True, help="metrics JSON output path")
    r.add_argument("--whisper-model", default="large-v3")
    r.add_argument("--language", default="de")
    r.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    r.add_argument("--diarization-model", default="pyannote/speaker-diarization-3.1")
    r.add_argument("--num-speakers", type=int, help="optional speaker-count hint")
    r.add_argument("--ecapa-onnx", help="voice-server ECAPA ONNX model path "
                                        "(enables the auto-match separation metric)")
    r.add_argument("--embed-chunk-s", type=int, default=20)
    r.add_argument("--embed-min-s", type=int, default=5)
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("probe-live-stt", help="latency probe against voice-server")
    s.add_argument("--url", required=True, help="voice-server base url")
    s.add_argument("--token", default=os.environ.get("VOICE_TOKEN", ""))
    s.add_argument("--sample", required=True, help="short wav to POST repeatedly")
    s.add_argument("--interval-s", type=float, default=2.0)
    s.add_argument("--duration-s", type=float, default=120.0)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_probe_live_stt)

    rep = sub.add_parser("report", help="evaluate metrics against the gates")
    rep.add_argument("metrics", nargs="+", help="one or more metrics JSONs from `run`")
    rep.add_argument("--gates", default="tests/eval/diarization/gates.yaml")
    rep.add_argument("--live-baseline", help="probe-live-stt JSON without batch load")
    rep.add_argument("--live-during", help="probe-live-stt JSON during a batch run")
    rep.set_defaults(func=cmd_report)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
