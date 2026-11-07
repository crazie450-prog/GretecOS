// The header (design system: spacing and layout): wordmark, search, status, tier and clock.
import { useEffect, useState } from "react";
import type { CoreState } from "../api/types";
import { clock } from "../state/format";
import type { StateChip } from "../state/states";
import { TierChip, stageTier } from "./core";

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const { hm, s } = clock(now);
  return (
    <div className="clock" aria-label="time">
      {hm}
      <span className="seconds">{s}</span>
    </div>
  );
}

export function Header(props: {
  chips: StateChip[];
  core: CoreState | null;
  glance: boolean;
  onSearch: () => void;
}) {
  const tier = props.core?.tier;
  return (
    <header className="shell-header" data-testid="header">
      <div className="wordmark">
        GREGTEC <span className="slash">/</span> OS
      </div>
      {!props.glance && (
        <button type="button" className="search-box" onClick={props.onSearch} data-testid="open-palette">
          <span>Search items, recipes, quests, settings</span>
          <kbd>Ctrl K</kbd>
        </button>
      )}
      <div className="status-chips" data-testid="status-chips">
        {props.chips.map((c) => (
          <span key={c.id} className={`state-chip tone-${c.tone}`} title={c.detail} data-state={c.id}>
            <span aria-hidden="true">{c.glyph}</span> {c.label}
          </span>
        ))}
      </div>
      {tier && (
        <div className="header-tier" title={tier.how === "override" ? "your tier override (D64)" : "from your quest progress (D64)"}>
          <TierChip tier={stageTier(tier.stage)} />
        </div>
      )}
      <Clock />
    </header>
  );
}
