# ✅ Чеклист проверки оптимизаций

## Быстрая проверка после deploy

### 1. Database Connection Pool
```bash
# Проверить, что пул создается с оптимизациями
docker logs core 2>&1 | grep "Database connection pool created"
# Ожидается: "(min=5, max=30, timeout=60s)"

# Проверить активные соединения
docker exec postgres psql -U postgres -d postgres -c "
SELECT datname, state, COUNT(*) as connections
FROM pg_stat_activity
WHERE pid != pg_backend_pid()
GROUP BY datname, state;"
```

### 2. WebSocket Manager
```bash
# Проверить запуск cleanup task
docker logs core 2>&1 | grep "WebSocket cleanup task started"

# Проверить rate limiting (должны появиться при спаме)
docker logs core 2>&1 | grep "ping rate limit exceeded"

# Проверить активные WebSocket соединения
docker logs core 2>&1 | grep "WebSocket connection registered" | tail -5
```

### 3. Cache Manager
```bash
# Проверить запуск фоновой очистки
docker logs core 2>&1 | grep "Cache background cleanup task started"

# Проверить работу cleanup (появится через 1 минуту, если есть протухшие записи)
docker logs core 2>&1 | grep "Background cleanup: removed"
```

### 4. Circuit Breaker
```bash
# Проверить нормальную работу (должно быть CLOSED)
docker logs processor 2>&1 | grep "circuit breaker"

# При проблемах с БД должно появиться
docker logs processor 2>&1 | grep "Circuit breaker: transitioning to OPEN"
```

### 5. Parser Backpressure
```bash
# Проверить размер очереди
docker logs parser 2>&1 | grep "queue at"

# При высокой нагрузке должно появиться
docker logs parser 2>&1 | grep "Backpressure ACTIVE"
docker logs parser 2>&1 | grep "Message.*dropped"
```

## Проверка производительности

### PostgreSQL Performance
```bash
# Top slow queries
docker exec postgres psql -U postgres -d postgres -c "
SELECT calls, ROUND(mean_exec_time::numeric, 2) AS avg_ms, LEFT(query, 80)
FROM pg_stat_statements
WHERE query NOT LIKE '%pg_stat_statements%'
ORDER BY mean_exec_time DESC LIMIT 5;"

# Cache hit ratio (должно быть >99%)
docker exec postgres psql -U postgres -d postgres -c "
SELECT 'index hit rate', 
       ROUND((sum(idx_blks_hit) / NULLIF(sum(idx_blks_hit + idx_blks_read), 0) * 100)::numeric, 2) 
FROM pg_statio_user_indexes
UNION ALL
SELECT 'table hit rate',
       ROUND((sum(heap_blks_hit) / NULLIF(sum(heap_blks_hit + heap_blks_read), 0) * 100)::numeric, 2)
FROM pg_statio_user_tables;"

# Connection pool status
docker exec postgres psql -U postgres -d postgres -c "
SELECT state, COUNT(*) as connections
FROM pg_stat_activity
WHERE pid != pg_backend_pid()
GROUP BY state;"
```

### Processor Metrics
```bash
# Проверить throughput
docker logs processor 2>&1 | grep "processed:" | tail -20

# Проверить ошибки
docker logs processor 2>&1 | grep "ERROR" | tail -10

# Health check
curl http://localhost:8765/health/ready
```

### Parser Metrics
```bash
# Проверить обработанные сообщения
docker logs parser 2>&1 | grep "enqueued" | tail -20

# Проверить масштабирование воркеров
docker logs parser 2>&1 | grep "Scaled"

# Проверить ошибки
docker logs parser 2>&1 | grep "ERROR" | tail -10
```

### Core API Metrics
```bash
# Health check
curl http://localhost:8080/health/ready

# WebSocket connections
docker logs core 2>&1 | grep "Total:" | tail -1

# Cache stats (если есть эндпоинт)
# curl http://localhost:8080/admin/cache/stats
```

## Нагрузочное тестирование

### WebSocket Load Test
```bash
# Установить wscat если нет
npm install -g wscat

# Открыть 10 параллельных соединений
for i in {1..10}; do
  wscat -c ws://localhost:80/ws &
done

# Проверить логи на rate limiting
docker logs core 2>&1 | grep "rate limit"
```

### API Load Test
```bash
# Установить wrk если нет
# apt-get install wrk или brew install wrk

# 10 секунд, 10 соединений, 100 потоков
wrk -t10 -c100 -d10s http://localhost:80/api/events

# Проверить долгие запросы
docker exec postgres psql -U postgres -d postgres -c "
SELECT pid, now() - query_start AS duration, LEFT(query, 60)
FROM pg_stat_activity
WHERE state != 'idle' AND query_start IS NOT NULL
ORDER BY duration DESC LIMIT 5;"
```

## Признаки проблем

### ❌ Проблемы Database
- Connection pool не создается → проверить POSTGRES_PASSWORD
- "too many connections" → увеличить max_connections или уменьшить pool_max_size
- Slow queries >1s → запустить мониторинг запросы

### ❌ Проблемы WebSocket
- "limit 1000 reached" → увеличить MAX_CONNECTIONS
- Нет cleanup логов → проверить start_cleanup_task() вызывается
- Memory leak → проверить _ping_counters очищается при unregister

### ❌ Проблемы Cache
- "too many entries" → увеличить max_size
- High eviction_count → увеличить max_size или уменьшить TTL
- Memory leak → проверить background cleanup работает

### ❌ Проблемы Circuit Breaker
- Постоянно OPEN → проверить БД доступность
- Не переходит в HALF_OPEN → проверить timeout настройку
- False positives → увеличить failure_threshold

### ❌ Проблемы Backpressure
- Много дропнутых сообщений → увеличить maxsize очереди
- Backpressure не активируется → проверить threshold (60/65)
- Queue overflow → добавить больше воркеров

## Rollback план

Если оптимизации вызвали проблемы:

```bash
# 1. Откатить Docker образы
docker-compose down
git checkout HEAD~1  # или конкретный коммит до оптимизаций
docker-compose build
docker-compose up -d

# 2. Или отключить отдельные оптимизации через патчи:

# Отключить circuit breaker (закомментировать проверку в _worker)
# Отключить backpressure (убрать проверку _backpressure_active)
# Отключить rate limiting (увеличить PING_RATE_LIMIT до 1000)

# 3. Проверить восстановление
docker-compose ps
docker logs core
docker logs processor
docker logs parser
```

## Метрики успеха

После применения оптимизаций ожидаются:

- ✅ Cache hit ratio >99%
- ✅ Connection pool utilization 30-70%
- ✅ Нет долгих запросов (>5s)
- ✅ WebSocket latency <100ms
- ✅ Processor throughput +15-20%
- ✅ Memory usage -20-30%
- ✅ Нет ERROR в логах (кроме обработанных retry)

## Следующие шаги

После подтверждения что оптимизации работают:

1. ✅ Prometheus для сбора метрик — развёрнут (`docker-compose.yml`, порт 9090, таргеты: core:8080 + postgres-exporter:9187)
2. Создать Grafana дашборды (Grafana развёрнут на :3000, данные в Prometheus уже идут)
3. Настроить алерты на критичные метрики
4. Запланировать batch INSERT оптимизацию
5. Рассмотреть read replicas для PostgreSQL
