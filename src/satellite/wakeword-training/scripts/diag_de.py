import numpy as np, wave, io, glob, json
from openwakeword.model import Model
from piper.voice import PiperVoice
from piper.config import SynthesisConfig
M="/work/my_custom_model/renfield_de.onnx"
m=Model(wakeword_models=[M], inference_framework="onnx"); key=list(m.models.keys())[0]
def score_arr(a):
    m.reset(); mx=0
    for i in range(0,max(0,len(a)-1280),1280): mx=max(mx,m.predict(a[i:i+1280]).get(key,0))
    return mx
def synth(v,spk):
    import random
    cfg=SynthesisConfig(length_scale=random.uniform(0.95,1.1),noise_scale=0.667,noise_w_scale=0.8,speaker_id=spk)
    buf=io.BytesIO(); wf=wave.open(buf,"wb"); v.synthesize_wav("Renfield",wf,syn_config=cfg); wf.close(); buf.seek(0)
    rd=wave.open(buf,"rb"); sr=rd.getframerate(); a=np.frombuffer(rd.readframes(rd.getnframes()),dtype=np.int16)
    if sr!=16000:
        import scipy.signal; a=scipy.signal.resample(a,int(len(a)*16000/sr)).astype(np.int16)
    return a
import random; random.seed(1)
VOICES=[("de_DE-thorsten-high",1),("de_DE-thorsten-medium",1),("de_DE-thorsten_emotional-medium",8),
 ("de_DE-eva_k-x_low",1),("de_DE-ramona-low",1),("de_DE-kerstin-low",1),("de_DE-karlsson-low",1),
 ("de_DE-pavoque-low",1),("de_DE-mls-medium",236)]
print("=== per-voice recall @ NATURAL pace (thr 0.5) ===")
allscores=[]
for name,ns in VOICES:
    v=PiperVoice.load(f"/work/piper_voices/{name}.onnx",config_path=f"/work/piper_voices/{name}.onnx.json")
    sc=[score_arr(synth(v, random.randrange(ns) if ns>1 else None)) for _ in range(15)]
    allscores+=sc
    print("  %-32s recall %3.0f%%  median %.2f" % (name, 100*np.mean([s>=0.5 for s in sc]), np.median(sc)))
print("OVERALL natural-pace recall: %.0f%%" % (100*np.mean([s>=0.5 for s in allscores])))
