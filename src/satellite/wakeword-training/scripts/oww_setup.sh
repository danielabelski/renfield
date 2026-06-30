#!/bin/bash
# Env setup for openWakeWord German "Renfield" training — runs INSIDE the pod.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
echo "### apt deps"
apt-get update -qq
apt-get install -y -qq git wget curl ffmpeg libsndfile1 sox unzip ca-certificates >/dev/null
echo "### clone openWakeWord + piper-sample-generator"
cd /work
[ -d openWakeWord ] || git clone --depth 1 https://github.com/dscripka/openWakeWord.git
[ -d piper-sample-generator ] || git clone --depth 1 https://github.com/rhasspy/piper-sample-generator.git
echo "### pip: openwakeword + training stack"
pip install -q --no-input openwakeword
# openWakeWord training-time deps (the automatic_model_training pipeline)
pip install -q --no-input \
  piper-phonemize-cross || pip install -q --no-input piper-phonemize || true
pip install -q --no-input \
  onnx onnxruntime mutagen torchinfo torchmetrics speechbrain==0.5.16 \
  audiomentations acoustics datasets scipy pronouncing tqdm pyyaml \
  webrtcvad soundfile librosa || true
echo "### piper-sample-generator generator model (LibriTTS multispeaker, for length/prosody variation)"
cd /work/piper-sample-generator
mkdir -p models
[ -f models/en_US-libritts_r-medium.pt ] || \
  wget -q -O models/en_US-libritts_r-medium.pt \
  https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt || true
echo "### versions"
python -c "import openwakeword, torch; print('openwakeword', getattr(openwakeword,'__version__','?'),'torch',torch.__version__)"
echo "### DONE setup"
