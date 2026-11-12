// The flow card's diagram (WO-3.6, PM02, D160): React Flow draws the card's machines and flows,
// elkjs lays them out (layered, left to right). The card is data from the core, never HTML: a
// node shows its label, count, machine and tier (the tier band in the GT tier colour, always
// beside the tier's name, D51); a flow its item and rate. Until elkjs answers (it loads as its
// own chunk) and if it ever fails, the boxes stand where layoutFlow puts them, so the diagram
// always draws. It is read-only: pan and zoom with the controls, nothing is edited here (WO-3.7).
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { useEffect, useMemo, useState } from "react";
import { plain } from "../state/format";
import { GAP_X, GAP_Y, NODE_H, NODE_W, layoutFlow } from "./flowLayout";
import type { FlowCard } from "./types.gen";

type Box = { id: string; x: number; y: number };
type FlowNodeData = { label: string; sub: string; tier: string | null };

export function edgeLabel(e: FlowCard["edges"][number]): string {
  return `${e.label}${e.rate !== null ? ` ${plain(e.rate)}${e.unit ? ` ${e.unit}` : ""}` : ""}`;
}

export function nodeSub(n: FlowCard["nodes"][number]): string {
  return [n.count !== null ? `${n.count}x` : null, n.machine, n.tier].filter(Boolean).join(" · ");
}

function MachineNode({ data }: NodeProps<Node<FlowNodeData>>) {
  return (
    <div className={`flow-node tier-${(data.tier ?? "none").toLowerCase()}`} style={{ width: NODE_W, height: NODE_H }}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <span className="tier-band" aria-hidden="true" />
      <span className="flow-node-label">{data.label}</span>
      <span className="flow-node-sub">{data.sub}</span>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}

const NODE_TYPES = { machine: MachineNode };

/** Positions from elkjs's layered layout (left to right); null when it can't lay the card out. */
export async function elkLayout(card: FlowCard): Promise<Box[] | null> {
  try {
    const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
    const ids = new Set(card.nodes.map((n) => n.id));
    const res = await new ELK().layout({
      id: "root",
      layoutOptions: {
        "elk.algorithm": "layered",
        "elk.direction": "RIGHT",
        "elk.layered.spacing.nodeNodeBetweenLayers": String(GAP_X),
        "elk.spacing.nodeNode": String(GAP_Y),
        "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      },
      children: card.nodes.map((n) => ({ id: n.id, width: NODE_W, height: NODE_H })),
      edges: card.edges
        .filter((e) => ids.has(e.from) && ids.has(e.to) && e.from !== e.to)
        .map((e, i) => ({ id: `e${i}`, sources: [e.from], targets: [e.to] })),
    });
    return (res.children ?? []).map((c) => ({ id: c.id, x: c.x ?? 0, y: c.y ?? 0 }));
  } catch {
    return null;
  }
}

export function FlowDiagram({ card }: { card: FlowCard }) {
  const fallback = useMemo(() => {
    const { boxes } = layoutFlow(card.nodes, card.edges);
    return [...boxes.values()].map((b) => ({ id: b.id, x: b.x, y: b.y }));
  }, [card]);
  const [placed, setPlaced] = useState<Box[]>(fallback);
  useEffect(() => {
    let live = true;
    setPlaced(fallback);
    void elkLayout(card).then((boxes) => {
      if (live && boxes) setPlaced(boxes);
    });
    return () => {
      live = false;
    };
  }, [card, fallback]);
  const at = new Map(placed.map((b) => [b.id, b]));
  const nodes: Node<FlowNodeData>[] = card.nodes.map((n) => ({
    id: n.id,
    type: "machine",
    position: { x: at.get(n.id)?.x ?? 0, y: at.get(n.id)?.y ?? 0 },
    data: { label: n.label, sub: nodeSub(n), tier: n.tier },
    draggable: false,
    connectable: false,
  }));
  const known = new Set(card.nodes.map((n) => n.id));
  const edges: Edge[] = card.edges
    .filter((e) => known.has(e.from) && known.has(e.to))
    .map((e, i) => ({
      id: `e${i}`,
      source: e.from,
      target: e.to,
      label: edgeLabel(e),
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed },
      className: "flow-edge",
    }));
  const height = Math.max(240, Math.min(640, Math.max(0, ...placed.map((b) => b.y)) + NODE_H + 64));
  return (
    <div className="flow-canvas" style={{ height }} role="img" aria-label={card.title}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.1 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
        minZoom={0.1}
      >
        <Background gap={24} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
