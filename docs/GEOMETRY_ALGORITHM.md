# Geometry Algorithm — 4 Hypotheses

## Обзор

`process_candidates()` — PostGIS-функция, которая является **единым арбитром геометрии**. Никакой стратегия не передаётся из Python: функция сама выбирает между 4 гипотезами на основе геометрий и scores кандидатов.

## Pipeline

```
raw_candidates → district_filter → final_candidates → candidates (deduplicated)
                                                              ↓
                                                    H1: single_match
                                                    H2: intersection
                                                    H3: midpoint
                                                    H4: cluster_centroid
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

### H1: single_match

**Условие:** ровно 1 кандидат после дедупликации.

**Геометрия:** `ST_MakeValid(geom)` кандидата.

**Score:** `adjusted_score`.

**Fallback:** если `adjusted_score < 0.85` → `random`.

### H2: intersection

**Условие:** 2+ кандидата, геометрии пересекаются.

**Геометрия:** `ST_PointOnSurface(ST_Intersection(a.geom, b.geom))` для POINT/MULTIPOINT, `ST_LineInterpolatePoint(..., 0.5)` для LINESTRING.

**Score:**
```
harmonic_mean(a, b) + 0.3
= (2 * a * b / (a + b + 0.001)) + 0.3
```

**Threshold (Option B):**
- Оба кандидата >= 0.85, ИЛИ
- Один >= 0.95, второй >= 0.80

### H3: midpoint

**Условие:** 2+ кандидата, геометрии НЕ пересекаются, расстояние ≤ 150м.

**Геометрия:** `ST_LineInterpolatePoint(ST_ShortestLine(a.geom, b.geom), 0.5)`.

**Score:**
```
harmonic_mean(a, b) + 0.2 * (1 - distance_m / 150)
```

**Threshold (Option B):** тот же, что у intersection.

### H4: cluster_centroid

**Условие:** 3+ кандидата, максимальное расстояние между любыми двумя ≤ 500м.

**Геометрия:** взвешенный центроид в EPSG:3857:

```sql
ST_Transform(
    ST_SetSRID(
        ST_MakePoint(
            SUM(ST_X(ST_Centroid(c.geom_m)) * c.adjusted_score) / SUM(c.adjusted_score),
            SUM(ST_Y(ST_Centroid(c.geom_m)) * c.adjusted_score) / SUM(c.adjusted_score)
        ),
        3857
    ),
    4326
)
```

**Важно:** `ST_Centroid()` используется перед `ST_X()`/`ST_Y()` потому что геометрии могут быть LINESTRING/POLYGON.

**Score:**
```
AVG(adjusted_score) + 0.4 * (1 - max_deviation_m / 500)
```

**Threshold:** `MIN(adjusted_score) >= 0.85`.

## Приоритет гипотез

При равенстве score более сложные стратегии побеждают:

| Приоритет | Стратегия | Объяснение |
|-----------|-----------|------------|
| 4 | `intersection` | точное пересечение — самая надёжная |
| 3 | `cluster_centroid` | кластер из 3+ объектов |
| 2 | `midpoint` | близкие непересекающиеся объекты |
| 1 | `single_match` | fallback для одного объекта |

## Fallback

Если ни одна гипотеза не сгенерирована:

1. Если лучший кандидат `adjusted_score >= 0.85` → `single_match` с его геометрией.
2. Иначе → `random` (случайная точка в `question_overlay`).

## Geometry-type safety

- Все координатные extraction для LINESTRING/POLYGON должны использовать `ST_Centroid()` перед `ST_X()`/`ST_Y()`.
- `ST_MakeValid()` применяется перед всеми PostGIS-операциями.
- `cluster_centroid` всегда возвращает POINT (проверка триггером).
- `midpoint` всегда возвращает POINT (проверка триггером).
- `random` всегда возвращает POINT (проверка триггером).

## Тонкая настройка

| Параметр | Значение | Описание |
|-----------|----------|----------|
| `v_score_threshold` | 0.85 | Минимальный score для single_match / cluster |
| `v_cluster_radius_m` | 500.0 | Макс. расстояние для cluster_centroid |
| `v_midpoint_radius_m` | 150.0 | Макс. расстояние для midpoint |
| `short_match_penalty` | 0.6 | Коэффициент для совпадений-цифр |
| `very_short_penalty` | 0.7 | Коэффициент для length < 3 |
| `intersection_bonus` | 0.3 | Бонус за пересечение |
| `midpoint_bonus` | 0.2 | Бонус за близость |
| `cluster_bonus` | 0.4 | Бонус за компактность кластера |
