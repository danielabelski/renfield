#!/usr/bin/env python3
"""Measure a satellite's capture path — per-channel + combined-mono RMS.

The validation gate for docs/design/satellite-audio-combine-pipeline.md and
its fleet audit (§7a). We only found the XVF3800 was feeding the wakeword
silence because we finally *measured* the combined-mono signal. This is that
measurement, as a repeatable tool: capture a few seconds from the mic, print
the RMS of each hardware channel and of the mono stream the wakeword would
actually receive under a given combine, so you can confirm real speech before
trusting detection — and re-run it to catch regressions.

Runs on the satellite (uses arecord + numpy). Stop the satellite service first
so the device is free:  sudo systemctl stop renfield-satellite

Examples:
  # XVF3800: capture both hw channels, see which carries the beam
  measure_capture_rms.py --device plughw:0,0 --channels 2 --seconds 6
  # Verify the configured combine (select ch0) recovers the speech
  measure_capture_rms.py --device plughw:0,0 --channels 2 --combine select --select-channel 0
"""

from __future__ import annotations

import argparse
import subprocess
import sys

import numpy as np

WIN = 4800  # 300 ms @ 16 kHz — the wakeword's rough decision window


def loudest_window_rms(mono: np.ndarray) -> float:
    if len(mono) < WIN:
        return float(np.sqrt(np.mean(mono.astype(float) ** 2))) if len(mono) else 0.0
    peaks = [
        np.sqrt(np.mean(mono[i : i + WIN].astype(float) ** 2))
        for i in range(0, len(mono) - WIN, WIN // 2)
    ]
    return float(max(peaks))


def capture(device: str, channels: int, rate: int, seconds: int) -> np.ndarray:
    """Return an (N, channels) int16 array captured via arecord."""
    cmd = [
        "arecord", "-D", device, "-f", "S16_LE", "-r", str(rate),
        "-c", str(channels), "-d", str(seconds), "-t", "raw",
    ]
    raw = subprocess.run(cmd, capture_output=True).stdout
    if not raw:
        sys.exit(f"ERROR: no audio captured from {device} (is the service stopped?)")
    return np.frombuffer(raw, dtype=np.int16).reshape(-1, channels)


def combine_mono(a: np.ndarray, mode: str, select_channel: int) -> np.ndarray:
    if a.shape[1] == 1 or mode == "passthrough":
        return a[:, 0]
    if mode == "select":
        return a[:, select_channel]
    if mode == "average":  # what a naive ALSA downmix roughly does — for contrast
        return a.mean(axis=1).astype(np.int16)
    raise SystemExit(f"unknown combine {mode}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--device", default="plughw:0,0")
    p.add_argument("--channels", type=int, default=2)
    p.add_argument("--rate", type=int, default=16000)
    p.add_argument("--seconds", type=int, default=6)
    p.add_argument("--combine", default="select", choices=["select", "average", "passthrough"])
    p.add_argument("--select-channel", type=int, default=0)
    p.add_argument(
        "--speech-floor", type=float, default=1000.0,
        help="loudest-300ms RMS below this on live speech = FAIL (fed too little signal)",
    )
    args = p.parse_args()

    print(f"Capturing {args.seconds}s from {args.device} ({args.channels}ch) — speak now.")
    a = capture(args.device, args.channels, args.rate, args.seconds)

    print("\nper-channel loudest-300ms RMS:")
    for c in range(a.shape[1]):
        print(f"  ch{c}: {loudest_window_rms(a[:, c]):8.0f}")

    if args.channels >= 2:
        print(f"  naive downmix (average): {loudest_window_rms(combine_mono(a, 'average', 0)):8.0f}"
              "   (what ALSA does — can cancel to silence)")

    mono = combine_mono(a, args.combine, args.select_channel)
    rms = loudest_window_rms(mono)
    verdict = "PASS" if rms >= args.speech_floor else "FAIL"
    label = f"{args.combine}" + (f" ch{args.select_channel}" if args.combine == "select" else "")
    print(f"\nwakeword input ({label}): loudest-300ms RMS = {rms:.0f}  → {verdict}")
    if verdict == "FAIL":
        print("  The wakeword is being fed too little signal. Pick the channel with the")
        print("  strongest per-channel RMS above, or fix the combine before trusting detection.")
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
