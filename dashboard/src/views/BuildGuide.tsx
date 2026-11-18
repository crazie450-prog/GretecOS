// The 3D build guide (WO-10.8, plan 7.10 "Viewer features", 7.14, D35, D319, D320): one
// multiblock's footprint to build from. Turn it (drag or the buttons) and zoom (wheel or buttons);
// hover or tap a block for its name and registry name (never a numeric id); the layer slider shows
// the structure up to a layer, with that layer's materials beside it; hatch slots are marked with
// the hatch kinds they take; the BOM per structure; a build checklist the core keeps.
// Everything shown comes from the core (gregtec.build.guide_data): positions from the footprint,
// names and colours from the block catalog. GOS never pastes a machine: Clayton builds it.
// Built to the design system: needs Clayton's board approval (D92, D158 (5)).
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { BuildGuide as GuideData, Facing, GuideQuery } from "../api/types";
import { Button, CitationChip, Panel } from "../components/core";
import { GLYPH } from "../state/states";
import { DEFAULT_VIEW, type ViewAngle, fallbackColour, hatchWords, project, zoomed } from "./guideMesh";

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

function Viewer(props: { g: GuideData; upTo: number }) {
  const { g, upTo } = props;
  const [view, setView] = useState<ViewAngle>(DEFAULT_VIEW);
  const [zoom, setZoom] = useState(1);
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; y: number; moved: number } | null>(null);
  const drawing = useMemo(() => project(g.cells, g.size, upTo, view), [g.cells, g.size, upTo, view]);
  const box = zoomed(drawing.box, zoom);

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
    const at = (target as Element | null)?.getAttribute?.("data-cell");
    return at === null || at === undefined ? null : Number(at);
  };
  const turn = (by: number) => setView((v) => ({ ...v, yaw: (v.yaw + by + 360) % 360 }));

  return (
    <div className="guide-viewer">
      <svg
        ref={svg}
        className="guide-scene"
        viewBox={box.map((n) => n.toFixed(3)).join(" ")}
        role="img"
        aria-label={`${g.name ?? g.controller}, layers 1 to ${upTo + 1} of ${g.size[1]}`}
        data-testid="guide-scene"
        data-faces={drawing.polys.length}
        data-yaw={Math.round(view.yaw)}
        data-zoom={zoom.toFixed(2)}
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
            if (d.moved > 4)
              setView((v) => ({
                yaw: (v.yaw - dx * 0.5 + 360) % 360,
                pitch: Math.max(5, Math.min(85, v.pitch + dy * 0.3)),
              }));
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
        {drawing.polys.map((p) => {
          const [, y, , b, s] = g.cells[p.cell];
          const block = b >= 0 ? g.palette[b] : null;
          const colour = block
            ? ((p.shade === "top" ? block.top : block.side) ?? block.top ?? block.side ?? fallbackColour(block.block))
            : undefined;
          const cls = [
            "guide-face",
            p.shade,
            block ? "" : "hatch-only",
            s >= 0 ? "hatch-slot" : "",
            y === upTo ? "current" : "below",
            hover === p.cell ? "hovered" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return <polygon key={`${p.cell}-${p.dir}`} className={cls} points={p.points} fill={colour} data-cell={p.cell} />;
        })}
      </svg>
      <p className="gos-sub guide-hover" data-testid="guide-hover" aria-live="polite">
        {hover === null ? "Hover or tap a block for its name and registry name." : cellLabel(g, hover)}
      </p>
      <div className="guide-controls">
        <Button testId="guide-turn-left" title="Turn left" onClick={() => turn(-45)}>
          ⟲
        </Button>
        <Button testId="guide-turn-right" title="Turn right" onClick={() => turn(45)}>
          ⟳
        </Button>
        <Button testId="guide-zoom-in" title="Zoom in" onClick={() => setZoom((z) => Math.min(8, z * 1.25))}>
          +
        </Button>
        <Button testId="guide-zoom-out" title="Zoom out" onClick={() => setZoom((z) => Math.max(0.25, z / 1.25))}>
          −
        </Button>
        <Button
          testId="guide-view-reset"
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
                    {o.value} · {blockName(g, o.block) === o.block ? o.block : blockName(g, o.block)}
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
                    <td>{blockName(g, k)}</td>
                    <td className="gos-src">{k}</td>
                    <td className="num">{n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty">{GLYPH.todo} No blocks in this layer</p>
          )}
          {layer && layer.hatch_cells > 0 && (
            <p className="gos-src">
              {layer.hatch_cells} {layer.hatch_cells === 1 ? "cell takes" : "cells take"} a hatch in this layer (marked)
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
                  <td>{p.name ?? p.block}</td>
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
