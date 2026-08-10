# Merge Report

## Summary

- **Base**: geo_refactored.csv (1513 rows)
- **Additions**: 5 new entries
- **Final**: geo_final.csv (1518 rows)

## Changes Applied

- Row 27: Автостанция привоз|Автостанция старосенная (point -> landmark) [station_non_point_geometry]
- Row 28: Аэропорт (point -> landmark) [station_non_point_geometry]
- Row 29: Аэропортовская (point -> landmark) [station_non_point_geometry]
- Row 30: ТЦ «Аэропортовский»|таврия аэропортовская (point -> landmark) [station_non_point_geometry]
- Мичурина площадь: closed LINESTRING -> POLYGON, landmark -> park
- Старобазарный|Кировский: market -> park
- Added: Одескабель (landmark)
- Added: Клеверный мост (landmark)
- Added: Альтаиры (landmark)
- Added: Автоцентр Тесла (landmark)
- Added: Базарчик (market)

## Type Distribution

| Type | Count |
|------|-------|
| settlement | 630 |
| street | 472 |
| landmark | 316 |
| point | 42 |
| park | 38 |
| market | 14 |
| district | 6 |

## Remaining Issues

- settlement points without polygons: 393 entries (kept as-is)
- street_polygon_geometry: 10 entries (kept as street)
- park_line_geometry: 5 entries (kept as park)
- bridge_non_line_geometry: 5 entries (kept as street)
- district_point_geometry: 1 entry (kept as district)

## Validation

- All types are from the allowed set: ['district', 'landmark', 'market', 'park', 'point', 'settlement', 'street']
- No empty names or geometries
- UTF-8 encoding
