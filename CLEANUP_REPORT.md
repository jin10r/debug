# Cleanup Report

## Summary

- **Original rows**: 1732
- **Final rows**: 1453
- **Removed duplicates**: 279
- **Manual review entries**: 25

## Steps Applied

1. **Normalized separators**: All `names` fields now use strict `|` without spaces
2. **Rounded coordinates**: All WKT coordinates rounded to 5 decimal places
3. **Filtered non-Cyrillic aliases**: Removed Latin/Romanian aliases from settlement types
4. **Deduplicated**: Kept POLYGON entries, removed POINT/LINESTRING duplicates (279 removed)
5. **Directory cleanup**: Removed artifact files, kept only `geo.csv`, `stopwords.csv`, and `geo_manual_review.csv`

## Removed Duplicates (279)

Representative sample:
- `Белозёрка|Білозерка` (town) — duplicate of POLYGON entry
- `Красносёлка|Красносілка` (town) — duplicate of POLYGON entry
- `Днестровск|Дністровськ` (town) — duplicate of POLYGON entry
- `Слободзея` (town) — duplicate of POLYGON entry
- And 275 more POINT/LINESTRING entries removed in favor of POLYGON

## Manual Review (25)

| names | type | reason |
|-------|------|--------|
| затока | landmark | no_polygon_in_group |
| Крыжановка|5ая крыжановка | landmark | no_polygon_in_group |
| Куяльник | street | no_polygon_in_group |
| Александровка|Олександрівка | town | no_polygon_in_group |
| Сергеевка|Сергіївка | town | no_polygon_in_group |
| Черноморское|Чорноморське | town | no_polygon_in_group |
| Адамовка|Адамівка | village | no_polygon_in_group |
| Великий Буялык|Великий Буялик | village | no_polygon_in_group |
| Граденицы|Градениці | village | no_polygon_in_group |
| Долиновка|Долинівка | village | no_polygon_in_group |
| Калиновка|Калинівка | village | no_polygon_in_group |
| Кошары|Кошари | village | no_polygon_in_group |
| Луговое|Лугове | village | no_polygon_in_group |
| Марьяновка|Мар’янівка | village | no_polygon_in_group |
| Николаевка|Миколаївка | village | no_polygon_in_group |
| Нововладимировка|Нововолодимирівка | village | no_polygon_in_group |
| Новодмитровка|Новодмитрівка | village | no_polygon_in_group |
| Покровка | village | no_polygon_in_group |
| Приозёрное|Приозерне | village | no_polygon_in_group |
| Ройлянка | village | no_polygon_in_group |
| Садовое|Садове | village | no_polygon_in_group |
| Ульяновка|Улянівка | village | no_polygon_in_group |
| Шеметово|Шеметове | village | no_polygon_in_group |
| Элеваторное|Елеваторне | village | no_polygon_in_group |
| Căuşeni | town | latin_only_name_settlement |

## Type Distribution (final)

| Type | Count |
|------|-------|
| street | 733 |
| village | 534 |
| landmark | 59 |
| town | 41 |
| station | 30 |
| park | 25 |
| infrastructure | 15 |
| market | 12 |
| district | 4 |

## Directory Cleanup

Removed from `postgres/data/`:
- `geo_additions.csv`
- `geo_ambiguous.csv`
- `geo_deduplicated.csv`
- `geo_final.csv`
- `geo_fixed.csv`
- `geo_refactored.csv`
- `geo_removed_duplicates.csv`
- `geo_review.csv`
- `MERGE_REPORT.md`
- `export.gpx`
- `geo_geometry_report.json`
- `geo.zip`

Remaining in `postgres/data/`:
- `geo.csv` (cleaned, 1453 rows)
- `stopwords.csv` (unchanged)
- `geo_manual_review.csv` (25 entries for manual analysis)

## Validation

- All `names` use strict `|` separator
- All coordinates rounded to 5 decimal places
- No Latin aliases in settlements (except Căuşeni which is Latin-only and flagged for review)
- All geometries are valid WKT
- UTF-8 encoding without BOM
- Types preserved from original (no automatic type changes)
