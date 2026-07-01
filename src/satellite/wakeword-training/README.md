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
- v1 `md5 = 6bdd7c61f31d2089220c8404716977cc` — **DO NOT SHIP.** It false-fired
  ~500×/hr fleet-wide in real rooms (see FP hardening below).
- **Deployed = v3** (`md5 = 5cef9bd7991fa48780272488e2869886`), hardened with
  real-ambient hard-negatives: **0 false wakes** fleet-wide, recall intact.

## Also shipped: renfield_en (US+UK) + renfield_it

Same recipe, per language — `gen_en.py` (9 US+UK voices), `gen_it.py` (2 IT
voices; piper ships only 2 Italian), `renfield_{en,it}.yaml` (= the DE v3 config
with `model_name` swapped; the room-ambient hard-negatives are **language-
independent**, so they're reused verbatim). Held-out real-ambient FP for both:
**0 false wakes** (peak 0.002 / 0.016), recall EN ~77 % / IT ~85 % synthetic.

**Loading all three at once:** the satellite detector already accepts a keyword
**list**, and the backend wake-word config now pushes a comma-separated set
(`renfield_de,renfield_en,renfield_it`) — `WakeWordConfig.keyword_list` splits it,
`update_config` validates each element. So one satellite wakes to "Renfield" in
any of the three pronunciations. Marginal CPU per extra model ≈ 0 (melspectrogram
+ embedding features are computed once and shared across all loaded classifiers).

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

## Real-ambient false-positive hardening (v2/v3 — REQUIRED before shipping)

**The synthetic FP metric lied by ~30×.** v1 measured ~16 fp/hr @0.9 on synthetic
speech, but in the real house it false-fired **~500×/hr fleet-wide** — a constant
wake→empty-transcription storm. Synthetic negatives do not represent your rooms.

The fix (scripts: `gen_hard_negs.py`, `validate_ambient.py`, `measure_wav.py`,
`renfield_de_v2.yaml`, `renfield_de_v3.yaml`):

1. **Record real room ambient** on each satellite (~10 min; `arecord -D default
   -f S16_LE -r 16000 -c 1`). XVF3800/USB mics are exclusive → stop the service
   to record; HAT mics allow concurrent capture via the shared `default` device.
2. **`gen_hard_negs.py`** embeds each wav (`AudioFeatures._get_embeddings` →
   `(frames,96)`) and splits each room **75/25 by time**: first 75% → windowed
   `(N,16,96)` training **hard-negatives**; last 25% → concatenated **held-out
   FP-validation** (`real_ambient_features.npy`). The split avoids train/val leakage.
3. **Retrain** with the ambient as a heavily-sampled `hard_negative` feature class
   AND as `false_positive_validation_data_path` (so the FP target optimizes against
   REAL noise). Denser windowing (`step=1`) + `max_negative_weight` 1000→1500 helped.
4. **`validate_ambient.py`** scores the held-out ambient (model ONNX is fixed
   batch=1 → score one 16-frame window at a time) + recall on positive clips.

Results (held-out real ambient): v1 **336/h** → v2 **36/h** → v3 **18/h** @0.9,
recall steady ~70-75%. Real-voice live test after deploy: **0 false wakes**, the
storm gone, "Renfield" still detected.

**Per-satellite mic gain is a first-class FP lever — check it before over-training.**
The per-room breakdown (`validate_ambient` split by room) was decisive: v3 fired on
**zero** HAT-mic ambient (peak <0.005) — **100% of residual FP was the one XVF3800
satellite**, whose AGC we'd cranked (`PP_AGCDESIREDLEVEL=0.03`) for far-field reach.
That gain amplified the room's noise floor to speech amplitude. Halving it to
**0.015** dropped that satellite's peak 0.96→0.29 (0 false wakes) while still
detecting a normal "Renfield" across the room. Lesson: don't fight an over-gained
mic with more training data — fix the gain (it's the dominant FP knob on XVF3800
sats), then let the model handle the rest. The gain lives in the gitignored
`host_vars/satellite-<room>.yml` (`xvf3800_tuning.PP_AGCDESIREDLEVEL`), persisted
on-device with `xvf_host SAVE_CONFIGURATION 1`.

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
