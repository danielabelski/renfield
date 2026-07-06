# Diarization eval — spike T1 & permanent regression harness

Harness: [`bin/run_diarization_eval.py`](../../../bin/run_diarization_eval.py) ·
Gates: [`gates.yaml`](gates.yaml) (fixed 2026-07-06, before first measurement) ·
Design: [`docs/design/meeting-transcription.md`](../../../docs/design/meeting-transcription.md)

## Two-tier fixture policy

| Tier | Where | Purpose |
|---|---|---|
| Synthetic (committed) | `fixtures/` | Privacy-clean regression anchor — placeholder names, TTS voices. Re-run after every whisper/pyannote/threshold change. |
| Real recordings (NEVER committed) | `local/` (gitignored) | Actual room/mic/German-household acoustics. The gates are judged on THESE; the synthetic fixture guards against regressions. |

Synthetic audio is sequential turns with gaps — it cannot exercise overlapping
speech or room reverb. Treat synthetic results as an upper bound; real
recordings decide the gates.

## 1. Generate the synthetic fixture (macOS, one-time)

```bash
python3 bin/run_diarization_eval.py generate-fixture \
  --script tests/eval/diarization/fixtures/meeting_script_de.txt \
  --out tests/eval/diarization/fixtures/meeting_synthetic_de.wav \
  --voices "Anna=Anna,Ben=Rocko,Clara=Sandy,David=Eddy (Deutsch (Deutschland))"
```

## 2. Record real fixtures (drop into `local/`)

Per capture comparison (D15): record the SAME short meeting (~10 min, 3-4
speakers) twice — phone in the table center AND an XVF3800 satellite test
capture. Write a `*.reference.json` by hand for a 2-3 minute excerpt (segment
starts/ends ±0.3 s is fine; that dominates the metric far less than speaker
identity).

## 3. Run on a GPU host

The run needs pyannote.audio + faster-whisper + speechbrain + onnxruntime —
exactly the voice-server stack plus pyannote. Easiest: a one-off container from
the voice-server image on a GPU box (e.g. cuda.local, which has docker + GPU;
avoid gpu-3 during the day — it serves live voice):

```bash
docker run --rm --gpus all \
  -v /path/to/renfield:/eval -w /eval \
  -e HF_TOKEN=hf_...   # gated pyannote models — one-time license accept on HF \
  registry.treehouse.x-idra.de/renfield/voice-server:<current-tag> \
  bash -lc "pip install 'pyannote.audio>=3.1' scipy pyyaml && \
    for M in base medium large-v3; do \
      python bin/run_diarization_eval.py run \
        --audio tests/eval/diarization/local/meeting_phone.wav \
        --reference tests/eval/diarization/local/meeting_phone.reference.json \
        --whisper-model \$M --ecapa-onnx \$SPEAKER_MODEL_PATH \
        --out /tmp/metrics-\$M.json ; done"
```

Note: this pip-installs pyannote at runtime for the SPIKE only — the build
phase bakes model + deps into the image (offline-first, no runtime HF access).
Pre-download the pyannote model once on a connected machine and mount the HF
cache (`-v ~/.cache/huggingface:/root/.cache/huggingface`) if the GPU host
should stay offline.

## 4. Live-latency impact (gate 4)

```bash
# baseline (no batch running), then again DURING a `run`:
python3 bin/run_diarization_eval.py probe-live-stt \
  --url https://renfield.local --sample tests/eval/diarization/fixtures/probe_short.wav \
  --duration-s 120 --out /tmp/live-baseline.json
```

## 5. Verdict

```bash
python3 bin/run_diarization_eval.py report /tmp/metrics-*.json \
  --gates tests/eval/diarization/gates.yaml \
  --live-baseline /tmp/live-baseline.json --live-during /tmp/live-during.json
```

Exit 0 = §2 build unblocked. The `auto_match_separation` gate only decides
whether the auto-matcher gets BUILT (D12) — failing it does not block §2
(pseudonyms + one-click labeling are the baseline product).
