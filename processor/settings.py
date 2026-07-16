"""Processor settings — импорт из core/settings для единообразия."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.settings import load_settings, LAYER_PRIORITY, DEFAULT_LAYER_KEYWORDS

logger = logging.getLogger(__name__)

try:
    settings = load_settings(require_jwt=False)
except Exception as e:
    logger.warning("Failed to load settings: %s", e)
    settings = None

__all__ = ['settings', 'LAYER_PRIORITY', 'DEFAULT_LAYER_KEYWORDS']
