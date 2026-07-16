"""Parser settings - импорт из core/settings для единообразия."""

import logging
import sys
from pathlib import Path

# Добавляем корень проекта в путь для импорта core
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.settings import load_settings

logger = logging.getLogger(__name__)

# Глобальный экземпляр настроек (без JWT для parser)
try:
    settings = load_settings(require_jwt=False)
except Exception as e:
    logger.warning("Failed to load settings: %s", e)
    settings = None

__all__ = ['settings']
