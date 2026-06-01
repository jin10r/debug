@echo off
REM ===========================================================================
REM  One-time setup: virtualenv + CUDA torch + training deps  (Windows / GTX 1050)
REM  Run from inside the ner_training_gpu folder:  setup.bat
REM ===========================================================================
setlocal

echo [1/5] Checking Python...
python --version || (echo ERROR: Python not found in PATH. Install Python 3.10/3.11 x64. & exit /b 1)

echo [2/5] Creating virtualenv .venv ...
python -m venv .venv || (echo ERROR: venv creation failed. & exit /b 1)
call .venv\Scripts\activate.bat

echo [3/5] Upgrading pip ...
python -m pip install --upgrade pip

echo [4/5] Installing CUDA build of PyTorch (cu121) ...
REM GTX 1050 = Pascal (compute capability 6.1), supported by cu121/cu124 wheels.
pip install "torch==2.5.1" --index-url https://download.pytorch.org/whl/cu121 || (echo ERROR: torch CUDA install failed. & exit /b 1)

echo [5/5] Installing the rest of the training deps ...
pip install -r requirements-gpu.txt || (echo ERROR: deps install failed. & exit /b 1)

echo.
echo === Verifying GPU visibility ===
python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"
echo.
echo Setup done. Next:  run_train.bat
endlocal
