# NER training pipeline — Windows + NVIDIA GTX 1050 (GPU)

Self-contained package to train the **LOC NER model** (`rubert-tiny2`, BIO token
classification) on a Windows machine with an **NVIDIA GTX 1050 / 1050 Ti** GPU and
export it to ONNX (int8) for the parser.

It produces the same artifact the parser uses — `model_quantized.onnx` + `tokenizer.json`
+ `labels.json` — but trains in **~1 hour on the GPU** instead of ~7–8 h on the old CPU.

---

## What's in here

| File | Purpose |
|---|---|
| `data/dataset.retrain.json` | **Prebuilt training data** (247k messages, cleaned + relabeled with oblique street forms + hard negatives). Ready to train — no preprocessing needed. |
| `prepare_data.py` | Tokenize + BIO-align + **case augmentation** (50% lowercased copies) → arrow dataset. CPU step. |
| `train_ner.py` | Fine-tune `rubert-tiny2` on the **GPU** (auto-CUDA + fp16). |
| `eval_ner.py` | Span-level P/R/F1 on the held-out test split — natural **and** lowercased (case-invariance check). |
| `export_onnx.py` | Export → ONNX, int8-quantize, smoke-test with onnxruntime only. |
| `ner_common.py` | Shared constants & paths (all inside this folder). |
| `requirements-gpu.txt` | Python deps (torch installed separately, see below). |
| `setup.bat`, `run_train.bat` | One-click Windows setup & run. |

> The dataset was built upstream on Linux (clean → gazetteer relabel → dedup). Rebuilding
> it needs the parser/gazetteer stack and is **not** required here — just train on the
> included `dataset.retrain.json`.

---

## Prerequisites

1. **Windows 10/11** with an **NVIDIA GTX 1050 (2 GB)** or **1050 Ti (4 GB)**.
2. **NVIDIA driver** installed and working. Verify in a terminal:
   ```
   nvidia-smi
   ```
   You should see the GPU and a driver/CUDA version. (Driver ≥ 452 is plenty; the CUDA
   toolkit is **not** needed — the PyTorch CUDA wheel bundles its own runtime.)
3. **Python 3.10 or 3.11, 64-bit** (`python --version`). Avoid 3.12+ for smoothest wheels.
4. ~5 GB free disk; internet on first run (downloads the ~120 MB base model once).

GTX 1050 is **Pascal, compute capability 6.1** — fully supported by current PyTorch CUDA
builds.

---

## Quick start (one-click)

From a terminal **inside this folder**:

```
setup.bat
run_train.bat
```

`setup.bat` creates `.venv`, installs the CUDA PyTorch + deps, and prints whether the GPU
is visible. `run_train.bat` runs prepare → train → eval → export end-to-end.

---

## Manual steps (what the .bat files do)

```bat
REM 1) create & activate venv
python -m venv .venv
.venv\Scripts\activate

REM 2) upgrade pip
python -m pip install --upgrade pip

REM 3) install the CUDA build of PyTorch  (NOT the default PyPI/CPU wheel!)
pip install "torch==2.5.1" --index-url https://download.pytorch.org/whl/cu121

REM 4) install the rest
pip install -r requirements-gpu.txt

REM 5) confirm the GPU is visible
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
REM  -> True NVIDIA GeForce GTX 1050

REM 6) prepare data (CPU, a few minutes)
python prepare_data.py --augment-lower 0.5

REM 7) train on GPU (auto fp16)
python train_ner.py --epochs 3 --batch 32

REM 8) evaluate
python eval_ner.py --dump 0

REM 9) export ONNX + int8 + smoke test
python export_onnx.py
```

---

## VRAM tuning (important on the 2 GB card)

`train_ner.py` enables **fp16** automatically on CUDA to cut VRAM use. Pick `--batch` for
your card:

| Card | VRAM | Recommended |
|---|---|---|
| GTX 1050 | 2 GB | `--batch 16 --grad-accum 2` (effective batch 32) |
| GTX 1050 | 2 GB | `--batch 32` often fits with fp16 — try it; drop to 16 if OOM |
| GTX 1050 Ti | 4 GB | `--batch 64` |

If you see `CUDA out of memory`: lower `--batch` (and raise `--grad-accum` to keep the
effective batch size). Close other GPU apps (browser/games) first — `nvidia-smi` shows
what's using VRAM.

---

## Expected time on a GTX 1050

`rubert-tiny2` is tiny (29 M params), so even an entry-level Pascal card is far faster than
the legacy CPU:

| Hardware | 3 epochs / 333k examples |
|---|---|
| Old CPU (i5-2450M) | ~7–8 h |
| **GTX 1050 (2 GB)** | **~1–1.5 h** |
| GTX 1050 Ti (4 GB) | ~40–60 min |

`prepare_data.py` adds a few minutes (CPU tokenization, one-time). Checkpoints are written
every 2000 steps; resume an interrupted run with `python train_ner.py --resume`.

---

## Outputs & deploying the result

After `export_onnx.py`, the artifact is in **`output\ner_loc_onnx\`**:

```
output\ner_loc_onnx\
  model_quantized.onnx     <- int8, ~28 MB  (what the parser loads)
  model.onnx               <- fp32 (optional)
  tokenizer.json
  labels.json
  config.json
```

To deploy into the main project, copy these back into the repo's gazetteer-agnostic model
folder, replacing the old artifact:

```
copy output\ner_loc_onnx\model_quantized.onnx   <repo>\models\ner_loc_onnx\
copy output\ner_loc_onnx\tokenizer.json         <repo>\models\ner_loc_onnx\
copy output\ner_loc_onnx\labels.json            <repo>\models\ner_loc_onnx\
copy output\ner_loc_onnx\config.json            <repo>\models\ner_loc_onnx\
```

Then on the Linux side run the parser regression gate (`compare_ngram_ner.py`) before
flipping `NER_ENABLED=true`.

---

## Troubleshooting

- **`torch.cuda.is_available()` is False** → you installed the CPU wheel. Reinstall torch
  with the `--index-url https://download.pytorch.org/whl/cu121` line (step 3). Confirm
  `nvidia-smi` works first.
- **`CUDA out of memory`** → lower `--batch` (see VRAM tuning); close other GPU apps.
- **Dataloader hangs / spawn errors on Windows** → keep `--workers 0` (the default). Try
  `--workers 2` only if data loading is a bottleneck.
- **First run slow to start** → it's downloading `cointegrated/rubert-tiny2` (~120 MB) once.
  For fully offline use, pre-download on a connected machine
  (`huggingface-cli download cointegrated/rubert-tiny2`) and set
  `set HF_HUB_OFFLINE=1` before training.
- **Driver too old for cu121** → either update the NVIDIA driver, or install a cu118 torch
  build instead: `pip install "torch==2.5.1" --index-url https://download.pytorch.org/whl/cu118`.

---

## Notes

- **No AVX env hack needed here.** The `ATEN_CPU_CAPABILITY=avx` / `DNNL_MAX_CPU_ISA=AVX`
  workaround in the Linux runbook is only for the old AVX-only CPU; on the GPU it's unused.
- **Input is raw text, not lemmas** — by design (see the main project's `REPORT_retrain.md`
  for why lemma-input was rejected). Do not lemmatize before training.
- The trained model is a **pure span detector** (O / B-LOC / I-LOC); it knows no street IDs.
  Linking spans → streets stays in the parser via the gazetteer, so the model is portable
  and unaffected by `streets.csv` changes.
