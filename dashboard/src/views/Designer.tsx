// Designer (plan 7.14: "Compare 2-3 routes, then build one"; plan 7.1, D61, WO-3.7). The routes
// side by side with their differences marked (the route comparison card), each route's flow
// diagram (React Flow + elkjs, the flow card's one layout, D159) and machine nodes; pick any node
// to swap it to another candidate recipe or pin it, and the core solves again; accept one route.
// Every number is the planner's and the order is rank.py's (the view never sorts routes). The
// design shown is in the address (#/designer/<plan id>), so a reload shows it again, pins and all;
// with none, the latest design opens. The approved Designer board (D92) hasn't come through the
// canvas handoff: built to the design system (D158 (5)), to be reconciled with the board.
// Designs, re-solves and pins run as design jobs (WO-3.9, D353): the busy panel shows the stage,
// the time and a Cancel; leaving the view leaves the job running to its saved result (D354).
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type {
  Alternatives,
  Designed,
  DesignerNode,
  DesignerRoute,
  DesignerView,
  DesignJobBody,
  DesignSpec,
  PlanLayout,
  SavedPlanRow,
} from "../api/types";
import { Card } from "../cards/Card";
import { FlowDiagram } from "../cards/FlowDiagram";
import { useCardActions } from "../cards/context";
import { RouteComparisonView } from "../cards/renderers";
import type { Card as CardData, ChoicesCard } from "../cards/types.gen";
import { JobProgress, useDesignJob } from "../components/JobProgress";
import { Button, CitationChip, Panel, StatusGlyph, TierChip } from "../components/core";
import { plain } from "../state/format";
import { GLYPH } from "../state/states";
import { guideFor, guideHash, useGuides } from "./Build";

const TIERS = ["", "ULV", "LV", "MV", "HV", "EV", "IV", "LuV", "ZPM", "UV", "UHV"];
const EXPORTS: [string, string][] = [
  ["bom", "BOM (TSV)"],
  ["png", "PNG"],
  ["gtnhplanner", "gtnhplanner.com"],
  ["gtnhflow", "gtnh-flow"],
];

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

/** The plan id the address names (#/designer/pl-...), or null. */
export function planFromHash(hash: string): string | null {
  const m = /^#\/?designer\/(pl-[a-z0-9._-]{1,61})$/.exec(hash);
  return m ? m[1] : null;
}

function showPlan(id: string): void {
  const want = `#/designer/${id}`;
  if (window.location.hash !== want) window.location.hash = want.slice(1);
}

/** The route's head line: its place in code's ranking, never a re-sort. */
export function routeLabel(r: Pick<DesignerRoute, "column" | "position" | "of">): string {
  return r.column === "addon" ? "Addon column" : `Route ${r.position} of ${r.of}`;
}

function Cite({ cite }: { cite: string | null }) {
  const { openCite, stale } = useCardActions();
  if (!cite) return null;
  const game = cite.startsWith("r:") || cite.startsWith("mte:");
  return <CitationChip cite={cite} stale={stale && game} onOpen={cite.startsWith("r:") ? openCite : undefined} />;
}

function DesignForm(props: { busy: boolean; onDesign: (spec: DesignSpec) => void }) {
  const [target, setTarget] = useState("");
  const [amount, setAmount] = useState("64");
  const [tier, setTier] = useState("");
  const [routes, setRoutes] = useState(3);
  const [addons, setAddons] = useState(false);
  const submit = (e: FormEvent): void => {
    e.preventDefault();
    if (!target.trim()) return;
    // A rate has a "/" (2/s); a plain number is a batch; nothing is the server's batch of 64 (D389 (5)).
    const a = amount.trim();
    props.onDesign({
      target: target.trim(),
      ...(a ? (a.includes("/") ? { rate: a } : { batch: a }) : {}),
      ...(tier ? { tier } : {}),
      routes,
      addons,
    });
  };
  return (
    <Panel label="New design" testId="designer-form">
      <form className="designer-form" onSubmit={submit}>
        <label className="field">
          <span className="gos-label">Target</span>
          <input
            className="ask-input"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="steel ingot, sulfuric acid"
            aria-label="Target item or fluid"
            data-testid="designer-target"
          />
        </label>
        <label className="field small">
          <span className="gos-label">Amount</span>
          <input
            className="ask-input num"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="64"
            aria-label="Amount: a batch (64) or a rate (2/s, 30/min, 100/h)"
            data-testid="designer-rate"
          />
        </label>
        <label className="field small">
          <span className="gos-label">Tier cap</span>
          <select
            className="ask-input base-select"
            value={tier}
            onChange={(e) => setTier(e.target.value)}
            aria-label="Tier cap"
            data-testid="designer-tier"
          >
            {TIERS.map((t) => (
              <option key={t} value={t}>
                {t || "My base"}
              </option>
            ))}
          </select>
        </label>
        <label className="field small">
          <span className="gos-label">Routes</span>
          <select
            className="ask-input base-select"
            value={routes}
            onChange={(e) => setRoutes(Number(e.target.value))}
            aria-label="How many routes"
            data-testid="designer-routes"
          >
            {[3, 2, 1].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label className="field check">
          <input type="checkbox" checked={addons} onChange={(e) => setAddons(e.target.checked)} data-testid="designer-addons" />
          <span>Addon machines too</span>
        </label>
        <Button type="submit" variant="primary" disabled={props.busy || !target.trim()} testId="designer-go">
          Design
        </Button>
      </form>
    </Panel>
  );
}

function SavedPlans(props: { plans: SavedPlanRow[] | null; current: string | null }) {
  return (
    <Panel label="Saved plans" testId="designer-saved" aside={<span className="gos-src">{props.plans?.length ?? 0} saved</span>}>
      {props.plans === null && <p className="empty">{GLYPH.progress} Loading</p>}
      {props.plans !== null && props.plans.length === 0 && (
        <p className="empty">{GLYPH.todo} No plans yet. A design saves every route it finds.</p>
      )}
      {props.plans !== null && props.plans.length > 0 && (
        <ul className="result-list designer-saved">
          {props.plans.slice(0, 12).map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className={`result${p.id === props.current ? " current" : ""}`}
                onClick={() => showPlan(p.id)}
                data-plan={p.id}
              >
                <span>{p.name}</span>
                <span className="gos-src">
                  {p.status}
                  {p.affected ? ` · ${GLYPH.warn} affected by update` : ""} · plan:{p.id}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function NodeRow(props: { route: DesignerRoute; node: DesignerNode; picked: boolean; onPick: () => void }) {
  const { node } = props;
  // WO-10.8: a multi with a footprint opens in the Build view's 3D guide.
  const guide = guideFor(useGuides(), node.mte ? Number(node.mte.slice(4)) : null);
  return (
    <li className={`designer-node${props.picked ? " picked" : ""}`} data-node={node.item ?? node.n}>
      <button type="button" className="result" onClick={props.onPick} aria-pressed={props.picked} data-testid={`swap-${props.route.id}-${node.n}`}>
        <span>
          {node.name}
          {node.pinned && <span className="gos-src"> · {GLYPH.done} pinned</span>}
        </span>
        <span className="gos-src">
          {node.count !== null ? `${plain(node.count)}x ` : ""}
          {node.machine ?? node.map ?? "crafting"}
        </span>
      </button>
      <span className="designer-node-meta">
        {node.tier && <TierChip tier={node.tier} />}
        <Cite cite={node.recipe} />
        {guide && (
          <a className="gos-src" href={guideHash({ controller: guide })} data-testid={`guide-link-${props.route.id}-${node.n}`}>
            Build guide
          </a>
        )}
      </span>
    </li>
  );
}

/** The route's floor plan (WO-10.5, D312, D313): the core lays the machines out on open, never
 * here; the card draws it. Needs Clayton's board approval (D92, D158 (5)). */
function FloorPlan({ planId }: { planId: string }) {
  const [open, setOpen] = useState(false);
  const [got, setGot] = useState<PlanLayout | null>(null);
  const [error, setError] = useState<string | null>(null);
  // WO-10.7 (D316): the procedural shell around this floor plan, no AI needed (No-AI mode).
  const [shell, setShell] = useState<CardData | null>(null);
  const [shellBusy, setShellBusy] = useState(false);
  const [shellError, setShellError] = useState<string | null>(null);
  useEffect(() => {
    if (!open || got || error) return;
    let live = true;
    api.layout(planId).then(
      (r) => live && setGot(r),
      (e: unknown) => live && setError(message(e)),
    );
    return () => {
      live = false;
    };
  }, [open, got, error, planId]);
  const designShell = () => {
    setShellBusy(true);
    setShellError(null);
    api.designShell([planId]).then(
      (r) => {
        setShell(r.card);
        setShellBusy(false);
      },
      (e: unknown) => {
        setShellError(message(e));
        setShellBusy(false);
      },
    );
  };
  return (
    <details
      className="designer-layout"
      data-testid={`layout-${planId}`}
      onToggle={(e) => {
        const now = (e.currentTarget as HTMLDetailsElement).open;
        if (now) setError(null); // reopening tries a failed load again
        setOpen(now);
      }}
    >
      <summary className="gos-label">Floor plan</summary>
      {open && !got && !error && <p className="empty">{GLYPH.progress} Laying out the machines</p>}
      {error && <p className="empty">{GLYPH.warn} {error}</p>}
      {got && <Card card={got.card} />}
      {got && !shell && (
        <Button testId={`shell-design-${planId}`} disabled={shellBusy} onClick={designShell}>
          {shellBusy ? `${GLYPH.progress} Designing a shell` : "Design a shell around it"}
        </Button>
      )}
      {shellError && <p className="empty">{GLYPH.warn} {shellError}</p>}
      {shell && <Card card={shell} />}
    </details>
  );
}

function RouteColumn(props: {
  route: DesignerRoute;
  names: Map<string, string>; // item key -> name, from every route of the design
  busy: boolean;
  picked: string | null;
  onPick: (node: DesignerNode) => void;
  onUnpin: (node: string) => void;
  onAccept: () => void;
  onRename: (name: string) => void;
  onResolve: () => void;
}) {
  const r = props.route;
  const [confirming, setConfirming] = useState(false);
  const [naming, setNaming] = useState<string | null>(null);
  const byBox = new Map(r.nodes.map((n) => [n.n, n]));
  const accepted = r.status !== "proposed";
  return (
    <section className={`gos-panel designer-route${accepted ? " accepted" : ""}`} data-route={r.id} data-testid={`route-${r.position}-${r.column}`}>
      <div className="panel-head">
        <span className="gos-label">{routeLabel(r)}</span>
        {accepted ? (
          <StatusGlyph status="ok" glyph={GLYPH.done} label={r.status} />
        ) : (
          <span className="gos-src">score {plain(r.score)}</span>
        )}
      </div>
      {naming === null ? (
        <div className="designer-name">
          <h2 className="gos-title">{r.name}</h2>
          <Button disabled={props.busy} onClick={() => setNaming(r.name)} testId={`rename-${r.id}`}>
            Rename
          </Button>
        </div>
      ) : (
        <form
          className="search-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (naming.trim()) props.onRename(naming.trim());
            setNaming(null);
          }}
        >
          <input
            className="ask-input"
            value={naming}
            maxLength={80}
            onChange={(e) => setNaming(e.target.value)}
            aria-label="Plan name"
            data-testid={`rename-input-${r.id}`}
          />
          <Button type="submit" variant="primary" disabled={props.busy || !naming.trim()} testId={`rename-save-${r.id}`}>
            Save
          </Button>
          <Button onClick={() => setNaming(null)}>Cancel</Button>
        </form>
      )}
      <div className="designer-cites">
        <Cite cite={`plan:${r.id}`} />
        <span className="gos-src">
          {r.target.name} ·{" "}
          {r.target.eta_s != null && r.target.qty != null
            ? `batch ${plain(r.target.qty)} · ETA ${plain(Math.round(r.target.eta_s))} s`
            : `${plain(r.target.rate_s)}/s`}{" "}
          · cap <TierChip tier={r.tier_cap} />
        </span>
      </div>
      {r.affected && (
        <div className="notice" role="status" data-testid={`affected-${r.id}`}>
          <span className="gos-label">
            {GLYPH.warn} Affected by update
          </span>
          <p className="gos-sub">
            Export {r.affected.run} changed or removed what this plan cites. Re-solve it on the new data, then accept the
            new route.
          </p>
          {r.affected.hits.length > 0 && (
            <ul className="designer-pins">
              {r.affected.hits.slice(0, 5).map((h) => (
                <li key={h.cite}>
                  <Cite cite={h.cite} /> <span className="gos-src">{h.why}</span>
                </li>
              ))}
            </ul>
          )}
          <Button variant="primary" disabled={props.busy} onClick={props.onResolve} testId={`affected-resolve-${r.id}`}>
            Re-solve
          </Button>
        </div>
      )}
      {r.pins.length > 0 && (
        <ul className="designer-pins" data-testid={`pins-${r.id}`}>
          {r.pins.map((p) => (
            <li key={p.node}>
              <span className="gos-src">
                {GLYPH.done} pinned {props.names.get(p.node) ?? p.node}
                {!r.nodes.some((n) => n.item === p.node) && " (not used by this route)"}
              </span>{" "}
              <Cite cite={p.recipe} />{" "}
              <Button disabled={props.busy} onClick={() => props.onUnpin(p.node)} testId={`unpin-${r.id}-${p.node}`}>
                Unpin
              </Button>
            </li>
          ))}
        </ul>
      )}
      {r.warnings.slice(0, 3).map((w) => (
        <p key={w} className="gos-sub">
          {GLYPH.warn} {w}
        </p>
      ))}
      <FlowDiagram
        card={r.flow}
        selected={props.picked}
        onSelect={(id) => {
          const node = byBox.get(id);
          if (node) props.onPick(node);
        }}
      />
      <ul className="designer-nodes" aria-label={`${r.name}: machine nodes`}>
        {r.nodes.map((n) => (
          <NodeRow key={n.n} route={r} node={n} picked={props.picked === n.n} onPick={() => props.onPick(n)} />
        ))}
      </ul>
      <FloorPlan planId={r.id} />
      <div className="designer-exports">
        {EXPORTS.map(([fmt, label]) => (
          <a key={fmt} className="gos-src" href={`/api/plans/${r.id}/export/${fmt}`}>
            {label}
          </a>
        ))}
      </div>
      {!accepted &&
        (confirming ? (
          <div className="designer-accept needs" data-testid={`accept-confirm-${r.id}`}>
            <p className="gos-sub">
              Accept "{r.name}"? GOS writes its line note in the vault and adds it to the EU ledger.
            </p>
            <Button
              variant="confirm"
              disabled={props.busy}
              onClick={() => {
                setConfirming(false);
                props.onAccept();
              }}
              testId={`accept-yes-${r.id}`}
            >
              Confirm
            </Button>
            <Button onClick={() => setConfirming(false)}>Cancel</Button>
          </div>
        ) : (
          <Button variant="primary" disabled={props.busy} onClick={() => setConfirming(true)} testId={`accept-${r.id}`}>
            Accept this route
          </Button>
        ))}
    </section>
  );
}

function AlternativesPanel(props: {
  alts: Alternatives | null;
  error: string | null;
  busy: boolean;
  all: boolean;
  setAll: (all: boolean) => void;
  onPin: (recipe: string) => void;
  onUnpin: () => void;
  onClose: () => void;
}) {
  const a = props.alts;
  return (
    <Panel
      label="Swap or pin"
      testId="designer-alternatives"
      aside={
        <Button onClick={props.onClose} testId="alternatives-close">
          Close
        </Button>
      }
    >
      {props.error && <p className="empty">{GLYPH.warn} {props.error}</p>}
      {!a && !props.error && <p className="empty">{GLYPH.progress} Loading the node's recipes</p>}
      {a && (
        <>
          <h2 className="gos-title">{a.name}</h2>
          <p className="gos-sub">
            {a.pinned ? (
              <>
                {GLYPH.done} Pinned to <Cite cite={a.pinned} />{" "}
                <Button disabled={props.busy} onClick={props.onUnpin} testId="alternatives-unpin">
                  Unpin
                </Button>
              </>
            ) : (
              "Not pinned: the designer picks the simplest recipe at your tier unless you pin one."
            )}
          </p>
          <div className="table-wrap">
            <table className="designer-alts">
              <thead>
                <tr>
                  <th>recipe</th>
                  <th>machine</th>
                  <th>tier</th>
                  <th className="num">EU/t</th>
                  <th className="num">ticks</th>
                  <th>status</th>
                  <th aria-label="pin" />
                </tr>
              </thead>
              <tbody>
                {a.alternatives.map((x) => (
                  <tr key={x.recipe} data-recipe={x.recipe} className={x.used ? "used" : undefined}>
                    <td>
                      <Cite cite={x.recipe} />
                    </td>
                    <td>{x.map ?? "-"}</td>
                    <td>{x.tier ? <TierChip tier={x.tier} /> : "-"}</td>
                    <td className="num">{x.eut !== null ? plain(x.eut) : "-"}</td>
                    <td className="num">{x.duration_t !== null ? plain(x.duration_t) : "-"}</td>
                    <td>
                      {x.used ? `${GLYPH.ok} in this route` : x.status}
                      {x.reason && <div className="gos-src">{x.reason}</div>}
                    </td>
                    <td>
                      {x.status !== "rejected" && a.pinned !== x.recipe && (
                        <Button disabled={props.busy} onClick={() => props.onPin(x.recipe)} testId={`pin-${x.recipe}`}>
                          Pin
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <label className="field check">
            <input type="checkbox" checked={props.all} onChange={(e) => props.setAll(e.target.checked)} data-testid="alternatives-all" />
            <span>Show all (every recipe, rejected ones with why)</span>
          </label>
        </>
      )}
    </Panel>
  );
}

export function Designer() {
  const [view, setView] = useState<DesignerView | null>(null);
  const [plans, setPlans] = useState<SavedPlanRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // what the core is doing
  const [clarify, setClarify] = useState<{ card: ChoicesCard; spec: DesignSpec } | null>(null);
  const [picked, setPicked] = useState<{ route: string; node: DesignerNode } | null>(null);
  const [alts, setAlts] = useState<Alternatives | null>(null);
  const [altsError, setAltsError] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const shown = useRef<string | null>(null);
  // A design that arrives after he left the view must not put its plan back in the address, or
  // the phone's More tab (WO-9.3) would bounce back here.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const altsRef = useRef<HTMLDivElement | null>(null);

  const refreshPlans = useCallback(() => {
    api.plans().then(
      (r) => setPlans(r.plans),
      () => setPlans([]),
    );
  }, []);

  const show = useCallback((v: DesignerView) => {
    shown.current = v.id;
    setView(v);
    setError(null);
    if (mounted.current) showPlan(v.id);
  }, []);

  const load = useCallback(
    (id: string) => {
      if (shown.current === id) return;
      shown.current = id;
      api.designerView(id).then(show, (e: unknown) => {
        shown.current = null;
        setError(message(e));
      });
    },
    [show],
  );

  useEffect(() => {
    refreshPlans();
    const fromHash = planFromHash(window.location.hash);
    if (fromHash) load(fromHash);
    else
      api.designerLatest().then(
        (r) => {
          if (r.ids.length) load(r.ids[0]);
          else setView(null);
        },
        (e: unknown) => setError(message(e)),
      );
    const on = (): void => {
      const id = planFromHash(window.location.hash);
      if (id) load(id);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, [load, refreshPlans]);

  // The picked node's candidates, again when "show all" changes or a re-solve moved the plan.
  const pickedRoute = picked?.route ?? null;
  const pickedItem = picked?.node.item ?? null;
  useEffect(() => {
    if (!pickedRoute || !pickedItem) return;
    let live = true;
    setAlts(null);
    setAltsError(null);
    api.alternatives(pickedRoute, pickedItem, all).then(
      (a) => live && setAlts(a),
      (e: unknown) => live && setAltsError(message(e)),
    );
    return () => {
      live = false;
    };
  }, [pickedRoute, pickedItem, all]);

  const run = async (what: string, call: () => Promise<{ view: DesignerView; problems?: string[] }>): Promise<boolean> => {
    setBusy(what);
    try {
      const out = await call();
      show(out.view);
      setNotice(out.problems && out.problems.length ? out.problems.join("; ") : null);
      refreshPlans();
      return true;
    } catch (e) {
      setNotice(message(e));
      return false;
    } finally {
      setBusy(null);
    }
  };

  // A design job's result is what POST /api/designer/design answers (D353).
  const designed = (result: unknown): void => {
    const out = result as Designed;
    show(out.view);
    setNotice(out.problems.length ? out.problems.join("; ") : null);
    refreshPlans();
  };
  const runner = useDesignJob(
    "designer",
    (result) => {
      setPicked(null);
      designed(result);
    },
    (text) => setNotice(text),
  );
  const working = busy !== null || runner.job !== null;

  const startJob = (body: DesignJobBody, then?: (result: unknown) => void): void => {
    setNotice(null);
    setBusy("Starting the design"); // busy from the click on; the job's panel follows
    runner.start(() => api.designJob(body), then).then(
      (question) => {
        setBusy(null);
        if (question && "spec" in body) setClarify({ card: question, spec: body.spec });
      },
      (e: unknown) => {
        setBusy(null);
        setNotice(message(e));
      },
    );
  };

  const design = (spec: DesignSpec): void => {
    setClarify(null);
    startJob({ spec });
  };

  const pick = (route: string, node: DesignerNode): void => {
    if (!node.item) return;
    setPicked({ route, node });
    setTimeout(() => altsRef.current?.scrollIntoView?.({ block: "nearest" }), 0);
  };

  const pin = (recipe: string | null): void => {
    if (!picked?.node.item) return;
    const item = picked.node.item;
    startJob({ pin: { plan: picked.route, node: item, recipe } }, (result) => {
      const out = result as Designed;
      // Keep the same item picked, on the best-ranked route that makes it, so the swap shows.
      const holder = out.view.routes.find((r) => r.nodes.some((n) => n.item === item));
      const again = holder?.nodes.find((n) => n.item === item);
      setPicked(holder && again ? { route: holder.id, node: again } : null);
      designed(out);
    });
  };

  const routes = view?.routes ?? [];
  const names = new Map(routes.flatMap((r) => r.nodes.flatMap((n) => (n.item ? [[n.item, n.name] as const] : []))));
  return (
    <div className="view-designer" data-testid="view-designer">
      <div className="view-head">
        <h1 className="page-title">Designer</h1>
        <p className="lede">Compare 2-3 routes, then build one. Ranked by code; pick any machine to swap or pin it.</p>
      </div>
      <div className="designer-top">
        <DesignForm busy={working} onDesign={design} />
        <SavedPlans plans={plans} current={view?.id ?? null} />
      </div>
      {runner.job && <JobProgress job={runner.job} onCancel={runner.cancel} testId="designer-busy" prefix="designer" />}
      {busy && !runner.job && (
        <div className="gos-panel" aria-busy="true" data-testid="designer-busy">
          <p className="empty">
            {GLYPH.progress} {busy}
          </p>
        </div>
      )}
      {notice && (
        <div className="notice gos-panel" role="alert" data-testid="designer-notice">
          <span>
            {GLYPH.warn} {notice}
          </span>
          <Button onClick={() => setNotice(null)}>Dismiss</Button>
        </div>
      )}
      {clarify && (
        <div className="gos-panel" data-testid="designer-clarify">
          <span className="gos-label">Which one?</span>
          <p className="gos-sub">{clarify.card.question}</p>
          <div className="designer-choices">
            {clarify.card.candidates.map((c) => (
              <Button key={c.key} onClick={() => design({ ...clarify.spec, target: c.key })} testId={`clarify-${c.n}`}>
                {c.name}
              </Button>
            ))}
          </div>
        </div>
      )}
      {error && (
        <div className="gos-panel">
          <p className="empty">
            {GLYPH.warn} {error}
          </p>
        </div>
      )}
      {!view && !error && (
        <div className="gos-panel" data-testid="designer-empty">
          <p className="empty">
            {GLYPH.todo} No design open. Ask for one above, or say "design a steel line at 2 per second".
          </p>
        </div>
      )}
      {view && (
        <>
          <div className="designer-bar gos-panel">
            <span className="gos-src" data-testid="designer-weights">
              {view.weights}
            </span>
            <Button disabled={working} onClick={() => startJob({ resolve: view.id })} testId="designer-resolve">
              Re-solve
            </Button>
          </div>
          <Panel label="Routes side by side" testId="designer-compare">
            <RouteComparisonView card={view.comparison} />
          </Panel>
          <div className="designer-routes" style={{ gridTemplateColumns: `repeat(${Math.max(1, routes.length)}, minmax(0, 1fr))` }}>
            {routes.map((r) => (
              <RouteColumn
                key={r.id}
                route={r}
                names={names}
                busy={working}
                picked={picked?.route === r.id ? picked.node.n : null}
                onPick={(n) => pick(r.id, n)}
                onUnpin={(node) => startJob({ pin: { plan: r.id, node, recipe: null } })}
                onAccept={() => void run("Accepting", () => api.accept(r.id))}
                onRename={(name) => void run("Renaming", () => api.rename(r.id, name))}
                onResolve={() => startJob({ resolve: r.id })}
              />
            ))}
          </div>
          {picked && (
            <div ref={altsRef}>
              <AlternativesPanel
                alts={alts}
                error={altsError}
                busy={working}
                all={all}
                setAll={setAll}
                onPin={(recipe) => pin(recipe)}
                onUnpin={() => pin(null)}
                onClose={() => setPicked(null)}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
