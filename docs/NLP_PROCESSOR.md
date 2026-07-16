# NLP Processor — Documentation

## Overview

The `processor` container is an independent microservice that consumes raw
Telegram messages from the `pending_events` queue, runs a multi-stage NLP
pipeline (layer classification + geo-entity recognition + semantic resolution),
computes geometry via PostGIS, and writes enriched events into the `events`
table.  It does **not** serve HTTP — it communicates exclusively through
PostgreSQL (polling, NOTIFY/LISTEN, and direct SQL).

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Runtime | Python 3.11 (slim-bookworm) | Minimal container footprint |
| Morphology | `mawo-pymorphy3` 1.0.4 | Lemmatization, POS tagging, proper-noun detection |
| Stemming | `snowballstemmer` 2.2 | OOV-stable suffix stemming for street matching |
| Fuzzy matching | `rapidfuzz` 3.0 | Typo correction (Tier 2) and surface form matching |
| Database driver | `asyncpg` 0.29 | Async PostgreSQL with connection pooling |
| Geometry engine | PostGIS (server-side) | `ST_Intersection`, `ST_ShortestLine`, `ST_Centroid`, `ST_Contains` |
| HTTP (optional) | `aiohttp` 3.9 | Ollama LLM calls for complex semantic resolution |
| LLM (optional) | Ollama + `qwen2.5:0.5b` | Strategy selection when pre-filter rules are insufficient |
| Parallelism | `concurrent.futures.ProcessPoolExecutor` | CPU-bound fuzzy matching offloaded to worker processes |
| Concurrency | `asyncio` | Non-blocking I/O, worker pool, pg_notify listener |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     processor container                     │
│                                                             │
│  ┌──────────┐   ┌──────────────┐   ┌───────────────────┐  │
│  │ Worker 0 │   │ Worker 1 ... │   │ pg_notify listener │  │
│  │ (async)  │   │   (async)    │   │ (geo_updated)      │  │
│  └────┬─────┘   └──────┬───────┘   └────────┬──────────┘  │
│       │                │                     │              │
│       ▼                ▼                     ▼              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                   Pipeline per message               │   │
│  │                                                     │   │
│  │  1. _is_junk() ──→ skip if trivial                  │   │
│  │  2. _nlp_classify() ──→ tokenize + lemmatize        │   │
│  │  3. LayerClassifier.classify() ──→ layer            │   │
│  │  4. is_promotional() / too long? ──→ random point   │   │
│  │  5. GeoMatcher.find_geo() ──→ geo candidates        │   │
│  │  6. SemanticResolver.resolve() ──→ strategy + ids   │   │
│  │  7. process_candidates() [PostGIS] ──→ geometry     │   │
│  │  8. INSERT INTO events ──→ done                      │   │
│  └─────────────────────────────────────────────────────┘   │
│                         │                                   │
│                         ▼                                   │
│              ┌──────────────────┐                           │
│              │   PostgreSQL     │                           │
│              │  pending_events  │ ← read (SKIP LOCKED)     │
│              │  events          │ → write                  │
│              │  events_meta     │ → update version         │
│              │  geo / stopwords │ ← read (init + reindex)  │
│              └──────────────────┘                           │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Ingestion

The parser writes raw Telegram messages into `pending_events`:

```
pending_events
├── id BIGSERIAL        — internal queue ID
├── message_id BIGINT   — Telegram message ID (dedup key)
├── text TEXT            — raw message text
├── event_time TIMESTAMPTZ
├── photo_file_id TEXT   — Telegram file_id for photo
├── status TEXT          — pending → done / error
└── created_at TIMESTAMPTZ
```

The processor polls with `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`,
ensuring concurrent workers never process the same row.

### 2. NLP Pipeline (per message)

#### Stage 1: Junk filtering (`_is_junk`)
Rejects messages that are:
- Empty or < 3 characters
- Emoji-only (no alphabetic characters)
- System messages ("слишком длинное...", "без описания")

**Returns:** `None` → message is silently dropped (not stored as event).

#### Stage 2: CPU-bound NLP (`_nlp_classify` via `asyncio.to_thread`)
Runs in a thread pool to release the GIL:
1. **Tokenize** (`word_tokenizer.tokenize`): split on non-alphanumeric chars;
   merge `[DIGIT, "я"]` → `"Nя"` for ordinal streets.
2. **Lemmatize** (`Morphology.lemmatize_tokens`): mawo_pymorphy3 with LRU cache
   (20K entries). Ordinal words → Arabic digits ("пятый" → "5").
3. **Layer classification** (`LayerClassifier.classify`): match lemmatized tokens
   against layer keyword lemmas using `startswith`. Priority: `bus → cops → traffic → pig`.

#### Stage 3: Promotional / too-long filter
- `is_promotional()`: regex detects URLs, Telegram handles, subscription calls.
  Promotional messages → `strategy=random`, random point in Odessa area.
- Messages > 380 chars → same treatment (not a valid location report).

#### Stage 4: Geo-entity recognition (`GeoMatcher.find_geo`)
Two-tier matching against the `geo` table (~1000 street/settlement/POI entries):

**Tier 1 — Stem exact (O(1) lookup):**
- Build Snowball stems for each token in the message
- Generate sliding-window candidates (1..3 tokens)
- Look up stem tuples in `PhoneticIndex._stem_index`
- Also tries order-independent lookup for multi-word names
  ("Застава 2" ≡ "2 Застава")

**Tier 2 — Surface typo correction (rapidfuzz):**
- For unmatched spans with ≥ 5 chars
- `rapidfuzz.process.extractOne` with `fuzz.ratio`
- Guard: first-char must match + length difference ≤ 20%
- Runs in `ProcessPoolExecutor` for parallelism

**Deduplication:** Per `geo_id`, only the highest-scoring match is kept.
**Top-K:** Up to 5 candidates returned (configurable via `max_entities`).

**Non-geo filtering:**
- `_NON_GEO_ADJECTIVES`: 35 color adjective forms (зелёного, синего, ...)
  that false-match as street names
- `_NON_GEO_WORDS`: known false positives (копейка, шума, рабина, яр)
- DB stopwords table

#### Stage 5: Semantic resolution (`SemanticResolver.resolve`)
Determines the PostGIS strategy when 2+ candidates exist:

**District filtering (if any candidate is type `district`):**
- PostGIS `ST_Contains(district.geom, ST_Centroid(other.geom))`
- Candidates outside the district are excluded
- The district itself is excluded from final geometry

**Pre-filter rules (no LLM):**
1. Directional prepositions ("от X до Y", "между X и Y") → `midpoint`
   (only if candidates include settlement types: village/town)
2. Type hints in text ("село X", "район Y") → `single_match` or `midpoint`
3. Duplicate names with third candidate → defer to model

**LLM fallback (Ollama, optional):**
- Sends prompt with message text + candidate list
- Model returns `{geo_ids, strategy, reasoning}`
- Strategies: `single_match`, `intersection`, `midpoint`
- Disabled by default (`ollama.enabled=false`)

#### Stage 6: PostGIS computation (`process_candidates` SQL function)

| Strategy | Condition | Geometry |
|----------|-----------|----------|
| `random` | No geo matches | Random point in Odessa bounding box |
| `single_match` | 1 match or fallback | Full geometry of the matched `geo` row |
| `intersection` | 2+ strong matches (score ≥ 0.85) that spatially intersect | `ST_Intersection` → `ST_PointOnSurface` or `ST_ConvexHull` |
| `pseudo_intersection` | 2+ strong matches that don't intersect but are within 150m | `ST_ShortestLine` midpoint |
| `midpoint` | Settlement-to-settlement with directional prepositions | `ST_LineInterpolatePoint(ST_ShortestLine(...), 0.5)` |

### 3. Event storage

```sql
INSERT INTO events (message_id, event_time, description, photo_url,
                    layer, strategy, geom, matches)
VALUES (...)
ON CONFLICT (message_id, event_time) DO NOTHING
```

The `ON CONFLICT` prevents duplicate events from the same Telegram message.
After insert, the processor:
1. Updates `events_meta.version` (triggers frontend cache invalidation)
2. Updates `events_meta.max_event_id`
3. Sends `pg_notify('events_new', ...)` with the GeoJSON feature payload

### 4. Real-time delivery

The `core` container's WebSocket manager listens for `events_new` notifications
and broadcasts the GeoJSON Feature to all connected clients.

## Module Reference

| File | Purpose |
|------|---------|
| `main.py` | Entry point: `ProcessorBot` orchestrates workers, DB, pg_notify |
| `morphology.py` | `Morphology` class: mawo_pymorphy3 wrapper with LRU caches, ordinal mapping, Snowball stemming |
| `word_tokenizer.py` | `tokenize()`: regex-based word splitting with ordinal merging |
| `text_preprocessor.py` | `preprocess_light()`, `clean()`, `strip_tail()`, `is_promotional()`, `strip_emoji()` |
| `phonetic_index.py` | `PhoneticIndex`: stem-tuple index + surface-phrase index for geo lookup |
| `geo_matcher.py` | `GeoMatcher`: two-tier geo entity recognition (stem exact + surface typo) |
| `layer_classifier.py` | `LayerClassifier`: keyword-based event layer assignment with `startswith` matching |
| `semantic_resolver.py` | `SemanticResolver`: strategy selection via pre-filter rules + optional Ollama LLM |
| `db_adapter.py` | `DBAdapter`: asyncpg connection pool wrapper |
| `settings.py` | Loads shared `core/settings.py` config (no JWT needed) |

## Configuration

All tuning parameters live in `core/settings.py` → `SimilarityConfig`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `entity_similarity_threshold` | 0.82 | Fuzzy threshold for lemma matching |
| `phonetic_match_threshold` | 0.85 | Surface fuzzy threshold (Tier 2) |
| `surface_typo_threshold` | 0.90 | Typo correction cutoff (high = strict) |
| `max_sliding_window` | 3 | Max tokens in sliding window candidates |
| `max_entities` | 5 | Top-K geo candidates returned |
| `max_text_length` | 380 | Messages longer than this → random point |
| `prepositional_boost` | 0.05 | Score bonus for preposition-anchored matches |
| `geometry_min_score` | 0.85 | Min score for geometry intersection participation |
| `pseudo_intersection_radius_meters` | 150.0 | Max distance for pseudo-intersection midpoint |

Processor-specific (in `ProcessorConfig`):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `worker_concurrency` | 5 | Number of concurrent async workers |
| `poll_interval` | 0.5s | Delay between polls when queue is empty |

## Performance Characteristics

- **Startup:** ~2-3s (mawo_pymorphy3 DAWG init ~15-20 MB RAM, geo index build)
- **Per-message latency:** ~5-15ms (stem matching <1ms, fuzzy ~2-5ms, PostGIS ~3-8ms)
- **Throughput:** ~100-200 messages/sec with 5 workers (bottleneck: PostGIS round-trips)
- **Memory:** ~80-120 MB (pymorphy3 DAWG + phonetic index + asyncpg pool)
- **Concurrency model:** async I/O + `ProcessPoolExecutor` for CPU-bound fuzzy matching
- **Backpressure:** `FOR UPDATE SKIP LOCKED` ensures horizontal scalability

## Known Limitations

1. **Single-match geometry bias:** When only 1 geo candidate matches a street, the
   full street geometry (1-3 km LINESTRING) is used instead of a specific point.
   This is addressed by the `midpoint`/`intersection` strategies but requires
   2+ candidates.

2. **Stable geo false positives:** "Ромашковая" matches "Ромашково" (lat=32.43,
   300km from Odessa) — the fuzzy threshold allows this. The geo table needs
   geographic bounding-box filtering.

3. **Layer misclassification:** `блокпост` is in `traffic` keywords but should
   be `cops`. `тцк/тцкашники` are not in any keyword set (falls to `pig`).

4. **Junk message gap:** Promotional/spam messages (vehicle listings, ads) are
   stored as events with `strategy=random` instead of being dropped. The
   `_is_junk()` filter catches only trivial cases.

5. **LLM dependency:** Semantic resolution falls back to pre-filter rules only.
   Complex multi-candidate scenarios (3+ candidates, ambiguous context) may
   produce suboptimal strategies without the LLM.

## Optimization Opportunities

See `OPTIMIZATION_PLAN_NLP_PROCESSOR.md` for the full prioritized list.
Key items:

- **Batch geo lookups:** Multiple `_link_span` calls per message could be batched
  into a single PostGIS query
- **Parallel `_link_span`:** Tier 1 (stem) and Tier 2 (fuzzy) could run
  concurrently via `asyncio.gather`
- **Combined regex in `preprocess_light`:** Multiple `re.sub` calls could be
  merged into a single pass
- **District pre-filter:** Could be pushed into `process_candidates` SQL function
  to avoid an extra round-trip
