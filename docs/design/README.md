# Dashboard design: what's approved and how it reaches Claude Code

WO-2.0 is done in Claude Design (D79) and Clayton approved it on 2026-11-05 (D92). This page says which boards are approved, where the design system lives, and how Claude Code picks the design up at WO-2.6.

## Sources

| What | Where | Notes |
|---|---|---|
| Mockup canvas "Gregtec OS dashboard mockups" | Claude Design, on Clayton's account | The boards. Claude Code gets them through the handoff below, never from screenshots (§7.14). |
| Design System "Gregtec OS" | Claude Design, on Clayton's account. A copy is installed on the canvas (`ds/gregtecos/`), so it travels with the handoff | Source of truth for tokens, type, spacing and the core components. |
| [system/](system/) | This repo | A copy of the design system's `README.md`, `tokens.json` and `components/bundle.css`, taken on 2026-11-05 (artifact version 1793906662-0453). Refresh it whenever the design system changes. `tokens.json` holds design tokens (colours, type, spacing), not secrets. |
| [age_themes.md](age_themes.md) | This repo | The age themes spec (D81): which theme goes with each age, switching, pinning and the ship order. |

## Approved boards (D92)

| Canvas row | Boards | Built in |
|---|---|---|
| Command HUD v0.4 | Workbench · Now, Glance mode, Workbench · Designer | WO-2.6 (Now, glance), WO-3.7 (Designer) |
| Workbench views | Power, Quests, Settings + Health, Crops | WO-4.6, WO-6.4, WO-2.6, WO-6.7 |
| First run, states and phone | First-run setup wizard, States, Phone · Now, Phone · plan from a Discord link | WO-2.9, WO-2.6 and every later view, WO-9.3 |
| Ages 1 to 5, Ages 6 to 10 | One Now board per age theme | WO-2.6 (switching); ship order in [age_themes.md](age_themes.md) |
| Comfort modes | Calm Ops, Field Manual | WO-2.6 |

**Not approved:** the "Feature concepts" row (What's this?, Shopping list, Hazard watch, Tier ladder, Boots like a multiblock). Don't build from those boards. Any concept Clayton picks later gets its own decision and work order.

**No board yet:** Chat, the Ctrl+K command palette and Costs (all in WO-2.6), Activity (WO-6.3), Ores (WO-6.6), My base and the Base view (WO-3.8, WO-7.4) and Build (Phase 10). Each gets a board and Clayton's approval before its work order builds it (§7.14).

## The design system in one minute

- **12 themes, one set of names.** Each theme's colours are tokens named `<slug>-<role>` (`brass-panel`, `holo-needs`). `components/bundle.css` maps each theme onto the same semantic variables (`--bg`, `--panel`, `--text`, `--needs`, …) under a `.theme-<slug>` class. Components read only the semantic variables, so switching theme never touches a component. No class means the Command HUD.
- **Slugs:** `brass` (Steam), `machine` (LV), `alu` (MV), `hud` (HV), `circuit` (EV), `tung` (IV), `holo` (LuV), `fusion` (ZPM), `naq` (UV), `deep` (UHV and up), plus the comfort modes `calm` and `manual`.
- **Shared tokens:** GT tier colours (`tier-*`, from GT 5.09.54.265 `TIER_COLORS`), `needs-you`, spacing `space-1`…`space-9`, layout sizes (`board-width`, `nav-width`, `rail-width`, `phone-width`, `hit-min`) and the type scale (`type-*`).
- **Contrast:** every text pair passes 4.5:1 in all 12 themes except `text-3`, which is for disabled controls and decoration only (the table is in `system/README.md`).
- **Fonts:** all Google Fonts under the OFL. The dashboard bundles them with their licence files and never loads them from Google at runtime.

## Handing off at WO-2.6

Clayton does this when Claude Code reaches WO-2.6, after Phase 1:

1. Open the canvas in Claude Design.
2. **Export → Send to Claude Code Web**, and pick `crazie450-prog/GretecOS`. ("Send to Claude Code" does the same for a local session.)
3. Paste the kickoff prompt below with it.

After WO-2.6 lands, `/design-sync` brings the real components back into Claude Design, so later boards are mocked from them (D79).

### Kickoff prompt

```text
WO-2.6: build the dashboard shell from this Claude Design handoff.

Read first: CLAUDE.md, docs/implementation_plan.md §7.14 and the WO-2.6 row in §8,
docs/design/README.md, docs/design/age_themes.md and docs/design/system/README.md.

- Build only from boards approved in D92 (docs/design/README.md). Ignore the
  "Feature concepts" row.
- Scope: glance and workbench modes, the Chrome app window (D52), the shell (nav,
  right rail, voice strip), Now, Settings + Health, and the states on the States
  board that these views need. Chat, the Ctrl+K palette and Costs are in WO-2.6
  but have no boards yet: if the handoff has no board for one, build the route
  and a placeholder, and ask in the PR's Questions section.
- Themes: colours, type, radii and spacing come from docs/design/system/tokens.json
  through the .theme-<slug> mapping in components/bundle.css. Never copy colour
  values out of the boards by hand. Ship ages 1 to 3 and both comfort modes first
  (D81 ship order); switching follows tier detection (D64), with pinning and the
  tier-up moment.
- Agents return cards, never HTML. Every game number keeps its citation chip.
- Bundle the fonts locally with their OFL licence files.
- Acceptance: matches the boards at 2560x1440; switching themes never moves
  anything; every theme passes the contrast rule; approvals and status stay
  distinguishable in all 12 themes.

One WO = one branch wo/2.6-<slug> + one PR.
```
