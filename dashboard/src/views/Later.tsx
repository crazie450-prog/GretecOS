// A view whose work order hasn't landed yet: its designed empty state, so the nav and its keys
// already sit where they will stay.
import type { View } from "./nav";
import { GLYPH } from "../state/states";

const JOB: Record<string, string> = {
  designer: "Compare 2-3 routes, then build one.",
  power: "Am I in deficit, at which tier?",
  quests: "What can I do next?",
  crops: "What should I breed next, and how?",
  base: "What's broken? What do I have?",
  activity: "What happened this session?",
  ores: "Where's the nearest vein?",
  build: "How do I build this multi?",
};

export function Later({ view }: { view: View }) {
  return (
    <div className="view-later" data-testid={`view-${view.id}`}>
      <div className="view-head">
        <h1 className="page-title">{view.label}</h1>
        <p className="lede">{JOB[view.id] ?? ""}</p>
      </div>
      <section className="gos-panel">
        <span className="gos-label">{GLYPH.todo} Not built yet</span>
        <p className="gos-sub">
          {view.label} arrives with {view.later}. Until then, Search and Chat answer from the same data.
        </p>
      </section>
    </div>
  );
}
