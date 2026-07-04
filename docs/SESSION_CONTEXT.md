# Session Context — Intelligent Semantic Analyzer

**Дата:** 2026-07-01
**Ветка:** `feat/geo-merge-llm-model`
**Последний коммит:** `51af953` — "Commit current state before semantic matcher integration"

---

## Что было сделано до этой сессии

Весь рефакторинг (`feat/geo-merge-llm-model`) — уже реализован, не закоммичен:
- Унификация `geo`-справочника (вместо `streets`)
- Новый `GeoMatcher` (T1/T2) взамен `StreetMatcher`
- Рефакторинг слоя БД (`db_geo.py`, `db_events.py`, `Request`-фасад)
- Партиционирование events, `clean_old_events`
- Батчинг, пул воркеров, retry, pg_notify
- Docker Compose с healthcheck/resource limits
- Тесты под `GeoMatcher`

---

## Текущая сессия — обсуждение семантического анализатора

### Исходная проблема (из предыдущей сессии в opencode)
GeoMatcher не может разрешать семантические конфликты: одноимённые объекты, контекстная привязка, направления. Нужен интеллектуальный анализатор на CPU с быстрым инференсом.

### Ключевое решение
**Модель НЕ вычисляет геометрию** — только анализирует семантику и выбирает стратегию + список geo_ids. PostGIS делает финальное вычисление. Это исключает галлюцинации.

### Утверждённые решения (v2 плана)

1. **Стратегии:**
   - `single_match` — возвращает **полную геометрию** единственного объекта
   - `intersection` — пересечение двух объектов
   - `midpoint` — объединяет старые `pseudo_intersection` и `midpoint`, только для типов `street`, `market`, `station`, `park`, `landmark`, расстояние ≤ 150м
   - `random` — fallback

2. **Температура модели = 0** (детерминированный вывод)

3. **Pre-filter до модели** — быстрые правила для очевидных случаев (типы, расстояния, предлоги)

4. **Модель необязательна** — fallback на текущую `process_candidates`

### Обсуждение MiniLLM
Рассматривался вариант in-container модели вместо Ollama на хосте.
- `minillm` (PyPI) — слишком ограничен (только встроенные модели, заморожен с 2024)
- **Рекомендация:** `llama-cpp-python` + Qwen2.5-0.5B GGUF Q4_K_M (~350MB) в Dockerfile.parser
- Решение по выбору движка отложено до этапа реализации

### Что не реализовано
План утверждён, реализация не начиналась. Этапы:
1. Обновить стратегии в БД (constraint + SQL функции)
2. Написать `SemanticResolver` (pre-filter + промпт + вызов модели)
3. Модифицировать `message_processor.py`
4. Обновить `SimilarityConfig`
5. Написать тесты

---

## Файлы плана
- `docs/SEMANTIC_ANALYZER_PLAN.md` — полный план реализации
- `docs/SESSION_CONTEXT.md` — этот файл (контекст сессии для возобновления)

## Команды для возобновления
```bash
# Текущее состояние
git status
git diff --stat

# Запуск тестов
pytest tests/test_street_matcher.py -v

# Сборка и запуск
docker-compose up --build
```
