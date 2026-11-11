// Draws any catalog card. A card is checked against the shared schema first: one that fails is
// never drawn (agents return data, never HTML; plan 7.14 rule 1), and a renderer that throws is
// caught, so one bad card can't take a view down.
import { Component, type ReactNode } from "react";
import { AlertView, ApprovalView } from "../components/core";
import { GLYPH } from "../state/states";
import { useCardActions } from "./context";
import {
  BomView,
  ChoicesCardView,
  FlowView,
  HealthView,
  OreListView,
  PowerLedgerView,
  QuestCardView,
  RecipeCardView,
  RouteComparisonView,
  TableCardView,
} from "./renderers";
import type { Card as CardData } from "./types.gen";
import { validate } from "./validate";

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  render(): ReactNode {
    if (this.state.failed) {
      return <CardProblem why="it couldn't be drawn" />;
    }
    return this.props.children;
  }
}

function CardProblem({ why }: { why: string }) {
  return (
    <div className="card card-problem" data-card="refused" role="note">
      <span className="gos-label">
        {GLYPH.failed} Card not shown
      </span>
      <p className="gos-sub">This card isn't in the catalog: {why}.</p>
    </div>
  );
}

function Draw({ card, compact }: { card: CardData; compact: boolean }) {
  const actions = useCardActions();
  switch (card.type) {
    case "recipe":
      return <RecipeCardView card={card} compact={compact} />;
    case "quest":
      return <QuestCardView card={card} compact={compact} />;
    case "table":
      return <TableCardView card={card} compact={compact} />;
    case "choices":
      return <ChoicesCardView card={card} />;
    case "route_comparison":
      return <RouteComparisonView card={card} />;
    case "bom":
      return <BomView card={card} />;
    case "flow":
      return <FlowView card={card} />;
    case "ore_list":
      return <OreListView card={card} />;
    case "power_ledger":
      return <PowerLedgerView card={card} />;
    case "approval":
      return <ApprovalView card={card} onAction={(c, a) => actions.act?.(c, a)} />;
    case "alert":
      return <AlertView card={card} />;
    case "health":
      return <HealthView card={card} />;
  }
}

export function Card({ card, compact = false }: { card: unknown; compact?: boolean }) {
  const problems = validate(card);
  if (problems.length) return <CardProblem why={problems[0]} />;
  return (
    <Boundary>
      <Draw card={card as CardData} compact={compact} />
    </Boundary>
  );
}
