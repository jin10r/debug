# CI/CD Pipeline Diagnostic Report

**Дата:** 2026-08-25 17:28–17:33 EDT  
**Ветка:** main  
**Система:** 7.7 GiB RAM + 5.7 GiB swap  
**Среда:** gitlab-ci-local, Docker Compose

---

## 1. Результаты этапов пайплайна

| Job | Stage | Результат | Примечание |
|-----|-------|-----------|------------|
| yaml-lint | .pre | PASS | 20 предупреждений по длине строк (не критично) |
| bandit-scan | security-scan | PASS | No issues identified |
| pip-audit | security-scan | PASS | No known vulnerabilities found |
| hadolint | security-scan | PASS | Только warnings (DL3008) |
| frontend-security | security-scan | **FAIL** | `Telegram is not defined` в `web/js/telegram-init.js:4-5` |
| backend-tests | test | SKIP | Не запущен из-за FAIL на предыдущем stage |
| integration-tests | test | SKIP | Не запущен из-за FAIL на предыдущем stage |
| parser-length-filter | test | SKIP | Не запущен из-за FAIL на предыдущем stage |
| test:settings-strict-bool | test | SKIP | Не запущен из-за FAIL на предыдущем stage |
| test:core-startup-matrix | test | SKIP | Не запущен из-за FAIL на предыдущем stage |
| frontend-build | test | SKIP | Не запущен из-за FAIL на предыдущем stage |
| build:core | build | SKIP | Не запущен из-за FAIL на предыдущем stage |
| build:parser | build | SKIP | Не запущен из-за FAIL на предыдущем stage |
| build:processor | build | SKIP | Не запущен из-за FAIL на предыдущем stage |
| build:web | build | SKIP | Не запущен из-за FAIL на предыдущем stage |
| build:postgres | build | SKIP | Не запущен из-за FAIL на предыдущем stage |
| trivy-scan | image-security | SKIP | Не запущен из-за FAIL на предыдущем stage |
| deploy | deploy | SKIP | `when: manual`, `CI_LOCAL != "true"` |
| deploy:local | deploy | PASS | Выполнен отдельно с `CI_LOCAL=true` |

### Ключевой вывод по пайплайну
Полный прогон прерван на этапе **security-scan** из-за ошибки ESLint (`frontend-security`). Задачи **build:*** с `docker:24-dind` **не были запущены** и соответственно не продемонстрировали таймаут healthcheck, как в предыдущих запусках. `deploy:local` выполнен успешно после отдельного запуска.

---

## 2. Лимиты ресурсов (docker-compose.yml)

| Сервис | CPU limit | Memory limit | Memory reservation |
|--------|-----------|--------------|-------------------|
| postgres | 1.0 | 1 GiB | 512 MiB |
| core | 1.0 | 768 MiB | 128 MiB |
| parser | 0.5 | 256 MiB | 128 MiB |
| nlp_processor | 1.5 | 1 GiB | 512 MiB |
| web | 0.5 | 128 MiB | 64 MiB |
| **ИТОГО** | **4.5** | **~3.1 GiB** | **~1.3 GiB** |

---

## 3. Фактическое потребление памяти после deploy:local

```
CONTAINER ID   NAME            CPU %     MEM USAGE / LIMIT   MEM %
f235837ed189   web             0.00%     3.758 MiB / 128 MiB  2.94%
7b0aa28f53bc   nlp_processor   0.86%     56.73 MiB / 1 GiB    5.54%
00031207d08d   core            0.00%     147.7 MiB / 768 MiB 19.24%
2d1d8ae73816   parser          0.08%     61.99 MiB / 256 MiB  24.22%
16ed65159dc8   postgres        9.76%     100 MiB / 1 GiB      9.77%
```

**Суммарное потребление контейнерами:** ~370 MiB из ~3.1 GiB (**12%** от лимитов)

---

## 4. Диагностика OOM

### 4.1 Сбор данных
- **Всего снапшотов:** 88
- **Интервал:** 5 секунд
- **Период:** ~7 минут 16 секунд

### 4.2 OOM события cgroup v2
```
oom 0
oom_kill 0
oom_group_kill 0
```
**Фактов OOM-киллов не зафиксировано.**

### 4.3 Memory pressure (cgroup v2)
```
some avg10=0.00 avg60=0.00 avg300=0.00
full avg10=0.00 avg60=0.00 avg300=0.00
```
**Давление памяти = 0.00** на всех интервалах для root, user.slice, system.slice.

### 4.4 Потребление памяти хост-системой
```
MemTotal:     7,932 MiB
MemFree:       ~400 MiB
MemAvailable: ~2.6 GiB
SwapTotal:    5,796 MiB
SwapFree:      ~5.8 GiB
```
Активное использование swap минимально (15 MiB).

### 4.5 dmesg
**Сообщений OOM не обнаружено.**

### 4.6 Docker контейнеры (пиковые значения за период мониторинга)
| Контейнер | Пик RSS | Лимит | % от лимита |
|-----------|---------|-------|-------------|
| web | ~5.5 MiB | 128 MiB | 4.3% |
| nlp_processor | ~90 MiB | 1 GiB | 8.8% |
| core | ~150 MiB | 768 MiB | 19.5% |
| parser | ~65 MiB | 256 MiB | 25.4% |
| postgres | ~115 MiB | 1 GiB | 11.3% |

---

## 5. Риски OOM

1. **core (19.5% / 768 MiB):** Нагрузка может расти при увеличении числа подключений. Резерв до лимита ~618 MiB.
2. **parser (25.4% / 256 MiB):** Нагрузка может расти при обработке больших сообщений/медиа. Резерв до лимита ~191 MiB.
3. **postgres (9.77% / 1 GiB):** При росте кэша и соединений может увеличиваться. Резерв до лимита ~900 MiB.
4. **nlp_processor (5.54% / 1 GiB):** С headroom ~944 MiB, но при загрузке больших языковых моделей возможен рост.
5. **Системный стек:** Firefox ESR, KDE Plasma, dockerd конкурируют за память с контейнерами. При запуске build:* с DinD потребуется дополнительно ~1–2 GiB для DinD-сервиса.
6. **Swap:** Доступен 5.7 GiB, но активное использование swap замедлит контейнеры при дефиците RAM.

---

## 6. Рекомендации

1. **Исправить frontend-security:** Добавить ESLint-правило `no-undef` в конфиг для глобального объекта `Telegram` (WebApp), либо подключить типы `@twa-dev/types`.
2. **Для build:* с DinD:** При локальном запуске выделятьDinD-сервису отдельный квот (2 GiB) и использовать `DOCKER_BUILDKIT=1` с внешним кэшем. Альтернатива — предварительно собирать образы через `deploy:local` (как было сделано).
3. **Мониторинг лимитов:** Установить `docker events` или Prometheus/Grafana с cAdvisor для отслеживания `memory.usage_in_bytes` / `memory.limit_in_bytes` и срабатывания OOM.
4. **Swap tuning:** Уменьшить `vm.swappiness` до 10–20, чтобы swap использовался только как буфер, а не как активная память.
5. **Запуск `deploy:local`:** Использовать как основной сценарий локального деплоя (правило G-3), так как он успешно работает без DinD и собирает все сервисы за ~30 секунд.

---

*Отчет сгенерирован автоматически. OOM-диагностика: /tmp/oom-monitor-77703/oom-monitor.log*
