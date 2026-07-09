# T-MODEL-SPIKE — How do we source the gesture model?

Status: **SPIKE / RESEARCH** (2026-06-27). Resolves Decision **D2** of
[`docs/design/non-verbal-communication.md`](../non-verbal-communication.md)
("spike pretrained vs. custom-trained gesture model BEFORE committing to a
training pipeline"), and feeds **D8** (eval harness) and the eng-review
additive finding "custom-model training data reintroduces raw-video-at-rest."

**Bottom line (recommendation up front):** The MediaPipe Gesture Recognizer
**stock model is a free, immediate win for the STATIC subset** of the starter
vocabulary (palm-stop, thumbs-up, thumbs-down, finger-count partially,
point-at-device partially) — ship that as the first shippable slice with
**zero training**. But the stock recognizer is **single-frame / static-pose
only**; **every MOTION gesture** (wave, swipe L/R, palm up-down volume) is
**physically impossible** for it and needs a custom **temporal model over
landmark sequences** — a later phase with its own training-data + retention
program. This static/motion split, not "pretrained vs custom," is the real
decision boundary, and it cleanly re-shapes the phasing.

---

## 1. Stock-vs-custom vocabulary gap table

The MediaPipe Gesture Recognizer ships a "canned gestures" classifier
recognizing **8 labels** out of the box:
`None` (Unrecognized), `Closed_Fist`, `Open_Palm`, `Pointing_Up`, `Thumb_Down`,
`Thumb_Up`, `Victory`, `ILoveYou`
([Gesture recognition task guide](https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer)).

Mapping the plan's [starter vocabulary](../non-verbal-communication.md#starter-command-gesture-vocabulary-phase-3) onto that set:

| Plan starter gesture | Intent | Stock label | Coverage |
|---|---|---|---|
| **Palm-stop** (open palm pushed at cam) | Stop / cancel | `Open_Palm` | ✅ **Stock-covered** (static pose) — *push-toward-cam motion is ignored; pose alone fires* |
| **Thumbs-up** | Confirm pending | `Thumb_Up` | ✅ **Stock-covered** |
| **Thumbs-down** | Reject pending | `Thumb_Down` | ✅ **Stock-covered** |
| **Point-at-device** (the *pose*) | Select a device | `Pointing_Up` (vertical only) | 🟡 **Partial** — stock detects "pointing **up**", not an arbitrary aim vector. Resolving *which* device needs the pose→entity ray-cast (plan's own Phase-3-late note), independent of the model. |
| **Finger-count 1** | Pick option 1 | `Pointing_Up` ≈ 1 finger | 🟡 **Partial** — only "1" coincides with a stock label; 2 ≈ `Victory` but 3/4/5 have **no stock label** |
| **Finger-count 2–5** | Pick option N | — | ❌ **Needs custom** (static, but new classes) — trivial to add via Model Maker *or* a hand-rolled finger-extension heuristic on the 21 landmarks |
| **Wave** | Touchless wake | — | ❌ **Needs custom + temporal** (motion) |
| **Swipe left / right** | Prev / next track | — | ❌ **Needs custom + temporal** (motion) |
| **Palm up-down** (raise/lower flat hand) | Volume up / down | — | ❌ **Needs custom + temporal** (motion) |

Unused stock labels (`Closed_Fist`, `Victory`, `ILoveYou`) are **free spare
classes** the vocabulary config could opportunistically bind (e.g.
`Closed_Fist` → mute, `Victory` → "option 2") without any training.

**Verdict on D2's literal question:** for the static subset, *pretrained wins
outright* — it is more accurate, zero-effort, and already validated by Google
on millions of hands. There is no reason to custom-train static poses that the
stock model already nails. Custom training is reserved for (a) the motion
gestures the stock model cannot do at all, and (b) finger-count 3–5, a tiny
static addition.

---

## 2. THE KEY FINDING — STATIC vs MOTION split

**The stock Gesture Recognizer classifies a single frame's hand pose.** It
supports three running modes — `IMAGE`, `VIDEO`, `LIVE_STREAM` — but VIDEO and
LIVE_STREAM only add *hand-tracking between frames to cut palm-detection
latency*; **they still classify gesture on each frame independently** and do
**not** consume a motion sequence
([Gesture recognition task guide](https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer);
[running-mode confirmation, MediaPipe #4448](https://github.com/google/mediapipe/blob/master/mediapipe/tasks/python/vision/gesture_recognizer.py)).
A "wave" or "swipe" has *no distinctive single frame* — mid-wave is just an open
palm — so the static recognizer **physically cannot** represent it. This is an
architectural limit, not a tuning gap.

Splitting the starter vocabulary by this boundary:

### STATIC (single-frame hand pose) — works with the stock recognizer NOW
| Gesture | Path |
|---|---|
| Palm-stop (`Open_Palm`) | stock model, today |
| Thumbs-up (`Thumb_Up`) | stock model, today |
| Thumbs-down (`Thumb_Down`) | stock model, today |
| Point-at-device *pose* (`Pointing_Up`/aim) | stock pose + separate ray-cast |
| Finger-count 1–2 (`Pointing_Up`/`Victory`) | stock model, today |
| Finger-count 3–5 | small static custom add (Model Maker or landmark heuristic) |

### MOTION (landmark sequence over time) — needs a CUSTOM temporal model, LATER
| Gesture | Why static can't | Path |
|---|---|---|
| Wave | mid-wave = open palm; meaning is in the oscillation | temporal model over landmark window |
| Swipe left / right | direction of travel, not a pose | temporal model + motion vector |
| Palm up-down (volume) | raise/lower trajectory, not a pose | temporal model + motion vector |

The standard recipe for the MOTION head is exactly what the design doc already
names: **MediaPipe Hands/Holistic landmarks per frame → a temporal classifier
(LSTM/Transformer/1-D-CNN, or a "point-history" classifier) over a sliding
window of landmark frames**. This is a well-trodden pattern with strong
published results (e.g. MediaPipe-keypoints→LSTM, and the Kazuhito00 reference
project that pairs a static **keypoint classifier** with a separate **point-
history classifier** for dynamic gestures)
([MediaPipe+LSTM, Tandfonline 2025](https://www.tandfonline.com/doi/full/10.1080/21642583.2025.2587853);
[Kazuhito00 hand-gesture-recognition-using-mediapipe](https://github.com/Kazuhito00/hand-gesture-recognition-using-mediapipe/blob/main/README_EN.md)).

**Phasing consequence (re-shapes the plan):** Phase 3 ("command-gesture
vocabulary + actuation") should split into **3a (STATIC, stock model, no
training)** and **3b (MOTION, custom temporal model, training program)**. 3a is
shippable as soon as attribution (T-ATTRIB-SPIKE) and the misfire gate
(T-MISFIRE) clear — it carries **no training-data / raw-video-at-rest cost at
all**. 3b is the heavier lift and is where the retention design in §5 applies.

---

## 3. MediaPipe Model Maker feasibility (the custom STATIC path)

If we want custom **static** classes (finger-count 3–5, or to re-tier/rename
the canned set), `mediapipe-model-maker` is the supported tool. Findings
([Hand gesture customization guide](https://ai.google.dev/edge/mediapipe/solutions/customization/gesture_recognizer);
[Dataset API](https://ai.google.dev/edge/api/mediapipe/python/mediapipe_model_maker/gesture_recognizer/Dataset)):

- **Training data format:** `<dataset>/<label_name>/<image>.*` — one folder per
  gesture class, folder name = label. One class **must** be named `none`
  (negatives: hands in no target gesture). **Just labeled still images**, not
  video.
- **What it actually trains:** Model Maker runs MediaPipe Hands on each image,
  **discards the pixels, and trains a small MLP head on the 21 hand
  landmarks**. Images with no detected hand are dropped. So the artifact is a
  landmark classifier — cheap, fast, tiny.
- **Effort / volume:** light. HParams default `epochs=10`, `batch_size=2`,
  `lr=0.001`; community results get usable accuracy with a few hundred images
  per class. Recommended split 80/10/10 train/val/test. Trains in minutes on a
  CPU; no GPU needed.
- **Output format:** a single `.task` model bundle (TFLite under the hood) that
  bundles hand-detection + canned-gesture + your custom head, plus metadata/
  label file. Drop-in for the same `GestureRecognizer` task API.
- **arm64 inference:** ✅ supported. The `mediapipe` PyPI package and the
  `.task`/TFLite runtime run on aarch64 (RaspberryPi OS / Ubuntu / Debian
  aarch64), with an official Raspberry Pi gesture-recognizer example and
  community aarch64 wheels
  ([Gesture recognizer Python guide](https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer/python);
  [RPi example](https://github.com/google-ai-edge/mediapipe-samples/blob/main/examples/gesture_recognizer/raspberry_pi/recognize.py);
  [PINTO0309/mediapipe-bin aarch64 wheels](https://github.com/PINTO0309/mediapipe-bin)).
  This lines up with the Orange Pi A733 Tier-1 target. **Caveat (already flagged
  by `T-SILICON-PROBE`):** this is **CPU** inference — MediaPipe does not target
  the Allwinner/Verisilicon NPU. GPU-delegate inference is also flaky on these
  boards ([MediaPipe #4712](https://github.com/google/mediapipe/issues/4712)).
  Measure sustained FPS + core temp before standardizing the fleet.

**Important scope limit:** Model Maker's gesture recognizer **only trains static
landmark classifiers**. It has **no temporal/sequence training path**. The
MOTION head (§2) is therefore **not** a Model Maker job — it is a separate
custom model (Keras/PyTorch LSTM-or-Transformer over landmark windows, exported
to TFLite/ONNX), trained outside Model Maker. Model Maker feasibility is high
**for the static subset only**.

---

## 4. Eval harness design (D8) — mirroring `kg_extraction_eval`

Reuse the exact two-layer pattern of
[`bin/run_kg_extraction_eval.py`](../../bin/run_kg_extraction_eval.py) +
[`tests/eval/kg_extraction_eval.yaml`](../../tests/eval/kg_extraction_eval.yaml):

1. **A PURE check function** (`check_gesture_expectations`) — no model, no
   hardware — that asserts a case's `expect`-block against a produced
   prediction dict. Unit-tested in
   `tests/eval/test_gesture_eval_runner.py` (mirrors
   `test_kg_extraction_eval_runner.py`), so it runs in the normal suite with
   **no camera / no GPU**.
2. **A `run_case`** that loads the labeled clip, runs the *real* recognizer
   (stock `.task` for static; the temporal model for motion), and feeds the
   prediction to the pure checker. Needs the model + sample assets, so it runs
   **on-demand** (like the Ollama-gated KG runner), not in CI.

### Proposed `bin/run_gesture_eval.py`
- `--fixture tests/eval/gesture_eval.yaml`
- `--case <id>` to run one case
- `--head static|motion|both` (default `both`)
- Exit non-zero on any failure (CI-gate shape, identical to the KG runner).
- Static cases load a **single labeled frame** (PNG/JPG) and call the stock
  `GestureRecognizer` in `IMAGE` mode. Motion cases load a **short labeled clip**
  (≤2 s, e.g. an `.npy`/`.json` of pre-extracted landmark frames so the eval
  needs no camera and **stores no raw video** — see §5) and call the temporal
  classifier over the window.

### Proposed `tests/eval/gesture_eval.yaml` (same style as the KG yaml)
```yaml
# Gesture recognition eval (D8). Two heads:
#   - static:  single labeled frame  -> stock MediaPipe Gesture Recognizer
#   - motion:  short landmark clip   -> custom temporal classifier
# Assets are landmark arrays / de-identified frames, NEVER raw household video.
cases:
  # ---- STATIC head (stock model) ----
  - id: static-thumbs-up-confirm
    head: static
    asset: assets/static/thumbs_up_01.json   # 21 landmarks
    expect:
      gesture: Thumb_Up
      min_confidence: 0.80
      intent: confirm_pending

  - id: static-open-palm-stop
    head: static
    asset: assets/static/open_palm_03.json
    expect:
      gesture: Open_Palm
      min_confidence: 0.80
      intent: stop_cancel

  - id: static-negative-no-false-fire   # the misfire guard (D7)
    head: static
    asset: assets/static/relaxed_hand_02.json
    expect:
      gesture_in: [None]                 # a resting hand must NOT actuate
      must_not_actuate: true

  # ---- MOTION head (custom temporal) ----
  - id: motion-swipe-left-prev
    head: motion
    asset: assets/motion/swipe_left_05.npy  # landmark window
    expect:
      gesture: swipe_left
      min_confidence: 0.75
      intent: media_prev

  - id: motion-wave-not-swipe            # confusable-pair gate
    head: motion
    asset: assets/motion/wave_02.npy
    expect:
      gesture: wave
      gesture_not_in: [swipe_left, swipe_right]
```

`expect`-block keys (parallel to the KG eval's pure-matcher keys):
`gesture` / `gesture_in` / `gesture_not_in`, `intent`, `min_confidence`,
`must_not_actuate` (negative cases), and an optional per-class
`confusable_with` list.

### Accuracy-gate metric (D8 — the bar that unblocks actuation)
A gesture head is **not trusted to actuate** until, on a **held-out** labeled
set (the eval's `test` split, never the training images):

1. **Per-class recall ≥ 0.90** for every *actuating* gesture (a confirm that
   misses is annoying but safe; the recall bar keeps it usable).
2. **False-actuation rate ≤ 1%** on the **negative / `none` class** — i.e. a
   resting or unrelated hand must almost never fire an action. This is the
   safety-critical number; it pairs with the D7 guards (confidence floor +
   N-frame debounce + per-gesture cooldown + safe-action allowlist).
3. **Confusable-pair precision ≥ 0.95** on the designed confusable cases
   (swipe-left vs swipe-right; wave vs swipe; thumbs-up vs thumbs-down) — a
   *wrong* actuation (next instead of previous) is worse than a no-op.

The CI-runnable layer asserts the pure checker; the on-demand layer asserts the
real model clears these three numbers. **Both heads must clear independently
before their actuation path is wired** (D8: "accuracy gate for BOTH heads").

---

## 5. Training-data + retention plan (custom heads only)

The static stock subset (§1–2, Phase 3a) needs **no household training data** —
skip this section for that slice. This applies to **finger-count 3–5** (small
static add) and the **MOTION head** (Phase 3b), the only parts that need
captured data.

### Source the public data first (de-risk before any household capture)
- **Static finger-count 3–5:** a few hundred labeled stills per class. Public
  hand-pose sets exist, or synth/augment; Model Maker needs only stills.
- **Motion (wave / swipe / palm-up-down):** **Jester (20BN-Jester)** is the
  on-the-nose public set — **148,092 labeled clips across 27 dynamic gesture
  classes**, and its label set *already includes* "Swiping Left", "Swiping
  Right", "Thumb Up/Down", "Sliding Two Fingers Up/Down", etc., crowd-recorded
  on webcams
  ([Jester dataset](https://huggingface.co/datasets/Ishara5/20bn-jester-event);
  [Kaggle mirror](https://www.kaggle.com/datasets/toxicmender/20bn-jester)).
  Train the temporal head on **landmarks extracted from Jester** (run MediaPipe
  Hands over the clips once, keep the landmark sequences, **discard the pixels**)
  — so even the *public* training corpus is reduced to landmarks, matching
  Tier-1's privacy posture.

### Household capture (only if public data is insufficient)
- **Who/how:** an explicit, consented in-app capture flow ("teach a gesture"),
  reusing the request-response satellite pattern (like `capture_snapshot` /
  IRK-pairing): a bounded recording window per labeled class, performed by
  consenting household adults, **labeled at capture time** (the user picks the
  class before performing it — no post-hoc annotation of ambiguous video).
- **Volume:** tens of clips per person per class is plenty for fine-tuning a
  landmark temporal model; this is augmentation on top of Jester, not a
  from-scratch corpus.

### ⚠️ The raw-video-at-rest problem (eng-review additive finding) + the fix
Capturing training video **reintroduces raw video at rest** — the exact thing
Tier-1 landmark-only streaming was built to avoid (Decision 4 + Decision 7
"nothing persisted"). The retention design must neutralize this:

1. **Extract-and-discard at the edge.** The capture flow runs MediaPipe on the
   satellite (or immediately on receipt) and **persists only the landmark
   sequence** (`{ts, hands[], pose[]}` arrays) + the label. **Raw frames are
   never written to disk** — the pixels live only in a RAM buffer for the
   duration of one extraction pass, then are dropped. The stored training asset
   is landmarks, identical in shape to what Tier-1 streams.
2. **If raw frames must briefly persist** (e.g. to validate extraction quality
   once), they live in a **dedicated, access-controlled, time-boxed staging
   area, auto-purged on a short TTL** (mirror the `schicht_a_fixtures_local`
   gitignored-local pattern), **never** in the product DB, **never** committed
   to git, **never** synced off the GPU/training box.
3. **Consent + provenance.** Each captured clip carries who-consented + when;
   capture is per-person opt-in and revocable (delete their landmark sets).
   This is the DE in-home-camera consent gate the design already calls out as
   on the critical path.
4. **Eval assets are landmark-only too** (§4) — the committed `tests/eval`
   fixtures are landmark arrays / de-identified frames, so the eval corpus
   never becomes a raw-video archive in the repo.
5. **Invariant carries through to runtime:** Decision 7's "nothing persisted"
   still holds for *inference* — the training program is the **only** place
   pixels ever transiently exist, it is **offline and one-time per gesture**,
   and it produces a landmark model, not a video store.

Net: the custom path **can** be built without standing up a household
raw-video archive — but only if the capture pipeline is landmark-extract-and-
discard from day one. If that constraint can't be met, prefer Jester-only
training and skip household capture.

---

## 6. Recommendation

1. **Adopt the stock MediaPipe Gesture Recognizer for the STATIC subset as the
   first shippable slice (Phase 3a).** Palm-stop, thumbs-up, thumbs-down, and
   finger-count 1–2 map directly to canned labels — **zero training, zero
   household data, zero raw-video-at-rest, arm64-supported**. This is the
   cheapest possible path to a working command-gesture channel and should ship
   as soon as attribution (T-ATTRIB-SPIKE) and the misfire gate (T-MISFIRE)
   clear. Bind spare canned labels (`Closed_Fist`/`Victory`/`ILoveYou`)
   opportunistically via the gesture→intent config.
2. **Treat the MOTION gestures (wave, swipe L/R, palm up-down) as a separate,
   later phase (3b) with a custom temporal model.** The stock model
   *cannot* do them — this is the real reason a custom path exists, not generic
   "pretrained vs custom." Train on **Jester landmarks first**, household
   capture only if needed.
3. **Use Model Maker only for small static additions** (finger-count 3–5), not
   for motion — it has no temporal training path.
4. **Build the D8 eval harness now**, mirroring `kg_extraction_eval` (pure
   checker in CI + on-demand real-model runner), with the three-number accuracy
   gate. Gate **each head's actuation independently** behind it.
5. **The phasing changes:** split Phase 3 into 3a (static/stock, no training,
   no raw-video) and 3b (motion/custom, training program + landmark-only
   retention). 3a de-risks the whole feature and proves the actuation gate with
   none of the training-data privacy cost.

This resolves **D2**: *pretrained for everything it can do, custom temporal
only for the motion gestures it provably cannot* — and confines the
raw-video-at-rest exposure to an offline, landmark-extract-and-discard training
step for one phase, not the running system.

---

## Sources
- [MediaPipe Gesture recognition task guide (canned 8-label set, running modes)](https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer)
- [Gesture recognizer Python / Raspberry Pi guide (arm64)](https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer/python)
- [RPi gesture recognizer example](https://github.com/google-ai-edge/mediapipe-samples/blob/main/examples/gesture_recognizer/raspberry_pi/recognize.py)
- [MediaPipe aarch64 Python wheels (PINTO0309/mediapipe-bin)](https://github.com/PINTO0309/mediapipe-bin)
- [Hand gesture recognition model customization guide (Model Maker)](https://ai.google.dev/edge/mediapipe/solutions/customization/gesture_recognizer)
- [Model Maker Dataset API (folder-per-class, `none` class, landmark extraction)](https://ai.google.dev/edge/api/mediapipe/python/mediapipe_model_maker/gesture_recognizer/Dataset)
- [MediaPipe gesture_recognizer.py (running-mode source)](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/python/vision/gesture_recognizer.py)
- [MediaPipe GPU delegate flakiness on edge (#4712)](https://github.com/google/mediapipe/issues/4712)
- [MediaPipe + LSTM for dynamic gestures (Tandfonline 2025)](https://www.tandfonline.com/doi/full/10.1080/21642583.2025.2587853)
- [Kazuhito00 — keypoint classifier (static) + point-history classifier (dynamic)](https://github.com/Kazuhito00/hand-gesture-recognition-using-mediapipe/blob/main/README_EN.md)
- [Jester (20BN) dynamic gesture dataset — 148k clips, 27 classes incl. swipes](https://huggingface.co/datasets/Ishara5/20bn-jester-event)
- [Jester Kaggle mirror](https://www.kaggle.com/datasets/toxicmender/20bn-jester)
