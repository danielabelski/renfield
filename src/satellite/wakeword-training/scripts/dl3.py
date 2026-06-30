import urllib.request, io, os, glob
import pyarrow.parquet as pq
import soundfile as sf, numpy as np, scipy.signal
os.makedirs("/work/background_clips", exist_ok=True)
# Download 2 parquet shards of AudioSet balanced-train (audio embedded as flac bytes)
shards = ["00", "01"]
for sh in shards:
    p = f"/work/audioset_{sh}.parquet"
    if not os.path.exists(p) or os.path.getsize(p) < 1e6:
        url = f"https://huggingface.co/datasets/agkphysics/AudioSet/resolve/main/data/bal_train/{sh}.parquet"
        print(f"### downloading shard {sh}", flush=True)
        urllib.request.urlretrieve(url, p)
    t = pq.read_table(p)
    if sh == "00": print("columns:", t.column_names, "rows:", t.num_rows, flush=True)
    audio = t.column("audio").to_pylist()
    n = 0
    for i, a in enumerate(audio):
        try:
            b = a["bytes"] if isinstance(a, dict) else a
            arr, sr = sf.read(io.BytesIO(b))
            if getattr(arr, "ndim", 1) > 1: arr = arr.mean(axis=1)
            if sr != 16000: arr = scipy.signal.resample(arr, int(len(arr)*16000/sr))
            arr = (np.clip(arr, -1, 1)*32767).astype(np.int16)
            sf.write(f"/work/background_clips/as_{sh}_{i:05d}.wav", arr, 16000); n += 1
        except Exception as e:
            if i < 3: print("  decode err:", str(e)[:100], flush=True)
        if (i+1) % 1000 == 0: print(f"  shard {sh}: {i+1}", flush=True)
    print(f"### shard {sh} -> {n} clips", flush=True)
print("### background total:", len(glob.glob('/work/background_clips/*.wav')), flush=True)
print("### NOISE_DONE", flush=True)
