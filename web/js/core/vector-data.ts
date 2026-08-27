/**
 * vector-data.ts — Load compressed GeoJSON, decompress, and build geojson-vt tile index.
 *
 * The data file (odessa.geojson.gz) is pre-compressed at build time and served
 * by nginx. This module fetches it, decompresses via DecompressionStream
 * (with a gzip magic-bytes guard), and indexes with geojson-vt.
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

    // Defensive: verify the response is actually gzip before piping through
    // DecompressionStream. Without this, a missing-file SPA fallback (HTML)
    // would produce "incorrect header check" and an unreadable stream.
    const gzipMagic = new Uint8Array([0x1f, 0x8b]);
    const head = new Uint8Array(await blob.slice(0, 2).arrayBuffer());
    const isGzip = head.length >= 2 && head[0] === gzipMagic[0] && head[1] === gzipMagic[1];

    let text: string;
    if ('DecompressionStream' in window && isGzip) {
      const ds = new DecompressionStream('gzip');
      text = await new Response(blob.stream().pipeThrough(ds)).text();
    } else {
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
