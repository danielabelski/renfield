#!/usr/bin/env python
"""Italian "Renfield" positives + IT adversarial near-misses -> renfield_it."""
import os, sys, json, wave, random, urllib.request, io
from pathlib import Path
import numpy as np
random.seed(99)
WORK = Path("/work"); VOICES = WORK/"piper_voices"; VOICES.mkdir(exist_ok=True)
MODEL_NAME = "renfield_it"
OUT = WORK/"my_custom_model"/MODEL_NAME
for sub in ["positive_train","positive_test","negative_train","negative_test"]:
    (OUT/sub).mkdir(parents=True, exist_ok=True)
HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICE_DEFS = [
    ("it/it_IT/paola/medium/it_IT-paola-medium", False),
    ("it/it_IT/riccardo/x_low/it_IT-riccardo-x_low", False),
]
USE_CUDA = os.environ.get("PIPER_CUDA","0")=="1"
from piper.voice import PiperVoice
from piper.config import SynthesisConfig
def fetch(rel):
    base=rel.split("/")[-1]; onnx=VOICES/(base+".onnx"); cfg=VOICES/(base+".onnx.json")
    for url,dst in [(f"{HF}/{rel}.onnx",onnx),(f"{HF}/{rel}.onnx.json",cfg)]:
        if not dst.exists() or dst.stat().st_size==0:
            try: urllib.request.urlretrieve(url,dst)
            except Exception as e: print("fetch fail",base,e); return None
    return onnx,cfg
def num_speakers(cfg):
    try: return max(1,int(json.load(open(cfg)).get("num_speakers",1)))
    except: return 1
def synth(voice,text,path,ls,nsc,nw,spk):
    cfg=SynthesisConfig(length_scale=ls,noise_scale=nsc,noise_w_scale=nw,speaker_id=spk,normalize_audio=True)
    buf=io.BytesIO(); wf=wave.open(buf,"wb"); voice.synthesize_wav(text,wf,syn_config=cfg); wf.close(); buf.seek(0)
    rd=wave.open(buf,"rb"); sr=rd.getframerate(); ch=rd.getnchannels(); a=np.frombuffer(rd.readframes(rd.getnframes()),dtype=np.int16)
    if ch>1: a=a.reshape(-1,ch).mean(axis=1).astype(np.int16)
    if sr!=16000:
        import scipy.signal; a=scipy.signal.resample(a,int(len(a)*16000/sr)).astype(np.int16)
    w=wave.open(str(path),"wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(a.tobytes()); w.close()
POSITIVE = ["Renfield","Renfield.","renfield"]
ADVERSARIAL = ["Renato","Rinaldo","Manfredi","Romualdo","Reginaldo","Renzo","Renata","Rolando","Arnaldo","Gerardo",
 "campo","fieno","renna","rendere","rende","rendi","rene","freno","fronte","renfeld","vendere","fende","felpa",
 "ren","fil","filo","renne","rendono","prende","spende","tende",
 "e poi","okay","ciao","grazie","per favore","accendi","la luce","sì","no","che ora","buongiorno","va bene"]
def main():
    NP=int(os.environ.get("N_POS","8000")); NPV=int(os.environ.get("N_POS_VAL","1500"))
    NN=int(os.environ.get("N_NEG","8000")); NNV=int(os.environ.get("N_NEG_VAL","1500"))
    voices=[]
    for rel,multi in VOICE_DEFS:
        r=fetch(rel)
        if not r: continue
        onnx,cfg=r
        try: v=PiperVoice.load(str(onnx),config_path=str(cfg),use_cuda=USE_CUDA)
        except Exception as e: print("load fail",onnx.stem,e); continue
        ns=min(15, num_speakers(cfg)) if multi else 1
        voices.append((onnx.stem,v,ns)); print("loaded",onnx.stem,"spk",ns,flush=True)
    print("### %d IT voices"%len(voices),flush=True)
    def gen(n,outdir,texts,tag):
        print("### gen %d %s"%(n,tag),flush=True)
        for i in range(n):
            name,voice,ns=random.choice(voices); text=random.choice(texts)
            try: synth(voice,text,outdir/("%s_%06d.wav"%(tag,i)),random.uniform(0.75,1.35),random.uniform(0.5,0.85),random.uniform(0.6,1.0),random.randrange(ns) if ns>1 else None)
            except Exception as e:
                if i<5: print("synth err",e)
            if (i+1)%1000==0: print("  %s %d/%d"%(tag,i+1,n),flush=True)
    gen(NP,OUT/"positive_train",POSITIVE,"pos"); gen(NPV,OUT/"positive_test",POSITIVE,"posv")
    gen(NN,OUT/"negative_train",ADVERSARIAL,"neg"); gen(NNV,OUT/"negative_test",ADVERSARIAL,"negv")
    for d in ["positive_train","positive_test","negative_train","negative_test"]:
        print(" ",d,len(list((OUT/d).glob("*.wav"))),flush=True)
    print("### GEN_IT_DONE",flush=True)
if __name__=="__main__": main()
