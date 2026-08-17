-- =============================================================================
-- enrich_gazetteer_aliases.sql
--
-- Идемпотентное обогащение справочника geo частыми алиасами,
-- идентифицированными из pg_judge_events_export3.csv.
--
-- Использование:
--   psql -U postgres -d postgres -v ON_ERROR_STOP=1 -f enrich_gazetteer_aliases.sql
-- =============================================================================

BEGIN;

-- ── 1. Донского → "донского" ─────────────────────────────────────────────────
UPDATE geo
SET names = array_append(names, 'донского')
WHERE names @> ARRAY['Дмитрия Донского']
  AND NOT (names @> ARRAY['донского']);

-- ── 2. 7-я Пересыпская → "7пересыпи", "7 пересыпи", "7я пересыпская" ────────
UPDATE geo
SET names = array_append(names, '7пересыпи')
WHERE names @> ARRAY['7-я Пересыпская']
  AND NOT (names @> ARRAY['7пересыпи']);

UPDATE geo
SET names = array_append(names, '7 пересыпи')
WHERE names @> ARRAY['7-я Пересыпская']
  AND NOT (names @> ARRAY['7 пересыпи']);

UPDATE geo
SET names = array_append(names, '7я пересыпская')
WHERE names @> ARRAY['7-я Пересыпская']
  AND NOT (names @> ARRAY['7я пересыпская']);

-- ── 3. Яхта → "яхт-клуб" ─────────────────────────────────────────────────────
UPDATE geo
SET names = array_append(names, 'яхт-клуб')
WHERE names @> ARRAY['Яхта']
  AND NOT (names @> ARRAY['яхт-клуб']);

-- ── 4. Куликово поле → "куликовский", "куликовское" ─────────────────────────
UPDATE geo
SET names = array_append(names, 'куликовский')
WHERE names @> ARRAY['Куликово поле']
  AND NOT (names @> ARRAY['куликовский']);

UPDATE geo
SET names = array_append(names, 'куликовское')
WHERE names @> ARRAY['Куликово поле']
  AND NOT (names @> ARRAY['куликовское']);

-- ── 5. Новые записи (если не существуют) ────────────────────────────────────
-- Эти улицы/POI отсутствуют в справочнике и должны быть добавлены вручную
-- или через импорт geo.csv. Здесь мы только создаём placeholder'ы,
-- которые можно заполнить геометрией позже.

INSERT INTO geo (names, type, geom)
SELECT ARRAY['Ленинградская'], 'street',
       ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)
WHERE NOT EXISTS (
    SELECT 1 FROM geo WHERE names @> ARRAY['Ленинградская']
);

INSERT INTO geo (names, type, geom)
SELECT ARRAY['Серова'], 'street',
       ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)
WHERE NOT EXISTS (
    SELECT 1 FROM geo WHERE names @> ARRAY['Серова']
);

INSERT INTO geo (names, type, geom)
SELECT ARRAY['Серов'], 'street',
       ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)
WHERE NOT EXISTS (
    SELECT 1 FROM geo WHERE names @> ARRAY['Серов']
);

INSERT INTO geo (names, type, geom)
SELECT ARRAY['ВОГ'], 'poi',
       ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)
WHERE NOT EXISTS (
    SELECT 1 FROM geo WHERE names @> ARRAY['ВОГ']
);

INSERT INTO geo (names, type, geom)
SELECT ARRAY['вог'], 'poi',
       ST_SetSRID(ST_MakePoint(30.83135, 46.49804), 4326)
WHERE NOT EXISTS (
    SELECT 1 FROM geo WHERE names @> ARRAY['вог']
);

COMMIT;

-- Проверка
SELECT
    names[1] AS primary_name,
    array_length(names, 1) AS alias_count,
    type
FROM geo
WHERE names @> ARRAY['Ленинградская']
   OR names @> ARRAY['Серова']
   OR names @> ARRAY['Серов']
   OR names @> ARRAY['ВОГ']
   OR names @> ARRAY['вог']
   OR names @> ARRAY['донского']
   OR names @> ARRAY['7-я Пересыпская']
   OR names @> ARRAY['Яхта']
   OR names @> ARRAY['Куликово поле']
ORDER BY names[1];
