"""Parser settings - импорт из core/settings для единообразия."""

import sys
from pathlib import Path

# Добавляем корень проекта в путь для импорта core
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.settings import load_settings, LAYER_PRIORITY, DEFAULT_LAYER_KEYWORDS

# Глобальный экземпляр настроек (без JWT для parser)
try:
    settings = load_settings(require_jwt=False)
except Exception as e:
    print(f"Failed to load settings: {e}")
    settings = None

__all__ = ['settings', 'LAYER_PRIORITY', 'DEFAULT_LAYER_KEYWORDS']
