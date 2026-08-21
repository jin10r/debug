/**
 * vector-layer.ts — Canvas GridLayer that renders geojson-vt tiles on Leaflet.
 *
 * Features are grouped by layer_type and drawn in strict Z-order:
 * water → boundary → roads (by class) → settlements.
 */
/* global L */
declare const L: typeof import('leaflet');
import type { TileIndex, TileFeature } from 'geojson-vt';

const TILE_SIZE = 256;
const EXTENT = 4096;

// Theme-aware styles — light and dark variants
const LIGHT_STYLES: Record<string, any> = {
  water:       { fill: '#a6cbe3', fillOpacity: 0.85, stroke: '#88b7d5', strokeWidth: 0.5 },
  boundary:    { stroke: '#555', strokeWidth: 1.5, dash: [6, 4] },
  motorway:    { stroke: '#e892a2', strokeWidth: 2.5 },
  trunk:       { stroke: '#f9b29c', strokeWidth: 2.0 },
  primary:     { stroke: '#fcd6a4', strokeWidth: 1.8 },
  secondary:   { stroke: '#eeeeee', strokeWidth: 1.2 },
  tertiary:    { stroke: '#ffffff', strokeWidth: 1.0 },
  settlements: { radius: 3, fill: '#333', stroke: '#fff' },
};

const DARK_STYLES: Record<string, any> = {
  water:       { fill: '#4db8d4', fillOpacity: 0.6, stroke: '#3a9ab5', strokeWidth: 0.5 },
  boundary:    { stroke: '#6b7b8d', strokeWidth: 1.5, dash: [6, 4] },
  motorway:    { stroke: '#c4506a', strokeWidth: 2.5 },
  trunk:       { stroke: '#b07060', strokeWidth: 2.0 },
  primary:     { stroke: '#c89050', strokeWidth: 1.8 },
  secondary:   { stroke: '#444', strokeWidth: 1.2 },
  tertiary:    { stroke: '#333', strokeWidth: 1.0 },
  settlements: { radius: 3, fill: '#c8c8c8', stroke: '#1a1a1a' },
};

let currentStyles = LIGHT_STYLES;

const ROAD_CLASSES = ['motorway', 'trunk', 'primary', 'secondary', 'tertiary'];
const DRAW_ORDER = ['water', 'boundary', 'roads', 'settlements'];

// Active layer reference for theme switching
let _activeVectorLayer: any = null;

/** Switch vector tile theme (light/dark) and redraw tiles. */
export function setVectorTheme(theme: 'light' | 'dark'): void {
  currentStyles = theme === 'dark' ? DARK_STYLES : LIGHT_STYLES;
  if (_activeVectorLayer && _activeVectorLayer._map) {
    _activeVectorLayer.redraw();
  }
}

export function createVectorLayer(tileIndex: TileIndex): L.GridLayer {
  const VectorLayer = L.GridLayer.extend({
    options: {
      tileSize: TILE_SIZE,
      updateWhenIdle: false,
      updateWhenZooming: true,
      keepBuffer: 4,
      zIndex: 100,
    },

    createTile(coords: L.Coords, done: L.DoneCallback): HTMLElement {
      const tile = L.DomUtil.create('canvas', 'leaflet-tile') as HTMLCanvasElement;
      tile.width = TILE_SIZE;
      tile.height = TILE_SIZE;

      const ctx = tile.getContext('2d');
      const data = tileIndex.getTile(coords.z, coords.x, coords.y);

      if (ctx && data && data.features.length > 0) {
        this._draw(ctx, data);
      }

      // Async done — don't block main thread
      requestAnimationFrame(() => done(null, tile));
      return tile;
    },

    _draw(ctx: CanvasRenderingContext2D, tileData: any): void {
      const s = TILE_SIZE / EXTENT;

      // Group features by layer type
      const groups: Record<string, TileFeature[]> = {};
      for (const f of tileData.features) {
        const lt = f.tags.layer_type;
        if (lt === 'roads') {
          const rc = f.tags.road_class || 'tertiary';
          (groups[rc] ??= []).push(f);
        } else if (lt) {
          (groups[lt] ??= []).push(f);
        }
      }

      // Draw in order (bottom to top)
      for (const key of DRAW_ORDER) {
        let features: TileFeature[];

        if (key === 'roads') {
          features = ROAD_CLASSES.flatMap((rc) => groups[rc] || []);
        } else {
          features = groups[key] || [];
        }

        if (!features.length) continue;

        for (const f of features) {
          const styleKey = key === 'roads' ? (f.tags.road_class || 'tertiary') : key;
          const style = currentStyles[styleKey] || currentStyles.tertiary;

          // Settlements: skip circle rendering — labels handle them via vector-labels.ts
          if (key === 'settlements') continue;

          switch (f.type) {
            case 3: this._polygon(ctx, f.geometry as number[][][], s, style); break;
            case 2: this._line(ctx, f.geometry as number[][][], s, style); break;
            case 1: this._point(ctx, f.geometry as number[][], s, style); break;
          }
        }
      }
    },

    _polygon(ctx: CanvasRenderingContext2D, geom: number[][][], s: number, st: any): void {
      ctx.fillStyle = st.fill || '#000';
      ctx.globalAlpha = st.fillOpacity ?? 1;
      for (const ring of geom) {
        ctx.beginPath();
        for (let i = 0; i < ring.length; i++) {
          const x = ring[i][0] * s;
          const y = ring[i][1] * s;
          i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.fill();
      }
      if (st.stroke && st.strokeWidth) {
        ctx.strokeStyle = st.stroke;
        ctx.lineWidth = st.strokeWidth;
        ctx.globalAlpha = 0.5;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    },

    _line(ctx: CanvasRenderingContext2D, geom: number[][][], s: number, st: any): void {
      ctx.strokeStyle = st.stroke || '#000';
      ctx.lineWidth = st.strokeWidth || 1;
      ctx.globalAlpha = st.strokeOpacity ?? 0.9;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.setLineDash(st.dash || []);
      for (const line of geom) {
        ctx.beginPath();
        for (let i = 0; i < line.length; i++) {
          const x = line[i][0] * s;
          const y = line[i][1] * s;
          i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    },

    _point(ctx: CanvasRenderingContext2D, geom: number[][], s: number, st: any): void {
      for (const [x, y] of geom) {
        ctx.beginPath();
        ctx.arc(x * s, y * s, st.radius || 3, 0, Math.PI * 2);
        ctx.fillStyle = st.fill || '#333';
        ctx.fill();
        ctx.strokeStyle = st.stroke || '#fff';
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    },
  });

  const layer = new (VectorLayer as any)();
  _activeVectorLayer = layer;
  return layer;
}
