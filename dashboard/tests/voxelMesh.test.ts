// WO-10.19 (D433): the WebGL shell viewer's mesh, pure. Hidden faces are culled on the voxel grid
// (a face against a block is never drawn; a top face under a block only at the slider's cut), the
// face colours follow the SVG viewer's rule, the view matrix agrees with guideMesh's toView and
// the SVG viewBox fit, and picking returns the nearest block drawn under the pointer.
import { describe, expect, it } from "vitest";
import { hallScene } from "../e2e/hall";
import { DEFAULT_VIEW, type GuideCell, type Vec, fallbackColour, structureBox, toView, zoomed } from "../src/views/guideMesh";
import { COVERED_ABOVE, FACE_STRIDE, buildMesh, faceColour, facesAt, pick, rgbOf, vertices, viewMatrix } from "../src/views/voxelMesh";

const GREY = { block: "minecraft:stone@0", top: "#808080", side: "#404040" };
const cell = (x: number, y: number, z: number, b = 0): GuideCell => [x, y, z, b, -1];

function faceList(mesh: ReturnType<typeof buildMesh>) {
  const u16 = new Uint16Array(mesh.faces);
  const u8 = new Uint8Array(mesh.faces);
  const u32 = new Uint32Array(mesh.faces);
  return Array.from({ length: mesh.count }, (_, i) => {
    const o = i * FACE_STRIDE;
    return {
      pos: [u16[o / 2], u16[o / 2 + 1], u16[o / 2 + 2]],
      dir: u8[o + 6],
      flags: u8[o + 7],
      rgb: [u8[o + 8], u8[o + 9], u8[o + 10]],
      cell: u32[o / 4 + 3],
    };
  });
}

describe("buildMesh", () => {
  it("one cube has six faces", () => {
    const m = buildMesh([cell(0, 0, 0)], [1, 1, 1], [GREY]);
    expect(m.count).toBe(6);
    expect(facesAt(m, 0)).toBe(6);
  });

  it("two stacked cubes: ten faces, and the lower top comes back at the cut", () => {
    const m = buildMesh([cell(0, 0, 0), cell(0, 1, 0)], [1, 2, 1], [GREY]);
    const faces = faceList(m);
    expect(m.count).toBe(11); // 10 always, plus the lower top, kept for the slider
    expect(faces.filter((f) => f.flags & COVERED_ABOVE)).toEqual([expect.objectContaining({ pos: [0, 0, 0], dir: 2 })]);
    expect(facesAt(m, 1)).toBe(10);
    expect(facesAt(m, 0)).toBe(6);
  });

  it("a solid 3x3x3 shows 54 faces", () => {
    const cells: GuideCell[] = [];
    for (let y = 0; y < 3; y++) for (let z = 0; z < 3; z++) for (let x = 0; x < 3; x++) cells.push(cell(x, y, z));
    const m = buildMesh(cells, [3, 3, 3], [GREY]);
    expect(facesAt(m, 2)).toBe(54);
    expect(facesAt(m, 1)).toBe(9 * 2 + 4 * 3 * 2); // two layers: top, bottom and 4 sides of 3x2
    expect(facesAt(m, 0)).toBe(9 * 2 + 4 * 3);
  });

  it("colours: top face its top, the rest the side, then the other, then the stand-in", () => {
    const palette = [GREY, { block: "a:b@0", top: "#ff0000", side: null }, { block: "c:d@1", top: null, side: null }];
    const m = buildMesh([cell(0, 0, 0, 0), cell(2, 0, 0, 1), cell(4, 0, 0, 2)], [5, 1, 1], palette);
    const faces = faceList(m);
    const of = (c: number, d: number) => faces.find((f) => f.cell === c && f.dir === d)?.rgb;
    expect(of(0, 2)).toEqual([128, 128, 128]);
    expect(of(0, 0)).toEqual([64, 64, 64]);
    expect(of(0, 3)).toEqual([64, 64, 64]); // the bottom uses the side, as in the SVG viewer
    expect(of(1, 4)).toEqual([255, 0, 0]);
    expect(of(2, 2)).toEqual(rgbOf(fallbackColour("c:d@1")));
    expect(faceColour(palette[1], 0)).toBe("#ff0000");
  });

  it("rgbOf reads #rrggbb and the stand-in's hsl()", () => {
    expect(rgbOf("#0a141e")).toEqual([10, 20, 30]);
    expect(rgbOf("hsl(0 100% 50%)")).toEqual([255, 0, 0]);
    expect(rgbOf("hsl(120 100% 25%)")).toEqual([0, 128, 0]);
    expect(rgbOf("hsl(200 22% 52%)")).toEqual([106, 142, 160]);
  });

  it("a ~20,000-block hall keeps only the faces that can be seen", () => {
    const s = hallScene();
    expect(s.cells.length).toBeGreaterThan(18_000);
    expect(s.cells.length).toBeLessThan(22_000);
    const m = buildMesh(s.cells, s.size, s.palette);
    expect(m.count).toBeLessThan(s.cells.length * 6 * 0.5);
    expect(facesAt(m, 0)).toBeLessThan(facesAt(m, 23));
  });
});

describe("vertices", () => {
  it("four vertices and two triangles per face, the corner in flags bits 1-2", () => {
    const m = buildMesh([cell(0, 0, 0), cell(0, 1, 0)], [1, 2, 1], [GREY]);
    const v = vertices(m);
    expect(v.data.byteLength).toBe(m.count * 4 * FACE_STRIDE);
    expect(Array.from(v.indices.slice(0, 12))).toEqual([0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]);
    const u8 = new Uint8Array(v.data);
    const covered = faceList(m).findIndex((f) => f.flags & COVERED_ABOVE);
    expect([0, 1, 2, 3].map((k) => u8[(covered * 4 + k) * FACE_STRIDE + 7])).toEqual([1, 3, 5, 7]);
  });
});

describe("viewMatrix", () => {
  it("puts a corner where the SVG viewBox would", () => {
    const size: Vec = [4, 3, 5];
    const view = { yaw: 60, pitch: 25 };
    const box = zoomed(structureBox(size, view), 1.5);
    const [W, H] = [800, 300];
    const m = viewMatrix(size, view, box, W, H);
    const p: Vec = [3, 1, 4];
    const ndc = [0, 1, 2].map((r) => m[r] * p[0] + m[4 + r] * p[1] + m[8 + r] * p[2] + m[12 + r]);
    // the SVG: screen (vx, -vy) through viewBox ``box`` with xMidYMid meet
    const [vx, vy, vz] = toView([p[0] - 2, p[1] - 1.5, p[2] - 2.5], view);
    const s = Math.min(W / box[2], H / box[3]);
    const sx = (vx - box[0]) * s + (W - box[2] * s) / 2;
    const sy = (-vy - box[1]) * s + (H - box[3] * s) / 2;
    expect(ndc[0]).toBeCloseTo((sx / W) * 2 - 1, 5);
    expect(ndc[1]).toBeCloseTo(1 - (sy / H) * 2, 5);
    expect(ndc[2]).toBeCloseTo(-vz / (Math.hypot(4, 3, 5) / 2 + 1), 5);
    expect(Math.abs(ndc[2])).toBeLessThan(1);
  });
});

describe("pick", () => {
  // a row of three with one on the middle, seen from the default view
  const size: Vec = [3, 2, 1];
  const cells: GuideCell[] = [cell(0, 0, 0), cell(1, 0, 0), cell(2, 0, 0), cell(1, 1, 0)];
  const mesh = buildMesh(cells, size, [GREY]);
  const [W, H] = [600, 400];
  const screen = (p: Vec, view = DEFAULT_VIEW, zoom = 1): [number, number] => {
    const m = viewMatrix(size, view, zoomed(structureBox(size, view), zoom), W, H);
    const nx = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12];
    const ny = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13];
    return [((nx + 1) / 2) * W, ((1 - ny) / 2) * H];
  };
  const at = (p: Vec, upTo: number, view = DEFAULT_VIEW) => {
    const [px, py] = screen(p, view);
    return pick(mesh, upTo, view, structureBox(size, view), W, H, px, py);
  };

  it("hits the top block from above", () => {
    expect(at([1.5, 2, 0.5], 1)).toBe(3);
  });

  it("skips a block above the cut and finds the one under it", () => {
    // the lower middle block's top: the upper block stands in front of it once it is shown
    expect(at([1.5, 1, 0.5], 1)).toBe(3);
    expect(at([1.5, 1, 0.5], 0)).toBe(1);
  });

  it("returns the nearest of two blocks on the ray", () => {
    const view = { yaw: 0, pitch: 5 }; // from the south, nearly level: the ray runs north
    const row: GuideCell[] = [cell(0, 0, 0), cell(0, 0, 1)];
    const m = buildMesh(row, [1, 1, 2], [GREY]);
    const box = structureBox([1, 1, 2], view);
    const mat = viewMatrix([1, 1, 2], view, box, W, H);
    const p: Vec = [0.5, 0.5, 2];
    const nx = mat[0] * p[0] + mat[4] * p[1] + mat[8] * p[2] + mat[12];
    const ny = mat[1] * p[0] + mat[5] * p[1] + mat[9] * p[2] + mat[13];
    expect(pick(m, 0, view, box, W, H, ((nx + 1) / 2) * W, ((1 - ny) / 2) * H)).toBe(1); // z=1 is nearer the viewer (south)
  });

  it("a miss is null", () => {
    expect(pick(mesh, 1, DEFAULT_VIEW, structureBox(size, DEFAULT_VIEW), W, H, 2, 2)).toBeNull();
  });
});
