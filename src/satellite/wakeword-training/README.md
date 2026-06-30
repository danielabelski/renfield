# Per-language "Renfield" wake-word training

Reproducible recipe for training a **single-word, per-language** "Renfield"
openWakeWord model on a k8s GPU pod. The shipped German model `renfield_de.onnx`
(in `data/wakeword-models/`, served to satellites + browser) was produced this way.

## Why per-language single-word models

- The stock `hey_renfield` model is **English-pronunciation only** (offline
  scoring: EN clips 0.74–0.89, German clips ≤0.28). A German speaker saying
  "Renfield" barely registers — the root cause of the "premium far-field array
  but devastating 1–3 m recall" report.
- openWakeWord is a tiny classifier on a frozen melspectrogram + speech-embedding
  pipeline. **One short word per model** lets the small net spend all its capacity
  on one pronunciation, which both raises recall and tightens the false-positive
  boundary. Trying to cover DE+EN+IT in one model diluted recall ~5× and pushed
  false-positives up.
- The satellite loads **multiple** models at once (the detector takes a keyword
  list; the backend pushes the list and serves each `.onnx`). So the end state is
  `renfield_de` + `renfield_en` + `renfield_it` loaded together — one per language.

## Result (renfield_de)

- Arch: `layer_size=48`, `max_negative_weight=700`, concentrated multi-voice
  positives, German adversarial near-miss negatives.
- Offline: ~76 % recall / ~16.6 false-positives-per-hour @ threshold 0.9 on
  continuous synthetic speech; adversarial near-miss accept ~2.5 %.
- **Real voice beat the synthetic metric**: 9/9 detections of a spoken German
  "Renfield" at 0.50–0.99. The synthetic TTS-on-TTS recall understates real-world
  recall — trust an on-device test over the offline number.
- `md5(renfield_de.onnx) = 6bdd7c61f31d2089220c8404716977cc`

## The GPU pod

The blocker was Blackwell (sm_120) + CUDA versions. What works:

- torch **2.7.0+cu128** (2.2 → "no kernel image for sm_120").
- onnxruntime-gpu **1.20.1** (CUDA-12). 1.27 needs libcudart.so.13 (CUDA-13) which
  isn't present; 1.20.1 runs against torch's bundled CUDA-12 libs.
- `LD_LIBRARY_PATH` from torch's bundled cuDNN/CUDA (`/work/ld.env`).
- **NFS RWX PVC** (`oww-nfs`) for `/work`, NOT a Longhorn RWO volume (RWO went
  "not ready for workloads" on pod recreate) and NOT node disk (image churn
  disk-pressure-tainted the node). Keeping the 17 GB negative features + clips on
  NFS keeps them off the node.
- torchcodec is CUDA-13-only → **bypassed**: decode audio with `soundfile`
  directly; download RIRs as already-16 kHz WAVs via `huggingface_hub`.
- Apply the 5 `train.py` patches — see `scripts/train.py.patches.md`.

## Pipeline (scripts/)

| Script | Role |
|---|---|
| `oww_setup.sh` | apt + pip env inside the pod (openwakeword + training stack; clones openWakeWord + piper-sample-generator) |
| `dl2.py` / `dl3.py` | fetch ACAV100M negative features (~17 GB) + MIT RIRs + AudioSet noise (already-16 kHz, no torchcodec) |
| `gen_de.py` | **German** positive + adversarial-negative generation (the shipped one) |
| `gen_samples.py` | multilingual generator (DE+EN-US+EN-UK+IT) — template for the other languages |
| `make_config.py` | write the openWakeWord training YAML |
| `renfield_de.yaml` | the German training config (layer 48, weight 700, fp-target 0.5/hr) |
| `run_train_de.sh` | run `openwakeword.train` end-to-end + export ONNX |
| `validate_de.py` / `diag_de.py` | overall + per-voice recall / false-accept validation |

### Lessons baked into the config / generator
- **Concentrate the voices.** A 236-speaker `de_DE-mls` model sat at ~13 % recall
  and *diluted* the whole set — the single biggest fix was regenerating with a
  small set of distinct, high-quality voices (cap multi-speaker models to ~15
  speakers; drop the worst performers). `diag_de.py` (per-voice recall) is how you
  find the diluters.
- **Adversarial near-misses matter.** German fillers + near-miss words (Rennfeld,
  Feld, Held, rennen, Manfred, Reinfeld, Enfield, …) as negatives tighten the
  boundary around the one short word. See `ADVERSARIAL` in the generator.
- **Negative weight is the recall↔FP dial.** Lower weight → higher recall, more
  FPs. Sweep: weight 200 ≈ 79 %/~100 fp-hr; 500 ≈ 73 %/65; **700 ≈ 76 %/16.6**
  (the chosen point, concentrated voices). Tune per language.
- **Validate on a real voice in the room**, not just the offline number.

## Train a new language (e.g. EN-US, EN-UK, IT)

1. Copy `gen_de.py` → `gen_<lang>.py`; swap `VOICE_DEFS` to that language's piper
   voices (concentrated, distinct) and `ADVERSARIAL` to that language's near-misses.
2. Copy `renfield_de.yaml` → `renfield_<lang>.yaml`; set `model_name=renfield_<lang>`.
3. Reuse the same negative features + RIRs (language-independent).
4. `run_train_<lang>.sh` → `renfield_<lang>.onnx`; validate with a per-voice diag.
5. Drop the `.onnx` in `data/wakeword-models/` + `src/frontend/public/wakeword-models/`,
   register the id in `AVAILABLE_KEYWORDS` (`services/wakeword_config_manager.py`),
   redeploy the backend, and add the id to the global wake-word list.

## Deploying a model (what makes it "live")

1. `cp <model>.onnx data/wakeword-models/` and `src/frontend/public/wakeword-models/`
   (deploy rsyncs `data/wakeword-models/` into the backend build context; the
   Dockerfile `COPY wakeword-models /app/wakeword-models` bakes it; the backend
   serves it at `/api/settings/wakeword/models/{id}` and satellites auto-download).
2. Register the id in `AVAILABLE_KEYWORDS` / `VALID_KEYWORDS`
   (`src/backend/services/wakeword_config_manager.py`) so validation accepts it.
3. Build + roll out the backend (see `.claude/skills/deploy-production`).
4. Set the global wake word (admin Settings → Wake Word, or the wakeword config
   API). The backend pushes the keyword list to every satellite, which downloads
   the model(s) and loads them. **The global config overrides any local satellite
   wake-word setting** — there is no per-satellite override today.
