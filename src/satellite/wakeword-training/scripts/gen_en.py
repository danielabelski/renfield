#!/usr/bin/env python
"""Combined US+UK English "Renfield" positives + EN adversarial near-misses -> renfield_en."""
import os, sys, json, wave, random, urllib.request, io
from pathlib import Path
import numpy as np
random.seed(99)
WORK = Path("/work"); VOICES = WORK/"piper_voices"; VOICES.mkdir(exist_ok=True)
MODEL_NAME = "renfield_en"
OUT = WORK/"my_custom_model"/MODEL_NAME
for sub in ["positive_train","positive_test","negative_train","negative_test"]:
    (OUT/sub).mkdir(parents=True, exist_ok=True)
HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICE_DEFS = [
    ("en/en_US/lessac/high/en_US-lessac-high", False),
    ("en/en_US/ryan/high/en_US-ryan-high", False),
    ("en/en_US/amy/medium/en_US-amy-medium", False),
    ("en/en_US/hfc_female/medium/en_US-hfc_female-medium", False),
    ("en/en_US/libritts_r/medium/en_US-libritts_r-medium", True),  # cap 15
    ("en/en_GB/alan/medium/en_GB-alan-medium", False),
    ("en/en_GB/cori/high/en_GB-cori-high", False),
    ("en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium", False),
    ("en/en_GB/vctk/medium/en_GB-vctk-medium", True),  # cap 15
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
ADVERSARIAL = ["Enfield","Greenfield","Garfield","Sheffield","Penfield","Springfield","Mansfield","Redfield",
 "Winfield","Canfield","Benfield","Hatfield","Mayfield","Whitfield","Caulfield","Wakefield","Litchfield",
 "Renfrew","Renfro","Renford","Redford","Bedford","Rutherford","Stratford",
 "rent field","wren field","ran field","rain field","ren feld","rend field","friend field",
 "render","rendered","rendering","remnant","reckoned","rent","friend","field","fielded","frenzied",
 "and then","okay","hello","thank you","what time","turn on","the light","please","yes","no","good morning"]
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
    print("### %d EN voices"%len(voices),flush=True)
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
    print("### GEN_EN_DONE",flush=True)
if __name__=="__main__": main()
