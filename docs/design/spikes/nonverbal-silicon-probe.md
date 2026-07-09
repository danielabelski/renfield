# T-SILICON-PROBE — MediaPipe on the Orange Pi Zero 3W (Allwinner A733)

Spike for **Non-Verbal Understanding** (`docs/design/non-verbal-communication.md`).
Gates Decision 3 / Phase 4: *standardize the gesture-satellite fleet on the A733*.

**Author:** automated probe, 2026-06-27.
**Status:** desk research + read-only board inspection **complete**; one physical
measurement step (USB camera on the board) **remains** — see "REMAINING PHYSICAL STEP".

> **Honesty up front:** I cannot produce *real, measured* fps without the hardware
> and a USB camera attached. The Esszimmer node was `NotReady` / unreachable during
> this probe (it has no camera anyway). What follows is a defensible **estimate**
> from published A76-class benchmarks plus a **runnable benchmark script** for
> someone to execute on the actual board.

---

## 1. NPU-vs-CPU verdict

**Verdict: MediaPipe will run landmarking on the A76 CPU (via the TFLite XNNPACK
delegate), NOT on the A733's 3-TOPS NPU. The eng-review suspicion is confirmed.**

Reasoning, from primary sources:

- **The MediaPipe Tasks Python API exposes only two delegates.** The
  `BaseOptions.Delegate` enum is literally `CPU = 0` and `GPU = 1`. There is **no
  NPU, EdgeTPU, or NNAPI value** in the Python binding. NNAPI/EdgeTPU paths exist
  only in the Android/C++ side, and even those target Google's Edge TPU / Android
  NNAPI HAL — **not** the Allwinner/Verisilicon/Imagination stack.
  ([BaseOptions.Delegate source](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/python/core/base_options.py),
  [Delegate enum docs](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/BaseOptions/Delegate))

- **The GPU delegate is not a usable path here either.** MediaPipe's GPU inference
  needs OpenGL ES 3.1+; Google's own docs note Python GPU support is "currently
  limited to Ubuntu," and on boards without a working ES 3.1 driver MediaPipe is
  built with `MEDIAPIPE_DISABLE_GPU=1` and **silently falls back to XNNPACK on the
  CPU** — the tell is the runtime log line `Created TensorFlow Lite XNNPACK delegate
  for CPU`. The A733's GPU is an **Imagination BXM**, which is not the OpenGL-ES
  target MediaPipe's prebuilt delegate expects; realistically you get CPU.
  ([GPU Support docs](https://ai.google.dev/edge/mediapipe/framework/getting_started/gpu_support),
  [XNNPACK-fallback issue #4711](https://github.com/google/mediapipe/issues/4711),
  [Linux GPU issue #5344](https://github.com/google/mediapipe/issues/5344))

- **There is no stock route from the MediaPipe `.task` bundle to the Allwinner NPU.**
  Using the 3-TOPS NPU would require: export the underlying TFLite graphs → convert
  to the **Verisilicon/Imagination vendor format** with the Allwinner BSP toolchain
  (the `aw-nn` / VeriSilicon **acuity/ovxlib + NBG** flow, vendor-specific and
  poorly documented for the A733/A523 family), then re-implement the MediaPipe
  pre/post-processing (palm-detection → ROI → landmark crop → gesture head) around
  the converted kernels outside MediaPipe's graph runner. That is a research-grade
  port, not a delegate flag. This matches the design doc's own "NPU model
  conversion" risk and Risk note.

**Consequence for the thermal/throughput math:** landmark extraction is a **sustained
multi-thread CPU load on the 2× A76 cores** of a passively-cooled SBC — exactly the
load profile the eng review flagged. The A55 cluster (6 cores) does not help much:
MediaPipe/XNNPACK scales across threads but the heavy palm-detection + landmark
convs are throughput-bound on the big cores, and pinning everything to the A55s is
slower. Plan for the A76 pair to run hot under continuous inference — which is the
direct argument for the design's **T2 "gesture-gated, bounded-window" capture**
(spin up on trigger, run a short window, sleep) rather than 24/7 streaming.

---

## 2. fps estimate (A76-class CPU, no NPU)

**Reference points (published, CPU-only):**

| Workload | Hardware | Measured fps | Source |
|---|---|---|---|
| Gesture Recognizer (hand landmark + gesture head) | Raspberry Pi 5 (4× A76 @ 2.4 GHz) | **5–8 fps** | [gesturebot](https://github.com/mvipin/gesturebot) |
| Full 33-landmark Pose | Raspberry Pi 5 (4× A76 @ 2.4 GHz) | **~6.1 fps** | [Hackaday pose analysis](https://lb.lax.hackaday.io/project/203704/log/242569-mediapipe-pose-detection-real-time-performance-analysis) |
| MediaPipe Hands | mobile-class SoC, GPU/optimized | designed for ~30 Hz | [MediaPipe Hands docs](https://mediapipe.readthedocs.io/en/latest/solutions/hands.html) |

**Scaling to the A733 (2× A76 @ 2.0 GHz):**

The Pi 5 has **4× A76 @ 2.4 GHz**; the A733 has **2× A76 @ 2.0 GHz**. That is roughly
**half the big-core count** and **~0.83× the clock** → expect on the order of
**0.4–0.5× the Pi 5's multithreaded MediaPipe throughput** for the parts that scale
with the big cores (palm detection + landmark convs), partially offset if XNNPACK can
usefully spill lighter stages onto the A55 cluster.

**Estimated range on the A733, CPU-only (Hand Landmarker / Gesture Recognizer):**

> **~3–5 fps** (single hand, 256-px landmark input), best case nudging higher with
> aggressive threading and a warm tracking state (MediaPipe skips palm-detection on
> frames where it can track the previous hand, which lifts steady-state fps).

Caveats that move the number:
- **Tracking vs detection:** steady-state (hand already tracked) is markedly faster
  than re-detection every frame; real-world fps is bimodal.
- **Thermal throttle:** sustained load on a passively-cooled A733 will down-clock;
  the *sustained* fps is what matters, not the first-10-second burst. This is the
  whole point of measuring core temp before/after (script below).
- **Two hands / pose / holistic:** Holistic (the design's MediaPipe Holistic call)
  is **substantially heavier** than Hand Landmarker alone — expect the low end of the
  range or below if pose+hands+face all run per frame. For a command-gesture MVP,
  **Hand Landmarker alone** is the right first measurement.

**Is ~3–5 fps "real-time enough"?** For *command gestures* (palm-stop, thumbs-up,
swipe, finger-count) over a bounded ~1–2 s window: **plausibly yes** — these are
short motions; 3–5 fps × a debounce window gives enough frames to classify, and the
design already accepts ~1 s spin-up latency (T2). For *continuous body-language*
reading at smooth cadence: **marginal** — fine as a coarse per-window read, not for
anything needing fluid temporal resolution. **The probe must confirm the *sustained*
(post-thermal) number clears the gesture-window requirement on real hardware.**

---

## 3. arm64 install feasibility

**Feasible, but not a clean `pip install mediapipe`.**

- **No official PyPI wheel for `linux/aarch64`.** The `mediapipe` project ships
  manylinux **x86_64** wheels and Android/iOS artifacts; arm64 Linux has been a
  long-standing gap. ([no-arm64-wheels issue #5965](https://github.com/google-ai-edge/mediapipe/issues/5965),
  [mediapipe on PyPI](https://pypi.org/project/mediapipe/))
- **Community prebuilt wheels exist** and are the pragmatic path:
  - **PINTO0309/mediapipe-bin** — prebuilt wheels for RaspberryPi OS aarch64, Ubuntu
    aarch64, Debian aarch64, Jetson. ([mediapipe-bin](https://github.com/PINTO0309/mediapipe-bin))
  - **jiuqiant/mediapipe_python_aarch64** — older aarch64 wheels.
    ([repo](https://github.com/jiuqiant/mediapipe_python_aarch64))
- **Building from source** is the fallback: Google publishes an aarch64 build
  pipeline (`python3-dev`, `cmake`, `protobuf-compiler`, `openjdk-11-jdk-headless`,
  bazel). ([build_python docs](https://ai.google.dev/edge/mediapipe/solutions/build_python))

**Practical notes for the A733 / Orange Pi "Jammy" image (Ubuntu 22.04 aarch64,
kernel 6.6.98-sun60iw2 — confirmed in §4):**
- Match the wheel's **glibc / Python ABI** to the image (Ubuntu 22.04 → Python 3.10;
  pick a `cp310` aarch64 wheel or build for it).
- Needs `libgl1`, `libglib2.0-0`, and the usual OpenCV runtime deps.
- The newer **`mediapipe` Tasks** API (`mediapipe.tasks.python.vision`) is what the
  design targets; verify the community wheel is recent enough to include
  `HandLandmarker`/`GestureRecognizer` Tasks (older 0.8.x wheels predate Tasks).
- Inside a **k8s pod** (the Esszimmer model), this is a container-image build
  concern, not a host pip install — bake the wheel + `/dev/video*` mount into the
  satellite image, same pattern as the existing `ctr -n k8s.io images import` flow.

---

## 4. Board inspection findings (read-only)

Performed via `kubectl --context renfield-private` (read-only; **no** package
install, **no** pod/node mutation).

| Check | Result |
|---|---|
| Node | `orangepizero3w` @ `192.168.1.82` |
| **Architecture** | **`arm64`** (confirmed `.status.nodeInfo.architecture`) |
| OS image | `Orange Pi 1.0.0 Jammy` (Ubuntu 22.04 base) |
| Kernel | `6.6.98-sun60iw2` (Allwinner "sun60iw2" = A733 family BSP kernel) |
| Node status | **`NotReady`** — `Ready=Unknown (NodeStatusUnknown)`, Mem/Disk/PID pressure all `Unknown` |
| Pod | `satellite-esszimmer-*`: one replica **`Terminating`** on the node, a new one **`Pending`** (unschedulable while node down) |
| **`kubectl exec` for live thermal/CPU** | **FAILED — `dial tcp 192.168.1.82:10250: connect: no route to host`.** The node/kubelet is unreachable, so I could **not** read `/sys/class/thermal/thermal_zone*/temp`, `nproc`, or probe `/dev/video*` from inside the running pod. |
| **Camera device** | **None.** `k8s/satellite-esszimmer.yaml` mounts only `/dev/snd` and `/dev/bus/usb` (+ `/run/dbus`, `/var/lib/bluetooth`). There is **no `/dev/video*` mount** → no camera exposed to the pod, exactly as the design doc states. A USB UVC camera + a `/dev/video*` (or `/dev/bus/usb` is already mounted, so a UVC device may enumerate) mount is required. |
| Pod CPU budget | `resources.limits.cpu: "2"`, `requests: 250m` — the current pod is **capped at 2 cores**. If MediaPipe runs in this pod, **raise the CPU limit** (it wants both A76s + spill threads) or the cgroup throttles inference independently of thermals. |

**This matches the design's "Hardware capability finding":** A733 is arm64, has no
camera today, and the node is the same passively-cooled board that has gone
`NotReady` repeatedly (the design and `CLAUDE.md` both note the node-resilience
history — consistent with a board that will also thermally throttle under sustained
CV load).

> The thermal-zone and live-fps numbers are **exactly** the data this probe cannot
> get remotely. They are the physical step below.

---

## 5. Benchmark script (run on the board with a USB camera)

Save as `bin/silicon_probe_mediapipe.py`. Measures **landmark fps over N frames** and
reads **core temperature before/after** (so you see thermal throttle). Works off a
USB camera (`--source 0`) or a sample video (`--source path.mp4`). CPU-only by design
(that is what the board will actually run — see §1).

```python
#!/usr/bin/env python3
"""T-SILICON-PROBE: MediaPipe Hand Landmarker fps + core-temp probe.

Run ON the Orange Pi Zero 3W (A733) with a USB UVC camera attached.

  pip install mediapipe opencv-python    # arm64 wheel — see report §3
  # download the model bundle once:
  #   wget -O hand_landmarker.task \
  #     https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
  python3 silicon_probe_mediapipe.py --source 0 --frames 600 --model hand_landmarker.task

Reports: warm-up fps, sustained fps, per-stage detection rate, and core temp
before/after so thermal throttle is visible. CPU-only (BaseOptions.Delegate.CPU) —
that is what this board actually runs; there is no NPU delegate (report §1).
"""
import argparse, glob, statistics, time

import cv2  # opencv-python
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision


def read_core_temp_c():
    """Best-effort: warmest thermal zone in °C, or None. Allwinner exposes
    /sys/class/thermal/thermal_zone*/temp in milli-°C."""
    temps = []
    for p in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            with open(p) as f:
                temps.append(int(f.read().strip()) / 1000.0)
        except (OSError, ValueError):
            pass
    return max(temps) if temps else None


def list_thermal_zones():
    out = []
    for tp in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        try:
            with open(f"{tp}/type") as f:
                ztype = f.read().strip()
        except OSError:
            ztype = "?"
        out.append(f"{tp.split('/')[-1]}={ztype}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="0",
                    help="USB camera index (e.g. 0) or a video file path")
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--model", default="hand_landmarker.task")
    ap.add_argument("--num-hands", type=int, default=1)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    args = ap.parse_args()

    print("Thermal zones:", list_thermal_zones() or "NONE FOUND")
    t_before = read_core_temp_c()
    print(f"Core temp BEFORE: {t_before} °C" if t_before else "Core temp BEFORE: unavailable")

    base = mp_python.BaseOptions(
        model_asset_path=args.model,
        delegate=mp_python.BaseOptions.Delegate.CPU,  # the only realistic delegate here
    )
    options = vision.HandLandmarkerOptions(
        base_options=base,
        num_hands=args.num_hands,
        running_mode=vision.RunningMode.VIDEO,  # temporal — matches the design's pipeline
    )
    landmarker = vision.HandLandmarker.create_from_options(options)

    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open source {args.source!r} — is the USB camera attached?")

    per_frame_ms, detections = [], 0
    n, warmup = 0, 30  # discard the first 30 frames (model warm-up / first detection)
    ts0 = time.time()
    wall_start = None

    while n < args.frames + warmup:
        ok, frame = cap.read()
        if not ok:
            print("Source ended early."); break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        ts_ms = int((time.time() - ts0) * 1000)

        t0 = time.perf_counter()
        result = landmarker.detect_for_video(mp_image, ts_ms)
        dt = (time.perf_counter() - t0) * 1000.0

        n += 1
        if n <= warmup:
            continue
        if wall_start is None:
            wall_start = time.perf_counter()
        per_frame_ms.append(dt)
        if result.hand_landmarks:
            detections += 1

    wall = time.perf_counter() - wall_start if wall_start else 0.0
    cap.release()
    t_after = read_core_temp_c()

    measured = len(per_frame_ms)
    print("\n===== T-SILICON-PROBE RESULTS =====")
    if measured:
        infer_fps = 1000.0 / statistics.mean(per_frame_ms)
        wall_fps = measured / wall if wall else float("nan")
        print(f"Frames measured (post-warmup): {measured}")
        print(f"Inference-only fps (1000/mean latency): {infer_fps:.2f}")
        print(f"End-to-end wall fps (incl. capture): {wall_fps:.2f}")
        print(f"Per-frame inference ms  mean={statistics.mean(per_frame_ms):.1f} "
              f"p50={statistics.median(per_frame_ms):.1f} "
              f"p95={statistics.quantiles(per_frame_ms, n=20)[18]:.1f} "
              f"max={max(per_frame_ms):.1f}")
        print(f"Hand-detected frames: {detections}/{measured} "
              f"({100*detections/measured:.0f}%)")
    else:
        print("No frames measured.")
    print(f"Core temp BEFORE: {t_before} °C   AFTER: {t_after} °C   "
          f"Δ: {None if (t_before is None or t_after is None) else round(t_after - t_before, 1)} °C")
    print("Note: CPU-only (no NPU delegate exists for this board — report §1).")
    print("Watch for fps decay across a long run = thermal throttle.")


if __name__ == "__main__":
    main()
```

**How to read the output:**
- **Sustained (not warm-up) inference fps** is the headline. Compare against the
  §2 estimate (~3–5 fps) and against the gesture-window requirement.
- **Core-temp Δ** + any fps decay over the run = thermal throttle. Run it for several
  minutes (`--frames 2000+`) to capture the *sustained* (throttled) state, since the
  design's whole "gesture-gated bounded window" decision hinges on whether the board
  can hold the rate without continuous-load throttling.
- **Hand-detected %** sanity-checks the camera/lighting (don't trust fps from frames
  with no hand — re-detection vs tracking changes the cost profile).

For a Holistic / pose comparison, swap in `PoseLandmarker` / the Holistic graph; it
will be **slower** (§2) — measure Hand Landmarker first as the MVP target.

---

## REMAINING PHYSICAL STEP

**One hands-on step is required and was not doable remotely:**

1. **Attach a USB UVC camera** to the Orange Pi Zero 3W (the board has no camera and
   the Pi CSI module is unsupported on the A733).
2. **Bring the node back `Ready`** (it was `NotReady`/unreachable during this probe —
   power-cycle per the documented node-resilience procedure, or run the probe on a
   bench A733 outside k8s).
3. **Expose the camera + raise CPU** if running in the pod: add a `/dev/video*` mount
   (or rely on the existing `/dev/bus/usb` UVC enumeration) and bump
   `resources.limits.cpu` above `2` for the test; or just run on the host venv.
4. **Install MediaPipe** via a community aarch64 wheel (§3).
5. **Run the script** for a multi-minute session and record: sustained fps + core-temp
   Δ + any fps decay (throttle).

**Decision gate this feeds:** if sustained CPU-only fps holds in/above the ~3–5 fps
estimate *without* runaway thermal throttle across a bounded gesture window, the A733
**plausibly clears real-time for command gestures** and standardizing the fleet on it
is justified — **on the CPU, with the NPU as a later, separate conversion spike, not a
v1 assumption.** If the sustained number collapses under throttle, the design's Tier-2
(video → GPU backend) fallback or active cooling becomes mandatory for that room.

---

## Sources

- MediaPipe Python `BaseOptions.Delegate` source (CPU=0, GPU=1 only): https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/python/core/base_options.py
- `BaseOptions.Delegate` enum docs: https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/BaseOptions/Delegate
- MediaPipe GPU Support (OpenGL ES 3.1+, Ubuntu-limited, `MEDIAPIPE_DISABLE_GPU`): https://ai.google.dev/edge/mediapipe/framework/getting_started/gpu_support
- XNNPACK CPU-fallback behavior (issue #4711): https://github.com/google/mediapipe/issues/4711
- Python Linux GPU limitation (issue #5344): https://github.com/google/mediapipe/issues/5344
- Pi 5 gesture recognition 5–8 fps (gesturebot): https://github.com/mvipin/gesturebot
- Pi 5 pose ~6.1 fps (Hackaday): https://lb.lax.hackaday.io/project/203704/log/242569-mediapipe-pose-detection-real-time-performance-analysis
- MediaPipe Hands "30 Hz CPU" design target: https://mediapipe.readthedocs.io/en/latest/solutions/hands.html
- No official arm64 wheels (issue #5965): https://github.com/google-ai-edge/mediapipe/issues/5965
- mediapipe on PyPI: https://pypi.org/project/mediapipe/
- PINTO0309/mediapipe-bin (aarch64 prebuilt wheels): https://github.com/PINTO0309/mediapipe-bin
- jiuqiant/mediapipe_python_aarch64: https://github.com/jiuqiant/mediapipe_python_aarch64
- Build MediaPipe Python (aarch64 from source): https://ai.google.dev/edge/mediapipe/solutions/build_python
- Gesture Recognizer task guide: https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer
