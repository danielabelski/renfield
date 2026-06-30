#!/usr/bin/env python
"""FP/hr on held-out REAL room ambient + recall on positive test, for a model.
Model ONNX has fixed batch=1 -> score one 16-frame window at a time."""
import numpy as np, onnxruntime as ort, glob, random, wave, sys, os
random.seed(0)
MODEL = sys.argv[1] if len(sys.argv) > 1 else "/work/my_custom_model/renfield_de.onnx"
mname = os.path.basename(MODEL)
val = np.load("/work/real_ambient_features.npy").astype(np.float32)
sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
iname = sess.get_inputs()[0].name; oname = sess.get_outputs()[0].name
W = 16; N = val.shape[0]; hrs = N * 0.08 / 3600.0
scores = np.empty(N - W, dtype=np.float32)
for i in range(N - W):
    out = sess.run([oname], {iname: val[i:i+W][None].astype(np.float32)})[0].reshape(-1)
    scores[i] = out[-1]
print("== %s == held-out real ambient %.2f h, peak score %.3f" % (mname, hrs, scores.max()))
for THR in (0.5, 0.7, 0.8, 0.9):
    h = int((scores >= THR).sum())
    print("  thr %.1f: REAL-AMBIENT false wakes = %d  -> %.1f/h" % (THR, h, h / hrs))
from openwakeword.model import Model
m = Model(wakeword_models=[MODEL], inference_framework="onnx"); k = list(m.models.keys())[0]
def score(p):
    rd = wave.open(p); a = np.frombuffer(rd.readframes(rd.getnframes()), dtype=np.int16); rd.close()
    m.reset(); mx = 0.0
    for i in range(0, max(0, len(a)-1280), 1280): mx = max(mx, m.predict(a[i:i+1280]).get(k, 0.0))
    return mx
pos = glob.glob("/work/my_custom_model/renfield_de/positive_test/*.wav"); random.shuffle(pos); pos = pos[:300]
ps = [score(p) for p in pos]
for THR in (0.5, 0.7, 0.8, 0.9):
    print("  thr %.1f: RECALL German Renfield = %.0f%%" % (THR, 100*np.mean([s>=THR for s in ps])))
