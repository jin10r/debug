/**
 * vector-data.ts — Load compressed GeoJSON, decompress, and build geojson-vt tile index.
 *
 * The data file (odessa.geojson.gz) is pre-compressed at build time and served
 * by nginx with gzip_static. This module fetches it, decompresses via
 * DecompressionStream (or falls back to raw text), and indexes with geojson-vt.
 */
import geojsonvt, { TileIndex } from 'geojson-vt';

const DATA_URL = '/assets/data/odessa.geojson.gz';
const MAX_ZOOM = 14;
const EXTENT = 4096;
const BUFFER = 64;

let tileIndex: TileIndex | null = null;
let loadPromise: Promise<TileIndex> | null = null;

export async function loadVectorData(): Promise<TileIndex> {
  if (tileIndex) return tileIndex;
  if (loadPromise) return loadPromise;

  loadPromise = (async () => {
    const t0 = performance.now();

    const res = await fetch(DATA_URL);
    if (!res.ok) throw new Error(`GeoJSON fetch failed: ${res.status}`);

    const blob = await res.blob();

    // Decompression via DecompressionStream (Chrome 80+, Firefox 113+, Safari 16.4+)
    let text: string;
    if ('DecompressionStream' in window) {
      const ds = new DecompressionStream('gzip');
      text = await new Response(blob.stream().pipeThrough(ds)).text();
    } else {
      // Fallback: nginx may serve uncompressed if request has no Accept-Encoding
      text = await blob.text();
    }

    const geojson = JSON.parse(text);

    tileIndex = geojsonvt(geojson, {
      maxZoom: MAX_ZOOM,
      indexMaxZoom: MAX_ZOOM - 2,
      tolerance: 3,
      extent: EXTENT,
      buffer: BUFFER,
    });    const elapsed = performance.now() - t0;

    console.log(
      `[vector-data] ${geojson.features.length} features indexed in ${elapsed.toFixed(0)}ms`
    );

    return tileIndex;
  })();

  return loadPromise;
}

export function getTileIndex(): TileIndex | null {
  return tileIndex;
}
