-- =============================================================================
-- backfill_geo_resolution.sql
--
-- Пересчитывает strategy / geom / confidence / geo_diagnostics для событий
-- последних 7 дней, используя текущую версию process_candidates().
--
-- Использование:
--   psql -U postgres -d postgres -v ON_ERROR_STOP=1 -f backfill_geo_resolution.sql
--
-- Режимы:
--   1. DRY RUN — раскомментируйте строку с SELECT отчёта (середина файла)
--   2. APPLY — раскомментируйте строки с UPDATE events (конец файла)
-- =============================================================================

BEGIN;

-- ── 1. Создаём временную таблицу для сравнения ──────────────────────────────
DROP TABLE IF EXISTS _backfill_compare;

CREATE TEMP TABLE _backfill_compare (
    event_id         INT,
    old_strategy     VARCHAR(40),
    new_strategy     VARCHAR(40),
    old_geom         GEOMETRY,
    new_geom         GEOMETRY,
    old_confidence   FLOAT,
    new_confidence   FLOAT,
    strategy_changed BOOLEAN,
    geom_changed     BOOLEAN
);

-- ── 2. Собираем события последних 7 дней с matches (не пустым) ──────────────
INSERT INTO _backfill_compare (
    event_id, old_strategy, old_geom, old_confidence
)
SELECT
    e.id,
    e.strategy,
    e.geom,
    e.confidence
FROM events e
WHERE e.event_time >= NOW() - INTERVAL '7 days'
  AND e.matches IS NOT NULL
  AND e.matches != '[]'::jsonb
  AND jsonb_array_length(e.matches) > 0;

-- ── 3. Пересчитываем через process_candidates() ─────────────────────────────
UPDATE _backfill_compare bc
SET
    new_strategy = pc.result_strategy,
    new_geom     = pc.result_geom,
    new_confidence = pc.result_confidence
FROM events e
CROSS JOIN LATERAL (
    SELECT result_strategy, result_geom, result_confidence
    FROM process_candidates(
        ARRAY(
            SELECT (m.value->>'geo_id')::INT
            FROM jsonb_array_elements(e.matches) AS m(value)
        ),
        ARRAY(
            SELECT (m.value->>'similarity')::FLOAT
            FROM jsonb_array_elements(e.matches) AS m(value)
        ),
        ARRAY(
            SELECT (m.value->>'matched_text')::TEXT
            FROM jsonb_array_elements(e.matches) AS m(value)
        ),
        30.83135,
        46.49804,
        0.045
    )
) pc
WHERE e.id = bc.event_id;

-- ── 4. Помечаем изменения ───────────────────────────────────────────────────
UPDATE _backfill_compare
SET
    strategy_changed = (old_strategy IS DISTINCT FROM new_strategy),
    geom_changed     = (old_geom IS DISTINCT FROM new_geom);

-- ══════════════════════════════════════════════════════════════════════════════
-- DRY RUN: раскомментируйте для просмотра отчёта БЕЗ записи
-- ══════════════════════════════════════════════════════════════════════════════
-- SELECT
--     COUNT(*) AS total_events,
--     SUM(CASE WHEN strategy_changed THEN 1 ELSE 0 END) AS strategy_changes,
--     SUM(CASE WHEN geom_changed THEN 1 ELSE 0 END) AS geom_changes,
--     ROUND(100.0 * SUM(CASE WHEN strategy_changed THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS change_pct
-- FROM _backfill_compare;
--
-- SELECT
--     old_strategy,
--     new_strategy,
--     COUNT(*) AS cnt
-- FROM _backfill_compare
-- WHERE strategy_changed
-- GROUP BY old_strategy, new_strategy
-- ORDER BY cnt DESC;
--
-- SELECT
--     event_id,
--     old_strategy,
--     new_strategy,
--     old_confidence,
--     new_confidence
-- FROM _backfill_compare
-- WHERE strategy_changed
-- LIMIT 50;

-- ══════════════════════════════════════════════════════════════════════════════
-- APPLY: раскомментируйте для применения изменений
-- ══════════════════════════════════════════════════════════════════════════════
-- UPDATE events e
-- SET
--     strategy        = bc.new_strategy,
--     geom            = bc.new_geom,
--     confidence      = bc.new_confidence,
--     geo_diagnostics = jsonb_build_object(
--                           'backfilled', true,
--                           'backfill_time', NOW(),
--                           'old_strategy', bc.old_strategy,
--                           'old_confidence', bc.old_confidence
--                       )
-- FROM _backfill_compare bc
-- WHERE e.id = bc.event_id
--   AND (bc.strategy_changed OR bc.geom_changed);

COMMIT;
