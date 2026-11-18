// The shell card's preview mesh as an isometric drawing (WO-10.7, D316). The core sends the
// shell's exposed faces, merged into rectangles (gregtec.build.shell.mesh); this only projects
// them: no block is placed or checked here. A face is [d, x, y, z, a, b, c]: d the way it looks
// (0 east, 1 west, 2 up, 3 down, 4 south, 5 north), x y z its lowest cell, a and b its extent
// along the plane's axes (x faces z then y, y faces x then z, z faces x then y), c its palette
// entry.

export type Face = number[];
export type Vec = [number, number, number];

const NORMALS: Vec[] = [
  [1, 0, 0],
  [-1, 0, 0],
  [0, 1, 0],
  [0, -1, 0],
  [0, 0, 1],
  [0, 0, -1],
];
// For each direction: [normal axis, u axis, v axis].
const PLANE: [number, number, number][] = [
  [0, 2, 1],
  [0, 2, 1],
  [1, 0, 2],
  [1, 0, 2],
  [2, 0, 1],
  [2, 0, 1],
];
const COS30 = Math.cos(Math.PI / 6);
const SIN30 = 0.5;

/** The four corners of a face rectangle, in shell coordinates (cell corners). */
export function corners(f: Face): Vec[] {
  const [d, x, y, z, a, b] = f;
  const [n, u, v] = PLANE[d];
  const base: Vec = [x, y, z];
  if (d % 2 === 0) base[n] += 1; // a face looking the + way sits on the cell's far side
  const at = (du: number, dv: number): Vec => {
    const p: Vec = [base[0], base[1], base[2]];
    p[u] += du;
    p[v] += dv;
    return p;
  };
  return [at(0, 0), at(a, 0), at(a, b), at(0, b)];
}

/** ``p`` turned a quarter ``turns`` times about the vertical axis through the shell's centre. */
export function turn(p: Vec, size: Vec, turns: number): Vec {
  let [x, y, z] = [p[0] - size[0] / 2, p[1], p[2] - size[2] / 2];
  for (let i = 0; i < ((turns % 4) + 4) % 4; i++) [x, z] = [-z, x];
  return [x, y, z];
}

export interface Poly {
  points: string;
  colour: number;
  shade: "top" | "side-a" | "side-b";
  depth: number;
}

/** The faces seen from above the south-east (after ``turns`` quarter turns), projected and
 * sorted far to near, and the drawing's view box. */
export function project(faces: Face[], size: Vec, turns = 0): { polys: Poly[]; box: [number, number, number, number] } {
  const polys: Poly[] = [];
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const f of faces) {
    const normal = turn(NORMALS[f[0]], [0, 0, 0], turns);
    const seen = normal[0] > 0.5 || normal[1] > 0.5 || normal[2] > 0.5;
    if (!seen) continue;
    const pts = corners(f).map((c) => turn(c, size, turns));
    const flat = pts.map(([x, y, z]) => [(x - z) * COS30, (x + z) * SIN30 - y]);
    for (const [px, py] of flat) {
      minX = Math.min(minX, px);
      maxX = Math.max(maxX, px);
      minY = Math.min(minY, py);
      maxY = Math.max(maxY, py);
    }
    const centre = pts.reduce((s, p) => s + p[0] + p[1] + p[2], 0) / 4;
    polys.push({
      points: flat.map(([px, py]) => `${px.toFixed(2)},${py.toFixed(2)}`).join(" "),
      colour: f[6],
      shade: normal[1] > 0.5 ? "top" : normal[0] > 0.5 ? "side-a" : "side-b",
      depth: centre,
    });
  }
  polys.sort((p, q) => p.depth - q.depth);
  if (!polys.length) return { polys, box: [0, 0, 1, 1] };
  return { polys, box: [minX - 1, minY - 1, maxX - minX + 2, maxY - minY + 2] };
}
