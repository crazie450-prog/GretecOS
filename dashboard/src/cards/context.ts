// What a card may do beyond drawing itself: open a citation, nudge a quest, act on an approval,
// pick a candidate. The view that shows the card provides these.
import { createContext, useContext } from "react";
import type { ApprovalCard } from "./types.gen";

export interface CardActions {
  stale: boolean; // recipe data doesn't match Play: chips become "data stale" badges
  openCite?: (cite: string) => void;
  openQuest?: (ref: string, spoil: number) => void;
  act?: (card: ApprovalCard, action: string) => void;
  pick?: (n: number) => void;
}

export const CardContext = createContext<CardActions>({ stale: false });

export function useCardActions(): CardActions {
  return useContext(CardContext);
}
