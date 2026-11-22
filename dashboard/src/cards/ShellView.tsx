// The shell card (WO-10.7, plan 7.10, D316-D318): a decorative shell's isometric preview, its
// palette manifest and the Lab-only paste steps. The core designed and checked the shell (no
// tile entity, nothing in a keep-out, corridor, chase or doorway); this only draws its faces.
// Built to the design system; needs Clayton's board approval (D92, D158 (5)).
import { useMemo, useState } from "react";
import { Button, CitationChip } from "../components/core";
import { GLYPH } from "../state/states";
import { type Vec, project } from "./shellMesh";
import type { ShellCard } from "./types.gen";

const VIEWS = ["south-east", "north-east", "north-west", "south-west"] as const;

export function ShellView({ card }: { card: ShellCard }) {
  const [turns, setTurns] = useState(0);
  const size: Vec = [card.size.x, card.size.y, card.size.z];
  const drawn = useMemo(() => project(card.faces, size, turns), [card.faces, card.size.x, card.size.y, card.size.z, turns]);
  const roles = card.palette.map((m) => m.role);
  const status = card.status === "accepted" ? `${GLYPH.ok} accepted as designed` : `${GLYPH.warn} repaired`;
  return (
    <div className="card shell-card" data-card="shell">
      <header className="card-head">
        <span className="gos-label">Shell · {card.source === "architect" ? "the architect's design" : "procedural"}</span>
        <h3 className="gos-title card-title">{card.title}</h3>
        <div className="card-meta">
          <span className="gos-src">
            {card.size.x} × {card.size.z} blocks · {card.size.y} high · {card.blocks} of {card.max_blocks} blocks
          </span>
          {card.ref && <CitationChip cite={card.ref} />}
        </div>
      </header>
      <p className="gos-sub shell-status" data-testid="shell-status">
        {status}
        {card.theme ? ` · theme ${card.theme}` : ""} · no tile entities, machines' space left clear
      </p>
      <div className="shell-view-row">
        <svg
          className="shell-preview"
          viewBox={drawn.box.map((n) => n.toFixed(2)).join(" ")}
          role="img"
          aria-label={`Shell preview from the ${VIEWS[turns]}`}
          data-testid="shell-preview"
          data-faces={drawn.polys.length}
        >
          {drawn.polys.map((p, i) => (
            <polygon key={i} className={`shell-face role-${roles[p.colour] ?? "block"} ${p.shade}`} points={p.points} />
          ))}
        </svg>
        <Button testId="shell-turn" title="Turn the view a quarter" onClick={() => setTurns((t) => (t + 1) % 4)}>
          ⟳ from the {VIEWS[(turns + 1) % 4]}
        </Button>
      </div>
      <table className="bom shell-palette" data-testid="shell-palette">
        <tbody>
          {card.palette.map((m) => (
            <tr key={m.block}>
              <td>
                <span className={`swatch role-${m.role}`} aria-hidden="true" /> {m.role}
              </td>
              <td>{m.name ?? m.block}</td>
              <td className="gos-src">{m.block}</td>
              <td className="num">{m.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details className="shell-paste" data-testid="shell-paste">
        <summary className="gos-label">Paste it (Lab only)</summary>
        <ol>
          {card.paste.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ol>
      </details>
      {card.notes.length > 0 && (
        <ul className="notes">
          {card.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
