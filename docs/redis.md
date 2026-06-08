# Redis microservice — session/nonce store

Сервис `redis` (`redis:7-alpine`) — хранилище сессий и nonce для аутентификации
сервиса `core`. Во внутренней сети (`backend`, нет `ports:`) — наружу не
публикуется. Пароль захардкожен (`redis`), синхронизирован с
`core/settings.py RedisConfig`.

## Конфигурация (`docker-compose.yml`)

```
redis-server --appendonly yes
             --maxmemory 256mb
             --maxmemory-policy allkeys-lru
             --requirepass redis
```

- `appendonly yes` — AOF-персистентность (том `redis_data`).
- `maxmemory 256mb` + `allkeys-lru` — потолок памяти с вытеснением по LRU.
- `requirepass` — пароль (внутри изолированной сети, наружу доступа нет).

## Использование

Подключается только сервис `core` через `RedisManager`
(`core/middlewares/auth.py`): `Redis.from_url(redis://redis:6379/0, password=…)`.
Хранит session/nonce данные аутентификации.

**Деградация, не отказ:** при недоступности Redis `core` переходит на in-memory
fallback, а `/health/ready` помечает redis как `degraded` (не `unhealthy`).

> Не путать с in-memory кэшем событий `core/utils/cache.py` (TTL+LRU внутри
> процесса `core`) — это отдельный механизм, Redis в нём не участвует.
