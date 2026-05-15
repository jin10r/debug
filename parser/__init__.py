"""Parser module for Survival Map v4."""

__version__ = '4.1.0'
__author__ = 'Survival Map Team'

from .message_processor import MessageProcessor
from .semantic_matcher import SemanticMatcher
from .embedder import RuBertEmbedder, EMBEDDING_DIM
from .db_adapter import DBAdapter

__all__ = [
    'MessageProcessor',
    'DBAdapter',
    'SemanticMatcher',
    'RuBertEmbedder',
    'EMBEDDING_DIM',
]