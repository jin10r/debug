"""TypeValidator — zero-shot type validation for geo candidates.

Uses OnnxEncoder (rubert-tiny2 ONNX int8) to validate candidate types
against surrounding text context.

Pipeline position: after GeoMatcher.find_geo(), before SemanticResolver.
For each candidate, extracts a context window and probes type fit.
If BERT is not available, falls back to heuristic markers only.
"""

import logging
from typing import Any, Dict, List, Optional

try:
    from .onnx_encoder import OnnxEncoder
except Exception:
    OnnxEncoder = None

logger = logging.getLogger(__name__)

# Context window radius (tokens before/after the candidate surface)
_CONTEXT_RADIUS = 5

# Heuristic fallback: type markers that can be matched without BERT
_HEURISTIC_MARKERS: Dict[str, str] = {
    "сквер": "park",
    "парк": "park",
    "сад": "park",
    "улица": "street",
    "проспект": "street",
    "переулок": "street",
    "бульвар": "street",
    "набережная": "embankment",
    "село": "village",
    "деревня": "village",
    "посёлок": "village",
    "пгт": "town",
    "город": "town",
    "площадь": "square",
    "мост": "bridge",
    "станция": "station",
    "остановка": "stop",
    "рынок": "market",
}

_TYPE_ORDER_PRIORITY = {
    'village': 10, 'town': 9, 'station': 8, 'park': 7,
    'landmark': 6, 'street': 5, 'square': 4, 'bridge': 3,
    'embankment': 2, 'market': 1, 'stop': 0, 'district': -1,
    'beach': -2, 'forest': -3, 'water': -4,
}


def _extract_context(tokens: list, surface: str, start_i: int, end_i: int) -> str:
    """Extract context window around candidate for type validation.

    Returns ±_CONTEXT_RADIUS tokens as a single string.
    """
    n = len(tokens)
    ctx_start = max(0, start_i - _CONTEXT_RADIUS)
    ctx_end = min(n, end_i + 1 + _CONTEXT_RADIUS)
    ctx_tokens = [t.text for t in tokens[ctx_start:ctx_end]]
    return ' '.join(ctx_tokens)


def _heuristic_type(text_lower: str) -> Optional[str]:
    """Fast heuristic type detection from text markers.

    Returns best type or None if no marker found.
    """
    best_type = None
    best_pos = -1
    for marker, target_type in _HEURISTIC_MARKERS.items():
        pos = text_lower.find(marker)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos
            best_type = target_type
    return best_type


class TypeValidator:
    """Zero-shot geo type validator using BERT or heuristic fallback."""

    def __init__(self, encoder: Optional[OnnxEncoder] = None) -> None:
        self._encoder = encoder
        self._initialized = False

    async def initialize(self) -> bool:
        if self._encoder and self._encoder.is_ready:
            self._initialized = True
            logger.info("[TypeValidator] Initialized with ONNX encoder")
        else:
            self._initialized = True
            logger.info("[TypeValidator] Initialized in heuristic-only mode")
        return True

    def validate(
        self,
        candidates: List[Dict[str, Any]],
        text: str,
        tokens: list,
    ) -> List[Dict[str, Any]]:
        """Validate and score types for each candidate.

        Returns enriched candidates with:
          - validated_type: best matching type
          - type_confidence: cosine similarity score (0-1) or heuristic binary 0/1
        """
        if not self._initialized:
            return candidates

        text_lower = text.lower()
        enriched = []

        for c in candidates:
            c = dict(c)
            surface = c.get('text', '')
            candidate_type = c.get('type', 'street')
            span = c.get('_span', (0, 0))

            heuristic_type = _heuristic_type(text_lower)

            if self._encoder and self._encoder.is_ready:
                context = _extract_context(tokens, surface, span[0], span[1])
                scores = self._encoder.probe(context)
                bert_type = max(scores, key=scores.get) if scores else candidate_type
                bert_score = scores.get(bert_type, 0.0) if scores else 0.0

                type_order_boost = _TYPE_ORDER_PRIORITY.get(bert_type, 0) * 0.01
                bert_score = min(1.0, bert_score + type_order_boost)

                if bert_type != candidate_type and bert_score > 0.3:
                    c['validated_type'] = bert_type
                    c['type_confidence'] = round(bert_score, 4)
                else:
                    c['validated_type'] = candidate_type
                    c['type_confidence'] = round(max(bert_score, 0.5), 4)
            elif heuristic_type:
                c['validated_type'] = heuristic_type
                c['type_confidence'] = 1.0 if heuristic_type == candidate_type else 0.5
            else:
                c['validated_type'] = candidate_type
                c['type_confidence'] = 0.5

            enriched.append(c)

        return enriched

    async def close(self) -> None:
        self._initialized = False
        logger.info("[TypeValidator] Closed")
