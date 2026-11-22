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

/** Which of NORMALS ``v`` is (the controller's front, from the core), or -1. */
export function normalIndex(v: number[]): number {
  return NORMALS.findIndex((n) => n[0] === v[0] && n[1] === v[1] && n[2] === v[2]);
}

/** An arrow out of the controller's front face, as an SVG path in the drawing's frame: from the
 * face's middle one block out along ``front`` (review M1: which way the controller looks, whatever
 * the turn). ``at`` is the controller's cell, ``size`` the structure's (as for :func:`project`). */
export function frontArrow(size: Vec, at: Vec, front: Vec, view: ViewAngle): string {
  const centre: Vec = [size[0] / 2, size[1] / 2, size[2] / 2];
  const mid: Vec = [at[0] + 0.5 + front[0] * 0.5, at[1] + 0.5, at[2] + 0.5 + front[2] * 0.5];
  const side: Vec = [-front[2], 0, front[0]]; // across the front, level
  const pt = (along: number, across: number): string => {
    const [px, py] = toView(
      [
        mid[0] + front[0] * along + side[0] * across - centre[0],
        mid[1] - centre[1],
        mid[2] + front[2] * along + side[2] * across - centre[2],
      ],
      view,
    );
    return `${px.toFixed(3)},${(-py).toFixed(3)}`;
  };
  // A path, not a polygon: the drawing's polygons are its cells' faces.
  return `M${pt(0.9, 0)} L${pt(0.1, 0.35)} L${pt(0.1, -0.35)} Z`;
}

/** The compass's four letters as unit directions on the screen (x right, y down) for ``view``:
 * where north, east, south and west lie in the drawing as it is turned and tilted. */
export function compass(view: ViewAngle): { letter: "N" | "E" | "S" | "W"; x: number; y: number }[] {
  const dirs: ["N" | "E" | "S" | "W", Vec][] = [
    ["N", [0, 0, -1]],
    ["E", [1, 0, 0]],
    ["S", [0, 0, 1]],
    ["W", [-1, 0, 0]],
  ];
  return dirs.map(([letter, d]) => {
    const [x, y] = toView(d, view);
    const len = Math.hypot(x, y) || 1;
    return { letter, x: x / len, y: -y / len };
  });
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

/** A box drawn as an outline over the blocks (WO-10.10: the floor plan's machines and keep-outs
 * inside a shell, D370): ``lo`` and ``hi`` its corner cells, inclusive, in the drawing's cells. */
export interface GhostBox {
  id: string;
  kind: "machine" | "keepout";
  label: string;
  lo: number[];
  hi: number[];
}

/** The box's 12 edges as one SVG path in the drawing's frame (as for :func:`project`). */
export function boxEdges(size: Vec, box: Pick<GhostBox, "lo" | "hi">, view: ViewAngle): string {
  const centre: Vec = [size[0] / 2, size[1] / 2, size[2] / 2];
  const lo = box.lo;
  const hi = box.hi.map((v) => v + 1);
  const pt = (c: Vec): string => {
    const [px, py] = toView([c[0] - centre[0], c[1] - centre[1], c[2] - centre[2]], view);
    return `${px.toFixed(3)},${(-py).toFixed(3)}`;
  };
  const corner = (i: number): Vec => [i & 1 ? hi[0] : lo[0], i & 2 ? hi[1] : lo[1], i & 4 ? hi[2] : lo[2]];
  const edges: [number, number][] = [];
  for (let i = 0; i < 8; i++)
    for (const bit of [1, 2, 4]) if (!(i & bit)) edges.push([i, i | bit]);
  return edges.map(([a, b]) => `M${pt(corner(a))} L${pt(corner(b))}`).join(" ");
}
