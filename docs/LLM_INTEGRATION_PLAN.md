# Compact LLM Integration Plan — nlp_processor

Based on `idea.txt` analysis of the current codebase (`processor/main.py`,
`semantic_resolver.py`, `layer_classifier.py`, `geo_matcher.py`,
`Dockerfile.processor`, `core/settings.py`).

## Current State

The processor already has an LLM integration point:

- `SemanticResolver._model_call()` calls **Ollama** via HTTP (`aiohttp`)
  with `qwen2.5:0.5b`, JSON format, temperature 0.0.
- **Disabled by default** (`ollama.enabled=false`) — works only when
  an external Ollama server is available.
- Called **only** for strategy selection (after geo matching, for 2+
  candidates when pre-filter rules are insufficient).
- `LayerClassifier` is purely rule-based (`startswith` on lemmas).
- `_is_junk()` and `is_promotional()` are purely regex-based.

**Problem with current Ollama integration:**
- External HTTP dependency: Ollama container or host service required
- No JSON Grammar → parses raw JSON from model response (can fail on
  malformed output)
- No KV-Cache → re-processes system prompt on every call
- Per-message overhead: HTTP connection + model load per request
- No batching → CPU underutilized for inference

## Proposal: Local `llama-cpp-python` with Hybrid Pipeline

Replace Ollama HTTP calls with local inference via `llama-cpp-python`,
using `Qwen2.5-0.5B-Q4_K_M` (~300 MB, fits in L3 cache). The model
runs **in-process** — no external server, no HTTP, no network.

### Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      Processor Container                          │
│                                                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │ Worker 0 │   │ Worker 1 │   │ Worker 2 │   │ Worker 3 │     │
│  │  (async) │   │  (async) │   │  (async) │   │  (async) │     │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘     │
│       │              │              │              │            │
│       └──────────────┴──────────────┴──────────────┘            │
│                              │                                   │
│                              ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Batch Buffer (up to batch_size)              │    │
│  │  Collects messages until batch_size or timeout_ms         │    │
│  └─────────────────────┬───────────────────────────────────┘    │
│                        │                                        │
│                        ▼                                        │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              LLM Ingestor Thread                        │    │
│  │  (single dedicated thread, llama-cpp-python, not async)  │    │
│  │                                                          │    │
│  │  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │    │
│  │  │ llama.cpp   │  │ KV-Cache     │  │ JSON Grammar  │  │    │
│  │  │ Model       │  │ (sys prompt) │  │ (valid output)│  │    │
│  │  │ Q4_K_M 0.5B │  │              │  │               │  │    │
│  │  └─────────────┘  └──────────────┘  └───────────────┘  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                        │                                        │
│                        ▼                                        │
│              ┌──────────────────┐                               │
│              │   Results        │                               │
│              │   dispatched     │                               │
│              │   back to        │                               │
│              │   workers        │                               │
│              └──────────────────┘                               │
└──────────────────────────────────────────────────────────────────┘
```

### Hybrid Decision Tree

```
Message ──→ _is_junk() ──→ True ──→ DROP
  │
  ▼
  ┌─ Cheap path (<1ms) ──────────────────────────────────────┐
  │  Tier 1 stem lookup (GeoMatcher)                          │
  │  LayerClassifier.classify()                                │
  │                                                            │
  │  IF (1 geo candidate AND layer != pig AND not promotional) │
  │    ──→ PostGIS → INSERT (fast path, ~80% of messages)      │
  │                                                            │
  └────────────────────────────────────────────────────────────┘
  │ (complex case: 2+ candidates, or layer=pig with
  │  suspicious keywords, or slang detected, or promotional)
  ▼
  ┌─ LLM path (~50ms batched) ────────────────────────────────┐
  │  Batch with other complex messages                         │
  │  LLM output: {layer, strategy, geo_ids, is_junk}           │
  │  ──→ PostGIS → INSERT                                      │
  └────────────────────────────────────────────────────────────┘
```

## Implementation Phases

### Phase 1: Foundation — `LLMBackend` class

**New file:** `processor/llm_backend.py`

A thread-safe wrapper around `llama-cpp-python` that:
- Loads model once at startup (single dedicated thread)
- Maintains KV-Cache for system prompt (reused across calls)
- Uses JSON Grammar to guarantee structured output
- Provides both single-inference and batch-inference methods

```
class LLMBackend:
    def __init__(self, model_path: str, n_ctx: int = 2048, batch_size: int = 8):
        self._model = llama.Llama(...)
        self._grammar = llama.Grammar.from_string(JSON_GRAMMAR)
        self._sys_prompt_kv = ...  # cached KV for system prompt
        self._batch = []
    
    def infer_single(self, messages: list[dict]) -> dict:
        """Single message inference (no batching)."""
    
    def infer_batch(self, messages: list[list[dict]]) -> list[dict]:
        """Batched inference for N messages."""
    
    @property
    def is_loaded(self) -> bool: ...
    
    def close(self): ...
```

**Config additions** in `core/settings.py`:

```python
@dataclass
class LlamaConfig:
    enabled: bool = False
    model_path: str = "/app/models/qwen2.5-0.5b-q4_k_m.gguf"
    n_ctx: int = 2048
    batch_size: int = 8
    batch_timeout_ms: int = 50  # max wait before flushing partial batch
    n_threads: int = 4         # CPU threads for inference
    verbose: bool = False

@dataclass
class Settings:
    ...
    llama: LlamaConfig = field(default_factory=LlamaConfig)
```

**Dependencies** — add to `processor/requirements.txt`:

```
llama-cpp-python>=0.3.0
```

**Dockerfile.processor** changes:
- Add model download in builder stage (curl/wget from HuggingFace)
- Model path: bind mount or baked into image (~300 MB extra)
- Install `llama-cpp-python` with `CMAKE_ARGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS"` (or AVX2-only for minimal size)

### Phase 2: LLM Layer Resolver

**New file:** `processor/llm_resolver.py`

Replaces `LayerClassifier.classify()` for complex cases and adds
context-aware junk/promotional detection.

```
class LLMLayerResolver:
    def __init__(self, llm: LLMBackend):
        self._llm = llm

    def classify(self, text: str, lemmas: list[Lemma]) -> dict:
        """Returns {layer, is_junk, confidence, reasoning}"""
```

**System prompt (KV-cached):**
```
Ты — классификатор событий для карты Одессы. Определи слой события
по тексту сообщения.

Слои:
- cops: полиция, патруль, мусора, ДПС, блокпост, ТЦК, тцкашники
- traffic: ДТП, авария, пробка, перекрытие, ремонт
- bus: автобусы, маршрутки, троллейбусы, транспорт
- pig: кабаны, свиньи, дикие животные
- junk: реклама, спам, ссылки, не релевантно

Правила:
- Если упомянуты и "копы" и "автобус" — выбирай cops (автобус — ориентир)
- Если упомянуты и "автобус" и "авария" — выбирай traffic
- Сленг: тцк/тцкашники/тцкашный → cops
- Реклама, ссылки, подписки → junk
```

**Response format (JSON Grammar):**
```json
{
    "layer": "cops" | "traffic" | "bus" | "pig" | "junk",
    "reasoning": "string"
}
```

**Integration point** in `_process_row()`:

```python
# After _nlp_classify, if layer is uncertain:
#   - LayerClassifier returned 'pig' but text has suspicious keywords
#   - Multiple layers matched (tie)
#   - Message is long and could be promotional
if self._llm and self._llm.is_loaded:
    if layer == 'pig' and self._has_suspicious_keywords(lemmas):
        llm_result = await self._llm_resolver.classify(raw_text, lemmas)
        if llm_result.get('layer') == 'junk':
            return None  # drop junk
        layer = llm_result.get('layer', layer)
```

### Phase 3: LLM Strategy Resolver

**File:** `processor/semantic_resolver.py` — replace `_model_call()`

Replace the existing Ollama HTTP-based `_model_call()` with local
`llama-cpp-python` inference. Same interface, same prompt logic,
much lower latency.

```python
async def _model_call(self, text: str, candidates: List[Dict]) -> Optional[Dict]:
    if not self._llm or not self._llm.is_loaded:
        return None
    
    prompt = _build_prompt(text, candidates)  # same as now
    result = await asyncio.to_thread(
        self._llm.infer_single, 
        self._build_messages(prompt)
    )
    # result is already validated by JSON Grammar → no parsing errors
    return result
```

**JSON Grammar for strategy output:**
```json
{
    "geo_ids": [int, ...],
    "strategy": "single_match" | "intersection" | "midpoint",
    "reasoning": "string"
}
```

**Benefits over current Ollama:**
- No HTTP overhead (~5-10ms saved per call)
- JSON Grammar → never malformed output
- KV-Cache → system prompt processed once
- Same model (qwen2.5:0.5b) but in-process
- Graceful fallback: if model not loaded → pre-filter only

### Phase 4 (Optional): Unified Compact Resolver

Combine Phase 2 + 3 into a **single LLM call** that determines
layer, strategy, geo_ids, and junk status in one shot.

```
Message ──→ tokenize + lemmatize + Tier 1 geo match
  │
  ▼
LLM call (batched):
  Input:  {text, lemmas, candidates: [{name, type, score, geo_id}]}
  Output: {layer, strategy, geo_ids, is_junk, reasoning}
  │
  ▼
PostGIS + INSERT
```

This eliminates the per-message pipeline entirely for complex cases
— one inference call replaces layer_classifier + semantic_resolver.

**System prompt:**
```
Ты — анализатор сообщений для карты событий Одессы.
По тексту найди слой, стратегию геолокации и релевантные geo_id.

Сообщение: {text}
Леммы: {lemmas}
Geo-кандидаты: {candidates}

Ответь JSON:
{
  "layer": "cops"|"traffic"|"bus"|"pig"|"junk",
  "strategy": "single_match"|"intersection"|"midpoint",
  "geo_ids": [<id>, ...],
  "reasoning": "string"
}

Правила выбора стратегии:
- single_match: конкретная улица/объект
- intersection: перекрёсток (упомянуты 2 улицы)
- midpoint: от X до Y, между X и Y (только для village/town)
```

### Phase 5: Batching

Since CPU inference is most efficient with batches, the batching
infrastructure is the key performance enabler.

**Batch flow:**

1. Each worker completes the cheap path (Tier 1, tokenize, lemmatize)
2. If message needs LLM → worker puts it into `asyncio.Queue` (shared)
3. A dedicated **batcher** coroutine pops from the queue:
   - Collects up to `batch_size` items OR waits `batch_timeout_ms`
   - Flushes partial batch on timeout
4. Sends batch to `LLMBackend.infer_batch()` running in thread pool
5. Results dispatched back to each worker's `Future`

```python
class BatchProcessor:
    def __init__(self, llm: LLMBackend, batch_size: int = 8, timeout_ms: int = 50):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._pending: dict[int, asyncio.Future] = {}  # msg_id → Future
    
    async def submit(self, msg_id: int, data: dict) -> dict:
        future = asyncio.Future()
        self._pending[msg_id] = future
        await self._queue.put((msg_id, data))
        return await future  # blocks until batch result
    
    async def _batcher_loop(self):
        while running:
            batch = []
            deadline = monotonic() + timeout_ms/1000
            while len(batch) < batch_size and monotonic() < deadline:
                try:
                    item = await asyncio.wait_for(self._queue.get(), deadline - monotonic())
                    batch.append(item)
                except asyncio.TimeoutError:
                    break
            if batch:
                results = await asyncio.to_thread(self._llm.infer_batch, [d for _, d in batch])
                for (msg_id, _), result in zip(batch, results):
                    self._pending[msg_id].set_result(result)
```

## Throughput Estimate

| Scenario | Per-msg latency | Throughput (4 workers) |
|----------|----------------|----------------------|
| Current (no LLM) | 5-15 ms | ~300 msg/s |
| Hybrid: 80% fast + 20% LLM (no batch) | ~60 ms avg | ~70 msg/s |
| Hybrid: 80% fast + 20% LLM (batch=8) | ~12 ms avg | ~330 msg/s |
| All LLM (no fast path, batch=8) | ~50 ms | ~80 msg/s |

**Key insight:** With batching and hybrid, the LLM doesn't reduce
throughput compared to the current pipeline — it improves quality
for the 20% complex cases at no throughput cost.

## Files to Create/Modify

| File | Action |
|------|--------|
| `processor/llm_backend.py` | **New** — `LLMBackend` class (llama-cpp-python wrapper) |
| `processor/llm_resolver.py` | **New** — `LLMLayerResolver` + `CompactResolver` |
| `processor/batcher.py` | **New** — `BatchProcessor` (async batching infrastructure) |
| `processor/requirements.txt` | Add `llama-cpp-python>=0.3.0` |
| `processor/main.py` | Add `LLMBackend` init, integrate into `_process_row()` |
| `processor/semantic_resolver.py` | Replace `_model_call()` with local llama-cpp |
| `Dockerfile.processor` | Add model download, OpenBLAS for llama.cpp |
| `core/settings.py` | Add `LlamaConfig` dataclass |
| `docker-compose.yml` | Optional: volume mount for model file |

## Model Selection

| Model | Size (Q4) | RAM | Quality | Notes |
|-------|-----------|-----|---------|-------|
| Qwen2.5-0.5B | ~350 MB | ~500 MB | Good | Best quality/size for RU/UA |
| SmolLM-360M | ~250 MB | ~400 MB | OK | Smaller, less accurate |
| Qwen2.5-1.5B | ~900 MB | ~1.2 GB | Better | If RAM allows |

**Recommendation:** Start with `Qwen2.5-0.5B-Q4_K_M`. It's the same
model already configured in Ollama (but runs locally). The 1.5B
version needs ~1.2 GB RAM — feasible if the processor container gets
more memory.

## Success Metrics

| Metric | Current | Target (with LLM) |
|--------|---------|-------------------|
| Layer accuracy | 72% (28% errors) | >90% |
| Strategy accuracy | 76-90% | >95% |
| Geo false positives | ~20% of events | <5% |
| Junk stored as events | 5-10% | <1% |
| Throughput | ~300 msg/s | >200 msg/s |
| P95 latency | 15 ms | <100 ms |

## Risks

1. **Model loading time**: llama.cpp init takes 1-3s. Must happen in
   `_init_nlp()` before workers start. Acceptable.

2. **Model file size**: 350 MB extra in container image. Use multi-stage
   download + optional volume mount for flexibility.

3. **CPU contention**: llama.cpp inference uses CPU threads. If
   `n_threads` > available cores, it can starve other workers.
   Solution: `n_threads=2` + pin to specific cores.

4. **Grammar maintenance**: JSON Grammar must stay in sync with
   expected output schema. Version-tagged in code.

5. **Cold start**: First inference after model load is slower (KV-cache
   warmup). Pre-warm with a dummy call in `_init_nlp()`.

6. **llama-cpp-python build**: Requires `gcc` + cmake at build time.
   Already handled in builder stage of Dockerfile.processor.

## Rollback Strategy

- `LLMBackend` checks `llama.enabled` flag. If disabled or model file
  missing → graceful degradation to current pipeline.
- All LLM methods return `None` on error → callers use existing fallback.
- The current Ollama `_model_call()` is preserved as standalone method
  — can be re-enabled by config toggle.
