# Patches to `openwakeword/train.py`

The upstream openWakeWord `auto_train` pipeline assumes a CPU-feature / local-GPU
workstation. To run it on the **k8s GPU pod** (RTX 5060 Ti, Blackwell sm_120,
torch 2.7.0+cu128, tiny `/dev/shm`) with our **pre-generated clips** (we inject
our own piper-tts WAVs and skip the English-only piper-sample-generator), apply
these 5 edits to `openWakeWord/openwakeword/train.py` after cloning. Line numbers
are approximate (upstream `main` as of 2026-06).

### 1. Top of file — torch sharing strategy (DataLoader bus-error fix)
k8s containers get a 64 MB `/dev/shm`; the default `file_descriptor` sharing
strategy overruns it. Switch to `file_system` right after `import torch`:
```python
import torch
import torch.multiprocessing as _tmp
_tmp.set_sharing_strategy('file_system')  # /dev/shm is tiny in k8s; use /tmp
```

### 2. `Model.summary()` — keep torchinfo on CPU (~line 198)
torchinfo's dummy forward pass trips on a GPU device mismatch; pin it to CPU:
```python
def summary(self):
    return torchinfo.summary(self.model, input_size=(1,) + self.input_shape, device='cpu')
```

### 3. `compute_features_from_generator` AudioFeatures → GPU (~line 413)
Run the melspectrogram + Google speech-embedding feature extraction on the GPU:
```python
F = AudioFeatures(device='gpu', ncpu=4)
```

### 4. `generate_samples` import made non-fatal (~line 647)
We inject our own clips, so the English-only piper-sample-generator is optional.
Without this, a missing/broken generator import aborts the whole run:
```python
try:
    sys.path.insert(0, os.path.abspath(config["piper_sample_generator_path"]))
    from generate_samples import generate_samples
except Exception as _e:
    generate_samples = None  # only needed for --generate_clips (we inject our own clips)
```

### 5. Training-time feature device + DataLoader workers (~line 827, ~877)
GPU features for the train pass, and **`num_workers=0`** on the `IterDataset`
DataLoader (the multi-worker prefetch is what hit the `/dev/shm` bus error;
patch 1 helps, but 0 workers is the belt-and-suspenders fix and is plenty fast
because features are already cached as `.npy`):
```python
F = openwakeword.utils.AudioFeatures(device='gpu')
...
X_train = torch.utils.data.DataLoader(IterDataset(batch_generator),
                                      batch_size=None, num_workers=0)
```

> Note: lines ~802-820 (`device="gpu" if torch.cuda.is_available() else "cpu"`)
> are already GPU-aware upstream and need no change once CUDA is available.
