#!/usr/bin/env bash
#
# XVF3800 on-device tuning / discovery helper.
#
# The XVF3800 runs AEC, beamforming, DoA, AGC and noise-suppression on-chip; the
# parameters live in device flash and are read/written over USB with `xvf_host`.
# Use this on the satellite (Pi) to discover the real command set, read the
# current pipeline state, A/B a tuning profile, and persist or revert it.
#
# It must run from the directory holding xvf_host + libcommand_map.so +
# libdevice_usb.so (that's why it lives alongside them and chdir's to itself).
#
# Usage:
#   ./tune.sh list                 # all control commands (xvf_host --list-commands)
#   ./tune.sh doa                  # live Direction-of-Arrival (azimuth + speech energy)
#   ./tune.sh dump                 # read current AGC / NS / AEC / gain values
#   ./tune.sh get  <CMD>           # read one command, e.g. ./tune.sh get PP_AGCMAXGAIN
#   ./tune.sh set  <CMD> <VALUE>   # write one command (RAM only until `save`)
#   ./tune.sh save                 # persist current config to flash
#   ./tune.sh reset                # clear flash config -> firmware defaults (reboot after)
#   ./tune.sh watch-doa            # stream DoA at ~4 Hz (Ctrl-C to stop)
#
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
XVF=./xvf_host

# The post-processing / AEC / gain params worth A/B-ing for a room. Names+ranges
# per the XMOS XVF3800 v3.2.1 control-command appendix.
READBACK_PARAMS=(
  PP_AGCONOFF PP_AGCMAXGAIN PP_AGCGAIN PP_AGCDESIREDLEVEL
  PP_MIN_NS PP_MIN_NN PP_ECHOONOFF
  AUDIO_MGR_MIC_GAIN AUDIO_MGR_REF_GAIN AUDIO_MGR_SYS_DELAY
  AEC_HPFONOFF
)

cmd="${1:-help}"
case "$cmd" in
  list)
    $XVF --list-commands
    ;;
  doa)
    echo "AEC_AZIMUTH_VALUES (beam1, beam2, free-running, auto-selected=DoA):"
    $XVF AEC_AZIMUTH_VALUES
    echo "AEC_SPENERGY_VALUES (per-beam speech energy; >0 = speech):"
    $XVF AEC_SPENERGY_VALUES
    ;;
  watch-doa)
    echo "Streaming DoA (Ctrl-C to stop)..."
    while true; do
      printf '%s  ' "$(date +%H:%M:%S)"
      $XVF AEC_AZIMUTH_VALUES
      sleep 0.25
    done
    ;;
  dump)
    for p in "${READBACK_PARAMS[@]}"; do
      printf '%-22s ' "$p"
      $XVF "$p" || echo "(unavailable)"
    done
    ;;
  get)
    [ $# -ge 2 ] || { echo "usage: $0 get <CMD>" >&2; exit 2; }
    $XVF "$2"
    ;;
  set)
    [ $# -ge 3 ] || { echo "usage: $0 set <CMD> <VALUE>" >&2; exit 2; }
    $XVF "$2" "$3"
    echo "set $2 = $3 (RAM only — run '$0 save' to persist)"
    ;;
  save)
    $XVF save_configuration 1
    echo "configuration saved to flash"
    ;;
  reset)
    $XVF clear_configuration 1
    echo "flash config cleared — reboot the satellite to load firmware defaults"
    ;;
  *)
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
