// The 3D build guide's drawing (WO-10.8, plan 7.10 "Viewer features", D319): a footprint's cells
// as unit cubes seen in an orthographic view the viewer turns (yaw) and tilts (pitch), drawn as SVG
// polygons far to near. No WebGL and no new dependency: equal cubes on a grid drawn back to front by
// their centres' depth never overlap wrongly in an orthographic view, and only the faces that look at
// the viewer and have no block in front of them are drawn. The core sends the cells (positions from
// the footprint, gregtec.build.guide_data); nothing is placed or checked here.

export type GuideCell = [number, number, number, number, number]; // x, y, z, palette index, slot
export type Vec = [number, number, number];

// The six directions a face looks: east, west, up, down, south, north (x east, y up, z south).
export const NORMALS: Vec[] = [
  [1, 0, 0],
  [-1, 0, 0],
  [0, 1, 0],
  [0, -1, 0],
  [0, 0, 1],
  [0, 0, -1],
];

// Each face's four corners as offsets from the cell's low corner, wound the same way.
const CORNERS: Vec[][] = [
  [
    [1, 0, 0],
    [1, 1, 0],
    [1, 1, 1],
    [1, 0, 1],
  ],
  [
    [0, 0, 0],
    [0, 0, 1],
    [0, 1, 1],
    [0, 1, 0],
  ],
  [
    [0, 1, 0],
    [0, 1, 1],
    [1, 1, 1],
    [1, 1, 0],
  ],
  [
    [0, 0, 0],
    [1, 0, 0],
    [1, 0, 1],
    [0, 0, 1],
  ],
  [
    [0, 0, 1],
    [1, 0, 1],
    [1, 1, 1],
    [0, 1, 1],
  ],
  [
    [0, 0, 0],
    [0, 1, 0],
    [1, 1, 0],
    [1, 0, 0],
  ],
];

export interface ViewAngle {
  yaw: number; // degrees about the vertical axis; 0 looks from the south (+z) towards the north
  pitch: number; // degrees above the horizon, 0..89
}

export const DEFAULT_VIEW: ViewAngle = { yaw: 35, pitch: 30 };

/** ``p`` in view space: turned by ``yaw`` about y, then tilted by ``pitch`` about x. The screen
 * is (x, -y); z is towards the viewer. */
export function toView(p: Vec, view: ViewAngle): Vec {
  const yaw = (view.yaw * Math.PI) / 180;
  const pitch = (view.pitch * Math.PI) / 180;
  const x1 = p[0] * Math.cos(yaw) - p[2] * Math.sin(yaw);
  const z1 = p[0] * Math.sin(yaw) + p[2] * Math.cos(yaw);
  const y2 = p[1] * Math.cos(pitch) - z1 * Math.sin(pitch);
  const z2 = p[1] * Math.sin(pitch) + z1 * Math.cos(pitch);
  return [x1, y2, z2];
}

export interface Poly {
  cell: number; // index into the cells given
  dir: number; // which of NORMALS the face looks along
  points: string;
  shade: "top" | "side-a" | "side-b" | "bottom";
  depth: number;
}

export interface Drawing {
  polys: Poly[];
  box: [number, number, number, number]; // the SVG viewBox of every cell, whatever is shown
}

const key = (x: number, y: number, z: number): string => `${x},${y},${z}`;

/** The cells to draw: every cell at or below layer ``upTo`` (the layer slider). */
export function shown(cells: GuideCell[], upTo: number): number[] {
  const out: number[] = [];
  cells.forEach((c, i) => {
    if (c[1] <= upTo) out.push(i);
  });
  return out;
}

/** The visible faces of the cells at or below ``upTo``, far to near. A face is hidden when it
 * looks away from the viewer or a block (not a hatch-only cell, which stays air) is in front of
 * it. ``size`` centres the drawing so it turns about the structure's middle. */
export function project(cells: GuideCell[], size: Vec, upTo: number, view: ViewAngle): Drawing {
  const centre: Vec = [size[0] / 2, size[1] / 2, size[2] / 2];
  const solid = new Set<string>();
  for (const c of cells) if (c[1] <= upTo && c[3] >= 0) solid.add(key(c[0], c[1], c[2]));
  const normals = NORMALS.map((n) => toView(n, view));
  const polys: Poly[] = [];
  for (const i of shown(cells, upTo)) {
    const [x, y, z] = cells[i];
    const mid = toView([x + 0.5 - centre[0], y + 0.5 - centre[1], z + 0.5 - centre[2]], view);
    for (let d = 0; d < 6; d++) {
      if (normals[d][2] <= 1e-9) continue; // looks away
      const [nx, ny, nz] = NORMALS[d];
      if (solid.has(key(x + nx, y + ny, z + nz))) continue; // a block in front
      const pts = CORNERS[d].map(([cx, cy, cz]) =>
        toView([x + cx - centre[0], y + cy - centre[1], z + cz - centre[2]], view),
      );
      polys.push({
        cell: i,
        dir: d,
        points: pts.map(([px, py]) => `${px.toFixed(3)},${(-py).toFixed(3)}`).join(" "),
        shade: d === 2 ? "top" : d === 3 ? "bottom" : d === 0 || d === 1 ? "side-a" : "side-b",
        depth: mid[2],
      });
    }
  }
  polys.sort((a, b) => a.depth - b.depth || a.cell - b.cell);
  // The box holds every corner of the whole structure, so the drawing doesn't jump as the layer
  // slider moves.
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const cx of [0, size[0]])
    for (const cy of [0, size[1]])
      for (const cz of [0, size[2]]) {
        const [px, py] = toView([cx - centre[0], cy - centre[1], cz - centre[2]], view);
        minX = Math.min(minX, px);
        maxX = Math.max(maxX, px);
        minY = Math.min(minY, -py);
        maxY = Math.max(maxY, -py);
      }
  const pad = 0.5;
  return { polys, box: [minX - pad, minY - pad, maxX - minX + 2 * pad, maxY - minY + 2 * pad] };
}

/** ``box`` zoomed by ``zoom`` (1 = all of it) about its centre. */
export function zoomed(box: Drawing["box"], zoom: number): Drawing["box"] {
  const [x, y, w, h] = box;
  const z = Math.max(0.25, Math.min(8, zoom));
  return [x + (w - w / z) / 2, y + (h - h / z) / 2, w / z, h / z];
}

/** A stand-in colour for a block the catalog has no colour for: steady per ``registry@meta``. */
export function fallbackColour(block: string): string {
  let h = 0;
  for (let i = 0; i < block.length; i++) h = (h * 31 + block.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360} 22% 52%)`;
}

/** A GT hatch element's name as words: ``InputBus`` -> ``Input Bus``. */
export function hatchWords(kind: string): string {
  return kind.replace(/([a-z0-9])([A-Z])/g, "$1 $2");
}
