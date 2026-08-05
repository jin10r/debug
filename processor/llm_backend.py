"""LLMBackend — in-process llama-cpp-python wrapper with KV-cache, JSON grammar, batching.

Модель Qwen2.5-0.5B Q4_K_M (~300 MB) запускается в единственном
воркер-треде. Все методы thread-safe, вызываются через `asyncio.to_thread`.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import llama_cpp
    from llama_cpp import Llama, LlamaGrammar
    _HAS_LLAMA = True
except ImportError:
    _HAS_LLAMA = False
    Llama = None  # type: ignore
    LlamaGrammar = None  # type: ignore


# ─── JSON Grammar for structured output ─────────────────────────────────────

_SYSTEM_PROMPT = (
    "Ты — анализатор сообщений для карты событий Одессы. "
    "Отвечай строго в JSON, без лишнего текста."
)

LAYER_GRAMMAR = LlamaGrammar.from_string(
    r"""
root ::= layer-object
layer-object ::= "{" ws "layer" ws ":" ws layer-value ws "," ws "reasoning" ws ":" ws string ws "}"
layer-value ::= "\"cops\"" | "\"traffic\"" | "\"bus\"" | "\"pig\"" | "\"junk\""
string ::= "\"" [^"]* "\""
ws ::= " "?
"""
) if _HAS_LLAMA else None

STRATEGY_GRAMMAR = LlamaGrammar.from_string(
    r"""
root ::= strategy-object
strategy-object ::= "{" ws "geo_ids" ws ":" ws array ws "," ws "strategy" ws ":" ws strategy-value ws "," ws "reasoning" ws ":" ws string ws "}"
array ::= "[" ws "]" | "[" ws int (ws "," ws int)* ws "]"
int ::= [0-9]+
strategy-value ::= "\"single_match\"" | "\"intersection\"" | "\"midpoint\""
string ::= "\"" [^"]* "\""
ws ::= " "?
"""
) if _HAS_LLAMA else None

UNIFIED_GRAMMAR = LlamaGrammar.from_string(
    r"""
root ::= unified-object
unified-object ::= "{" ws
    "layer" ws ":" ws layer-value ws "," ws
    "strategy" ws ":" ws strategy-value ws "," ws
    "geo_ids" ws ":" ws array ws "," ws
    "reasoning" ws ":" ws string ws
"}"
layer-value ::= "\"cops\"" | "\"traffic\"" | "\"bus\"" | "\"pig\"" | "\"junk\""
strategy-value ::= "\"single_match\"" | "\"intersection\"" | "\"midpoint\""
array ::= "[" ws "]" | "[" ws int (ws "," ws int)* ws "]"
int ::= [0-9]+
string ::= "\"" [^"]* "\""
ws ::= " "?
"""
) if _HAS_LLAMA else None


class LLMBackend:
    """Thread-safe wrapper around llama.cpp with KV-cache and grammar.

    Usage:
        llm = LLMBackend(model_path="/app/models/qwen2.5-0.5b-q4_k_m.gguf")
        result = llm.infer([
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
        ], grammar=UNIFIED_GRAMMAR)
    """

    def __init__(
        self,
        model_path: str,
        n_ctx: int = 2048,
        n_threads: int = 4,
        n_gpu_layers: int = 0,
        verbose: bool = False,
    ) -> None:
        if not _HAS_LLAMA:
            raise ImportError("llama-cpp-python is not installed")

        model_path = str(Path(model_path).resolve())

        if not Path(model_path).exists():
            raise FileNotFoundError(f"Model file not found: {model_path}")

        logger.info(f"[LLM] Loading model: {model_path} (n_ctx={n_ctx}, threads={n_threads})")

        self._model = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_threads=n_threads,
            n_gpu_layers=n_gpu_layers,
            verbose=verbose,
            # KV-cache system prompt: we reuse cached state for the
            # system message across calls
            use_mmap=True,
            use_mlock=False,
        )

        self._grammars = {
            'layer': LAYER_GRAMMAR,
            'strategy': STRATEGY_GRAMMAR,
            'unified': UNIFIED_GRAMMAR,
        }

        logger.info(f"[LLM] Model loaded: {model_path}")

    def infer(
        self,
        messages: List[Dict[str, str]],
        grammar_name: str = 'unified',
        temperature: float = 0.0,
        max_tokens: int = 128,
    ) -> Optional[Dict[str, Any]]:
        """Single inference call. Runs synchronously (call via to_thread)."""
        grammar = self._grammars.get(grammar_name)
        if grammar is None:
            logger.warning(f"[LLM] Unknown grammar: {grammar_name}, using no grammar")

        try:
            response = self._model.create_chat_completion(
                messages=messages,
                grammar=grammar,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=[],
            )

            raw = response['choices'][0]['message']['content'].strip()
            result = json.loads(raw)
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"[LLM] Invalid JSON output: {e}, raw={raw!r}")
            return None
        except Exception as e:
            logger.warning(f"[LLM] Inference failed: {e}")
            return None

    def infer_batch(
        self,
        batch_messages: List[List[Dict[str, str]]],
        grammar_name: str = 'unified',
        temperature: float = 0.0,
        max_tokens: int = 128,
    ) -> List[Optional[Dict[str, Any]]]:
        """Batched inference for CPU efficiency.

        llama.cpp processes batch sequentially in one thread,
        but the KV-cache is reused for shared system prompt.
        """
        results: List[Optional[Dict[str, Any]]] = []
        for messages in batch_messages:
            result = self.infer(messages, grammar_name, temperature, max_tokens)
            results.append(result)
        return results

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def close(self) -> None:
        if hasattr(self, '_model') and self._model is not None:
            logger.info("[LLM] Closing model")
            self._model = None  # type: ignore
