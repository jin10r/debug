# Session Context — 16 July 2026

## Summary
Полный рефакторинг: NLP-пайплайн вынесен из parser в отдельный processor сервис. Добавлена интеграция локальной LLM (Qwen2.5-0.5B) через llama-cpp-python. Написаны правила проекта (RULES).

## Key Changes
- `processor/` — новый сервис с NLP pipeline (tokenizer → lemmatize → classify → find_geo → resolve)
- `parser/` — очищен от NLP-кода, теперь только парсинг Telegram + запись в pending_events
- `docs/RULES*.md` — полный набор правил для всех сервисов
- `postgres/data/geo.csv` — исправлена геометрия Шовкуненко (POLYGON→LINESTRING, продлён до пересечения Черняховского и Говорова)
- LLM integration: LLMBackend, LLMLayerResolver, LLMStrategyResolver, UnifiedLLMResolver, BatchProcessor
- R-PR8.1: каждое сообщение отображается на фронтенде (LLM junk → random, а не дроп)

## Current State
- Dockerfile.processor готов (multi-stage, сборка llama-cpp-python с OpenBLAS)
- processor/main.py имеет принудительный LLM на каждое сообщение (если включён)
- Все 56 тестов проходят

## Known Issues
- Для работы LLM нужны: модель Qwen2.5-0.5B-Q4_K_M.gguf в models/ и `llama.enabled=true` в конфиге
- Образ processor требует пересборки (docker compose build processor)
- build-essential, pkg-config, libopenblas-dev нужны в builder-stage
- libgomp1, libopenblas0 нужны в runtime-stage
- В geo.csv всё ещё могут быть ложные срабатывания матчинга (см. events_202607161439.csv — анализ качества)

## Next Steps
1. Собрать и запустить processor с LLM
2. Поместить модель в models/
3. Включить llama.enabled=true
4. Протестировать качество LLM-классификации на реальных событиях
5. (Unrelated) Написать docs/NLP_PROCESSOR.md если ещё нет
