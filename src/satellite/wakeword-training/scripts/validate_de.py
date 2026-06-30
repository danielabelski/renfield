import numpy as np, wave, glob, random
import onnxruntime as ort
from openwakeword.model import Model
random.seed(0)
MODEL = "/work/my_custom_model/renfield_de.onnx"
m = Model(wakeword_models=[MODEL], inference_framework="onnx"); key = list(m.models.keys())[0]
def score_wav(path):
    rd = wave.open(path); a = np.frombuffer(rd.readframes(rd.getnframes()), dtype=np.int16); m.reset(); mx = 0.0
    for i in range(0, max(0, len(a)-1280), 1280): mx = max(mx, m.predict(a[i:i+1280]).get(key, 0.0))
    return mx
pos = glob.glob("/work/my_custom_model/renfield_de/positive_test/*.wav"); random.shuffle(pos)
neg = glob.glob("/work/my_custom_model/renfield_de/negative_test/*.wav"); random.shuffle(neg)
ps = [score_wav(p) for p in pos[:400]]; ns = [score_wav(p) for p in neg[:400]]
# per-frame scores over 11h generic speech
val = np.load("/work/validation_set_features.npy").astype(np.float32); W=16; N=val.shape[0]; hrs=N*0.08/3600.0
sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"]); iname=sess.get_inputs()[0].name
sc = np.empty(N-W, dtype=np.float32)
for i in range(N-W):
    sc[i] = sess.run(None, {iname: val[i:i+W][None].astype(np.float32)})[0].reshape(-1)[-1]
def events(scores, thr, refr=19):
    e=0; i=0; n=len(scores)
    while i<n:
        if scores[i]>=thr: e+=1; i+=refr
        else: i+=1
    return e
print("=== RENFIELD_DE VALIDATION ===")
for t in (0.5,0.7,0.9):
    print("thr %.1f | recall %.1f%% | adversarial-FA %.1f%% | false-pos %.1f /hour (events)" % (
        t, 100*np.mean([s>=t for s in ps]), 100*np.mean([s>=t for s in ns]), events(sc,t)/hrs))
print("positive median=%.3f  adversarial median=%.3f" % (np.median(ps), np.median(ns)))
