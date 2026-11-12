// Crops (plan 7.14 "What should I breed next, and how?", 7.17; WO-6.7, D275). The path to a target
// crop in the crop planner's order (the view never re-ranks or recomputes it), the chosen step's
// layout grid around the double stick, its requirements, odds and times with their citation chips,
// the stats plan, and the crops he has. Edits (his crops, the target) are his, from a paired
// dashboard. Crops has an approved board (D92) but the canvas handoff isn't in the repo: designed
// to the system, needs Clayton's board check (D158 (5)).
import { type FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { CropCell, CropLine, CropRequirement, CropStep, CropsView } from "../api/types";
import { Button, CitationChip, Panel, StatusGlyph, TierChip } from "../components/core";
import { GLYPH } from "../state/states";

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

const KIND: Record<CropStep["kind"], string> = {
  crop_manager: "Crop Manager",
  obtain: "Plant a seed",
  sticks: "Crop sticks",
  pool: "Mutation pool",
  breeder: "Crop Breeder",
};

const REQ_STATUS: Record<CropRequirement["status"], { status: "ok" | "warn" | "info"; word: string }> = {
  met: { status: "ok", word: "met" },
  needed: { status: "warn", word: "needed" },
  check: { status: "info", word: "check" },
};

/** The step a click picked, else the next one not done, else the last. */
export function shownStep(steps: CropStep[], next: number | null, picked: number | null): CropStep | null {
  if (steps.length === 0) return null;
  return steps.find((s) => s.n === picked) ?? steps.find((s) => s.n === next) ?? steps[steps.length - 1];
}

/** Three whole numbers 1-31, or null. */
export function parseStats(text: string): number[] | null {
  const parts = text.trim().split(/[\s/,]+/).filter(Boolean);
  if (parts.length !== 3) return null;
  const nums = parts.map((p) => (/^[0-9]{1,2}$/.test(p) ? Number(p) : NaN));
  return nums.every((n) => n >= 1 && n <= 31) ? nums : null;
}

function Cites({ refs }: { refs: string[] }) {
  if (refs.length === 0) return null;
  return (
    <span className="crop-cites">
      {refs.map((r) => (
        <CitationChip key={r} cite={r} />
      ))}
    </span>
  );
}

function Lines({ lines, testId, glyph }: { lines: CropLine[]; testId: string; glyph?: string }) {
  if (lines.length === 0) return null;
  return (
    <ul className="crop-lines" data-testid={testId}>
      {lines.map((l, i) => (
        <li key={`${i}-${l.text}`}>
          <span className="crop-line-text">
            {glyph && <span aria-hidden="true">{glyph} </span>}
            {l.text}
          </span>
          <Cites refs={l.refs} />
        </li>
      ))}
    </ul>
  );
}

const SPOTS: Record<string, string> = { "0,1": "north", "2,1": "south", "1,2": "east", "1,0": "west", "1,1": "double stick" };

function Cell({ cell, r, c }: { cell: CropCell; r: number; c: number }) {
  const where = SPOTS[`${r},${c}`] ?? "corner";
  if (cell.role === "empty") {
    return (
      <div className="crop-cell empty" aria-label={`${where}: keep clear`}>
        <span className="gos-src">{where === "corner" ? "" : "clear"}</span>
      </div>
    );
  }
  return (
    <div className={`crop-cell ${cell.role}`} data-crop={cell.crop ?? ""} aria-label={`${where}: ${cell.name ?? ""}`}>
      <span className="gos-label">{cell.role === "double" ? "double stick" : where}</span>
      <span className="crop-cell-name">{cell.name}</span>
      {cell.under && <span className="gos-src">under it: {cell.under}</span>}
    </div>
  );
}

function StepDetail({ step }: { step: CropStep }) {
  return (
    <div className="crop-step" data-testid="crops-step" data-step={step.n}>
      <p className="gos-sub">
        <span className="gos-chip">{KIND[step.kind]}</span> {step.title} <CitationChip cite={step.ref} />
      </p>
      {step.layout.length > 0 && (
        <div className="crop-layout" role="group" aria-label="Layout around the double stick, north up" data-testid="crops-layout">
          {step.layout.map((row, r) => row.map((cell, c) => <Cell key={`${r}-${c}`} cell={cell} r={r} c={c} />))}
        </div>
      )}
      {step.requirements.length > 0 && (
        <ul className="crop-lines" data-testid="crops-reqs">
          {step.requirements.map((q, i) => (
            <li key={`${i}-${q.text}`} data-status={q.status}>
              <StatusGlyph status={REQ_STATUS[q.status].status} label={REQ_STATUS[q.status].word} />
              <span className="crop-line-text">{q.text}</span>
              <Cites refs={q.refs} />
            </li>
          ))}
        </ul>
      )}
      <Lines lines={step.numbers} testId="crops-numbers" />
      <Lines lines={step.warnings} testId="crops-warnings" glyph={GLYPH.warn} />
      <Lines lines={step.keep} testId="crops-keep" />
    </div>
  );
}

export function Crops() {
  const [data, setData] = useState<CropsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [look, setLook] = useState<string | null>(null); // a crop looked at without following it
  const [picked, setPicked] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [haveCrop, setHaveCrop] = useState("");
  const [haveStats, setHaveStats] = useState("1 1 1");
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.crops(look).then(
      (v) => {
        if (!live) return;
        setData(v);
        setError(null);
      },
      (e: unknown) => live && setError(message(e)),
    );
    return () => {
      live = false;
    };
  }, [look]);

  const edited = (p: Promise<CropsView>) =>
    p.then(
      (v) => {
        setData(v);
        setNote(null);
        setLook(null);
        setPicked(null);
      },
      (e: unknown) => setNote(`${GLYPH.warn} ${message(e)}`),
    );

  const byName = (words: string): string | null => {
    const w = words.trim().toLowerCase();
    const hit = data?.crops.find((c) => c.name.toLowerCase() === w || c.crop.toLowerCase() === w);
    return hit ? hit.crop : w ? words.trim() : null;
  };

  const lookUp = (e: FormEvent) => {
    e.preventDefault();
    const crop = byName(text);
    if (crop) {
      setPicked(null);
      setLook(crop);
    }
  };

  const addHave = (e: FormEvent) => {
    e.preventDefault();
    const crop = byName(haveCrop);
    const stats = parseStats(haveStats);
    if (!crop || !stats) {
      setNote(`${GLYPH.warn} Name a crop and three stats from 1 to 31 (Growth Gain Resistance)`);
      return;
    }
    edited(api.cropHave(crop, stats));
  };

  const guide = data?.guide ?? null;
  const step = guide ? shownStep(guide.steps, guide.next, picked) : null;
  const following = data?.target && guide && data.target.crop === guide.target;

  return (
    <div className="view-crops" data-testid="view-crops">
      <div className="view-head">
        <h1 className="page-title">Crops</h1>
        <p className="lede">What should you breed next, and how? Odds and times are computed from CropsNH's rules.</p>
      </div>
      {error && (
        <p className="empty" data-testid="crops-empty">
          {GLYPH.waiting} {error}
        </p>
      )}
      {!data && !error && <p className="empty">{GLYPH.progress} Loading the crop data</p>}
      {data && (
        <div className="crops-grid">
          <div className="col">
            <Panel
              label={guide ? `Path to ${guide.target_name}` : "Path"}
              testId="crops-path"
              aside={data.tier_name ? <TierChip tier={data.tier_name} /> : undefined}
            >
              {data.error && (
                <p className="empty" data-testid="crops-error">
                  {GLYPH.warn} {data.error}
                </p>
              )}
              {guide ? (
                <>
                  <ol className="crop-path" data-testid="crops-steps">
                    {guide.steps.map((s) => (
                      <li key={s.n} className={step?.n === s.n ? "current" : undefined}>
                        <button
                          type="button"
                          className="crop-path-step"
                          onClick={() => setPicked(s.n)}
                          aria-pressed={step?.n === s.n}
                          data-testid={`crops-step-${s.n}`}
                        >
                          <StatusGlyph
                            status={s.done ? "ok" : s.n === guide.next ? "warn" : "info"}
                            glyph={s.done ? GLYPH.done : s.n === guide.next ? GLYPH.progress : GLYPH.todo}
                            label={s.done ? "done" : s.n === guide.next ? "next" : "to do"}
                          />
                          <span className="crop-path-title">
                            {s.n}. {s.title}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ol>
                  {guide.steps.length === 0 && <p className="empty">{GLYPH.done} You have it: no step left.</p>}
                  {guide.total && <Lines lines={[guide.total]} testId="crops-total" />}
                  {!following && (
                    <Button variant="primary" onClick={() => edited(api.cropTarget(guide.target))} testId="crops-follow">
                      Follow {guide.target_name}
                    </Button>
                  )}
                </>
              ) : (
                !data.error && <p className="empty">{GLYPH.todo} Pick a crop to breed toward.</p>
              )}
            </Panel>
            {step && (
              <Panel label={`Step ${step.n} of ${guide?.steps.length ?? 0}`} testId="crops-step-panel">
                <StepDetail step={step} />
              </Panel>
            )}
            {guide?.stats && (
              <Panel label={`Stats plan to ${guide.stats.target.join("/")}`} testId="crops-stats">
                <Lines lines={guide.stats.lines} testId="crops-stats-lines" />
                <Lines lines={guide.stats.warnings} testId="crops-stats-warnings" glyph={GLYPH.warn} />
              </Panel>
            )}
          </div>
          <div className="col">
            <Panel label="Target" testId="crops-target">
              <form className="search-form" onSubmit={lookUp}>
                <input
                  className="ask-input"
                  list="crop-names"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="Crop (Ferrofern, Coppon, ...)"
                  aria-label="Crop"
                  maxLength={120}
                  data-testid="crops-input"
                />
                <datalist id="crop-names">
                  {data.crops.map((c) => (
                    <option key={c.crop} value={c.name} />
                  ))}
                </datalist>
                <Button type="submit" variant="primary" testId="crops-go">
                  Show
                </Button>
              </form>
              <p className="gos-sub" data-testid="crops-following">
                {data.target ? (
                  <>
                    Following {data.target.name} <CitationChip cite={data.target.ref} /> · aim {data.target.aim.join("/")}
                  </>
                ) : (
                  "Following no crop yet. Or say \"next crop step\"."
                )}
              </p>
              {data.picks.length > 0 && (
                <div className="crop-picks" data-testid="crops-picks">
                  <span className="gos-src">{data.picks_note}:</span>
                  {data.picks.map((p) => (
                    <button
                      type="button"
                      key={p.crop}
                      className="gos-chip"
                      onClick={() => {
                        setPicked(null);
                        setLook(p.crop);
                      }}
                    >
                      {p.name}
                    </button>
                  ))}
                </div>
              )}
            </Panel>
            <Panel label="Your crops" testId="crops-have">
              {data.have.length > 0 ? (
                <div className="table-wrap">
                  <table className="base-table">
                    <thead>
                      <tr>
                        <th>crop</th>
                        <th className="num">gr/ga/re</th>
                        <th aria-label="remove" />
                      </tr>
                    </thead>
                    <tbody>
                      {data.have.map((h) => (
                        <tr key={h.crop}>
                          <td>
                            {h.name} <CitationChip cite={h.ref} />
                          </td>
                          <td className="num">{h.stats.join("/")}</td>
                          <td>
                            <Button onClick={() => edited(api.cropHaveRemove(h.crop))}>Remove</Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty">{GLYPH.todo} None listed. Crops a seed item plants (wheat, carrot) count already.</p>
              )}
              <form className="search-form" onSubmit={addHave}>
                <input
                  className="ask-input"
                  list="crop-names"
                  value={haveCrop}
                  onChange={(e) => setHaveCrop(e.target.value)}
                  placeholder="Crop you have"
                  aria-label="Crop you have"
                  maxLength={120}
                  data-testid="crops-have-crop"
                />
                <input
                  className="ask-input crop-stats-input"
                  value={haveStats}
                  onChange={(e) => setHaveStats(e.target.value)}
                  aria-label="Growth Gain Resistance"
                  maxLength={12}
                  data-testid="crops-have-stats"
                />
                <Button type="submit" testId="crops-have-add">
                  Add
                </Button>
              </form>
              {note && (
                <p className="answer-text" data-testid="crops-note">
                  {note}
                </p>
              )}
            </Panel>
            <Panel label="Setup the odds assume" testId="crops-setup">
              <p className="gos-sub">
                Watered sticks, sky access, {data.setup.liked_biome_tags} liked biome tags. Run {data.run}.
              </p>
              <p className="gos-sub">{GLYPH.waiting} Breeding station status arrives with WO-7.6.</p>
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}
