# NER retrain readiness report (dataset rebuild #2)

_Generated 2026-06-01. Pipeline: `training/rebuild_dataset.py` → `prepare_data.py` → `train_ner.py` →
`export_onnx.py` → `eval_ner.py` / `compare_ngram_ner.py`._

## 1. Why this retrain

Retrain #1 (case augmentation) made the model **letter-case invariant** (lowercased-test F1 0.992, gap ~0) but
live recall barely moved (NER 370 vs n-gram 534 streets / 500 msgs). Root cause, proven by probe: the model detects
**nominative** streets in any letter-case but misses **oblique/directional** mentions (`по канатной`,
`в сторону базарной`) — because the *labels* never contained them. The old auto-labeler skipped secondary/directional
streets; the NER faithfully learned that gap.

This retrain fixes it at the **data** level: full re-annotation with the gazetteer n-gram matcher (which *does*
catch oblique forms), template dedup (leakage), and added hard negatives.

## 2. Dataset rebuild — what changed

Source `train_data/dataset.clean.json` (252,542 records) → `train_data/dataset.retrain.json`.

| Metric | Value |
|---|---|
| Input records | 252,542 |
| Dropped (>380 chars) | 0 |
| Entities (labels): old → new | 342,436 → **357,757** (**+15,321** oblique/secondary streets) |
| Positives after dedup | 239,090 → **233,875** (5,215 templates collapsed, ~2%) |
| Zero-match pool (deduped) | 13,452 → **13,129** |
| Hard negatives added (cap 10%) | **13,129** (5.3% of train; dataset had 0 before) |
| **Train file total** | **247,004** |
| Enrichment candidates | 13,129 → `postgres/data/streets_candidats.csv` (text + freq) |

**Verification passed:** 65,161 records now carry a **directional-context** labelled street (`по/на/к/в сторону/
не доезжая` + street) — previously systematically unlabelled. All probe streets (`дальницкой`, `базарной`,
`балковской`, `среднефонтанской`, `пушкинской`, …) survive dedup.

**Gazetteer pruned** (`training/prune_streets.py`): `streets.csv` 1060 → **905 rows** — 155 streets that never
occur in the dataset were removed (e.g. `Айвазовского`, `Венгерская`, `Бродская`). Original backed up to
`postgres/data/streets.full.csv`. NER training is gazetteer-independent, so this only affects inference linking
(smaller index, fewer FP surfaces); a removed street won't link if it appears in a future message — restore from
the backup if needed.

After `prepare_data.py --augment-lower 0.5`: **333,219** train examples (222,304 + 110,915 lowercased; 34.9%
all-lowercase, 0 offset mismatches), + `validation` / `validation_lower` / `test` splits.

## 3. Expected pros / cons of the new labelling

**Pros**
- Oblique/directional streets now labelled → closes the core recall gap (the goal).
- Hard negatives (messages with no street) → better precision; none existed before.
- Template dedup → less train/test leakage → more honest eval.

**Cons / honest caveats**
- **Distillation ceiling.** Relabelling with `find_streets` makes the NER a *fast distillation of the n-gram
  matcher*. Its quality ceiling = the matcher's. The win is **inference speed + case/oblique robustness**, not
  accuracy above n-gram. It also inherits some n-gram FPs (mitigated by the plate guard; **measured** on the
  500-sample compare — if precision drops, revert to `models/ner_loc_onnx_clean2_bak/`).
- Hard negatives drawn from relabel-misses may include real *unknown* streets (capped at 10%; also logged to
  `streets_candidats.csv` for review/enrichment).

## 4. Offline training runbook

Training needs no network **if the base model is pre-cached**. One-time, on a machine with internet:

```bash
# 1) cache base model + tokenizer into the HF cache (~120 MB)
.venv-train/bin/huggingface-cli download cointegrated/rubert-tiny2
#    (or: python -c "from huggingface_hub import snapshot_download; snapshot_download('cointegrated/rubert-tiny2')")
```

Then fully offline (no network), from the repo root:

```bash
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1            # force offline; fail fast if cache missing

# 2) (re)build dataset — only if streets.csv / dataset.clean.json changed
.venv-eval/bin/python training/rebuild_dataset.py         # -> dataset.retrain.json (+ candidats)

# 3) tokenise + BIO + case-aug  (writes training/data/, gitignored)
cd training && ../.venv-train/bin/python prepare_data.py --augment-lower 0.5

# 4) train (CPU, ~7–10 h, 3 epochs).
#    REQUIRED on this AVX-only Sandy Bridge CPU to avoid SIGILL (exit 132):
#      - num_workers=0 (default)  AND
#      - force AVX-only kernels via env (torch/oneDNN/MKL otherwise flakily dispatch AVX2/FMA):
ATEN_CPU_CAPABILITY=avx DNNL_MAX_CPU_ISA=AVX MKL_ENABLE_INSTRUCTIONS=AVX \
OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 \
  ../.venv-train/bin/python train_ner.py --epochs 3 --batch 16 --grad-accum 2 --threads 4
#    resume after interruption (checkpoints every 2000 steps) — keep the SAME env prefix:
#  ATEN_CPU_CAPABILITY=avx DNNL_MAX_CPU_ISA=AVX MKL_ENABLE_INSTRUCTIONS=AVX \
#    ../.venv-train/bin/python train_ner.py --resume

# 5) export ONNX + int8 quantize + smoke test (onnxruntime only)
../.venv-train/bin/python export_onnx.py                  # -> models/ner_loc_onnx/model_quantized.onnx

# 6) evaluate
../.venv-train/bin/python eval_ner.py --dump 0            # natural + lowercased F1 (case-invariance gate)
cd .. && .venv-eval/bin/python training/compare_ngram_ner.py --n 500 --seed 42   # vs n-gram, precision gate
```

Notes: `.venv-train` = torch/transformers/optimum (training+export); `.venv-eval` = parser deps + onnxruntime
(eval/compare). Hardware: 4-core AVX-only (no AVX2/FMA) — `--workers 0` avoids the SIGILL from forked dataloader
workers. RAM ~2.5 GB; stop Docker if tight. Training is resumable; safe to background.

## 5. Model size & making it more compact

`rubert-tiny2`: 29.1 M params, **90% (26.2 M) is the token-embedding matrix** (vocab 83,828 × hidden 312); the
3 transformer layers are only 2.3 M. Current artifacts: `model.onnx` 111 MB (fp32), **`model_quantized.onnx`
27.9 MB (int8, shipped)**.

Levers (largest first):
- **Vocabulary pruning** — biggest win. The 83.8k general-Russian vocab is overkill for this domain; keep only
  tokens appearing in the training corpus + special tokens (≈20–30k) and remap ids → embeddings shrink ~3×
  → total ~10–12 M params → **int8 ≈ 10–14 MB**. Risk: OOV at inference, avoided by retaining every training-corpus
  token. Would be a new optional step in `export_onnx.py`.
- **int8 quantization** — already applied (112 → 28 MB).
- int4 / smaller hidden / fewer layers — poor ROI (onnxruntime int4 maturity; or requires a different base /
  distillation, which costs accuracy).

**Recommendation:** 28 MB int8 is already deployable; add vocab-pruning only if a ~12 MB target is needed.

## 6. Lemma-training assessment (should we train on lemmas?)

Idea: feed lemmatized text so `канатной`/`канатная`/`по балковской` collapse to one form — a direct attack on the
oblique-case problem.

- **Pros:** removes grammatical-case variance at the input; `mawo_pymorphy3` is **already** loaded in the parser for
  span→street linking, so no new heavy inference dependency.
- **Cons (decisive):**
  1. **Fights the pretrained model.** `rubert-tiny2` was pretrained on natural inflected Russian; lemmatized text
     (`по балковский`) is ungrammatical → degrades the contextual embeddings that are the transformer's main value.
  2. **Offset mapping.** NER must emit char spans in the **original** text; training/inferring on lemmas needs a
     lemma-token → original-char-offset map — extra machinery and a bug surface.
  3. **Wrong lever.** The defect was **label incompleteness**, not the model's inability to handle morphology (the
     probe showed it detects lowercase *nominative* fine). Fixing labels (this retrain) addresses the real cause.
  4. Adds lemmatization latency before every NER call.

**Verdict: do not train on lemmas as the primary input.** The same goal (case/oblique invariance) is achieved more
safely at the data level (oblique relabel + case augmentation), keeping the model "natural" and inference light.
If desired, lemma-input is worth at most a **side experiment** to compare F1 — not the main path.

## 7. Post-training acceptance gate (fill after training completes)

- [ ] `eval_ner.py`: natural F1 ≈ 0.98 **and** probe strings (`по канатной`, `на дальницкой`, `в сторону базарной`)
      now detected (the real fix vs retrain #1).
- [ ] `compare_ngram_ner.py` (500): NER total streets ↑ toward n-gram (was 370/534); **precision held** (FP count
      not materially worse — else revert to `ner_loc_onnx_clean2_bak/`).
- [ ] Docker: `docker compose build parser && up` — model loads, sample message geocodes via `process_candidates`.
- [ ] Decide `ner_enabled` flip + whether to apply curated `streets_candidats.csv` enrichment.
```
```
