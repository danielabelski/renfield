#!/usr/bin/env python
"""Generate multilingual (DE+EN) 'Renfield' positives + adversarial negatives
for openWakeWord training. Writes 16kHz mono WAVs into the train.py dir layout.

Diversity sources: many piper voices incl. MULTI-SPEAKER models (de_DE-mls,
en_US-libritts_r) + per-clip length/noise/speaker variation. Adversarial
negatives = curated near-miss words (DE+EN) + random words, so the single short
word 'Renfield' learns tight boundaries (false-positive control)."""
import os, sys, json, wave, random, subprocess, urllib.request, io
from pathlib import Path
import numpy as np

random.seed(1234)
WORK = Path("/work")
VOICES = WORK / "piper_voices"
VOICES.mkdir(exist_ok=True)
MODEL_NAME = "renfield"
OUT = WORK / "my_custom_model" / MODEL_NAME
for sub in ["positive_train", "positive_test", "negative_train", "negative_test"]:
    (OUT / sub).mkdir(parents=True, exist_ok=True)

HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
# (relative path under piper-voices, is_multispeaker)
VOICE_DEFS = [
    # --- German ---
    ("de/de_DE/thorsten/high/de_DE-thorsten-high", False),
    ("de/de_DE/thorsten/medium/de_DE-thorsten-medium", False),
    ("de/de_DE/thorsten_emotional/medium/de_DE-thorsten_emotional-medium", True),
    ("de/de_DE/eva_k/x_low/de_DE-eva_k-x_low", False),
    ("de/de_DE/ramona/low/de_DE-ramona-low", False),
    ("de/de_DE/kerstin/low/de_DE-kerstin-low", False),
    ("de/de_DE/karlsson/low/de_DE-karlsson-low", False),
    ("de/de_DE/pavoque/low/de_DE-pavoque-low", False),
    ("de/de_DE/mls/medium/de_DE-mls-medium", True),   # many speakers
    # --- English (US) ---
    ("en/en_US/lessac/high/en_US-lessac-high", False),
    ("en/en_US/ryan/high/en_US-ryan-high", False),
    ("en/en_US/amy/medium/en_US-amy-medium", False),
    ("en/en_US/hfc_female/medium/en_US-hfc_female-medium", False),
    ("en/en_US/libritts_r/medium/en_US-libritts_r-medium", True),  # ~900 speakers
    # --- English (UK) ---
    ("en/en_GB/alan/medium/en_GB-alan-medium", False),
    ("en/en_GB/cori/high/en_GB-cori-high", False),
    ("en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium", False),
    ("en/en_GB/vctk/medium/en_GB-vctk-medium", True),   # ~109 UK speakers
    # --- Italian ---
    ("it/it_IT/riccardo/x_low/it_IT-riccardo-x_low", False),
    ("it/it_IT/paola/medium/it_IT-paola-medium", False),
]

def fetch(rel):
    base = rel.split("/")[-1]
    onnx = VOICES / (base + ".onnx"); cfg = VOICES / (base + ".onnx.json")
    for url, dst in [(f"{HF}/{rel}.onnx", onnx), (f"{HF}/{rel}.onnx.json", cfg)]:
        if not dst.exists() or dst.stat().st_size == 0:
            try:
                urllib.request.urlretrieve(url, dst)
            except Exception as e:
                print("  fetch FAIL", base, e); return None
    return onnx, cfg

from piper.voice import PiperVoice
from piper.config import SynthesisConfig

USE_CUDA = os.environ.get("PIPER_CUDA", "0") == "1"
def load(onnx, cfg):
    try:
        return PiperVoice.load(str(onnx), config_path=str(cfg), use_cuda=USE_CUDA)
    except Exception as e:
        print("  load FAIL", onnx.name, e); return None

def num_speakers(cfg):
    try:
        return max(1, int(json.load(open(cfg)).get("num_speakers", 1)))
    except Exception:
        return 1

def synth(voice, text, path, length_scale, noise_scale, noise_w, speaker_id):
    """Synthesize one clip via the new piper API; write as 16kHz mono PCM16."""
    cfg = SynthesisConfig(length_scale=length_scale, noise_scale=noise_scale,
                          noise_w_scale=noise_w, speaker_id=speaker_id,
                          normalize_audio=True)
    buf = io.BytesIO(); wf = wave.open(buf, "wb")
    voice.synthesize_wav(text, wf, syn_config=cfg)
    wf.close(); buf.seek(0)
    rd = wave.open(buf, "rb")
    sr = rd.getframerate(); ch = rd.getnchannels(); data = rd.readframes(rd.getnframes()); rd.close()
    a = np.frombuffer(data, dtype=np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if sr != 16000:
        import scipy.signal
        a = scipy.signal.resample(a, int(len(a) * 16000 / sr)).astype(np.int16)
    w = wave.open(str(path), "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(a.tobytes()); w.close()

POSITIVE_TEXTS = ["Renfield", "Renfield.", "renfield"]
# Adversarial near-misses (DE + EN) + fillers to tighten the boundary
ADVERSARIAL = [
    "Rennfeld", "Sennfeld", "Wendfeld", "Penfield", "Renfield", "Bernfeld",
    "Rentier", "rennen", "Renate", "Feld", "Held", "Geld", "Wendt", "Reinfeld",
    "ren feld", "renn weg", "Enfield", "Greenfield", "Garfield", "Sheffield",
    "Hennfeld", "Remfeld", "Renke", "fielt", "Fieldwork", "rinse field",
    "wann fährt", "ren", "feld renn", "Manfred", "Gottfried", "Wilfried",
]

def main():
    n_pos_train = int(os.environ.get("N_POS", "8000"))
    n_pos_test  = int(os.environ.get("N_POS_VAL", "1500"))
    n_neg_train = int(os.environ.get("N_NEG", "8000"))
    n_neg_test  = int(os.environ.get("N_NEG_VAL", "1500"))
    print("### fetching voices")
    voices = []
    for rel, multi in VOICE_DEFS:
        r = fetch(rel)
        if not r: continue
        onnx, cfg = r; v = load(onnx, cfg)
        if v is None: continue
        ns = num_speakers(cfg) if multi else 1
        voices.append((onnx.stem, v, ns))
        print(f"  loaded {onnx.stem} (speakers={ns})")
    if not voices:
        print("NO VOICES — abort"); sys.exit(1)
    de_voices = [v for v in voices if v[0].startswith("de_")]
    print(f"### {len(voices)} voices ({len(de_voices)} DE)")

    def gen(n, out_dir, texts, tag):
        print(f"### generating {n} {tag} -> {out_dir.name}")
        for i in range(n):
            name, voice, ns = random.choice(voices)
            text = random.choice(texts)
            ls = random.uniform(0.75, 1.35)
            nsc = random.uniform(0.5, 0.85)
            nw = random.uniform(0.6, 1.0)
            spk = random.randrange(ns) if ns > 1 else None
            try:
                synth(voice, text, out_dir / f"{tag}_{i:06d}.wav", ls, nsc, nw, spk)
            except Exception as e:
                if i < 5: print("  synth err", name, e)
            if (i+1) % 1000 == 0:
                print(f"  {tag} {i+1}/{n}")

    gen(n_pos_train, OUT/"positive_train", POSITIVE_TEXTS, "pos")
    gen(n_pos_test,  OUT/"positive_test",  POSITIVE_TEXTS, "posv")
    gen(n_neg_train, OUT/"negative_train", ADVERSARIAL, "neg")
    gen(n_neg_test,  OUT/"negative_test",  ADVERSARIAL, "negv")
    for d in ["positive_train","positive_test","negative_train","negative_test"]:
        print(f"  {d}: {len(list((OUT/d).glob('*.wav')))} clips")
    print("### GEN_DONE")

if __name__ == "__main__":
    main()
