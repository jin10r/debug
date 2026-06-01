@echo off
REM ===========================================================================
REM  Full training pipeline: prepare -> train -> eval -> export  (Windows / GPU)
REM  Run after setup.bat, from inside the ner_training_gpu folder:  run_train.bat
REM ===========================================================================
setlocal
call .venv\Scripts\activate.bat || (echo ERROR: run setup.bat first. & exit /b 1)

echo === [1/4] prepare_data (tokenize + BIO + case augmentation) ===
python prepare_data.py --augment-lower 0.5 || (echo prepare_data FAILED & exit /b 1)

echo === [2/4] train_ner (CUDA, fp16) ===
REM 2 GB card: if you hit CUDA out-of-memory, use:  --batch 16 --grad-accum 2
REM 4 GB card (1050 Ti): you can use:               --batch 64
python train_ner.py --epochs 3 --batch 32 || (echo train FAILED & exit /b 1)

echo === [3/4] eval_ner (natural + lowercased F1) ===
python eval_ner.py --dump 0

echo === [4/4] export_onnx (ONNX + int8 + smoke test) ===
python export_onnx.py

echo.
echo Done. Artifacts in:  output\ner_loc_onnx\
echo Copy model_quantized.onnx + tokenizer.json + labels.json back to the main repo
echo at:  models\ner_loc_onnx\   (see README, "Deploying the result").
endlocal
