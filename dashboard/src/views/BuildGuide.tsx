// The 3D build guide (WO-10.8, plan 7.10 "Viewer features", 7.14, D35, D319, D320): one
// multiblock's footprint to build from. Turn it (drag or the buttons) and zoom (wheel or buttons);
// hover or tap a block for its name and registry name (never a numeric id); the layer slider shows
// the structure up to a layer, with that layer's materials beside it; hatch slots are marked with
// the hatch kinds they take; the BOM per structure; a build checklist the core keeps.
// Everything shown comes from the core (gregtec.build.guide_data): positions from the footprint,
// names and colours from the block catalog. GOS never pastes a machine: Clayton builds it.
// WO-10.10: the drawing is `Scene`, which the shell workbench uses too (a shell's blocks with
// the floor plan's machines and keep-outs as ghosts, D370).
// Built to the design system: needs Clayton's board approval (D92, D158 (5)).
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { BuildGuide as GuideData, Facing, GuideQuery } from "../api/types";
import { Button, CitationChip, Panel } from "../components/core";
import { GLYPH } from "../state/states";
import {
  DEFAULT_VIEW,
  type GhostBox,
  type GuideCell,
  type Vec,
  type ViewAngle,
  boxEdges,
  compass,
  fallbackColour,
  frontArrow,
  hatchWords,
  normalIndex,
  project,
  zoomed,
} from "./guideMesh";

const FACINGS: Facing[] = ["north", "east", "south", "west"];
const message = (e: unknown): string => (e instanceof ApiError ? e.message : String(e));

/** What hovering or tapping cell ``i`` says: name, registry name, place and hatch kinds. */
export function cellLabel(g: GuideData, i: number): string {
  const [x, y, z, b, s] = g.cells[i];
  const [cx, , cz] = g.controller_at;
  const block = b >= 0 ? g.palette[b] : null;
  const what = block ? `${block.name ?? "unnamed block"} · ${block.block}` : "hatch only (air until a hatch goes in)";
  const rel = (n: number): string => (n > 0 ? `+${n}` : String(n));
  const at = `layer ${y + 1} · ${rel(x - cx)} east, ${rel(z - cz)} south of the controller`;
  const hatches = s >= 0 ? ` · takes ${g.slots[s].kinds.map(hatchWords).join(", ")}` : "";
  return `${what} · ${at}${hatches}`;
}

function blockName(g: GuideData, key: string): string {
  return g.palette.find((p) => p.block === key)?.name ?? key;
}

export interface SceneProps {
  cells: GuideCell[];
  size: Vec;
  upTo: number;
  palette: { block: string; top: string | null; side: string | null }[];
  label: string; // the drawing's accessible name
  describe: (cell: number) => string; // what hovering or tapping a cell says
  hint: string; // what the hover line says before a cell is hovered
  compassLabel: string;
  prefix?: string; // test ids: `${prefix}-scene`, `${prefix}-turn-left`, ... ("guide" by default)
  controllerAt?: Vec | null; // the guide's controller, its front face marked
  front?: number[];
  ghosts?: GhostBox[]; // boxes drawn as outlines over the blocks (the shell's machines, keep-outs)
}

/** The 3D drawing (D319): turn and tilt (drag, buttons), zoom (wheel, buttons), a compass that
 * turns with it, hover or tap a block for what it is, cells shown up to layer ``upTo``. */
export function Scene(props: SceneProps) {
  const { cells, size, upTo, palette } = props;
  const prefix = props.prefix ?? "guide";
  const [view, setView] = useState<ViewAngle>(DEFAULT_VIEW);
  const [zoom, setZoom] = useState(1);
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; y: number; moved: number } | null>(null);
  const drawing = useMemo(() => project(cells, size, upTo, view), [cells, size, upTo, view]);
  const box = zoomed(drawing.box, zoom);
  // The controller and the way its front looks (review M1): its front face is marked, an arrow
  // points out of it, and the compass turns with the drawing, so a turned view can't mirror it.
  const at = props.controllerAt ?? null;
  const controller = at ? cells.findIndex(([x, y, z]) => x === at[0] && y === at[1] && z === at[2]) : -1;
  const frontDir = props.front ? normalIndex(props.front) : -1;
  const arrow =
    at && props.front && controller >= 0 && frontDir >= 0 && at[1] <= upTo
      ? frontArrow(size, at, props.front as Vec, view)
      : null;
  // The faces are built once per drawing (a big shell has thousands): a hover, a zoom or a
  // parent's refresh must not rebuild them (D388: the bake-off panel lagged the whole PC).
  const faces = useMemo(
    () =>
      drawing.polys.map((p) => {
        const [, y, , b, s] = cells[p.cell];
        const block = b >= 0 ? palette[b] : null;
        const colour = block
          ? ((p.shade === "top" ? block.top : block.side) ?? block.top ?? block.side ?? fallbackColour(block.block))
          : undefined;
        const cls = [
          "guide-face",
          p.shade,
          block ? "" : "hatch-only",
          s >= 0 ? "hatch-slot" : "",
          y === upTo ? "current" : "below",
          p.cell === controller ? "controller" : "",
          p.cell === controller && p.dir === frontDir ? "controller-front" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return <polygon key={`${p.cell}-${p.dir}`} className={cls} points={p.points} fill={colour} data-cell={p.cell} />;
      }),
    [drawing, cells, palette, upTo, controller, frontDir],
  );
  // The hovered block's faces are marked in place, not by building every face again.
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    el.querySelectorAll(".guide-face.hovered").forEach((f) => f.classList.remove("hovered"));
    if (hover !== null) el.querySelectorAll(`.guide-face[data-cell="${hover}"]`).forEach((f) => f.classList.add("hovered"));
  }, [hover, faces]);
  // A drag turns the view at most once a frame.
  const pending = useRef<{ dx: number; dy: number; frame: number } | null>(null);
  const turnBy = (dx: number, dy: number): void => {
    const p = pending.current ?? { dx: 0, dy: 0, frame: 0 };
    p.dx += dx;
    p.dy += dy;
    if (!pending.current) {
      p.frame = window.requestAnimationFrame(() => {
        const got = pending.current;
        pending.current = null;
        if (!got) return;
        setView((v) => ({
          yaw: (v.yaw - got.dx * 0.5 + 360) % 360,
          pitch: Math.max(5, Math.min(85, v.pitch + got.dy * 0.3)),
        }));
      });
    }
    pending.current = p;
  };
  useEffect(() => () => {
    if (pending.current) window.cancelAnimationFrame(pending.current.frame);
  }, []);

  useEffect(() => {
    // A wheel listener React would add is passive, so it couldn't keep the page from scrolling.
    const el = svg.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((z) => Math.max(0.25, Math.min(8, z * (e.deltaY < 0 ? 1.15 : 1 / 1.15))));
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, []);

  const cellOf = (target: EventTarget | null): number | null => {
    const got = (target as Element | null)?.getAttribute?.("data-cell");
    return got === null || got === undefined ? null : Number(got);
  };
  const turn = (by: number) => setView((v) => ({ ...v, yaw: (v.yaw + by + 360) % 360 }));

  return (
    <div className="guide-viewer">
      <svg
        ref={svg}
        className="guide-scene"
        viewBox={box.map((n) => n.toFixed(3)).join(" ")}
        role="img"
        aria-label={props.label}
        data-testid={`${prefix}-scene`}
        data-faces={drawing.polys.length}
        data-yaw={Math.round(view.yaw)}
        data-zoom={zoom.toFixed(2)}
        data-ghosts={props.ghosts?.length ?? 0}
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY, moved: 0 };
          (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (d) {
            const dx = e.clientX - d.x;
            const dy = e.clientY - d.y;
            d.moved += Math.abs(dx) + Math.abs(dy);
            d.x = e.clientX;
            d.y = e.clientY;
            if (d.moved > 4) turnBy(dx, dy);
            return;
          }
          setHover(cellOf(e.target));
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (d && d.moved <= 4) {
            // A tap (the phone has no hover): the block under the finger. Pointer capture sends
            // the up to the drawing, so look the target up at the point.
            const under = document.elementFromPoint?.(e.clientX, e.clientY) ?? e.target;
            setHover(cellOf(under));
          }
        }}
        onPointerLeave={() => {
          if (!drag.current) setHover(null);
        }}
      >
        {faces}
        {(props.ghosts ?? []).map((g) => (
          <path
            key={`${g.kind}-${g.id}`}
            className={`guide-ghost ${g.kind}`}
            d={boxEdges(size, g, view)}
            data-ghost={g.kind}
          >
            <title>{g.label}</title>
          </path>
        ))}
        {arrow && <path className="guide-front-arrow" d={arrow} data-testid={`${prefix}-front-arrow`} />}
      </svg>
      <svg
        className="guide-compass"
        viewBox="-1.6 -1.6 3.2 3.2"
        role="img"
        aria-label={props.compassLabel}
        data-testid={`${prefix}-compass`}
      >
        <circle r={1.05} />
        {compass(view).map((c) => (
          <g key={c.letter}>
            {c.letter === "N" && <line x1={0} y1={0} x2={c.x * 0.7} y2={c.y * 0.7} />}
            <text x={(c.x * 1.2).toFixed(3)} y={(c.y * 1.2).toFixed(3)} className={c.letter === "N" ? "north" : undefined} data-letter={c.letter}>
              {c.letter}
            </text>
          </g>
        ))}
      </svg>
      <p className="gos-sub guide-hover" data-testid={`${prefix}-hover`} aria-live="polite">
        {hover === null ? props.hint : props.describe(hover)}
      </p>
      <div className="guide-controls">
        <Button testId={`${prefix}-turn-left`} title="Turn left" onClick={() => turn(-45)}>
          ⟲
        </Button>
        <Button testId={`${prefix}-turn-right`} title="Turn right" onClick={() => turn(45)}>
          ⟳
        </Button>
        <Button testId={`${prefix}-zoom-in`} title="Zoom in" onClick={() => setZoom((z) => Math.min(8, z * 1.25))}>
          +
        </Button>
        <Button testId={`${prefix}-zoom-out`} title="Zoom out" onClick={() => setZoom((z) => Math.max(0.25, z / 1.25))}>
          −
        </Button>
        <Button
          testId={`${prefix}-view-reset`}
          title="Back to the first view"
          onClick={() => {
            setView(DEFAULT_VIEW);
            setZoom(1);
          }}
        >
          Reset view
        </Button>
      </div>
    </div>
  );
}

function Viewer(props: { g: GuideData; upTo: number }) {
  const { g, upTo } = props;
  return (
    <Scene
      cells={g.cells}
      size={g.size as Vec}
      upTo={upTo}
      palette={g.palette}
      label={`${g.name ?? g.controller}, layers 1 to ${upTo + 1} of ${g.size[1]}`}
      describe={(i) => cellLabel(g, i)}
      hint="Hover or tap a block for its name and registry name. The arrow shows which way the controller's front faces."
      compassLabel={`Compass: the controller's front faces ${g.facing}`}
      controllerAt={g.controller_at as Vec}
      front={g.front}
    />
  );
}

export function GuideView(props: { query: GuideQuery; onQuery: (q: GuideQuery) => void }) {
  const { query } = props;
  const [g, setG] = useState<GuideData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [upTo, setUpTo] = useState(0);
  const [done, setDone] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const tiers = JSON.stringify(query.tiers ?? {});

  useEffect(() => {
    let live = true;
    setError(null);
    api.buildGuide(query).then(
      (got) => {
        if (!live) return;
        setG(got);
        setDone(got.done);
        setUpTo((u) => Math.min(Math.max(u, 0), got.size[1] - 1));
      },
      (e: unknown) => live && setError(message(e)),
    );
    return () => {
      live = false;
    };
    // The query's parts, not its identity: a new object with the same values doesn't reload.
  }, [query.controller, query.variant, query.facing, tiers]);

  const tick = (item: string, on: boolean) => {
    setBusy(true);
    setCheckError(null);
    api.guideCheck(query, item, on).then(
      (r) => {
        setDone(r.done);
        setBusy(false);
      },
      (e: unknown) => {
        setCheckError(message(e));
        setBusy(false);
      },
    );
  };

  if (error) return <p className="empty">{GLYPH.warn} {error}</p>;
  if (!g) return <p className="empty">{GLYPH.progress} Loading the footprint</p>;
  const layer = g.layers.find((l) => l.y === upTo);
  const top = g.size[1] - 1;
  return (
    <div className="guide-grid" data-testid="guide-view" data-key={g.key}>
      <Panel
        label="3D build guide"
        className="guide-main"
        testId="guide-main"
        aside={g.ref ? <CitationChip cite={g.ref} /> : undefined}
      >
        <h2 className="gos-title">{g.name ?? g.controller}</h2>
        <p className="gos-src guide-meta">
          {g.controller} · {g.size[0]} × {g.size[2]} × {g.size[1]} high · front faces {g.facing} ·{" "}
          {g.source === "lab-capture" ? "Lab capture" : "extractor"}
          {g.catalog ? "" : " · no block catalog yet: registry names only (gos db build after a client export)"}
        </p>
        <div className="guide-pickers">
          {g.variants.length > 1 && (
            <label className="gos-src">
              Shape{" "}
              <select
                className="ask-input"
                value={g.variant}
                data-testid="guide-variant"
                onChange={(e) => props.onQuery({ ...query, variant: e.target.value })}
              >
                {g.variants.map((v) => (
                  <option key={v.variant} value={v.variant}>
                    {v.variant} ({v.size.join("×")})
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="gos-src">
            Front faces{" "}
            <select
              className="ask-input"
              value={g.facing}
              data-testid="guide-facing"
              onChange={(e) => props.onQuery({ ...query, facing: e.target.value as Facing })}
            >
              {FACINGS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          {Object.entries(g.choices).map(([ch, options]) => (
            <label key={ch} className="gos-src">
              {ch}{" "}
              <select
                className="ask-input"
                value={g.tiers[ch] ?? ""}
                data-testid={`guide-tier-${ch}`}
                onChange={(e) => props.onQuery({ ...query, tiers: { ...(query.tiers ?? {}), [ch]: Number(e.target.value) } })}
              >
                {options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.value} · {o.name ?? blockName(g, o.block)}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
        <Viewer g={g} upTo={upTo} />
        <label className="guide-layer">
          <span className="gos-label">
            Layer {upTo + 1} of {top + 1}
          </span>
          <input
            type="range"
            min={0}
            max={top}
            step={1}
            value={upTo}
            onChange={(e) => setUpTo(Number(e.target.value))}
            aria-label="Layer"
            data-testid="guide-layer"
          />
        </label>
        {g.caveats.map((c) => (
          <p key={c} className="gos-sub">
            {GLYPH.warn} {c}
          </p>
        ))}
      </Panel>
      <div className="guide-side">
        <Panel label={`Layer ${upTo + 1}: materials`} testId="guide-layer-materials">
          {layer && Object.keys(layer.materials).length ? (
            <table className="bom">
              <tbody>
                {Object.entries(layer.materials).map(([k, n]) => (
                  <tr key={k} data-block={k} data-count={n}>
                    <td>{g.palette.find((p) => p.block === k)?.name ?? "—"}</td>
                    <td className="gos-src">{k}</td>
                    <td className="num">{n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty">{GLYPH.todo} No blocks in this layer</p>
          )}
          {layer && layer.hatch_slots > 0 && (
            <p className="gos-src">
              {layer.hatch_slots} hatch {layer.hatch_slots === 1 ? "slot" : "slots"} in this layer (outlined)
              {layer.hatch_only > 0 && `, ${layer.hatch_only} of them hatch-only (dashed, air until a hatch goes in)`}
            </p>
          )}
        </Panel>
        <Panel
          label="Checklist"
          testId="guide-checklist"
          aside={
            <span className="gos-src">
              {done.length} of {g.checklist.length}
            </span>
          }
        >
          <ul className="guide-checks">
            {g.checklist.map((i) => (
              <li key={i.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={done.includes(i.id)}
                    disabled={busy}
                    onChange={(e) => tick(i.id, e.target.checked)}
                    data-item={i.id}
                  />{" "}
                  {i.label}
                </label>
              </li>
            ))}
          </ul>
          {checkError && <p className="empty">{GLYPH.warn} {checkError}</p>}
          {done.length > 0 && (
            <Button
              testId="guide-check-reset"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                api.guideReset(query).then(
                  (r) => {
                    setDone(r.done);
                    setBusy(false);
                  },
                  (e: unknown) => {
                    setCheckError(message(e));
                    setBusy(false);
                  },
                );
              }}
            >
              Start the next one
            </Button>
          )}
        </Panel>
        <Panel label="Hatch slots" testId="guide-slots">
          {g.slots.length ? (
            <ul className="guide-slots">
              {g.slots.map((s) => (
                <li key={s.kinds.join(",")} data-kinds={s.kinds.join(",")}>
                  <span className="swatch guide-slot-swatch" aria-hidden="true" /> {s.cells}{" "}
                  {s.cells === 1 ? "cell takes" : "cells take"} {s.kinds.map(hatchWords).join(", ")}
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty">No hatch slots recorded</p>
          )}
        </Panel>
        <Panel
          label="Bill of materials"
          testId="guide-bom"
          aside={<span className="gos-src">{g.palette.reduce((n, p) => n + p.count, 0)} blocks</span>}
        >
          <table className="bom">
            <tbody>
              {g.palette.map((p) => (
                <tr key={p.block} data-block={p.block} data-count={p.count}>
                  <td>{p.name ?? "—"}</td>
                  <td className="gos-src">{p.block}</td>
                  <td className="num">{p.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="gos-src">The controller is built from its item; GOS never pastes a machine.</p>
        </Panel>
      </div>
    </div>
  );
}
