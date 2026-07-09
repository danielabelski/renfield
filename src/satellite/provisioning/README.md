# Renfield Satellite Provisioning

Ansible playbook for provisioning Raspberry Pi Zero 2 W satellites with ReSpeaker audio HATs.

## Supported Hardware

| HAT | `hat_type` | LEDs | SPI | Power Pin | ALSA Card |
|-----|-----------|------|-----|-----------|-----------|
| ReSpeaker 2-Mic HAT V1 (WM8960) | `2mic` | 3 | 0:0 | — | `seeed2micvoicec` |
| ReSpeaker 2-Mic HAT V2 (TLV320AIC3104) | `2mic-v2` | 3 | 0:0 | — | `seeed2micvoicec` |
| ReSpeaker 4-Mic Array | `4mic` | 12 | 0:1 | GPIO 5 | `seeed4micvoicec` |

## Prerequisites

On your **control machine** (Mac/Linux):

```bash
pip install ansible
```

On the **Pi** (fresh Raspberry Pi OS image):
- SSH enabled, user configured
- Network connected (Wi-Fi or Ethernet)
- SPI and I2C enabled in `raspi-config`

## Quick Start

Provision a single satellite:

```bash
cd src/satellite/provisioning
ansible-playbook -i inventory.yml provision.yml --limit satellite-fitnessraum -v
```

Provision all satellites:

```bash
ansible-playbook -i inventory.yml provision.yml -v
```

## Tags

Run individual phases:

```bash
# Just update the config
ansible-playbook -i inventory.yml provision.yml --limit satellite-fitnessraum --tags config

# Just redeploy code
ansible-playbook -i inventory.yml provision.yml --limit satellite-fitnessraum --tags app

# Just update models
ansible-playbook -i inventory.yml provision.yml --limit satellite-fitnessraum --tags models

# Just (re)apply WM8960 audio-mixer tuning (ALC + mic input path) — restart-free
ansible-playbook -i inventory.yml provision.yml --limit satellite-wohnzimmer --tags audio
```

Available tags: `system`, `boot`, `driver`, `python`, `app`, `config`, `models`, `service`, `audio`

### WM8960 audio tuning (`2mic` / Whisplay)

openWakeWord scores on raw amplitude, so WM8960-based HATs need mixer tuning in
front of the model (`--tags audio`, restart-free):

- **ALC** (`wm8960_alc_enabled`, + `wm8960_alc_*` / `wm8960_noise_gate_*`) —
  hardware AGC that lifts quiet/far speech toward a target level.
- **Input capture path** (`wm8960_input_path_enabled`, + `wm8960_input_boost`
  0–3) — routes the mics to the ADC (unmutes the input path) and sets the
  **pre-ALC** Input Boost. The Seeed 2-Mic (`2mic`) ships this **muted / maxed**,
  which starves the ADC — a `2mic` sat that never fires a wakeword is almost
  certainly this. An SNR sweep found the maxed boost (+29 dB) buries speech in
  ALC-amplified noise (SNR ~4.6 dB); `wm8960_input_boost: 1` (+13 dB) → ~17.8 dB.
  The ALC normalizes loudness, so the **Input Boost, not the PGA, is the SNR
  lever**. NB: `2mic-v2` is a different codec (TLV320AIC3x) — these vars don't
  apply. Persisted on-device by `alsactl store`.

## Dry Run

Preview changes without applying:

```bash
ansible-playbook -i inventory.yml provision.yml --limit satellite-fitnessraum --check -v
```

## HiFiBerry DLNA Renderers (http TTS)

The `hifiberry` inventory group + `provision-hifiberry.yml` are **separate from
the satellites** — a HiFiBerry is a DLNA music renderer, not a Renfield
satellite. The playbook pins `renfield.local` in `/etc/hosts` so the HiFiBerry's
gstreamer can fetch backend-served **http** TTS audio (relay/announce). Without
it, TTS to the HiFiBerry fails *silently* (the renderer reports `playing` but
never fetches the URL).

Why only the HiFiBerry: Linn/openHome renderers resolve `renfield.local` via DNS
natively; the HiFiBerry's systemd-resolved hijacks `.local` as mDNS (NOTFOUND
before DNS), so gstreamer's getaddrinfo can't resolve it. (TTS delivery is http,
so no TLS/CA step is needed — that was removed when delivery moved off the
self-signed https URL.) Full background: `docs/MESSAGE_RELAY.md` → "TTS audio
delivery to renderers".

```bash
cd src/satellite/provisioning
# Login user is root (HiFiBerryOS has no sudo) → --ask-pass (password: hifiberry)
ansible-playbook -i inventory.yml provision-hifiberry.yml --ask-pass --limit hifiberry-arbeitszimmer

# Dry run
ansible-playbook -i inventory.yml provision-hifiberry.yml --ask-pass --check
```

Idempotent. Tags: `hosts`. **Re-run after a HiFiBerryOS update** — an OS update
wipes the `/etc/hosts` edit.

## Adding a New Satellite

1. Add the host to `inventory.yml`
2. Create `host_vars/<hostname>.yml` with HAT-specific settings
3. Run the playbook

## Host Variables

| Variable | Description | 2-mic default | 4-mic default |
|----------|-------------|---------------|---------------|
| `hat_type` | `"2mic"`, `"2mic-v2"`, or `"4mic"` | `"2mic"` | `"4mic"` |
| `satellite_id` | Unique satellite ID | — | — |
| `satellite_room` | Room name | — | — |
| `led_num` | Number of APA102 LEDs | `3` | `12` |
| `led_spi_device` | SPI device number | `0` | `1` |
| `led_power_pin` | GPIO for LED power | `null` | `5` |
| `audio_device` | ALSA capture device | `"capture"` | `"default"` |
| `audio_playback_device` | ALSA playback device | `"plughw:0,0"` | `"default"` |
| `audio_channels` | Recording channels | `2` | `1` |
| `beamforming_enabled` | Delay-and-Sum beamforming | `true` | `false` |

## Safety Notes

- The playbook uses `systemctl start`, not `restart` — safe for first provisioning
- Each step is idempotent — safe to re-run
- Driver installation triggers a reboot (handled automatically)
- For code-only updates, use `--tags app` to skip hardware steps

## Verification

After provisioning, check:

```bash
# Sound card detected?
ssh satellite-fitnessraum.local "cat /proc/asound/cards"

# Service running?
ssh satellite-fitnessraum.local "sudo journalctl -u renfield-satellite -n 30"

# Expected log lines:
#   LED power enabled on GPIO5       (4-mic only)
#   SPI opened: bus 0, device 1      (4-mic only)
#   Connected to server
```
