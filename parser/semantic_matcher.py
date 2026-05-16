"""Обратная совместимость — делегирует в LexicalMatcher.

SemanticMatcher (rubert-tiny2 + pgvector) заменён на LexicalMatcher
(pymorphy2 + rapidfuzz). Этот модуль реэкспортирует новый класс под
старым именем, чтобы не менять импорты в остальных модулях.
"""

from .lexical_matcher import LexicalMatcher as SemanticMatcher, SIMILARITY_THRESHOLD, MAX_ENTITIES

__all__ = ['SemanticMatcher', 'SIMILARITY_THRESHOLD', 'MAX_ENTITIES']
