// The shell viewer's WebGL mesh (WO-10.19, D433): a shell's cells as unit cubes on a voxel grid,
// with only the faces that can ever be seen (hidden-face culling: a face against a block is never
// drawn, except a top face that the layer slider can uncover). Built once per build, whatever the
// layer, turn, zoom or hover: those are uniforms in GLScene. The view is the same orthographic
// turn and tilt as guideMesh's SVG drawing (toView, zoomed, xMidYMid meet), so the ghost overlay
// lines up, and picking walks the grid (Amanatides-Woo) for the first block on the ray. Pure: no
// DOM, unit-tested in jsdom. Cells with no block (b < 0, the build guide's hatch-only cells) are
// not drawn here; shells have none, and the build guide keeps the SVG viewer.
import { type GuideCell, type Vec, type ViewAngle, fallbackColour } from "./guideMesh";

export type Box = [number, number, number, number]; // an SVG viewBox: x, y, w, h

/** Bytes per face record (and per vertex): x, y, z (uint16), dir, flags (uint8), rgba (uint8 x4), cell (uint32). */
export const FACE_STRIDE = 16;
/** flags bit: a top face with a block on it, seen only when its layer is the slider's cut. */
export const COVERED_ABOVE = 1;

export interface VoxelMesh {
  size: Vec;
  grid: Int32Array; // cell index + 1 per voxel, 0 = empty (x + X * (z + Z * y))
  faces: ArrayBuffer; // count * FACE_STRIDE bytes
  count: number;
  always: Int32Array; // per layer: faces seen whenever the layer is shown
  coveredUp: Int32Array; // per layer: top faces seen only at the cut
}

const DIRS: Vec[] = [
  [1, 0, 0],
  [-1, 0, 0],
  [0, 1, 0],
  [0, -1, 0],
  [0, 0, 1],
  [0, 0, -1],
];

/** "#rrggbb" or guideMesh's fallback "hsl(h s% l%)" as [r, g, b] 0..255. */
export function rgbOf(colour: string): [number, number, number] {
  const hex = /^#([0-9a-f]{6})$/i.exec(colour.trim());
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const hsl = /^hsl\(\s*([\d.]+)[\s,]+([\d.]+)%[\s,]+([\d.]+)%\s*\)$/i.exec(colour.trim());
  if (hsl) {
    const h = Number(hsl[1]) / 360;
    const s = Number(hsl[2]) / 100;
    const l = Number(hsl[3]) / 100;
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const ch = (t0: number): number => {
      const t = t0 < 0 ? t0 + 1 : t0 > 1 ? t0 - 1 : t0;
      const v = t < 1 / 6 ? p + (q - p) * 6 * t : t < 1 / 2 ? q : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6 : p;
      return Math.round(v * 255);
    };
    return [ch(h + 1 / 3), ch(h), ch(h - 1 / 3)];
  }
  return [128, 128, 128];
}

/** A face's colour as the SVG viewer picks it: the top face its block's top, every other its side,
 * then the other, then the steady stand-in. */
export function faceColour(block: { block: string; top: string | null; side: string | null }, dir: number): string {
  return (dir === 2 ? block.top : block.side) ?? block.top ?? block.side ?? fallbackColour(block.block);
}

/** The mesh of every face that can be seen at some layer. */
export function buildMesh(
  cells: GuideCell[],
  size: Vec,
  palette: { block: string; top: string | null; side: string | null }[],
): VoxelMesh {
  const [X, Y, Z] = size;
  const grid = new Int32Array(X * Y * Z);
  const at = (x: number, y: number, z: number): number => x + X * (z + Z * y);
  const inside = (x: number, y: number, z: number): boolean => x >= 0 && y >= 0 && z >= 0 && x < X && y < Y && z < Z;
  cells.forEach(([x, y, z, b], i) => {
    if (b >= 0 && inside(x, y, z)) grid[at(x, y, z)] = i + 1;
  });
  // colours per palette entry and direction, worked out once
  const rgb = palette.map((p) => DIRS.map((_, d) => rgbOf(faceColour(p, d))));
  let cap = 0;
  for (const c of cells) if (c[3] >= 0) cap += 6;
  const buf = new ArrayBuffer(cap * FACE_STRIDE);
  const u16 = new Uint16Array(buf);
  const u8 = new Uint8Array(buf);
  const u32 = new Uint32Array(buf);
  const always = new Int32Array(Y);
  const coveredUp = new Int32Array(Y);
  let n = 0;
  cells.forEach(([x, y, z, b], i) => {
    if (b < 0 || !inside(x, y, z) || grid[at(x, y, z)] !== i + 1) return;
    for (let d = 0; d < 6; d++) {
      const [dx, dy, dz] = DIRS[d];
      const nx = x + dx;
      const ny = y + dy;
      const nz = z + dz;
      const blocked = inside(nx, ny, nz) && grid[at(nx, ny, nz)] !== 0;
      // a block beside or below is always shown with this one; a block above only up to its layer
      if (blocked && d !== 2) continue;
      const o = n * FACE_STRIDE;
      u16[o / 2] = x;
      u16[o / 2 + 1] = y;
      u16[o / 2 + 2] = z;
      u8[o + 6] = d;
      u8[o + 7] = blocked ? COVERED_ABOVE : 0;
      const c = rgb[b]?.[d] ?? [128, 128, 128];
      u8[o + 8] = c[0];
      u8[o + 9] = c[1];
      u8[o + 10] = c[2];
      u8[o + 11] = 255;
      u32[o / 4 + 3] = i;
      if (blocked) coveredUp[y] += 1;
      else always[y] += 1;
      n += 1;
    }
  });
  return { size, grid, faces: buf.slice(0, n * FACE_STRIDE), count: n, always, coveredUp };
}

/** Faces whose corners 0..3 (GLScene's vertex shader) run anticlockwise seen from outside the
 * block (E, Down, S): their triangles are listed the other way round, so every face is clockwise
 * from outside and GLScene can cull the faces turned away (gl.frontFace(gl.CW)). */
const ANTICLOCKWISE = [true, false, false, true, true, false];

/** The faces as GPU vertices (four per face, the face's record with the corner 0..3 in flags bits
 * 1-2) and two triangles per face, clockwise seen from outside. Instancing is avoided on purpose:
 * software GPUs (SwiftShader, old drivers) pay per instance, and a plain indexed draw is fast everywhere. */
export function vertices(mesh: VoxelMesh): { data: ArrayBuffer; indices: Uint32Array } {
  const src = new Uint8Array(mesh.faces);
  const data = new ArrayBuffer(mesh.count * 4 * FACE_STRIDE);
  const dst = new Uint8Array(data);
  const indices = new Uint32Array(mesh.count * 6);
  for (let f = 0; f < mesh.count; f++) {
    const face = src.subarray(f * FACE_STRIDE, (f + 1) * FACE_STRIDE);
    for (let k = 0; k < 4; k++) {
      const o = (f * 4 + k) * FACE_STRIDE;
      dst.set(face, o);
      dst[o + 7] = (face[7] & COVERED_ABOVE) | (k << 1);
    }
    const v = f * 4;
    indices.set(ANTICLOCKWISE[face[6]] ? [v, v + 2, v + 1, v, v + 3, v + 2] : [v, v + 1, v + 2, v, v + 2, v + 3], f * 6);
  }
  return { data, indices };
}

/** How many faces are drawn with the slider at ``upTo`` (the canvas's data-faces). */
export function facesAt(mesh: VoxelMesh, upTo: number): number {
  let n = 0;
  const top = Math.min(upTo, mesh.always.length - 1);
  for (let y = 0; y <= top; y++) n += mesh.always[y];
  if (upTo >= 0 && upTo < mesh.coveredUp.length) n += mesh.coveredUp[upTo];
  return n;
}

/** The view rotation's three rows (as guideMesh's toView). */
function rows(view: ViewAngle): [Vec, Vec, Vec] {
  const yaw = (view.yaw * Math.PI) / 180;
  const pitch = (view.pitch * Math.PI) / 180;
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const cp = Math.cos(pitch);
  const sp = Math.sin(pitch);
  return [
    [cy, 0, -sy],
    [-sy * sp, cp, -cy * sp],
    [sy * cp, sp, cy * cp],
  ];
}

/** The fit of ``box`` (screen units, y down) into a ``w`` x ``h`` canvas as SVG's xMidYMid meet:
 * NDC x = vx * ax + bx, NDC y = vy * ay + by. */
function fit(box: Box, w: number, h: number): { ax: number; bx: number; ay: number; by: number } {
  const [x, y, bw, bh] = box;
  const s = Math.min(w / bw, h / bh);
  const ax = (2 * s) / w;
  const ay = (2 * s) / h;
  return { ax, bx: -(x + bw / 2) * ax, ay, by: (y + bh / 2) * ay };
}

/** The depth range: half the structure's diagonal and a block. */
const reach = (size: Vec): number => Math.hypot(size[0], size[1], size[2]) / 2 + 1;

/** World (cell units) to clip space, column-major for gl.uniformMatrix4fv: the structure turned
 * about its middle, fitted into the canvas as the SVG viewBox ``box`` would be, nearer = smaller depth. */
export function viewMatrix(size: Vec, view: ViewAngle, box: Box, w: number, h: number): Float32Array {
  const r = rows(view);
  const f = fit(box, w, h);
  const c: Vec = [size[0] / 2, size[1] / 2, size[2] / 2];
  const k = -1 / reach(size);
  const row = (v: Vec, a: number, b: number): [number, number, number, number] => [
    v[0] * a,
    v[1] * a,
    v[2] * a,
    b - a * (v[0] * c[0] + v[1] * c[1] + v[2] * c[2]),
  ];
  const m0 = row(r[0], f.ax, f.bx);
  const m1 = row(r[1], f.ay, f.by);
  const m2 = row(r[2], k, 0);
  // column-major
  return new Float32Array([m0[0], m1[0], m2[0], 0, m0[1], m1[1], m2[1], 0, m0[2], m1[2], m2[2], 0, m0[3], m1[3], m2[3], 1]);
}

/** The cell under canvas pixel (``px``, ``py``) of a ``w`` x ``h`` canvas, or null: the first
 * block on the ray at or below ``upTo``, which is the nearest face drawn there. */
export function pick(
  mesh: VoxelMesh,
  upTo: number,
  view: ViewAngle,
  box: Box,
  w: number,
  h: number,
  px: number,
  py: number,
): number | null {
  const { size, grid } = mesh;
  const [X, Y, Z] = size;
  const f = fit(box, w, h);
  const vx = ((px * 2) / w - 1 - f.bx) / f.ax;
  const vy = (1 - (py * 2) / h - f.by) / f.ay;
  const r = rows(view);
  const far = reach(size) + 1;
  // back to the world: the transpose, then the middle
  const o: number[] = [0, 1, 2].map((i) => r[0][i] * vx + r[1][i] * vy + r[2][i] * far + size[i] / 2);
  const d: number[] = [0, 1, 2].map((i) => -r[2][i]);
  // where the ray enters and leaves the grid's box
  let t0 = 0;
  let t1 = Infinity;
  for (let i = 0; i < 3; i++) {
    if (Math.abs(d[i]) < 1e-12) {
      if (o[i] < 0 || o[i] > size[i]) return null;
      continue;
    }
    let a = (0 - o[i]) / d[i];
    let b = (size[i] - o[i]) / d[i];
    if (a > b) [a, b] = [b, a];
    t0 = Math.max(t0, a);
    t1 = Math.min(t1, b);
  }
  if (t0 > t1) return null;
  const p = [0, 1, 2].map((i) => o[i] + d[i] * (t0 + 1e-9));
  const cell = p.map((v, i) => Math.min(size[i] - 1, Math.max(0, Math.floor(v))));
  const step = d.map((v) => (v > 0 ? 1 : v < 0 ? -1 : 0));
  const tMax = d.map((v, i) => (v > 0 ? (cell[i] + 1 - p[i]) / v : v < 0 ? (cell[i] - p[i]) / v : Infinity));
  const tDelta = d.map((v) => (v !== 0 ? Math.abs(1 / v) : Infinity));
  for (;;) {
    const [x, y, z] = cell;
    if (x < 0 || y < 0 || z < 0 || x >= X || y >= Y || z >= Z) return null;
    if (y <= upTo) {
      const got = grid[x + X * (z + Z * y)];
      if (got) return got - 1;
    }
    const i = tMax[0] < tMax[1] ? (tMax[0] < tMax[2] ? 0 : 2) : tMax[1] < tMax[2] ? 1 : 2;
    cell[i] += step[i];
    tMax[i] += tDelta[i];
  }
}
