declare module 'geojson-vt' {
  import { FeatureCollection, Feature } from 'geojson';

  export interface Options {
    maxZoom?: number;
    indexMaxZoom?: number;
    indexMaxPoints?: number;
    tolerance?: number;
    extent?: number;
    buffer?: number;
    lineMetrics?: boolean;
    promoteId?: string | null;
    generateId?: boolean;
    debug?: number;
  }

  export interface TileFeature {
    geometry: number[][][] | number[][] | number[];
    type: 1 | 2 | 3; // 1=Point, 2=LineString, 3=Polygon
    tags: Record<string, any>;
    id?: number | string;
  }

  export interface Tile {
    features: TileFeature[];
    numPoints: number;
    numSimplified: number;
    numFeatures: number;
    x: number;
    y: number;
    z: number;
  }

  export interface TileIndex {
    getTile(z: number, x: number, y: number): Tile | null;
    destroy(): void;
  }

  function geojsonvt(
    data: FeatureCollection | Feature[],
    options?: Options
  ): TileIndex;

  export default geojsonvt;
}
