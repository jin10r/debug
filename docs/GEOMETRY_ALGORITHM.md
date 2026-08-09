# Geometry Algorithm — V2 Hypotheses

## Обзор

`process_candidates()` — PostGIS-функция, которая является **единым арбитром геометрии**. Никакая стратегия не передаётся из Python: функция сама выбирает между 4 гипотезами на основе геометрий и scores кандидатов.

## Pipeline

```
raw_candidates → district_filter → final_candidates → candidates (deduplicated)
                                                                ↓
                                                      H1: single_match
                                                      H2: intersection
                                                      H3: street_segment
                                                      H4: weighted_centroid
                                                                ↓
                                                      best_hypothesis (priority + score)
                                                                ↓
                                                      fallback (single_match / random)
```

## Предобработка кандидатов

### Штраф за короткие совпадения (adjusted_score)

| matched_text | коэффициент |
|--------------|-------------|
| length >= 3 | 1.0 |
| length < 3 | 0.7 |
| только цифры (`^\d+$`) | 0.6 |

```sql
adjusted_score = score * CASE
    WHEN length(COALESCE(matched_text, '')) < 3 THEN 0.7
    WHEN matched_text ~ '^\d+$' THEN 0.6
    ELSE 1.0
END
```

### Дедупликация

Кандидаты с одинаковой геометрией (с точностью 0.0001° ≈ 11м) сливаются:

```sql
ST_AsText(ST_SnapToGrid(geom, 0.0001)) AS geom_hash
-- DISTINCT ON (geom_hash) ORDER BY adjusted_score DESC
```

### Фильтр по району

Если среди кандидатов есть объект типа `district`, все остальные проверяются на containment внутри него.

## 4 Гипотезы

### H1: single_match (weight 0.4)

**Условие:** ровно 1 кандидат после дедупликации, `adjusted_score >= 0.85`.

**Геометрия:** `ST_MakeValid(geom)` кандидата.

**Score:** `adjusted_score * 0.4`.

**Thresholds:**
- `>= 0.85` → `single_match` (full confidence)
- `< 0.85` → `random`

### H2: intersection (weight 1.0)

**Условие:** 2+ кандидата, геометрии пересекаются.

**Геометрия:** `ST_PointOnSurface(ST_Intersection(a.geom, b.geom))` для POINT/MULTIPOINT, `ST_LineInterpolatePoint(..., 0.5)` для LINESTRING.

**Score:**
```
harmonic_mean(a, b) * 1.0
= (2 * a * b / (a + b + 0.001)) * 1.0
```

**Threshold (Option B):**
- Оба кандидата >= 0.85, ИЛИ
- Один >= 0.95, второй >= 0.80

### H3: street_segment (weight 0.9)

**Условие:** Линия (LINESTRING/MULTILINESTRING), пересекающая 2+ объекта, сегмент между первым и последним пересечением ≤ 2000м.

**Геометрия:** `ST_LineSubstring` между `LEAST(first_loc, last_loc)` и `GREATEST(first_loc, last_loc)`, где локации вычисляются через `ST_LineLocatePoint` от точек пересечения.

**Score:** `line.adjusted_score * 0.9`.

**Threshold:** `line.adjusted_score >= 0.85`, `crossings.n >= 2`, `last_loc > first_loc`.

### H4: weighted_centroid (weight 0.85)

**Условие:** 2+ кандидата, scatter (max distance от любого point к centroid) ≤ 1500м.

**Геометрия:** Weighted centroid в EPSG:3857 с двумя типами точек:

```sql
-- Точки пересечения пар (вес ×2.5)
wc_intersection_points AS (
    SELECT ST_PointOnSurface(ST_Intersection(a.geom, b.geom)) AS pt,
           (a.adjusted_score + b.adjusted_score) / 2.0 * 2.5 AS weight
    FROM candidates a, candidates b
    WHERE a.id < b.id AND ST_Intersects(...)
),
-- Опорные точки кандидатов (вес ×1.0)
wc_candidate_points AS (
    SELECT ST_PointOnSurface(geom) AS pt,
           adjusted_score * 1.0 AS weight
    FROM candidates
)
```

**Score:**
```
GREATEST(0.1,
    AVG(base_score) * 0.85
    - LEAST(0.3, scatter_m * 0.0004)
)
```

**Threshold:** `COUNT(*) >= 2`, `scatter_m <= 1500`.

## Приоритет гипотез

При равенстве score более сложные стратегии побеждают:

| Приоритет | Стратегия | Объяснение |
|-----------|-----------|------------|
| 5 | `intersection` | точное пересечение — самая надёжная |
| 4 | `street_segment` | линия с 2+ пересечениями |
| 3 | `weighted_centroid` | компактный кластер 2+ объектов |
| 1 | `single_match` | fallback для одного объекта |

## Fallback

Если ни одна гипотеза не сгенерирована:

1. Если лучший кандидат `adjusted_score >= 0.85` → `single_match` с его геометрией (full confidence).
2. Иначе → `random` (случайная точка в `question_overlay`).

## Geometry-type safety

- `random`, `intersection`, `weighted_centroid` → всегда POINT (проверка триггером).
- `street_segment` → всегда LINESTRING (проверка триггером).
- `single_match` → любой тип (гео-объект может быть точкой или линией).
- `ST_MakeValid()` применяется перед всеми PostGIS-операциями.

## Тонкая настройка

| Параметр | Значение | Описание |
|-----------|----------|----------|
| `v_score_threshold` | 0.85 | Минимальный score для single_match и spatial hypotheses |
| `v_cluster_radius_m` | 500.0 | Макс. расстояние для cluster (legacy, не используется) |
| `v_wc_max_scatter_m` | 1500.0 | Макс. scatter для weighted_centroid |
| `v_ss_max_segment_m` | 2000.0 | Макс. длина сегмента для street_segment |
| `short_match_penalty` | 0.6 | Коэффициент для совпадений-цифр |
| `very_short_penalty` | 0.7 | Коэффициент для length < 3 |
| `intersection_weight` | 1.0 | Weight для intersection hypothesis |
| `street_segment_weight` | 0.9 | Weight для street_segment hypothesis |
| `weighted_centroid_weight` | 0.85 | Weight для weighted_centroid hypothesis |
| `single_match_weight` | 0.4 | Weight для single_match hypothesis |
