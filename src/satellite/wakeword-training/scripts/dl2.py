import os, glob, shutil, tarfile, urllib.request
import numpy as np, soundfile as sf, scipy.signal
from huggingface_hub import hf_hub_download, list_repo_files

# 1) MIT RIRs (already 16kHz wav in the repo)
os.makedirs("/work/mit_rirs", exist_ok=True)
repo = "davidscripka/MIT_environmental_impulse_responses"
files = [f for f in list_repo_files(repo, repo_type="dataset") if f.startswith("16khz/") and f.endswith(".wav")]
print(f"### RIRs: {len(files)} files", flush=True)
for i, f in enumerate(files):
    try:
        p = hf_hub_download(repo, f, repo_type="dataset")
        shutil.copy(p, "/work/mit_rirs/" + os.path.basename(f))
    except Exception as e:
        print("  rir fail", f, e, flush=True)
    if (i+1) % 50 == 0: print(f"  rir {i+1}/{len(files)}", flush=True)
print("### RIRs done:", len(glob.glob("/work/mit_rirs/*.wav")), flush=True)

# 2) AudioSet noise -> 16k mono wav (soundfile, no datasets)
os.makedirs("/work/audioset", exist_ok=True); os.makedirs("/work/background_clips", exist_ok=True)
tar = "/work/audioset/bal_train09.tar"
if not os.path.exists(tar) or os.path.getsize(tar) < 1e6:
    print("### downloading audioset tar", flush=True)
    urllib.request.urlretrieve("https://huggingface.co/datasets/agkphysics/AudioSet/resolve/main/data/bal_train09.tar", tar)
print("### extracting", flush=True)
with tarfile.open(tar) as t: t.extractall("/work/audioset")
flacs = glob.glob("/work/audioset/**/*.flac", recursive=True)
print(f"### audioset flacs: {len(flacs)}", flush=True)
for i, fl in enumerate(flacs):
    try:
        a, sr = sf.read(fl)
        if a.ndim > 1: a = a.mean(axis=1)
        if sr != 16000: a = scipy.signal.resample(a, int(len(a)*16000/sr))
        a = (np.clip(a, -1, 1)*32767).astype(np.int16)
        sf.write("/work/background_clips/" + os.path.basename(fl).replace(".flac", ".wav"), a, 16000)
    except Exception:
        pass
    if (i+1) % 500 == 0: print(f"  bg {i+1}/{len(flacs)}", flush=True)
print("### background clips:", len(glob.glob("/work/background_clips/*.wav")), flush=True)
print("### DATA2_DONE", flush=True)
