/** classifies an array of rings into polygons with outer rings and holes
 * @param {Point[][]} rings
 */
export function classifyRings(rings: Point[][]): Point[][][];
/** @import Pbf from 'pbf' */
/** @import {Feature} from 'geojson' */
export class VectorTileFeature {
    /**
     * @param {Pbf} pbf
     * @param {number} end
     * @param {number} extent
     * @param {string[]} keys
     * @param {unknown[]} values
     */
    constructor(pbf: Pbf, end: number, extent: number, keys: string[], values: unknown[]);
    /** @type {Record<string, unknown>} */
    properties: Record<string, unknown>;
    extent: number;
    /** @type {0 | 1 | 2 | 3} */
    type: 0 | 1 | 2 | 3;
    /** @type {number | undefined} */
    id: number | undefined;
    _pbf: Pbf;
    _geometry: number;
    _keys: string[];
    _values: unknown[];
    loadGeometry(): Point[][];
    bbox(): number[];
    /**
     * @param {number} x
     * @param {number} y
     * @param {number} z
     * @return {Feature}
     */
    toGeoJSON(x: number, y: number, z: number): Feature;
}
export namespace VectorTileFeature {
    let types: ["Unknown", "Point", "LineString", "Polygon"];
}
export class VectorTileLayer {
    /**
     * @param {Pbf} pbf
     * @param {number} [end]
     */
    constructor(pbf: Pbf, end?: number | undefined);
    version: number;
    name: string;
    extent: number;
    length: number;
    _pbf: Pbf;
    /** @type {string[]} */
    _keys: string[];
    /** @type {unknown[]} */
    _values: unknown[];
    /** @type {number[]} */
    _features: number[];
    /** return feature `i` from this layer as a `VectorTileFeature`
     * @param {number} i
     */
    feature(i: number): VectorTileFeature;
}
export class VectorTile {
    /**
     * @param {Pbf} pbf
     * @param {number} [end]
     */
    constructor(pbf: Pbf, end?: number | undefined);
    /** @type {Record<string, VectorTileLayer>} */
    layers: Record<string, VectorTileLayer>;
}
import Point from '@mapbox/point-geometry';
import type Pbf from 'pbf';
import type { Feature } from 'geojson';
