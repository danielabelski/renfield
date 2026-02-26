# Satellite Camera — Visual Queries

Visual queries allow satellites with a camera to capture a snapshot at wakeword detection
and send it alongside the transcribed speech to a Vision-LLM for image-aware responses.

## Use Case

> "Hey Renfield, was steht auf diesem Zettel?"

1. Wakeword detected -> camera snapshot captured in background
2. User finishes speaking -> audio transcribed, snapshot attached to `audio_end` message
3. Backend receives image + text -> routes to Vision-LLM (e.g. `minicpm-v`)
4. Vision-LLM describes what it sees -> TTS response spoken back

## Architecture

```
Satellite                              Backend
--------                              -------
Wakeword detected
  |-> camera.capture() (async)
  |-> Start listening
  ...user speaks...
  |-> End listening
  |-> Collect snapshot
  |-> send audio_end(image=base64)  -->  satellite_handler
                                           |-> Whisper STT
                                           |-> Intent extraction (text-only)
                                           |-> Response generation:
                                           |     image present + vision model?
                                           |       YES -> chat_stream_with_image()
                                           |       NO  -> chat_stream()
                                           |-> Piper TTS
                                     <--  |-> send tts_audio
```

## Setup

### 1. Satellite Configuration

In `satellite.yaml` (or via Ansible provisioning):

```yaml
camera:
  enabled: true
  resolution: "1280x720"  # optional, default
  quality: 85             # JPEG quality, optional
```

Requires `rpicam-still` to be available on the Pi (standard with Raspberry Pi OS).

### 2. Backend Configuration

Set in `.env`:

```bash
OLLAMA_VISION_MODEL=minicpm-v
```

Then pull the model:

```bash
ollama pull minicpm-v
```

If `OLLAMA_VISION_MODEL` is empty (default), visual queries are disabled and the
image is silently ignored — the satellite still captures but the backend uses the
standard text-only chat model.

### 3. Provisioning

The Ansible provisioning supports camera via `camera_enabled`:

- `group_vars/satellites.yml`: `camera_enabled: false` (default for all satellites)
- `host_vars/satellite-arbeitszimmer.yml`: `camera_enabled: true` (per-satellite override)

## Supported Hardware

- **IMX219** (Pi Camera Module v2) — tested on satellite-arbeitszimmer
- Any camera supported by `rpicam-still` (Pi Camera Module v3, HQ Camera, etc.)

## How It Works

### Timing

The snapshot is triggered immediately on wakeword detection, before the user starts
speaking. By the time the user finishes their question (typically 2-5 seconds), the
snapshot is already captured and ready. This means zero additional latency.

### Graceful Degradation

- No camera hardware -> `camera.open()` returns False -> camera set to None
- `rpicam-still` not installed -> same as above
- Capture fails -> snapshot is None -> standard text-only response
- No vision model configured -> image silently ignored
- Other satellites without camera -> completely unaffected

### WebSocket Protocol

The existing `audio_end` message gains an optional `image` field:

```json
{
  "type": "audio_end",
  "session_id": "sat-arbeitszimmer-1234567890",
  "reason": "silence",
  "image": "<base64-encoded JPEG>"
}
```

No new message types are introduced. Satellites without cameras simply omit the field.

## Vision Models

Tested models:

| Model | Size | Speed | Quality |
|-------|------|-------|---------|
| `minicpm-v` | 5.5GB | ~10s | Good for text reading, scene description |
| `llava:7b` | 4.7GB | ~8s | Good general vision |
| `llava:13b` | 8.0GB | ~15s | Better quality, slower |

Choose based on your hardware. On `cuda.local` with GPU, `minicpm-v` provides a good
balance of speed and quality.
