// A made-up big shell scene for the viewer's speed check (WO-10.19, D433): a 72x24x72 hall
// (floor, four walls, a roof, interior columns and two mezzanine floors) of about 20,000 blocks,
// three palette entries (one with no colours, so the stand-in colour is used) and two ghosts.
// bigHallScene is the largest plot (128x28x128, one mezzanine): about 57,000 blocks, under the
// core's 60,000-cell viewer cap. Only the viewer's numbers: nothing here is a game number.
import type { ShellScene } from "../src/api/types";

export function hallScene(build = "sh-perfhall0000", [X, Y, Z]: [number, number, number] = [72, 24, 72], decks = [8, 16]): ShellScene {
  const cells: ShellScene["cells"] = [];
  const add = (x: number, y: number, z: number, b: number) => cells.push([x, y, z, b, -1]);
  for (let y = 0; y < Y; y++)
    for (let z = 0; z < Z; z++)
      for (let x = 0; x < X; x++) {
        const edge = x === 0 || z === 0 || x === X - 1 || z === Z - 1;
        if (y === 0) add(x, y, z, 0); // floor
        else if (y === Y - 1) add(x, y, z, 2); // roof
        else if (edge) add(x, y, z, 1); // walls
        else if (x % 12 === 6 && z % 12 === 6) add(x, y, z, 1); // columns
        else if (decks.includes(y) && x > 8 && x < X - 9 && z > 8 && z < Z - 9 && (x + z) % 3 !== 0) add(x, y, z, 0); // mezzanines
      }
  // sorted by (y, z, x) as the core sends them
  const counts = [0, 0, 0];
  for (const c of cells) counts[c[3]] += 1;
  const layers = Array.from({ length: Y }, (_, y) => ({ y, blocks: 0, materials: {} as Record<string, number> }));
  const names = ["minecraft:stonebrick@0", "gregtech:gt.blockconcretes@3", "minecraft:stone_slab@0"];
  for (const [, y, , b] of cells) {
    layers[y].blocks += 1;
    layers[y].materials[names[b]] = (layers[y].materials[names[b]] ?? 0) + 1;
  }
  return {
    build,
    size: [X, Y, Z],
    palette: [
      { block: names[0], name: "Stone Bricks", role: "floor", count: counts[0], top: "#7a7a7a", side: "#6e6e6e" },
      { block: names[1], name: "Concrete", role: "wall", count: counts[1], top: null, side: null },
      { block: names[2], name: "Stone Slab", role: "roof", count: counts[2], top: "#9a9a9a", side: null },
    ],
    cells,
    layers,
    ghosts: {
      machines: [{ id: "m1", name: "Big Machine", ref: "mte:1000", lo: [20, 1, 20], hi: [30, 6, 30] }],
      keepouts: [{ id: "k1", lo: [40, 1, 40], hi: [50, 4, 50] }],
    },
    caveats: [],
  };
}

export const bigHallScene = (build = "sh-perfhall0000"): ShellScene => hallScene(build, [128, 28, 128], [14]);
