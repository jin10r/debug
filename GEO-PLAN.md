# Survival Map — План: geo.csv + Qwen2.5-0.5B model-service

## 1. Цели

1. ✅ Объединено `streets.csv` (882) + `settlements.csv` (846) в единый `geo.csv` (1728) с 8 типами — `geo` является единственным газеттиром
2. ✅ Создан model-service с Qwen2.5-0.5B-Instruct (Q4_K_M, ~400 MB) как Tier-2 upgrade
3. LLM НЕ видит WKT — только centroid/bbox (~40 символов). PostGIS вычисляет геометрию по ID
4. Tool calling с повышением порога (0.5→0.7→0.85→0.95→0.99) для предотвращения зацикливания
5. `spatial_filter_outliers()` для дизамбигуации омонимов

## 2. Архитектура

```
parser:
  Tier-1 (GeoMatcher) → INSERT event (<50ms)
    └── если random / best_sim < 0.85
         → POST /resolve → model-service (LLM) → UPDATE event (3-15s)

model-service:
  LLM (Qwen2.5-0.5B) ←→ tools.py (search_geo, compute_intersection, ...) ←→ asyncpg ←→ PostGIS
  LLM оперирует только ID — PostGIS вычисляет геометрию
```

## 3. Ключевые архитектурные правила

| # | Правило | Почему |
|---|---------|--------|
| 1 | LLM не видит WKT. `search_geo()` возвращает centroid (~40 символов), не полный полигон (2000+) | 0.5B модель — 32K контекст. Один полигон съедает 6% |
| 2 | LLM передаёт ID в PostGIS. `compute_intersection([12, 45])` → PostGIS делает ST_Intersection | PostGIS — геопространственный калькулятор, LLM — стратег |
| 3 | Каждый цикл повышает min_sim: 0.5 → 0.7 → 0.85 → 0.95 → 0.99 | Предотвращает зацикливание — на каждом шаге кандидатов меньше |
| 4 | `spatial_filter_outliers()` отсекает объекты далеко от кластера | Решает проблему "Александровка ×3" |
| 5 | Tier-1 всегда вставляет событие сразу. Tier-2 только UPDATE | Пользователь видит локацию <50ms, через секунды она уточняется |
| 6 | Tier-2 вызывается только когда Tier-1 не уверен (random / best_sim < 0.85) | LLM не нагружается на простых случаях |
| 7 | Все ошибки LLM → silent fallback к Tier-1 без потери события | Система никогда не зависает из-за LLM |

## 4. Фазы реализации

---

### Фаза 0 — Данные (DONE)

**Вход:** `postgres/data/streets.csv` (882 записи, POINT/LINESTRING/POLYGON), `git show 485741d:postgres/data/settlements.csv` (846 записей, POLYGON, поле `place`)

**Выход:** `postgres/data/geo.csv` (1728 записей, 8 типов)

**Файлы:**
- `scripts/merge_geo.py` ✓ — скрипт слияния

**Правила типизации:**
- streets.csv: default `street`, переклассификация по keywords в любом алиасе:
  - `станция`, `автостанция`, `аэропорт` → `station`
  - `вокзал`, `больница`, `госпиталь`, `поликлиника` → `infrastructure`
  - `парк <имя>`, `сквер`, `лунапарк`, `зоопарк` → `park`
  - `площадь`, `кладбище`, `собор` → `landmark`
  - `рынок`, `базар` → `market`
- settlements.csv: `place` = village→`village`, town/city→`town`
- `порт` исключён (ложные срабатывания: "спорта", "транспорт")

**Результат:**

| Тип | Кол-во |
|-----|--------|
| street | 786 |
| village | 795 |
| town | 51 |
| station | 30 |
| park | 25 |
| landmark | 18 |
| market | 12 |
| infrastructure | 11 |
| **Итого** | **1728** |

---

### Фаза 1 — База данных (DONE)

**Задачи:**
1. Создать таблицу `geo` в `02-tables.sql`
2. Загрузить `geo.csv` через `04-load-data.sql`
3. Создать `geo_matcher.py` — поисковый слой поверх `geo` (rapidfuzz + pg_trgm)

**Таблица:**
```sql
CREATE TABLE geo (
    id SERIAL PRIMARY KEY,
    names TEXT[] NOT NULL,
    geom GEOMETRY(Geometry, 4326) NOT NULL,
    type VARCHAR(20) NOT NULL DEFAULT ''
);
CREATE INDEX idx_geo_names ON geo USING GIN (names);
CREATE INDEX idx_geo_type ON geo USING btree (type);
CREATE INDEX idx_geo_geom ON geo USING GIST (geom);
```

**geo_matcher.py:**
- Принимает: `query: str`, `type_filter: str = ""`, `min_sim: float = 0.5`, `top_k: int = 10`
- Возвращает: `[{id, names, type, centroid_wkt, bbox_wkt, sim}]`
- Использует `pg_trgm` + нормализацию через pymorphy3 (HTTP к parser)
- Фильтр по типу если задан
- `street`-тип ищется LINESTRING → centroid (аппроксимация точкой)

**Миграция:**
- `streets` и `settlements` — удалены, `geo` является единственным газеттиром
- Парсер использует `geo` через `geo_matcher.py`

---

### Фаза 2 — Инструменты (model/tools.py) (DONE)

**Принцип: LLM → ID → tools.py → asyncpg → PostGIS → результат без WKT**

```python
# tools.py — каждый tool вызывается LLM по имени
# LLM НИКОГДА не получает полный WKT — только centroid + bbox

async def search_geo(query, type_filter="", top_k=10, min_sim=0.5) -> list[dict]:
    """Поиск в geo. Возвращает [{id, names, type, centroid_wkt, bbox_wkt, sim}]"""
    # centroid: ST_AsText(ST_Centroid(geom)) — POINT, ~40 символов
    # bbox: ST_AsText(ST_Envelope(geom)) — POLYGON, ~80 символов
    # НИКОГДА не возвращает полный WKT

async def get_geo_info(gids: list[int]) -> list[dict]:
    """Информация об объектах: id, names, type, centroid, bbox"""

async def compute_intersection(gids: list[int]) -> dict | None:
    """ST_Intersection(a.geom, b.geom). PostGIS делает — LLM получает результат."""

async def compute_convex_hull(gids: list[int]) -> dict:
    """ST_ConvexHull(ST_Collect(geom)) для polygon_intersection"""

async def compute_distance(gids: list[int]) -> dict:
    """ST_Distance, ST_MaxDistance — расстояния между объектами"""

async def get_nearby(gid: int, radius_m=500, limit=5) -> list[dict]:
    """ST_DWithin — объекты в радиусе. Возвращает [{id, name, type, distance_m}]"""

async def spatial_filter_outliers(gids: list[int], threshold=2.0) -> list[int]:
    """Фильтр омонимов:
    1. ST_Centroid(ST_Collect(geom)) — средний центр кластера
    2. Для каждого: ST_Distance(centroid, geom)
    3. Удалить outliers: dist > mean_dist + threshold * stddev_dist
    4. Вернуть отфильтрованный список ID
    Пример: "Александровка ×3" → spatial_filter_outliers([12,45,78]) → [45, 78]
    """

async def normalize_text(text: str) -> list[dict]:
    """pymorphy3: нормализация. Вызов HTTP → parser /morphology"""
```

---

### Фаза 3 — Model service (model/) (DONE)

**Структура:**
```
model/
├── server.py           # aiohttp: POST /resolve, GET /health
├── engine.py           # Llama() singleton (llama-cpp-python)
├── tools.py            # инструменты из Фазы 2
├── pipeline.py         # tool calling loop с повышением порога
├── model_manager.py    # скачивание GGUF при первом старте
├── requirements.txt    # llama-cpp-python, aiohttp, asyncpg, ...
├── Dockerfile.model    # multi-stage build
├── models/             # volume для GGUF (сохраняется между рестартами)
│   └── qwen2.5-0.5b-instruct-q4_k_m.gguf
└── tests/
    ├── test_tools.py
    └── test_pipeline.py
```

**pipeline.py — Tool calling loop:**
```python
MAX_TOOL_TURNS = 5
MIN_SIM_CYCLES = [0.5, 0.7, 0.85, 0.95, 0.99]

async def resolve_location(text, tier1=None) -> dict:
    messages = [SYSTEM_PROMPT, USER_MESSAGE(text, tier1)]
    for turn in range(MAX_TOOL_TURNS):
        min_sim = MIN_SIM_CYCLES[turn]
        messages.append({"role": "system",
            "content": f"[Цикл {turn+1}/{MAX_TOOL_TURNS}] Порог: min_sim={min_sim}"})
        response = llm.chat(messages, tools=TOOLS, temperature=0.0)
        if response.get("tool_calls"):
            for tc in response["tool_calls"]:
                result = await execute_tool(tc, min_sim=min_sim)
                messages.append({"role": "tool", ...})
        else:
            return parse_response(response["content"])
    return fallback(text, "max_turns_exceeded")
```

**System prompt (сокращённый):**

```
Ты — гео-матчер для карты событий Одессы.

Инструменты:
1. normalize_text(words) — нормализация падежных форм
2. search_geo(query, type_filter, top_k, min_sim) — поиск в geo (возвращает centroid, не WKT!)
3. get_geo_info(gids) — детали объектов
4. compute_intersection(gids) — точка пересечения (PostGIS)
5. compute_convex_hull(gids) — полигон зоны (PostGIS)
6. compute_distance(gids) — расстояние (PostGIS)
7. get_nearby(gid, radius_m, limit) — объекты рядом (PostGIS)
8. spatial_filter_outliers(gids) — отсев омонимов по расстоянию

Алгоритм:
1. Извлеки топонимы. Составные (2+ слова) в приоритете.
2. Нормализуй через normalize_text, ищи через search_geo.
3. Если омонимы → spatial_filter_outliers().
4. Выбери стратегию:
   - 1 match → single_match (centroid объекта)
   - "/", "пересечение" → compute_intersection()
   - "между" → compute_convex_hull()
   - 0 matches → random

Ответ (JSON): {strategy, matches: [{id, name, type, sim}], geom_wkt, confidence, explanation}
geom_wkt = финальный результат PostGIS, не WKT из таблицы.
```

**engine.py:**
- Llama singleton (загружается один раз при старте сервера)
- `model_path` из env `MODEL_PATH`
- `n_ctx=8192` (32K контекст модели, но для экономии RAM ограничиваем 8K)
- `n_batch=8` (CPU, маленький batch)

**server.py:**
- aiohttp, один endpoint `POST /resolve`
- Принимает: `{text, context: {tier1: ...}}`
- Вызывает `pipeline.resolve_location(text, tier1)`
- Возвращает: `{strategy, matches, geom_wkt, confidence, explanation}`
- timeout на весь pipeline: 30s

**Dockerfile.model:**
```dockerfile
FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y libgomp1 libatomic1 curl ca-certificates
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python -c "from model_manager import ensure_model; ensure_model()"
EXPOSE 9092
HEALTHCHECK CMD curl -f http://localhost:9092/health || exit 1
CMD ["python", "-m", "model.server"]
```

**docker-compose модель:**
```yaml
model-service:
  build:
    context: ./model
    dockerfile: Dockerfile.model
  ports: ["127.0.0.1:9092:9092"]
  environment:
    - DB_DSN=postgres://postgres:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
    - MODEL_PATH=/app/models/qwen2.5-0.5b-instruct-q4_k_m.gguf
    - HF_MODEL_REPO=Qwen/Qwen2.5-0.5B-Instruct-GGUF
    - HF_MODEL_FILE=qwen2.5-0.5b-instruct-q4_k_m.gguf
  volumes:
    - ./model/models:/app/models:rw
  deploy:
    resources:
      limits: {cpus: '1.5', memory: 1024M}
      reservations: {cpus: '1.0', memory: 768M}
  healthcheck:
    test: curl -f http://localhost:9092/health
    interval: 30s; timeout: 10s; retries: 3; start_period: 30s
```

---

### Фаза 4 — Интеграция с парсером (DONE)

**model_client.py:**
```python
# parser/model_client.py
async def resolve_location(text, tier1_result=None) -> dict | None:
    """POST /resolve к model-service. timeout=15s. Любая ошибка → None."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        async with s.post("http://model-service:9092/resolve",
            json={"text": text, "context": {"tier1": tier1_result}}) as resp:
            return await resp.json() if resp.status == 200 else None
```

**Интеграция в message_processor.py:**
```python
async def process_message(self, msg_data):
    # Tier-1: быстрый матчинг + INSERT (событие на карте за <50ms)
    strategy, matches, geom = await self._tier1_match(msg_data)
    event_id = await self._insert_event(msg_data, strategy, matches, geom)

    # Tier-2: LLM уточнение в фоне
    if strategy == 'random' or (matches and max(m.sim for m in matches) < 0.85):
        llm_result = await model_client.resolve_location(msg_data.text,
            {"strategy": strategy, "matches": matches})
        if llm_result and llm_result.get("confidence", 0) > 0.5:
            await self._update_event(event_id, llm_result["strategy"],
                llm_result["matches"], llm_result["geom_wkt"])
    return event_id
```

---

### Фаза 5 — Тестирование

**geo.csv:**
```bash
# Валидация: все WKT парсятся, нет дубликатов, типы разрешены
python scripts/validate_geo.py
# Сравнение: каждая запись streets+settlements представлена в geo
python scripts/compare_geo.py
```

**model-service smoke tests:**
```bash
# Одиночный матч
curl -X POST http://localhost:9092/resolve \
  -d '{"text":"На Сибирской блокпост","context":{}}'
# → single_match, id: Сибирская

# Перекрёсток
curl -X POST http://localhost:9092/resolve \
  -d '{"text":"Королёва/Костанди менты","context":{}}'
# → single_intersection, compute_intersection([49, 291])

# Омонимы
curl -X POST http://localhost:9092/resolve \
  -d '{"text":"Александровка дпс","context":{}}'
# → spatial_filter_outliers → отсеяны дальние Александровки

# Нет топонимов
curl -X POST http://localhost:9092/resolve \
  -d '{"text":"Тцк","context":{}}'
# → random

# Settlement не разбивается
curl -X POST http://localhost:9092/resolve \
  -d '{"text":"Сухий Лиман блокпост","context":{}}'
# → single_match, type=village, НЕ "Сухой" + "Лиманная"
```

**Integration tests (pytest):**
```python
async def test_tier1_tier2_flow():
    msg = MockMessage("На Сибирской блокпост")
    r1 = await tier1_match(msg)  # random (Сибирская может не быть в gazetteer)
    r2 = await resolve_location(msg.text, r1)
    assert r2["strategy"] == "single_match"
    assert r2["matches"][0]["name"] == "Сибирская"

async def test_homonym_filter():
    r = await resolve_location("Александровка дпс")
    matched = [m for m in r["matches"] if m["name"] == "Александровка"]
    assert len(matched) <= 2  # хотя бы 1 из 6 отсеян
```

**QA на датасете 67 событий:**
- Сравнить Tier-1 vs Tier-2 precision
- Замерить latency (p50/p95/p99)
- Проверить fallback (kill model-service → parser работает без LLM)

---

## 5. Chain of fallback

```
LLM timeout (15s) / error
  └─→ Tier-1 результат остаётся (уже на карте)

LLM confidence < 0.5
  └─→ Tier-1 результат

LLM strategy = random
  └─→ Tier-1 результат (тоже random — ничего не потеряно)

model-service недоступен
  └─→ parser работает с Tier-1 без LLM

LLM зациклилась (>5 turns)
  └─→ Tier-1 результат

LLM не вызвала tools на 1-м turn
  └─→ Принудительно вызвать search_geo для извлечённых кодом сущностей
```

## 6. Non-goals

- ~~Удаление `streets`/`settlements` (через месяц после стабильной работы `geo`)~~ ✅ Выполнено
- Классификация слоёв (pig/cops/bus/traffic) — LayerClassifier
- Определение времени события — text_preprocessor
- GPU-инференс — только CPU
- Замена Tier-1 на LLM — LLM только дополнение
- Дообучение модели — только инференс
- MCP / LangChain / agent frameworks — 30 строк кода проще и быстрее

## 7. Roadmap

| Фаза | Что | Статус |
|------|-----|--------|
| **0** | `geo.csv` + `merge_geo.py` | ✅ |
| **1** | `02-tables.sql` + `04-load-data.sql` + `geo_matcher.py` | ✅ |
| **2** | `model/tools.py` (search_geo, compute_*, spatial_filter_outliers) | ✅ |
| **3** | `model/` — engine, pipeline, server, Dockerfile | ✅ |
| **4** | `model_client.py` + интеграция в message_processor | ✅ |
| **5** | Smoke tests + pytest + QA на 67 событиях | ⬜ |
