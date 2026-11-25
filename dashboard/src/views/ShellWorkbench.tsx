// The shell workbench (WO-10.10, plan 7.10 "Shell designer", 7.14, D367-D370): the separate
// window Clayton iterates on a shell in before it goes near a world. Words, theme and the sizes
// the gate supports (max_blocks, headroom); Design and Revise run as shell jobs (stage, time,
// Cancel; one at a time, WO-3.9's machinery) with GOS's own generator or reviser (no model, free);
// "Ask the architect" sends the same words to the architect with the design picked here named, so
// that design is revised in the deep lane (D369, D370); with a live shell designer set in gos.toml
// (D423) it and "Design with the architect" run as shell jobs drawn by that model instead, and the
// status line says its cap and what today leaves. "Plan-free" (D424) designs a building with no
// machines from the roofed floor he asks for; the core sizes its plot (the note is the core's).
// The version list shows what was asked, by whom (the designer's label), the blocks and
// the cost of each, makes any version current (undo) and picks two to compare side by side
// (materials and block counts). The viewer is the build guide's 3D drawing (D319: free turn, zoom,
// layer slider, per-layer materials) with the floor plan's machines and keep-outs as ghosts.
// Everything shown comes from the core; nothing here writes a world: the .schematic is still
// only `gos shell export` and his paste in the Lab (D318).
// Built to the design system, no board yet: needs Clayton's board approval (D92, D158 (5)).
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api/client";
import type {
  SavedPlanRow,
  ShellCompare,
  ShellDesign,
  ShellDesignSummary,
  ShellDesignerStatus,
  ShellJobBody,
  ShellJobResult,
  ShellPlot,
  ShellPlotLimits,
  ShellScene,
  ShellThemes,
  ShellVersion,
} from "../api/types";
import type { ShellCard } from "../cards/types.gen";
import { JobProgress, useDesignJob } from "../components/JobProgress";
import { Button, CitationChip, Panel } from "../components/core";
import { GLYPH } from "../state/states";
import { Scene } from "./BuildGuide";
import type { GhostBox, Vec } from "./guideMesh";

const message = (e: unknown): string => (e instanceof ApiError ? e.message : String(e));
const REFRESH_MS = 10_000; // the architect's versions arrive from the deep lane: look again

/** A version's cost as the list says it: GOS's own is free; the architect's is its task's. */
export function costText(v: Pick<ShellVersion, "cost_usd" | "by">): string {
  if (v.cost_usd === 0) return "free";
  if (v.cost_usd === null) return "AI (see Costs)";
  return `$${v.cost_usd.toFixed(2)}`;
}

/** Who drew a version: the live shell designer by its label (D423), else the architect or GOS. */
export function byText(v: Pick<ShellVersion, "by" | "designer">): string {
  if (v.designer) return `AI (${v.designer})`;
  return v.by === "architect" ? "the architect" : "GOS's own";
}

const count = (n: number): string => n.toLocaleString("en-US");

/** What a design houses, as its list says it: its plans, or a plan-free building's floor (D424). */
export function housesText(d: { plans: string[]; area?: number | null }): string {
  return d.area != null ? `plan-free · ≥${count(d.area)} floor` : `plans ${d.plans.join(", ")}`;
}

/** The live shell designer's status line (D423), or null when none is set. */
export function designerText(s: ShellDesignerStatus): string | null {
  if (!s.configured) return null;
  if (!s.ready) return `Designer: ${s.label} · not ready: ${s.why ?? "the core didn't say why"}`;
  // the day cap only warns, as for the other AI work (D429)
  const left =
    s.day_left_usd === null
      ? ""
      : s.day_left_usd > 0
        ? ` · $${s.day_left_usd.toFixed(2)} left today`
        : " · past today's day cap (a warning only)";
  const big = s.big_cap_usd > s.cap_usd ? ` ($${s.big_cap_usd.toFixed(2)} a large one)` : "";
  return `Designer: ${s.label} · up to $${s.cap_usd.toFixed(2)} a request${big}${left}`;
}

/** A plan-free building's plot as the core sized it (the dashboard never computes it). */
export function plotText(p: ShellPlot): string {
  const factor = Number.isInteger(p.factor) ? p.factor.toFixed(1) : String(p.factor);
  return `plot about ${p.size[0]} × ${p.size[2]} at factor ${factor}`;
}

/** The ghosts a scene draws: the floor plan's machines and keep-outs, in the shell's frame. */
export function ghostsOf(scene: ShellScene): GhostBox[] {
  return [
    ...scene.ghosts.keepouts.map((k) => ({ id: k.id, kind: "keepout" as const, label: `keep-out ${k.id}`, lo: k.lo, hi: k.hi })),
    ...scene.ghosts.machines.map((m) => ({
      id: m.id,
      kind: "machine" as const,
      label: `${m.name ?? m.id}${m.ref ? ` (${m.ref})` : ""}`,
      lo: m.lo,
      hi: m.hi,
    })),
  ];
}

/** The workbench form as a job reads it. */
export interface ShellForm {
  words: string;
  theme: string;
  plans: string[];
  lineType: string;
  maxBlocks: string;
  headroom: string;
  free?: boolean; // a plan-free building (D424): its floor area instead of plans
  area?: string;
  siteFactor?: string; // the plot size factor (site or plan-free)
}

/** The body of a workbench job from the form (a string: nothing to send, and why). `ai`: the
 * live shell designer draws it (D423); `plot`: the core's limits for the floor and the factor. */
export function jobBody(
  kind: "design" | "revise",
  form: ShellForm,
  design: string | null,
  ai = false,
  plot?: ShellPlotLimits,
): ShellJobBody | string {
  const num = (s: string): number | undefined => (s.trim() === "" ? undefined : Number(s));
  const maxBlocks = num(form.maxBlocks);
  const headroom = num(form.headroom);
  for (const [name, v] of [
    ["max blocks", maxBlocks],
    ["headroom", headroom],
  ] as const)
    if (v !== undefined && !Number.isInteger(v)) return `${name} is a whole number`;
  const sizes = {
    ...(maxBlocks !== undefined ? { max_blocks: maxBlocks } : {}),
    ...(headroom !== undefined ? { headroom } : {}),
  };
  const words = form.words.trim();
  if (kind === "design") {
    const factor = num(form.siteFactor ?? "");
    if (factor !== undefined) {
      const [, lo, hi] = plot?.site_factor ?? [0, 0, Infinity];
      if (!Number.isFinite(factor) || factor < lo || factor > hi)
        return plot ? `The plot size factor is from ${lo} to ${hi}` : "The plot size factor is a number";
    }
    const common = {
      ...(form.theme ? { theme: form.theme } : {}),
      line_type: form.lineType,
      ...(words ? { prompt: words } : {}),
      ...sizes,
      ...(ai ? { ai: true } : {}),
    };
    if (form.free) {
      const area = num(form.area ?? "");
      const [lo, hi] = plot?.area ?? [1, Infinity];
      if (area === undefined || !Number.isInteger(area) || area < lo || area > hi)
        return plot ? `Give the floor area (${count(lo)} to ${count(hi)})` : "Give the floor area (whole blocks)";
      return { design: { mode: "free", area, ...(factor !== undefined ? { site_factor: factor } : {}), ...common } };
    }
    if (!form.plans.length) return "Pick the plan the shell houses";
    // a plot size factor sizes a site design's plot (D422): the design is one then
    return { design: { plans: form.plans, ...(factor !== undefined ? { mode: "site", site_factor: factor } : {}), ...common } };
  }
  if (!design) return "Design a shell first";
  if (ai) {
    if (!words) return "Say what to change first";
    if (maxBlocks !== undefined || headroom !== undefined || form.theme)
      return "The architect revises from your words alone: clear the theme and sizes, or use Revise";
    return { revise: { design, words, ai: true } };
  }
  if (!words && maxBlocks === undefined && headroom === undefined && !form.theme)
    return "Say what to change, or set a size or another theme";
  return { revise: { design, words, ...(form.theme ? { theme: form.theme } : {}), ...sizes } };
}

/** Where a scene comes from when it is not one of his own builds (the bake-off's blind entries, WO-10.11). */
export interface SceneSource {
  key: string; // changes when another scene is meant
  load: () => Promise<ShellScene>;
}

function useScene(build: string | null, source?: SceneSource): { scene: ShellScene | null; error: string | null } {
  const [scene, setScene] = useState<ShellScene | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = source?.key ?? build;
  useEffect(() => {
    if (!key) return;
    let live = true;
    setScene(null);
    setError(null);
    (source ? source.load() : api.shellScene(key)).then(
      (s) => live && setScene(s),
      (e: unknown) => live && setError(message(e)),
    );
    return () => {
      live = false;
    };
    // the source is named by its key: a new object with the same key is the same scene
  }, [key]);
  return { scene, error };
}

function sceneProps(scene: ShellScene, upTo: number, ghosts: boolean, prefix: string) {
  return {
    cells: scene.cells,
    size: scene.size as Vec,
    upTo,
    palette: scene.palette,
    label: `Shell ${scene.build}, layers 1 to ${upTo + 1} of ${scene.size[1]}`,
    describe: (i: number) => {
      const [, y, , b] = scene.cells[i];
      const p = scene.palette[b];
      return `${p?.name ?? "unnamed block"} · ${p?.block ?? "?"} · ${p?.role ?? ""} · layer ${y + 1}`;
    },
    hint: "Drag to turn, wheel or the buttons to zoom; hover or tap a block for its name and registry name.",
    compassLabel: "Compass: the layout's north is the world's north",
    prefix,
    ghosts: ghosts ? ghostsOf(scene) : [],
  };
}

/** One version in the 3D viewer, its layer slider and that layer's materials. */
type Scene3DProps = { build: string; prefix?: string; compact?: boolean; source?: SceneSource };

/** One version in the 3D viewer (memoised: a parent that refreshes every few seconds, like the
 * bake-off panel, doesn't redraw a scene whose build and source key are the same, D388). */
export const ShellScene3D = memo(
  ShellScene3DInner,
  (a: Scene3DProps, b: Scene3DProps) =>
    a.build === b.build && a.prefix === b.prefix && a.compact === b.compact && a.source?.key === b.source?.key,
);

function ShellScene3DInner(props: Scene3DProps) {
  const prefix = props.prefix ?? "shell3d";
  const { scene, error } = useScene(props.build, props.source);
  const [upTo, setUpTo] = useState<number | null>(null);
  const [ghosts, setGhosts] = useState(true);
  if (error) return <p className="empty">{GLYPH.warn} {error}</p>;
  if (!scene) return <p className="empty">{GLYPH.progress} Loading the shell</p>;
  const top = scene.size[1] - 1;
  const at = Math.min(upTo ?? top, top);
  const layer = scene.layers.find((l) => l.y === at);
  const name = (k: string) => scene.palette.find((p) => p.block === k)?.name ?? "—";
  return (
    <div className="shell-scene" data-testid={`${prefix}-view`} data-build={scene.build}>
      <Scene {...sceneProps(scene, at, ghosts, prefix)} />
      <label className="guide-layer">
        <span className="gos-label">
          Layer {at + 1} of {top + 1}
        </span>
        <input
          type="range"
          min={0}
          max={top}
          step={1}
          value={at}
          onChange={(e) => setUpTo(Number(e.target.value))}
          aria-label="Layer"
          data-testid={`${prefix}-layer`}
        />
      </label>
      {scene.area != null ? (
        <p className="gos-src" data-testid={`${prefix}-plan-free`}>
          plan-free: no machines · at least {count(scene.area)} blocks of roofed floor for you to fill
        </p>
      ) : (
        <label className="gos-src shell-ghost-toggle">
          <input type="checkbox" checked={ghosts} onChange={(e) => setGhosts(e.target.checked)} data-testid={`${prefix}-ghosts`} />{" "}
          Show the machines and keep-outs inside ({scene.ghosts.machines.length} machines, {scene.ghosts.keepouts.length} keep-outs)
        </label>
      )}
      {!props.compact && (
        <div data-testid={`${prefix}-layer-materials`}>
          <span className="gos-label">Layer {at + 1}: materials</span>
          {layer && Object.keys(layer.materials).length ? (
            <table className="bom">
              <tbody>
                {Object.entries(layer.materials).map(([k, n]) => (
                  <tr key={k} data-block={k} data-count={n}>
                    <td>{name(k)}</td>
                    <td className="gos-src">{k}</td>
                    <td className="num">{n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty">{GLYPH.todo} No blocks in this layer</p>
          )}
        </div>
      )}
      {!props.compact && (
        <details className="shell-manifest" data-testid={`${prefix}-manifest`}>
          <summary className="gos-label">
            The whole shell: {scene.palette.reduce((n, p) => n + p.count, 0)} blocks, {scene.palette.length} kinds
          </summary>
          <table className="bom">
            <tbody>
              {scene.palette.map((p) => (
                <tr key={p.block} data-block={p.block} data-count={p.count}>
                  <td>{p.role}</td>
                  <td>{p.name ?? "—"}</td>
                  <td className="gos-src">{p.block}</td>
                  <td className="num">{p.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {scene.caveats.map((c) => (
        <p key={c} className="gos-sub">
          {GLYPH.warn} {c}
        </p>
      ))}
    </div>
  );
}

function VersionRow(props: {
  v: ShellVersion;
  current: boolean;
  shown: boolean;
  picked: boolean;
  busy: boolean;
  onShow: () => void;
  onCurrent: () => void;
  onPick: (on: boolean) => void;
}) {
  const { v } = props;
  return (
    <tr data-version={v.n} data-build={v.build} className={props.current ? "current" : undefined}>
      <td>
        <button type="button" className="gos-chip" onClick={props.onShow} data-testid={`shell-show-${v.n}`} aria-pressed={props.shown}>
          v{v.n}
        </button>
        {props.current && (
          <span className="gos-src" data-testid="shell-current-mark">
            {" "}
            {GLYPH.ok} current
          </span>
        )}
      </td>
      <td className="shell-asked">{v.asked}</td>
      <td className="gos-src">{byText(v)}</td>
      <td className="num" data-testid={`shell-blocks-${v.n}`}>
        {v.blocks}
      </td>
      <td className="gos-src" data-testid={`shell-cost-${v.n}`}>
        {costText(v)}
      </td>
      <td className="gos-src">{v.status === "repaired" ? `${GLYPH.warn} repaired` : "accepted"}</td>
      <td>
        <label className="gos-src">
          <input type="checkbox" checked={props.picked} onChange={(e) => props.onPick(e.target.checked)} data-testid={`shell-compare-${v.n}`} /> compare
        </label>
      </td>
      <td>
        {!props.current && (
          <Button testId={`shell-make-current-${v.n}`} disabled={props.busy} onClick={props.onCurrent} title="Make this version current again; nothing is deleted">
            Make current
          </Button>
        )}
      </td>
    </tr>
  );
}

function Compare(props: { a: string; b: string; versions: ShellVersion[] }) {
  const [got, setGot] = useState<ShellCompare | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setGot(null);
    api.shellCompare(props.a, props.b).then(
      (c) => live && setGot(c),
      (e: unknown) => live && setError(message(e)),
    );
    return () => {
      live = false;
    };
  }, [props.a, props.b]);
  const n = (b: string) => props.versions.find((v) => v.build === b)?.n;
  return (
    <Panel label={`Compare v${n(props.a)} and v${n(props.b)}`} testId="shell-compare" className="shell-compare">
      <div className="shell-compare-grid">
        <div>
          <span className="gos-label">v{n(props.a)}</span>
          <ShellScene3D build={props.a} prefix="shell3d-a" compact />
        </div>
        <div>
          <span className="gos-label">v{n(props.b)}</span>
          <ShellScene3D build={props.b} prefix="shell3d-b" compact />
        </div>
      </div>
      {error && <p className="empty">{GLYPH.warn} {error}</p>}
      {got && (
        <>
          <p className="gos-sub" data-testid="shell-compare-blocks" data-delta={got.blocks_delta}>
            {got.a.blocks} blocks against {got.b.blocks} ({got.blocks_delta >= 0 ? "+" : ""}
            {got.blocks_delta}) · {got.a.size.join("×")} against {got.b.size.join("×")} · headroom {got.a.headroom} against {got.b.headroom}
          </p>
          <table className="bom" data-testid="shell-compare-materials">
            <thead>
              <tr>
                <th>Block</th>
                <th className="gos-src">registry@meta</th>
                <th className="num">v{n(props.a)}</th>
                <th className="num">v{n(props.b)}</th>
                <th className="num">change</th>
              </tr>
            </thead>
            <tbody>
              {got.materials.map((m) => (
                <tr key={m.block} data-block={m.block} data-delta={m.delta}>
                  <td>{m.name ?? "—"}</td>
                  <td className="gos-src">{m.block}</td>
                  <td className="num">{m.a}</td>
                  <td className="num">{m.b}</td>
                  <td className="num">{m.delta > 0 ? `+${m.delta}` : m.delta}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {got.ops.length > 0 && (
            <ul className="notes" data-testid="shell-compare-ops">
              {got.ops.map((o) => (
                <li key={o}>{o}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}

export function ShellWorkbench() {
  const [designs, setDesigns] = useState<ShellDesignSummary[] | null>(null);
  const [ds, setDs] = useState<string | null>(null);
  const [design, setDesign] = useState<ShellDesign | null>(null);
  const [themes, setThemes] = useState<ShellThemes | null>(null);
  const [plans, setPlans] = useState<SavedPlanRow[]>([]);
  const [words, setWords] = useState("");
  const [theme, setTheme] = useState("");
  const [plan, setPlan] = useState("");
  const [plan2, setPlan2] = useState("");
  const [lineType, setLineType] = useState("general");
  const [maxBlocks, setMaxBlocks] = useState("");
  const [headroom, setHeadroom] = useState("");
  const [free, setFree] = useState(false);
  const [area, setArea] = useState("");
  const [siteFactor, setSiteFactor] = useState("");
  const [plot, setPlot] = useState<{ text: string; ok: boolean } | null>(null);
  const [designer, setDesigner] = useState<ShellDesignerStatus | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [card, setCard] = useState<ShellCard | null>(null);

  const load = useCallback((pick?: string | null) => {
    api.shellDesigns().then(
      (r) => {
        setDesigns(r.designs);
        setDs((prev) => pick ?? (prev && r.designs.some((d) => d.id === prev) ? prev : r.current));
      },
      (e: unknown) => setNotice(message(e)),
    );
    // the live shell designer (D423): an older core without the route has none
    api.shellDesigner().then(setDesigner, () => setDesigner(null));
  }, []);

  useEffect(() => {
    load();
    api.shellThemes().then(setThemes, () => undefined);
    api.plans().then(
      (r) => {
        setPlans(r.plans);
        setPlan((p) => p || (r.plans[0]?.id ?? ""));
      },
      () => undefined,
    );
    const timer = window.setInterval(() => load(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!ds) return;
    let live = true;
    api.shellDesign(ds).then(
      (r) => {
        if (!live) return;
        setDesign((prev) => {
          if (!prev || prev.id !== r.design.id || prev.current !== r.design.current) setShown(r.design.current);
          return r.design;
        });
      },
      (e: unknown) => live && setNotice(message(e)),
    );
    return () => {
      live = false;
    };
  }, [ds, designs]);

  // A plan-free building's plot, as the core sizes it for the floor, factor and headroom (D424).
  useEffect(() => {
    const floor = Number(area);
    if (!free || area.trim() === "" || !Number.isInteger(floor)) {
      setPlot(null);
      return;
    }
    const factor = siteFactor.trim() === "" ? undefined : Number(siteFactor);
    const room = headroom.trim() === "" ? undefined : Number(headroom);
    let live = true;
    const timer = window.setTimeout(() => {
      api.shellPlot(floor, factor, room).then(
        (p) => live && setPlot({ text: plotText(p), ok: true }),
        (e: unknown) => live && setPlot({ text: message(e), ok: false }),
      );
    }, 300);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [free, area, siteFactor, headroom]);

  const view = shown ?? design?.current ?? null;
  useEffect(() => {
    if (!view) return;
    let live = true;
    api.shell(view).then(
      (r) => live && setCard(r.card as ShellCard),
      () => live && setCard(null),
    );
    return () => {
      live = false;
    };
  }, [view]);

  const runner = useDesignJob(
    "shell",
    (result) => {
      const r = result as ShellJobResult;
      const cost = r.cost_usd !== undefined ? `, $${r.cost_usd.toFixed(2)}` : "";
      if (r.refused?.length) {
        const by = r.designer ? ` (asked ${r.designer}${cost})` : "";
        setNotice(`Refused, nothing written; the current version stays${by}: ${r.refused.join(" | ")}`);
      } else {
        const done = r.changes?.length ? `New version: ${r.changes.join("; ")}` : "Designed: its first version is current.";
        const repairs = r.rounds ? `, ${r.rounds} repair${r.rounds === 1 ? "" : "s"}` : "";
        setNotice(r.designer ? `${done} Drawn by ${r.designer}${cost}${repairs}.` : done);
      }
      // what today leaves after the designer's spend (D423)
      if (r.designer) api.shellDesigner().then(setDesigner, () => undefined);
      setPicked([]);
      load(r.design ?? null);
    },
    (text) => setNotice(text),
  );
  const working = busy !== null || runner.job !== null;
  const form = { words, theme, plans: [plan, plan2].filter(Boolean), lineType, maxBlocks, headroom, free, area, siteFactor };
  // The live shell designer (D423): set, and ready now (his AI switch and the credit pause allow it; the day cap only warns, D429).
  const drawer = designer?.configured ? designer : null;
  const ready = drawer?.ready === true;

  const start = (kind: "design" | "revise", ai = false): void => {
    const body = jobBody(kind, form, ds, ai, themes?.plot);
    if (typeof body === "string") {
      setNotice(body);
      return;
    }
    setNotice(null);
    setBusy(ai && drawer ? `Asking ${drawer.label}` : kind === "design" ? "Starting the design" : "Starting the revision");
    runner.start(() => api.shellJob(body)).then(
      () => setBusy(null),
      (e: unknown) => {
        setBusy(null);
        setNotice(message(e));
      },
    );
  };

  const architect = (): void => {
    if (drawer) {
      start("revise", true); // the designer revises the design picked here, as a job
      return;
    }
    const text = words.trim();
    if (!text) {
      setNotice("Say what to change first");
      return;
    }
    if (!ds) return;
    setBusy("Asking the architect");
    // The design picked here is the one revised, whatever Chat worked on last (the router is not asked).
    api.ask(text, ds).then(
      (r) => {
        setBusy(null);
        setNotice(
          r.lane === "deep"
            ? `${r.text} The architect revises ${ds}; its version shows here when it is done.`
            : r.text,
        );
        load();
      },
      (e: unknown) => {
        setBusy(null);
        setNotice(message(e));
      },
    );
  };

  const makeCurrent = (build: string): void => {
    if (!design) return;
    setBusy("Making it current");
    api.shellCurrent(design.id, build).then(
      (r) => {
        setBusy(null);
        setDesign(r.design);
        setShown(r.design.current);
        load(r.design.id);
      },
      (e: unknown) => {
        setBusy(null);
        setNotice(message(e));
      },
    );
  };

  const limits = themes?.limits;
  // The saved plans, and the plans the shells already house (a plan file without a row too).
  const choices = useMemo(() => {
    const out = plans.map((p) => ({ id: p.id, name: p.name }));
    for (const d of designs ?? [])
      for (const id of d.plans) if (!out.some((p) => p.id === id)) out.push({ id, name: "from a shell" });
    return out;
  }, [plans, designs]);
  const versions = useMemo(() => [...(design?.versions ?? [])].reverse(), [design]);
  return (
    <div className="shell-workbench" data-testid="shell-workbench">
      <Panel label="Shell workbench" testId="shell-form" aside={<span className="gos-src">no world is touched here</span>}>
        <p className="gos-sub">
          Words shape revisions and the architect; Design new uses the theme, plans and sizes (GOS's free generator keeps the words as
          the design's request). GOS's own generator and reviser are free and need no AI; Ask the architect (AI) revises the design
          picked below from the same words. Every version is kept. Plan-free is a building with no machines, sized by the roofed floor
          you ask for; you place the machines yourself.
        </p>
        <textarea
          className="ask-input shell-words"
          rows={4}
          maxLength={400}
          value={words}
          onChange={(e) => setWords(e.target.value)}
          placeholder="make the roof steeper; taller, and no windows on the north wall"
          aria-label="What to design or change"
          data-testid="shell-words"
        />
        <div className="shell-form-row shell-mode" role="group" aria-label="What a new shell houses">
          <button type="button" className="gos-chip" aria-pressed={!free} onClick={() => setFree(false)} data-testid="shell-mode-plans">
            On plans
          </button>
          <button type="button" className="gos-chip" aria-pressed={free} onClick={() => setFree(true)} data-testid="shell-mode-free">
            Plan-free
          </button>
        </div>
        <div className="shell-form-row">
          {free ? (
            <label className="gos-src">
              Interior floor (blocks){" "}
              <input
                className="ask-input num"
                type="number"
                inputMode="numeric"
                min={themes?.plot?.area[0]}
                max={themes?.plot?.area[1]}
                placeholder="1500"
                value={area}
                onChange={(e) => setArea(e.target.value)}
                data-testid="shell-area"
              />
              {plot && (
                <span className="gos-src" data-testid="shell-plot" data-ok={plot.ok}>
                  {plot.ok ? "" : `${GLYPH.warn} `}
                  {plot.text}
                </span>
              )}
            </label>
          ) : (
            <>
              <label className="gos-src">
                Plan{" "}
                <select className="ask-input" value={plan} onChange={(e) => setPlan(e.target.value)} data-testid="shell-plan">
                  <option value="">Pick a plan</option>
                  {choices.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.id})
                    </option>
                  ))}
                </select>
              </label>
              <label className="gos-src">
                And its power{" "}
                <select className="ask-input" value={plan2} onChange={(e) => setPlan2(e.target.value)} data-testid="shell-plan-2">
                  <option value="">none</option>
                  {choices
                    .filter((p) => p.id !== plan)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} ({p.id})
                      </option>
                    ))}
                </select>
              </label>
            </>
          )}
          <label className="gos-src">
            Theme{" "}
            <select className="ask-input" value={theme} onChange={(e) => setTheme(e.target.value)} data-testid="shell-theme">
              <option value="">{design ? "keep the version's" : "blank: nearby, else one that fits"}</option>
              {(themes?.themes ?? []).map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.id} v{t.v})
                </option>
              ))}
            </select>
          </label>
          <label className="gos-src">
            Line type{" "}
            <select className="ask-input" value={lineType} onChange={(e) => setLineType(e.target.value)} data-testid="shell-line-type">
              {(themes?.line_types ?? ["general"]).map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="gos-src">
            Max blocks{" "}
            <input
              className="ask-input num"
              type="number"
              inputMode="numeric"
              min={limits?.max_blocks[1]}
              max={limits?.max_blocks[2]}
              placeholder={String(limits?.max_blocks[0] ?? "")}
              value={maxBlocks}
              onChange={(e) => setMaxBlocks(e.target.value)}
              data-testid="shell-max-blocks"
            />
          </label>
          <label className="gos-src">
            Headroom{" "}
            <input
              className="ask-input num"
              type="number"
              inputMode="numeric"
              min={limits?.headroom[1]}
              max={limits?.headroom[2]}
              placeholder={String(limits?.headroom[0] ?? "")}
              value={headroom}
              onChange={(e) => setHeadroom(e.target.value)}
              data-testid="shell-headroom"
            />
          </label>
        </div>
        <details className="shell-advanced" data-testid="shell-advanced">
          <summary className="gos-label">Advanced</summary>
          <div className="shell-form-row">
            <label className="gos-src">
              Plot size factor{" "}
              <input
                className="ask-input num"
                type="number"
                inputMode="decimal"
                step="any"
                min={themes?.plot?.site_factor[1]}
                max={themes?.plot?.site_factor[2]}
                placeholder={String(themes?.plot?.site_factor[0] ?? "")}
                value={siteFactor}
                onChange={(e) => setSiteFactor(e.target.value)}
                data-testid="shell-site-factor"
              />
            </label>
            <span className="gos-src">A bigger plot round the floor or the machines; on plans it makes the new design a site design.</span>
          </div>
        </details>
        <div className="shell-form-row">
          <Button variant="primary" testId="shell-design" disabled={working} onClick={() => start("design")} title="A new design (GOS's generator, free)">
            Design new
          </Button>
          <Button testId="shell-revise" disabled={working || !ds} onClick={() => start("revise")} title="A new version of the current shell (GOS's reviser, free)">
            Revise
          </Button>
          {drawer && (
            <Button
              testId="shell-ai-design"
              disabled={working || !ready}
              onClick={() => start("design", true)}
              title={ready ? `${drawer.label} (AI) draws a new design from the words, theme and plans or floor` : (drawer.why ?? "")}
            >
              Design with the architect
            </Button>
          )}
          <Button
            testId="shell-ask"
            disabled={working || !ds || (drawer !== null && !ready)}
            onClick={architect}
            title={
              drawer
                ? ready
                  ? `${drawer.label} (AI) revises the design picked below from these words`
                  : (drawer.why ?? "")
                : "The architect (AI) revises the design picked below from these words"
            }
          >
            Ask the architect
          </Button>
        </div>
        {drawer && (
          <p className="gos-sub" data-testid="shell-designer" data-ready={ready}>
            {ready ? GLYPH.ok : GLYPH.warn} {designerText(drawer)}
          </p>
        )}
      </Panel>
      {runner.job && <JobProgress job={runner.job} onCancel={runner.cancel} testId="shell-busy" prefix="shell" />}
      {busy && !runner.job && (
        <div className="gos-panel" aria-busy="true" data-testid="shell-busy">
          <p className="empty">
            {GLYPH.progress} {busy}
          </p>
        </div>
      )}
      {notice && (
        <div className="notice gos-panel" role="status" data-testid="shell-notice">
          <span>{notice}</span>
        </div>
      )}
      <Panel
        label="Versions"
        testId="shell-versions"
        aside={
          design ? (
            <span className="gos-src">
              {design.area != null ? `${housesText(design)} · ` : ""}
              {design.versions.length} kept · nothing is ever deleted
            </span>
          ) : undefined
        }
      >
        {designs && !designs.length && (
          <p className="gos-sub" data-testid="shells-empty">
            {GLYPH.todo} No shell yet. Pick a plan and press Design new, or ask in Chat ("design a foundry shell around my steel line").
          </p>
        )}
        {designs && designs.length > 1 && (
          <select
            className="ask-input base-select"
            aria-label="Design"
            value={ds ?? ""}
            onChange={(e) => {
              setPicked([]);
              setDs(e.target.value);
            }}
            data-testid="shell-design-pick"
          >
            {designs.map((d) => (
              <option key={d.id} value={d.id}>
                {d.id} · {d.asked} · v{d.current_n} of {d.versions} · {housesText(d)}
              </option>
            ))}
          </select>
        )}
        {design && (
          <table className="bom shell-version-list" data-testid="shell-version-list" data-design={design.id}>
            <thead>
              <tr>
                <th>Version</th>
                <th>Asked</th>
                <th>By</th>
                <th className="num">Blocks</th>
                <th>Cost</th>
                <th>Gate</th>
                <th />
                <th />
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <VersionRow
                  key={v.build}
                  v={v}
                  current={v.build === design.current}
                  shown={v.build === view}
                  picked={picked.includes(v.build)}
                  busy={working}
                  onShow={() => setShown(v.build)}
                  onCurrent={() => makeCurrent(v.build)}
                  onPick={(on) => setPicked((p) => (on ? [...p.filter((b) => b !== v.build), v.build].slice(-2) : p.filter((b) => b !== v.build)))}
                />
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      {design && picked.length === 2 && <Compare a={picked[0]} b={picked[1]} versions={design.versions} />}
      {view && (
        <Panel
          label={`Shell ${view}${design ? ` · v${design.versions.find((v) => v.build === view)?.n ?? "?"}` : ""}`}
          testId="shell-viewer"
          className="shell-viewer"
          aside={card?.ref ? <CitationChip cite={card.ref} /> : undefined}
        >
          <ShellScene3D build={view} />
          {card && (
            <>
              <p className="gos-sub" data-testid="shell-status">
                {card.status === "accepted" ? `${GLYPH.ok} accepted as designed` : `${GLYPH.warn} repaired`}
                {card.theme ? ` · theme ${card.theme}` : ""} · {card.blocks} of {card.max_blocks} blocks · no tile entities,{" "}
                {design?.area != null ? "its floor left clear for your machines" : "machines' space left clear"}
              </p>
              <details className="shell-paste" data-testid="shell-paste">
                <summary className="gos-label">Export and paste it (Lab only)</summary>
                <ol>
                  {card.paste.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ol>
              </details>
            </>
          )}
        </Panel>
      )}
    </div>
  );
}
