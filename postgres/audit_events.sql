-- =============================================================================
-- audit_events.sql — аудит аномалий в таблице events
-- Запуск: sudo docker exec -i postgres psql -U postgres -d postgres < postgres/audit_events.sql
-- =============================================================================

\echo '=== 1.0 Сводка: strategy × geom_type ==='
SELECT strategy,
       ST_GeometryType(geom) AS geom_type,
       COUNT(*) AS cnt
FROM events
GROUP BY strategy, ST_GeometryType(geom)
ORDER BY strategy, geom_type;

\echo ''
\echo '=== 1.1 DB-1: single_match + POINT, но matched street ≠ POINT ==='
SELECT e.id,
       LEFT(e.description, 60) AS desc_short,
       ST_GeometryType(e.geom) AS event_geom,
       s.names[1] AS matched_street,
       ST_GeometryType(s.geom) AS street_geom,
       (top_match.m->>'similarity')::float AS score
FROM events e
CROSS JOIN LATERAL (
    SELECT m, (m->>'street_id')::int AS sid
    FROM jsonb_array_elements(e.matches) AS m
    ORDER BY (m->>'similarity')::float DESC
    LIMIT 1
) top_match
JOIN streets s ON s.id = top_match.sid
WHERE e.strategy = 'single_match'
  AND ST_GeometryType(e.geom) = 'ST_Point'
  AND ST_GeometryType(s.geom) != 'ST_Point'
ORDER BY e.id DESC;

\echo ''
\echo '=== 1.2 DB-2: single_intersection с non-POINT geom ==='
SELECT id, LEFT(description, 60) AS desc_short, ST_GeometryType(geom) AS geom_type
FROM events
WHERE strategy = 'single_intersection'
  AND ST_GeometryType(geom) != 'ST_Point'
ORDER BY id DESC;

\echo ''
\echo '=== 1.3 DB-3: polygon_intersection с POINT geom ==='
SELECT id, LEFT(description, 60) AS desc_short, ST_GeometryType(geom) AS geom_type
FROM events
WHERE strategy = 'polygon_intersection'
  AND ST_GeometryType(geom) = 'ST_Point'
ORDER BY id DESC;

\echo ''
\echo '=== 1.4 DB-4: random с непустыми matches ==='
SELECT id, LEFT(description, 60) AS desc_short, matches
FROM events
WHERE strategy = 'random'
  AND matches IS NOT NULL
  AND matches != '[]'::jsonb
  AND jsonb_array_length(matches) > 0
ORDER BY id DESC;

\echo ''
\echo '=== 1.5 DB-5: matches ссылаются на несуществующий street_id ==='
SELECT e.id, LEFT(e.description, 60) AS desc_short, (m->>'street_id')::int AS dead_sid
FROM events e
CROSS JOIN jsonb_array_elements(e.matches) AS m
WHERE NOT EXISTS (
    SELECT 1 FROM streets s WHERE s.id = (m->>'street_id')::int
)
ORDER BY e.id DESC;

\echo ''
\echo '=== 1.6 DB-6: geometry за пределами bounding box Одессы ==='
SELECT id, LEFT(description, 60) AS desc_short, strategy,
       round(ST_X(ST_PointOnSurface(geom))::numeric, 5) AS lon,
       round(ST_Y(ST_PointOnSurface(geom))::numeric, 5) AS lat
FROM events
WHERE NOT ST_Within(
    ST_PointOnSurface(geom),
    ST_MakeEnvelope(30.50, 46.25, 30.95, 46.70, 4326)
)
ORDER BY id DESC;

\echo ''
\echo '=== 1.7 DB-7: NULL geom ==='
SELECT id, LEFT(description, 60) AS desc_short, strategy
FROM events
WHERE geom IS NULL
ORDER BY id DESC;

\echo ''
\echo '=== 1.8 matches count × strategy ==='
SELECT strategy,
       jsonb_array_length(matches) AS match_count,
       COUNT(*) AS events
FROM events
GROUP BY strategy, jsonb_array_length(matches)
ORDER BY strategy, match_count;

\echo ''
\echo '=== ИТОГ: общее кол-во событий ==='
SELECT COUNT(*) AS total_events,
       COUNT(*) FILTER (WHERE strategy = 'random') AS random_cnt,
       COUNT(*) FILTER (WHERE strategy = 'single_match') AS single_match_cnt,
       COUNT(*) FILTER (WHERE strategy = 'single_intersection') AS single_int_cnt,
       COUNT(*) FILTER (WHERE strategy = 'polygon_intersection') AS poly_int_cnt,
       COUNT(*) FILTER (WHERE geom IS NULL) AS null_geom_cnt
FROM events;
