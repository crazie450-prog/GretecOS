// The phone's "More" tab (WO-9.3, D159, D302): every view past its five tabs, one tap each, with
// the question it answers (plan 7.14's "primary job"). Built to the design system; no approved
// board yet, so it waits for Clayton's (D92, D158 (5)).
import { MORE_VIEWS, type ViewId } from "./nav";

const JOB: Partial<Record<ViewId, string>> = {
  designer: "Compare routes, then build one",
  power: "Am I in deficit, at which tier?",
  quests: "What can I do next?",
  crops: "What should I breed next, and how?",
  base: "What's broken? What do I have?",
  activity: "What happened this session?",
  ores: "Where's the nearest vein?",
  build: "How do I build this multi?",
};

export function More(props: { open: (view: ViewId) => void }) {
  return (
    <div className="view-more" data-testid="view-more">
      <div className="view-head">
        <h1 className="page-title">More</h1>
        <p className="lede">Every other view, the same as on monitor 2.</p>
      </div>
      <ul className="more-list gos-panel" data-testid="more-list">
        {MORE_VIEWS.map((v) => (
          <li key={v.id}>
            <button type="button" className="more-item" onClick={() => props.open(v.id)} data-testid={`more-${v.id}`}>
              <span className="gos-title">{v.label}</span>
              <span className="gos-sub">{JOB[v.id] ?? ""}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
