#!/usr/bin/env python
"""Ambient wav -> embedding stream, split 75/25 by time:
 first 75% -> windowed training hard-negatives; last 25% -> held-out FP-validation."""
import numpy as np, wave, glob, sys, os
from openwakeword.utils import AudioFeatures
F = AudioFeatures(device="gpu")

def load(p):
    rd = wave.open(p); a = np.frombuffer(rd.readframes(rd.getnframes()), dtype=np.int16); rd.close()
    return a

train_streams, val_streams = [], []
for p in sorted(glob.glob("/work/ambient/*.wav")):
    name = os.path.basename(p)
    a = load(p)
    emb = np.asarray(F._get_embeddings(a)).astype(np.float32)
    cut = int(len(emb) * 0.75)
    train_streams.append(emb[:cut]); val_streams.append(emb[cut:])
    secs = len(a) / 16000.0
    print("  %s: %.0fs emb %s (train %d, val %d)" % (name, secs, emb.shape, cut, len(emb) - cut), flush=True)
if not train_streams:
    print("NO AMBIENT WAVS in /work/ambient"); sys.exit(1)

val = np.concatenate(val_streams, axis=0)
np.save("/work/real_ambient_features.npy", val.astype(np.float32))
print("real_ambient_features (held-out FP val): %s  ~%.2f h" % (val.shape, val.shape[0]*0.08/3600), flush=True)

tr = np.concatenate(train_streams, axis=0); W, step = 16, 1
wins = np.stack([tr[i:i+W] for i in range(0, len(tr)-W, step)]).astype(np.float16)
np.save("/work/hard_neg_features.npy", wins)
print("hard_neg_features (training): %s" % (wins.shape,), flush=True)
print("HARDNEG_DONE", flush=True)
