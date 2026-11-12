// The flow card's first layout (D160): each machine in the column after the furthest machine that
// feeds it, rows in input order. Loops (a closed fuel loop feeding itself) keep their first
// placement, so the layout always ends. WO-3.6's FlowDiagram draws with it until elkjs's layered
// layout arrives, and keeps it if elkjs fails.
export interface FlowBox {
  id: string;
  col: number;
  row: number;
  x: number;
  y: number;
}

export const NODE_W = 220;
export const NODE_H = 72;
export const GAP_X = 96;
export const GAP_Y = 28;

export function layoutFlow(
  nodes: { id: string }[],
  edges: { from: string; to: string }[],
): { boxes: Map<string, FlowBox>; width: number; height: number } {
  const ids = nodes.map((n) => n.id);
  const known = new Set(ids);
  const col = new Map(ids.map((id) => [id, 0]));
  const usable = edges.filter((e) => known.has(e.from) && known.has(e.to) && e.from !== e.to);
  // Longest-path layering, relaxed at most once per node: a cycle can't push columns forever.
  for (let pass = 0; pass < ids.length; pass += 1) {
    let moved = false;
    for (const e of usable) {
      const want = (col.get(e.from) ?? 0) + 1;
      if (want > (col.get(e.to) ?? 0) && want < ids.length) {
        col.set(e.to, want);
        moved = true;
      }
    }
    if (!moved) break;
  }
  const rows = new Map<number, number>();
  const boxes = new Map<string, FlowBox>();
  for (const id of ids) {
    const c = col.get(id) ?? 0;
    const r = rows.get(c) ?? 0;
    rows.set(c, r + 1);
    boxes.set(id, { id, col: c, row: r, x: c * (NODE_W + GAP_X), y: r * (NODE_H + GAP_Y) });
  }
  const cols = Math.max(0, ...[...boxes.values()].map((b) => b.col)) + 1;
  const tallest = Math.max(0, ...rows.values());
  return {
    boxes,
    width: ids.length ? cols * NODE_W + (cols - 1) * GAP_X : 0,
    height: ids.length ? tallest * NODE_H + (tallest - 1) * GAP_Y : 0,
  };
}
