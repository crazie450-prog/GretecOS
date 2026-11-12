// Ores (plan 7.6, 7.14: "Where's the nearest X?"; WO-6.6). A material's veins as the catalog's
// ore list card, in the core's order (live first, then the material's best layer, then nearest:
// D142 (c); the view never re-ranks), a simple map of them around where he was at the last save,
// and a JourneyMap waypoint per vein that is only proposed here: GOS reads it back on the approval
// card and he confirms it there or by voice (D59); the core builds it from the vein, never from
// this page's numbers. No approved board (D92): designed to the system, needs Clayton's board
// approval (D158 (5)).
import { type FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { OrePosition, OresView } from "../api/types";
import { Card } from "../cards/Card";
import type { OreListCard, OreRow } from "../cards/types.gen";
import { Button, Panel } from "../components/core";
import { GLYPH } from "../state/states";

const MARKS = 5; // veins with a waypoint button: the first rows, as ranked

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

function dimLabel(dim: number, name: string | null | undefined): string {
  return name ? `${name} (${dim})` : `dim ${dim}`;
}

/** The card's rows when ``card`` is an ore list (the catalog check happens in <Card>). */
export function oreRows(card: unknown): OreRow[] {
  const c = card as OreListCard | null;
  return c && c.type === "ore_list" && Array.isArray(c.rows) ? c.rows : [];
}

export interface MapPoint {
  n: number; // the row's place in the list, from 1
  x: number; // -100..100, east is +x
  y: number; // -100..100, south is +y (Minecraft's +z)
  depleted: boolean;
  ref: string | null;
}

/** The rows in the view's dimension (with none chosen, the first row's: one plot never mixes
 * dimensions) around ``centre`` (his position, else the first vein), scaled so the farthest
 * fits. Only placement: the order and distances are the core's. */
export function mapPoints(rows: OreRow[], dim: number | null, centre: { x: number; z: number } | null): MapPoint[] {
  const shown = dim ?? (rows.length > 0 ? rows[0].dim : null);
  const here = rows.map((r, i) => ({ r, n: i + 1 })).filter(({ r }) => r.dim === shown);
  if (here.length === 0) return [];
  const c = centre ?? { x: here[0].r.x, z: here[0].r.z };
  const reach = Math.max(32, ...here.map(({ r }) => Math.max(Math.abs(r.x - c.x), Math.abs(r.z - c.z))));
  const scale = 90 / reach;
  return here.map(({ r, n }) => ({
    n,
    x: Math.round((r.x - c.x) * scale * 10) / 10,
    y: Math.round((r.z - c.z) * scale * 10) / 10,
    depleted: r.depleted,
    ref: r.ref,
  }));
}

function OreMap(props: { rows: OreRow[]; dim: number | null; position: OrePosition | null }) {
  const centred = props.position !== null && props.position.dim === props.dim;
  const centre = centred && props.position ? { x: props.position.x, z: props.position.z } : null;
  const points = mapPoints(props.rows, props.dim, centre);
  if (points.length === 0) return <p className="empty">{GLYPH.todo} No veins to place on the map.</p>;
  return (
    <svg
      className="ore-map"
      viewBox="-110 -110 220 220"
      role="img"
      aria-label={`${points.length} veins around ${centred ? "you" : "the first vein"}, north up`}
      data-testid="ores-map"
    >
      <line className="ore-map-axis" x1="-105" y1="0" x2="105" y2="0" />
      <line className="ore-map-axis" x1="0" y1="-105" x2="0" y2="105" />
      <text className="ore-map-north" x="0" y="-96" textAnchor="middle">
        N
      </text>
      {centred && <circle className="ore-map-you" cx="0" cy="0" r="5" data-testid="ores-map-you" />}
      {points.map((p) => (
        <g key={`${p.n}-${p.ref}`} className={p.depleted ? "ore-map-vein depleted" : "ore-map-vein"} data-vein={p.ref ?? ""}>
          <circle cx={p.x} cy={p.y} r="7" />
          <text x={p.x} y={p.y + 3.5} textAnchor="middle">
            {p.n}
          </text>
        </g>
      ))}
    </svg>
  );
}

export function Ores() {
  const [data, setData] = useState<OresView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [query, setQuery] = useState<{ material: string | null; dim: number | null }>({ material: null, dim: null });
  const [said, setSaid] = useState<string | null>(null); // the waypoint's read-back

  useEffect(() => {
    let live = true;
    api.ores(query.material, query.dim).then(
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
  }, [query]);

  const find = (e: FormEvent) => {
    e.preventDefault();
    const material = text.trim();
    if (material) {
      setSaid(null);
      setQuery({ material, dim: query.dim });
    }
  };

  const mark = (vein: string) => {
    api.oreWaypoint(vein).then(
      (r) => setSaid(r.text),
      (e: unknown) => setSaid(`${GLYPH.warn} ${message(e)}`),
    );
  };

  const rows = oreRows(data?.card ?? null);
  const pos = data?.position ?? null;

  return (
    <div className="view-ores" data-testid="view-ores">
      <div className="view-head">
        <h1 className="page-title">Ores</h1>
        <p className="lede">Where's the nearest vein? Every vein GOS loaded, ranked by the ore's layer, then distance.</p>
      </div>
      {error && (
        <p className="empty" data-testid="ores-empty">
          {GLYPH.waiting} {error}
        </p>
      )}
      {!data && !error && <p className="empty">{GLYPH.progress} Loading the veins</p>}
      {data && (
        <div className="ores-grid">
          <div className="col">
            <Panel label="Find a vein" testId="ores-find">
              <form className="search-form" onSubmit={find}>
                <input
                  className="ask-input"
                  list="ore-materials"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="Ore material (Iron, Naquadah, ...)"
                  aria-label="Ore material"
                  maxLength={100}
                  data-testid="ores-input"
                />
                <datalist id="ore-materials">
                  {data.materials.map((m) => (
                    <option key={m} value={m} />
                  ))}
                </datalist>
                <select
                  className="ask-input base-select"
                  aria-label="Dimension"
                  value={query.dim === null ? "" : String(query.dim)}
                  onChange={(e) => setQuery({ material: query.material, dim: e.target.value === "" ? null : Number(e.target.value) })}
                  data-testid="ores-dim"
                >
                  <option value="">{pos ? "Where you are" : "Every dimension"}</option>
                  {data.dims.map((d) => (
                    <option key={d.dim} value={String(d.dim)}>
                      {dimLabel(d.dim, d.name)}
                    </option>
                  ))}
                </select>
                <Button type="submit" variant="primary" testId="ores-go">
                  Find
                </Button>
              </form>
              <p className="gos-sub">Or say "where's the nearest …". Mix and ore names are the export's, shown as written.</p>
            </Panel>
            <Panel label="Nearest veins" testId="ores-list">
              {data.card ? (
                <Card card={data.card} />
              ) : (
                <p className="empty">{GLYPH.todo} Name an ore to list its veins.</p>
              )}
            </Panel>
            {rows.length > 0 && (
              <Panel label="JourneyMap waypoint" testId="ores-marks">
                {data.can_mark ? (
                  <ul className="ore-marks">
                    {rows.slice(0, MARKS).map((r, i) =>
                      r.ref ? (
                        <li key={r.ref} data-mark={r.ref}>
                          <span className="ore-mark-name">
                            {i + 1}. {r.name}
                            {r.distance !== null && <span className="num"> · {r.distance} blocks</span>}
                          </span>
                          <Button onClick={() => mark(r.ref as string)} testId={`ores-mark-${i + 1}`}>
                            Propose waypoint
                          </Button>
                        </li>
                      ) : null,
                    )}
                  </ul>
                ) : (
                  <p className="empty" data-testid="ores-no-mark">
                    {GLYPH.waiting} {data.mark_note}
                  </p>
                )}
                {said && (
                  <p className="answer-text" data-testid="ores-said">
                    {said}
                  </p>
                )}
                <p className="gos-sub">
                  A waypoint is a world action: GOS reads it back, and you confirm it on the approval card or by saying
                  "confirm". JourneyMap shows it after you change dimension or rejoin the world.
                </p>
              </Panel>
            )}
          </div>
          <div className="col">
            <Panel label="Map" testId="ores-map-panel" aside={<span className="gos-src">north up</span>}>
              {rows.length > 0 ? (
                <OreMap rows={rows} dim={data.dim} position={pos} />
              ) : (
                <p className="empty">{GLYPH.todo} The map shows the listed veins.</p>
              )}
            </Panel>
            <Panel label="Where you are" testId="ores-where">
              {pos ? (
                <p className="gos-sub" data-testid="ores-position">
                  <span className="num">
                    x {pos.x}, y {pos.y}, z {pos.z}
                  </span>{" "}
                  in {dimLabel(pos.dim, pos.dim_name)}, at the last save of {pos.world} ({pos.age_s} s ago)
                </p>
              ) : (
                <p className="empty" data-testid="ores-position">
                  {GLYPH.waiting} {data.position_note}
                </p>
              )}
              <p className="gos-sub" data-testid="ores-source">
                {data.source.world
                  ? `Veins from ${data.source.world}${data.source.instance ? ` (${data.source.instance === "lab" ? "Lab" : "Play"})` : ""}: ${data.source.veins}`
                  : "No veins loaded yet: run gos ores load"}
              </p>
              {data.dims.length > 0 && (
                <div className="table-wrap">
                  <table className="base-table">
                    <thead>
                      <tr>
                        <th>dimension</th>
                        <th className="num">veins</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.dims.map((d) => (
                        <tr key={d.dim}>
                          <td>{dimLabel(d.dim, d.name)}</td>
                          <td className="num">{d.veins}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}
