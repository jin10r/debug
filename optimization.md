# Комплексный план оптимизации Survival Map

План охватывает все аспекты оптимизации: производительность, ресурсы, масштабируемость, надёжность, безопасность, упрощение деплоя, а также очистку проекта от лишних файлов и мёртвого кода.

## 1. Очистка проекта от лишних файлов и мёртвого кода

### 1.1 Удаление пустых и неиспользуемых директорий
- **Удалить:** `model/` - пустая директория (сервис model удалён из архитектуры)
- **Удалить:** `parser/models/e5-small-onnx/` - пустая директория (модель e5-small больше не используется)
- **Удалить:** `model/models/` - пустая вложенная директория

### 1.2 Удаление временных файлов
- **Удалить:** `logs.txt` - старые логи (35 строк от 2026-06-29)
- **Удалить:** `events_export.csv` - временный файл экспорта событий

### 1.3 Удаление дублирующихся файлов документации
- **Удалить:** `PARSER_OPTIMIZATION_PLAN.md` (в корне) - дубликат `docs/POSTGRES_OPTIMIZATION_PLAN.md`
- **Удалить:** `POSTGRES_OPTIMIZATION_PLAN.md` (в корне) - дубликат `docs/POSTGRES_OPTIMIZATION_PLAN.md`
- **Оставить:** только версии в `docs/` для централизации документации

### 1.4 Очистка неиспользуемых импортов и кода
- **Проверить:** `parser/semantic_resolver.py` - использует Ollama, но сервис model удалён
  - Если Ollama не используется - упростить код или удалить
  - Если используется внешняя Ollama - оставить как есть
- **Проверить:** все Python файлы на неиспользуемые импорты (можно использовать `autoflake` или `ruff`)

## 2. Исправление критических ошибок

### 2.1 Исправление SQL ошибки в `clean_old_events()`
**Проблема:** Ошибка `column reference "relname" is ambiguous` в функции `clean_old_events()` (строка 16 в `postgres/init-scripts/03-functions.sql`)

**Причина:** JOIN двух таблиц `pg_class` с одинаковым именем колонки `relname` без алиасов

**Решение:** Добавить алиасы для всех колонок `relname`:
```sql
-- Заменить строку 16:
SELECT c.relname
-- На:
SELECT c.relname AS partition_name
```

**Влияние:** Критично - pg_cron не может выполнять очистку старых событий

## 3. Оптимизация производительности

### 3.1 Parser - оптимизация CPU-bound операций
**Текущее состояние:** ~200-400ms на сообщение, GIL ограничивает один процесс

**Оптимизации:**
- Увеличить `worker_concurrency` с 3 до 5 (в `core/settings.py:ParserConfig`)
- Увеличить `message_queue_maxsize` с 60 до 100 для обработки всплесков
- Увеличить `history_limit` с 60 до 100 для лучшего recovery после краша
- ProcessPoolExecutor уже используется в `geo_matcher.py` - оставить как есть

**Ожидаемый эффект:** +20-30% throughput на многоядерных системах

### 3.2 PostgreSQL - оптимизация конфигурации
**Текущее состояние:** 1 CPU, 1GB RAM, базовая конфигурация

**Оптимизации:**
- Увеличить `shared_buffers` с 256MB до 384MB (в `postgres/init-scripts/`)
- Увеличить `effective_cache_size` с 384MB до 768MB
- Увеличить `work_mem` с 4MB до 8MB
- Увеличить `maintenance_work_mem` с 64MB до 128MB
- Уменьшить `autovacuum_naptime` с 30s до 20s для high-churn таблицы events

**Ожидаемый эффект:** +30-40% query performance

### 3.3 Core - оптимизация connection pool
**Текущее состояние:** `pool_min_size=5, pool_max_size=20`

**Оптимизации:**
- Увеличить `pool_max_size` с 20 до 30 (в `core/settings.py:DatabaseConfig`)
- Добавить мониторинг использования pool через Prometheus metrics

**Ожидаемый эффект:** +5-10% reduction в DB latency при высокой нагрузке

### 3.4 Web - nginx оптимизация
**Текущее состояние:** базовая конфигурация

**Оптимизации:**
- Включить gzip сжатие для статических файлов
- Добавить кэширование статических assets (CSS/JS/images)
- Оптимизировать worker_processes и worker_connections

**Ожидаемый эффект:** +20-30% быстрее загрузка фронтенда

## 4. Оптимизация использования ресурсов

### 4.1 Docker resource limits
**Текущее состояние:**
- postgres: 1 CPU / 1GB RAM
- parser: 1 CPU / 512MB RAM
- core: 1 CPU / 512MB RAM
- web: 0.5 CPU / 128MB RAM

**Оптимизации:**
- Увеличить parser RAM с 512MB до 768MB (для DAWG + кэши)
- Увеличить core RAM с 512MB до 768MB (для WebSocket connections)
- Оставить postgres как есть (1GB достаточно)

### 4.2 Memory оптимизация в parser
- Увеличить LRU cache sizes для morphology (с 10K до 20K)
- Добавить мониторинг cache hit/miss ratios
- Рассмотреть квантизацию ONNX модели e5-small до INT8 (если используется)

**Ожидаемый эффект:** -15-20% memory footprint при той же производительности

## 5. Масштабируемость

### 5.1 Подготовка к horizontal scaling
**Текущее состояние:** один экземпляр каждого сервиса

**Оптимизации:**
- Вынести JWT secret в Docker secret или env (сейчас автогенерируется при старте)
- Добавить Redis для distributed cache (вместо in-memory LRU в core)
- Разделить каналы Telegram между несколькими parser instances

**Приоритет:** P1 - необходимо для масштабирования

### 5.2 PostgreSQL read replica
**Текущее состояние:** одна нода БД

**Оптимизации:**
- Добавить конфигурацию для streaming replica
- Настроить read queries routing в core
- Использовать Patroni для HA (долгосрочно)

**Приоритет:** P2 - для высокой нагрузки

### 5.3 Parser partitioning
**Текущее состояние:** один parser обрабатывает все каналы

**Оптимизации:**
- Добавить поддержку распределения каналов между instances
- Использовать distributed queue (NATS/RabbitMQ) или разделить каналы в конфиге

**Приоритет:** P1 - для масштабирования

## 6. Надёжность

### 6.1 Healthchecks улучшение
**Текущее состояние:** базовые healthchecks

**Оптимизации:**
- Parser: заменить heartbeat-файл на HTTP endpoint с проверкой очереди и workers
- Core: добавить проверку DB connection и WebSocket loop
- PostgreSQL: добавить проверку replication lag (если replica)

### 6.2 Graceful shutdown
**Текущее состояние:** `stop_grace_period: 30s` для всех сервисов

**Оптимизации:**
- Увеличить parser `stop_grace_period` с 30s до 60s (для drain очереди)
- Добавить graceful shutdown для WebSocket connections в core

### 6.3 Monitoring и alerting
**Текущее состояние:** Prometheus metrics экспортируются, но нет сборщика

**Оптимизации:**
- Добавить Prometheus в docker-compose
- Добавить Grafana с готовыми дашбордами
- Настроить алерты для:
  - High latency (>500ms)
  - Memory usage (>80%)
  - Queue depth (>50)
  - Error rate spikes (>1%)

**Приоритет:** P1 - для production

### 6.4 Backup и recovery
**Текущее состояние:** нет автоматических бэкапов

**Оптимизации:**
- Добавить pg_dump cron job для ежедневных бэкапов
- Настроить WAL archiving для point-in-time recovery
- Хранить бэкапы externally (S3 или volume)

**Приоритет:** P1 - для production data safety

## 7. Безопасность

### 7.1 Secrets management
**Текущее состояние:** JWT secret автогенерируется, BOT_TOKEN в .env

**Оптимизации:**
- Вынести JWT secret в Docker secret или env переменную
- Вынести session.session в Docker secret (сейчас монтируется как volume)
- Добавить .env в .gitignore (уже есть, но проверить)

### 7.2 TLS/SSL
**Текущее состояние:** nginx без HTTPS

**Оптимизации:**
- Добавить Let's Encrypt с certbot или Cloudflare Tunnel
- Настроить HTTPS termination на nginx или reverse proxy

**Приоритет:** P0 - критично для production Mini App

### 7.3 Security hardening
**Текущее состояние:** базовые security_opt в docker-compose

**Оптимизации:**
- Добавить `set_real_ip_from` в nginx для X-Forwarded-For
- Убрать лишние capabilities в web (CHOWN, SETGID, SETUID, DAC_OVERRIDE)
- Добавить rate limiting на уровне nginx (уже есть, но проверить)

### 7.4 Input validation
**Текущее состояние:** базовая валидация

**Оптимизации:**
- Добавить Pydantic field constraints в API models (min_length, max_length, ge, le)
- Усилить валидацию Telegram initData
- Добавить CSRF token validation (уже есть, но проверить)

## 8. Упрощение деплоя и CI/CD

### 8.1 CI/CD pipeline
**Текущее состояние:** базовый .gitlab-ci.yml

**Оптимизации:**
- Добавить автоматические тесты при каждом push
- Добавить автоматический build Docker images
- Добавить автоматический deploy на staging/production
- Добавить rollback mechanism

**Приоритет:** P1 - для автоматизации

### 8.2 Docker optimization
**Текущее состояние:** multi-stage builds

**Оптимизации:**
- Использовать BuildKit для кэширования слоёв
- Оптимизировать порядок слоёв для лучшего кэширования
- Уменьшить размер images (алpine-slim уже используется)

### 8.3 Configuration management
**Текущее состояние:** .env + hardcoded defaults в settings.py

**Оптимизации:**
- Централизовать все tunable параметры в settings.py (уже сделано)
- Добавить validation всех конфигурационных параметров при старте
- Добавить configuration drift detection

## 9. Порядок реализации

### Phase 1: Критические исправления (неделя 1) ✅ ВЫПОЛНЕНО
1. ✅ Исправить SQL ошибку в `clean_old_events()` - **P0**
2. ✅ Удалить лишние файлы и директории - **P0**
3. ✅ Увеличить resource limits в docker-compose - **P1**

### Phase 2: Производительность (неделя 2) ✅ ВЫПОЛНЕНО
1. ✅ Оптимизировать PostgreSQL конфигурацию - **P1** (уже была оптимизирована)
2. ✅ Оптимизировать parser worker concurrency - **P1**
3. ✅ Включить nginx gzip и кэширование - **P2** (уже было включено)
4. ✅ Увеличить parser stop_grace_period - **P2**

### Phase 3: Надёжность и мониторинг (неделя 3-4)
1. Добавить Prometheus + Grafana - **P1**
2. Улучшить healthchecks - **P1**
3. Добавить бэкапы - **P1**
4. Настроить TLS/SSL - **P0**

### Phase 4: Масштабируемость (неделя 5-6)
1. Вынести JWT secret в Docker secret - **P1**
2. Добавить Redis для distributed cache - **P1**
3. Подготовить конфигурацию для parser scaling - **P1**

### Phase 5: CI/CD и автоматизация (неделя 7-8)
1. Настроить CI/CD pipeline - **P1**
2. Оптимизировать Docker builds - **P2**
3. Добавить автоматические тесты - **P1**

## 10. Метрики успеха

**Производительность:**
- Сократить время обработки сообщения с 200-400ms до <150ms
- Сократить latency API ответов на 30%
- Увеличить throughput на 50%

**Ресурсы:**
- Сократить memory footprint на 15-20%
- Улучшить cache hit ratio до >80%
- Сократить CPU usage на 20%

**Надёжность:**
- Достичь 99.9% uptime
- Сократить MTTR (Mean Time To Recovery) до <5 минут
- Zero data loss

**Безопасность:**
- Включить TLS/SSL для production
- Все secrets в Docker secrets
- Усилить input validation

**Деплой:**
- Автоматический CI/CD pipeline
- Автоматические тесты
- One-command deploy

## 11. Риски и митигация

**Риски производительности:**
- Тестировать все изменения в staging
- Мониторить метрики при rollout
- Иметь rollback plan

**Риски совместимости:**
- Backward compatibility для API
- Graceful degradation для внешних сервисов (Ollama)
- Feature flags для новых функций

**Риски данных:**
- Бэкапы перед schema changes
- Тестировать миграции на копии данных
- Валидировать data integrity после изменений
