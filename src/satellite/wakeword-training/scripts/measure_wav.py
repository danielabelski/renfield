#!/usr/bin/env python
"""Embed one wav, score the current renfield_de.onnx, report FP at thresholds."""
import numpy as np, onnxruntime as ort, wave, sys, os
from openwakeword.utils import AudioFeatures
F = AudioFeatures(device="gpu")
p = sys.argv[1]
rd = wave.open(p); a = np.frombuffer(rd.readframes(rd.getnframes()), dtype=np.int16); rd.close()
emb = np.asarray(F._get_embeddings(a)).astype(np.float32)
sess = ort.InferenceSession("/work/my_custom_model/renfield_de.onnx", providers=["CPUExecutionProvider"])
iname=sess.get_inputs()[0].name; oname=sess.get_outputs()[0].name; W=16; N=len(emb)
sc = np.array([sess.run([oname],{iname:emb[i:i+W][None]})[0].reshape(-1)[-1] for i in range(N-W)])
hrs = N*0.08/3600.0
print("%s: %.1f min, peak score %.3f" % (os.path.basename(p), N*0.08/60, sc.max()))
for THR in (0.5,0.7,0.8,0.9):
    h=int((sc>=THR).sum()); print("  thr %.1f: %d false wakes -> %.1f/h" % (THR,h,h/hrs))
