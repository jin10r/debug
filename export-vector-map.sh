#!/bin/bash
# ============================================================
# Экспорт OSM PBF → упрощённый GeoJSON для geojson-vt
#
# Использование:
#   ./export-vector-map.sh                              # по умолчанию
#   ./export-vector-map.sh --simplify 0.5 --min-area 0.002
#   ./export-vector-map.sh --simplify 0.08 --min-road 0.01 --gzip-only
#
# Параметры (все необязательные):
#   --simplify N     visvalingam % (по умолчанию 0.1)
#   --min-area N     мин. площадь полигона воды, градус² (по умолчанию 0.001)
#   --min-road N     мин. длина дороги, градус (по умолчанию 0.015)
#   --precision N    знаков после запятой (по умолчанию 4)
#   --no-tertiary    убратьertiary дороги
#   --gzip-only      только gzip без .geojson
#   --input PATH     входной .osm.pbf (по умолчанию web/odessa_oblast.osm.pbf)
#   --output PATH    выходной .gz (по умолчанию web/assets/data/odessa.geojson.gz)
# ============================================================
set -euo pipefail

# ── Параметры по умолчанию ──────────────────────────────────
SIMPLIFY="0.1"
MIN_AREA="0.001"
MIN_ROAD="0.015"
PRECISION="4"
INCLUDE_TERTIARY="true"
GZIP_ONLY="false"
INPUT="${INPUT:-web/odessa_oblast.osm.pbf}"
OUTPUT="${OUTPUT:-web/assets/data/odessa.geojson.gz}"

# ── Парсинг аргументов ──────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --simplify)   SIMPLIFY="$2"; shift 2 ;;
    --min-area)   MIN_AREA="$2"; shift 2 ;;
    --min-road)   MIN_ROAD="$2"; shift 2 ;;
    --precision)  PRECISION="$2"; shift 2 ;;
    --no-tertiary) INCLUDE_TERTIARY="false"; shift ;;
    --gzip-only)  GZIP_ONLY="true"; shift ;;
    --input)      INPUT="$2"; shift 2 ;;
    --output)     OUTPUT="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,/^# ==/p' "$0" | head -n -1 | sed 's/^# //' | sed 's/^#//'
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Проверки ────────────────────────────────────────────────
if [ ! -f "$INPUT" ]; then
  echo "ERROR: $INPUT not found" >&2; exit 1
fi
for cmd in osmium node; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: $cmd not found"; exit 1; }
done

# ── Настройки ───────────────────────────────────────────────
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
FILTERED="$BUILD_DIR/filtered.osm.pbf"
RAW_GEOJSON="$BUILD_DIR/raw.geojson"
SIMPLIFIED="$BUILD_DIR/simplified.geojson"
FINAL_JSON="${OUTPUT%.gz}"
mkdir -p "$(dirname "$OUTPUT")"

file_size() { wc -c < "$1" | tr -d ' '; }

# OSM теги для фильтрации
HIGHWAY_TAGS="motorway,trunk,primary,secondary"
if [ "$INCLUDE_TERTIARY" = "true" ]; then
  HIGHWAY_TAGS="$HIGHWAY_TAGS,tertiary"
fi

echo "╔══════════════════════════════════════════╗"
echo "║  Vector Map Export                       ║"
echo "╠══════════════════════════════════════════╣"
echo "║  Input:      $INPUT"
echo "║  Output:     $OUTPUT"
echo "║  Simplify:   visvalingam ${SIMPLIFY}%"
echo "║  Min area:   ${MIN_AREA}°²  (~$(echo "$MIN_AREA * 111000" | bc 2>/dev/null || echo "?")m)"
echo "║  Min road:   ${MIN_ROAD}°   (~$(echo "$MIN_ROAD * 111000" | bc 2>/dev/null || echo "?")m)"
echo "║  Precision:  ${PRECISION} decimals"
echo "║  Tertiary:   $INCLUDE_TERTIARY"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Step 1: Фильтрация ─────────────────────────────────────
echo "▶ Step 1/5: Фильтрация объектов..."
osmium tags-filter "$INPUT" \
  w/highway=$HIGHWAY_TAGS \
  w/natural=water,coastline,bay \
  w/waterway=river,stream,canal \
  w/boundary=administrative \
  n/place=city,town,village,hamlet \
  -o "$FILTERED" --overwrite
echo "  PBF после фильтрации: $(($(file_size "$FILTERED") / 1024 / 1024)) MB"

# ── Step 2: Конвертация ────────────────────────────────────
echo "▶ Step 2/5: Конвертация в GeoJSON..."
npx osmtogeojson "$FILTERED" > "$RAW_GEOJSON"
echo "  Raw GeoJSON: $(($(file_size "$RAW_GEOJSON") / 1024 / 1024)) MB"

# ── Step 3: Упрощение ──────────────────────────────────────
echo "▶ Step 3/5: Упрощение геометрии (visvalingam ${SIMPLIFY}%)..."
npx mapshaper "$RAW_GEOJSON" \
  -simplify visvalingam "${SIMPLIFY}%" \
  -o format=geojson precision="0.${PRECISION//[^0-9]/}00001" "$SIMPLIFIED"

# Склейка multipart-файлов от mapshaper
SIMPLIFIED_COUNT=$(ls -1 "$BUILD_DIR"/simplified*.geojson 2>/dev/null | wc -l)
if [ "$SIMPLIFIED_COUNT" -gt 1 ]; then
  echo "  Склейка $SIMPLIFIED_COUNT файлов..."
  node -e "
    const fs = require('fs');
    const files = fs.readdirSync('$BUILD_DIR').filter(f => f.startsWith('simplified') && f.endsWith('.geojson')).sort();
    const m = { type: 'FeatureCollection', features: [] };
    for (const f of files) { const d = JSON.parse(fs.readFileSync('$BUILD_DIR/' + f, 'utf8')); if (d.features) m.features.push(...d.features); }
    fs.writeFileSync('$SIMPLIFIED', JSON.stringify(m));
  "
  rm -f "$BUILD_DIR"/simplified[0-9]*.geojson
fi
echo "  После упрощения: $(($(file_size "$SIMPLIFIED") / 1024 / 1024)) MB"

# ── Step 4: Постобработка ───────────────────────────────────
echo "▶ Step 4/5: Постобработка (layer_type, name_ru, фильтр мелочи)..."
node -e "
const fs = require('fs');
const gj = JSON.parse(fs.readFileSync('$SIMPLIFIED', 'utf8'));

const MIN_POLYGON_AREA = $MIN_AREA;
const MIN_LINE_LENGTH = $MIN_ROAD;

function transliterate(t) {
  if (!t) return t;
  return t.replace(/і/g,'и').replace(/І/g,'И').replace(/ї/g,'и').replace(/Ї/g,'И')
    .replace(/є/g,'е').replace(/Є/g,'Е').replace(/івська/g,'овская')
    .replace(/івське/g,'овское').replace(/ський/g,'ский').replace(/ське/g,'ское').replace(/ська/g,'ская');
}
function classify(p) {
  if (p.natural==='water'||p.natural==='coastline'||p.natural==='bay'||p.waterway) return 'water';
  if (p.highway) return 'roads';
  if (p.boundary==='administrative') return 'boundary';
  if (p.place) return 'settlements';
  return null;
}
function bboxArea(g) {
  if (!g?.coordinates) return Infinity;
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  (function s(c) {
    if (typeof c[0]==='number') { x0=Math.min(x0,c[0]);x1=Math.max(x1,c[0]);y0=Math.min(y0,c[1]);y1=Math.max(y1,c[1]); }
    else for (const v of c) s(v);
  })(g.coordinates);
  return (x1-x0)*(y1-y0);
}
function lineLen(g) {
  if (!g?.coordinates) return Infinity;
  let l=0;
  (function s(c) {
    if (typeof c[0][0]==='number') { for(let i=1;i<c.length;i++) l+=Math.hypot(c[i][0]-c[i-1][0],c[i][1]-c[i-1][1]); return; }
    for (const v of c) s(v);
  })(g.coordinates);
  return l;
}
const kept = []; let dNg=0,dNt=0,dSm=0;
for (const f of gj.features) {
  if (!f.geometry?.coordinates) { dNg++; continue; }
  const cs = JSON.stringify(f.geometry.coordinates);
  if (cs==='[]'||cs==='[[]]'||cs==='[[[]]]') { dNg++; continue; }
  const p = f.properties||{}, lt = classify(p);
  if (!lt) { dNt++; continue; }
  if (lt==='water'&&(f.geometry.type==='Polygon'||f.geometry.type==='MultiPolygon') && bboxArea(f.geometry)<MIN_POLYGON_AREA) { dSm++; continue; }
  if (lt==='roads' && lineLen(f.geometry)<MIN_LINE_LENGTH) { dSm++; continue; }
  p.layer_type = lt;
  if (lt==='roads') p.road_class = p.highway;
  if (p['name:ru']) p.name_ru = p['name:ru'];
  else if (p.name) { p.name_ru = /[іїєІЇЄ]/.test(p.name) ? transliterate(p.name) : p.name; }
  else p.name_ru = '';
  f.properties = p; kept.push(f);
}
gj.features = kept;
fs.writeFileSync('$FINAL_JSON', JSON.stringify(gj));
console.log('  Features: ' + kept.length + ' (dropped: ' + dNg + ' no-geom, ' + dNt + ' no-type, ' + dSm + ' too small)');
"

# ── Step 5: Сжатие ─────────────────────────────────────────
echo "▶ Step 5/5: Сжатие..."
gzip -9 -c "$FINAL_JSON" > "$OUTPUT"

RAW_SIZE=$(file_size "$FINAL_JSON")
GZ_SIZE=$(file_size "$OUTPUT")

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Результат                               ║"
echo "╠══════════════════════════════════════════╣"
echo "║  Raw:    $((RAW_SIZE / 1024)) KB ($((RAW_SIZE / 1024 / 1024)) MB)"
echo "║  Gzip:   $((GZ_SIZE / 1024)) KB"
echo "║  Output: $OUTPUT"
if [ "$GZ_SIZE" -gt 1048576 ]; then
echo "║  ⚠️  gzip > 1 МБ — увеличьте --simplify"
fi
echo "╚══════════════════════════════════════════╝"

if [ "$GZIP_ONLY" = "true" ]; then
  rm -f "$FINAL_JSON"
  echo "  .geojson удалён (--gzip-only)"
fi
