#!/usr/bin/env python3
"""Скрипт для конвертации rubert-tiny2 в формат ONNX."""

import os
import sys
from pathlib import Path

def main():
    output_dir = Path("./rubert-tiny2-onnx")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"🔄 Конвертация rubert-tiny2 → ONNX в {output_dir}...")
    
    try:
        from optimum.exporters.onnx import main_export
    except ImportError:
        print("❌ Не установлена optimum. Установите: pip install optimum[onnx]")
        sys.exit(1)
    
    try:
        main_export(
            model_name_or_path="cointegrated/rubert-tiny2",
            output=output_dir,
            task="feature-extraction",
            opset=14,
        )
        print(f"✅ Модель успешно конвертирована в {output_dir}")
        print(f"📁 Файлы: {list(output_dir.glob('*'))}")
    except Exception as e:
        print(f"❌ Ошибка конвертации: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
