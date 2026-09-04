Implementation Plan - Security-First Optimization for Survival Map v2.1
Problem Statement: Проект Survival Map (5 микросервисов + фронтенд) требует оптимизации с приоритетом на безопасность. Обнаружены критичные уязвимости (XSS через photo_url, session.session в репозитории, deprecated API), проблемы конфигурации и технический долг. Необходимо обновить codebase-review.md и создать план исправлений.

Requirements:

Security-first подход (исправить все уязвимости)
Полный охват всех 5 сервисов + фронтенд + документация
Критичные улучшения Docker security из best practices
Обновление документации codebase-review.md
Background: Анализ выявил:

Исправленные проблемы (с прошлого ревью): stale .js → .ts, нет deprecated get_event_loop в большинстве мест
Новые проблемы: XSS в photo_url, session.session в git (игнорируется, но файл существует), deprecated asyncio.get_event_loop в CircuitBreaker
Частично исправленные: tsconfig strict:false, trailing comma в cops keywords
Best practices: Docker hardening применён частично, есть возможности улучшения
Proposed Solution: Поэтапный план с приоритизацией по security → performance → maintainability. Каждый таск завершается демонстрируемым результатом.

Task Breakdown:
Task 1: Security Audit - Session File Exposure (HIGH)

Objective: Удалить parser/session.session из git-отслеживания и проверить историю
Implementation:
Проверить, отслеживается ли файл: git ls-files parser/session.session
Если отслеживается: удалить через git rm --cached parser/session.session
Убедиться, что .gitignore содержит *.session и *.session-journal
Проверить git history на наличие секретов (git log с фильтром)
Создать .env.example с заглушкой для session path
Test: git status не показывает session.session; git log не содержит секретов
Demo: git ls-files | grep session возвращает пусто
Task 2: XSS Fix - photo_url Sanitization in map.ts (HIGH)

Objective: Исправить XSS-уязвимость при вставке photo_url в img src
Implementation:
Создать функцию sanitizeUrl() в web/js/core/map.ts
Использовать encodeURIComponent или DOMPurify для sanitization
Добавить whitelist для разрешённых доменов (/media/events/, /api/media/)
Применить sanitization в createPopupContent()
Добавить unit-тест для XSS-векторов (javascript:, data:, onerror=)
Test: Тесты XSS-векторов проходят (javascript: blocked, onerror escaped)
Demo: Попап с вредоносным photo_url не выполняет JS
Task 3: Settings Hardening - Trailing Comma & Password Default (HIGH)

Objective: Исправить баги конфигурации в common/settings.py
Implementation:
Убрать trailing comma после 'мусорской' в cops keywords (создаёт пустую строку)
Добавить валидацию POSTGRES_PASSWORD в load_settings() (как JWT_SECRET)
Создать функцию _resolve_postgres_password() с fail-fast логикой
Обновить error messages с ссылками на правила (R-C8, G-9)
Test: Unit-тесты: keywords не содержат ''; POSTGRES_PASSWORD="" падает при старте
Demo: python -c "from common.settings import DEFAULT_LAYER_KEYWORDS; print('cops' in DEFAULT_LAYER_KEYWORDS['cops'])" = True
Task 4: Deprecation Fix - asyncio.get_event_loop() (MEDIUM)

Objective: Заменить deprecated asyncio.get_event_loop() на get_running_loop()
Implementation:
Найти все вхождения через grep: processor/main.py (CircuitBreaker)
Заменить на asyncio.get_running_loop() с try/except RuntimeError
Добавить fallback для синхронного контекста (если нужен)
Обновить документацию RULES_PROCESSOR.md
Test: pytest проходит; warning DeprecationWarning не появляется
Demo: python -W error::DeprecationWarning -m processor.main не падает
Task 5: TypeScript Strict Mode - Incremental Enablement (MEDIUM)

Objective: Включить strictNullChecks в tsconfig.json для type safety
Implementation:
Изменить "strictNullChecks": false → true в tsconfig.json
Запустить npm run typecheck
Исправить выявленные ошибки типа (поэтапно, файл за файлом)
Начать с core/.ts файлов, затем modules/.ts
Добавить | undefined для nullable полей
Test: npm run typecheck проходит без ошибок
Demo: npm run typecheck exit code 0
Task 6: Docker Security Enhancement - Healthcheck & Restart Policies (MEDIUM)

Objective: Улучшить Docker security и resilience на основе best practices
Implementation:
Добавить healthcheck для parser (curl на heartbeat file)
Добавить restart: on-failure для всех сервисов
Увеличить stop_grace_period для processor (NLP cleanup)
Добавить security_opt: no-new-privileges для postgres (уже есть для остальных)
Документировать изменения в RULES_POSTGRES.md
Test: docker-compose up -d; docker ps показывает healthy статус
Demo: docker inspect parser | jq '.[0].State.Health.Status' = "healthy"
Task 7: Performance Optimization - NLP Pipeline Caching (MEDIUM)

Objective: Оптимизировать GeoMatcher и PhoneticIndex для снижения CPU/памяти
Implementation:
Добавить TTL-кэш для fuzzy match результатов (functools.lru_cache)
Оптимизировать rebuild PhoneticIndex при geo_updated (инкрементальный)
Добавить timeout для ProcessPoolExecutor (из geo_matcher.py)
Мониторинг: логировать cache hit/miss ratio
Test: Unit-тесты NLP проходят; производительность +10% (benchmark)
Demo: Обработка 100 сообщений в processor логирует cache hits
Task 8: Documentation Update - codebase-review.md v2.2 (LOW)

Objective: Актуализировать docs/codebase-review.md с учётом исправлений
Implementation:
Обновить секцию "Найденные проблемы" (переместить исправленные в "Исправлено")
Добавить новые проблемы (если найдены) с приоритетами
Обновить секцию "Статус исправлений" с новыми пунктами
Добавить секцию "Security Improvements" с чеклистом
Обновить дату и версию документа
Test: Документ соответствует текущему состоянию кода
Demo: markdownlint проходит; ссылки валидны
Task 9: Test Coverage - Frontend Unit Tests (LOW)

Objective: Добавить тестовое покрытие для TypeScript/JS модулей
Implementation:
Установить jest или vitest для тестирования frontend
Создать test/ директорию в web/
Написать тесты для критичных функций: createPopupContent, sanitizeUrl
Добавить npm test скрипт в package.json
Интегрировать в .gitlab-ci.yml (frontend-tests stage)
Test: npm test проходит; coverage > 70% для map.ts
Demo: CI pipeline показывает frontend-tests passed
Task 10: Final Integration & Verification (LOW)

Objective: Интегрировать все изменения и провести финальную верификацию
Implementation:
Запустить полный CI pipeline (bandit, pip-audit, hadolint, tests)
Проверить docker-compose up --build
Провести security scan (trivy, bandit)
Обновить README.md с новыми инструкциями
Создать CHANGELOG.md с списком изменений
Test: Все CI stages проходят; docker-compose healthy
Demo: docker-compose ps показывает все сервисы healthy
Mermaid Diagram - Architecture & Security Layers:

mermaid

graph TB
    subgraph "External"
        TG[Telegram MTProto]
        USER[Browser / WebView]
    end

    subgraph "Security Layer - nginx"
        Nginx[nginx:80<br/>CSP, Rate Limit, XSS Guard]
    end

    subgraph "Application Layer"
        Parser[parser<br/>kurigram + photo download]
        Processor[processor<br/>NLP: pymorphy3 + rapidfuzz]
        Core[core:8080<br/>aiohttp + JWT + WebSocket]
    end

    subgraph "Data Layer"
        PG[(postgres:5432<br/>PostGIS + pg_cron)]
        Media[/media/events<br/>Photo Storage]
    end

    TG -->|MTProto| Parser
    Parser -->|INSERT| PG
    PG -->|SKIP LOCKED| Processor
    Processor -->|NLP + INSERT| PG
    PG -->|pg_notify| Core
    Core -->|WebSocket| Nginx
    USER -->|HTTP/WS| Nginx
    Nginx -->|proxy| Core
    Core -->|SELECT| PG
    Core -->|READ| Media

    style Nginx fill:#f9f,stroke:#333,stroke-width:4px
    style PG fill:#ff9,stroke:#333,stroke-width:2px
Dependencies:

Task 2 зависит от Task 5 (TypeScript strict mode может выявить type ошибки)
Task 8 требует завершения Task 1-7 для актуального контента
Task 10 требует завершения всех предыдущих тасков
Risks:

Task 5 (TypeScript strict) может выявить много type errors — mitigation: поэтапное включение
Task 7 (NLP caching) может повлиять на точность матчера — mitigation: тесты на реальных данных
