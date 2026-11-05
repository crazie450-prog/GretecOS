# Gregtec OS

A local voice assistant and planning suite for **GT New Horizons 2.9**. It designs production and power lines from your own instance's recipe data, answers recipe, quest and wiki questions with cited numbers, watches the base live through OpenComputers, and shows everything on a second-monitor dashboard and a phone.

**Status:** planning is done (plan v1.2, 2026-11-01). The build starts at WO-0.1. Nothing here runs yet.

## Docs

| Doc | What it is |
|---|---|
| [docs/implementation_plan.md](docs/implementation_plan.md) | The spec. §2 decisions are binding; §8 holds the work orders and phase gates. |
| [docs/decisions.md](docs/decisions.md) | Append-only log of decision changes after v1.1 (D78 onward). |
| [docs/research/](docs/research/) | Research reports A–I. The plan cites them as `[A§5.4]`. |
| [docs/reviews/QA_review_v1.0.md](docs/reviews/QA_review_v1.0.md) | QA review of plan v1.0, with Clayton's answers (became D41–D77). |
| [docs/design/README.md](docs/design/README.md) | The approved dashboard design (D92): which boards are approved, the design system copy in `docs/design/system/`, and the WO-2.6 handoff steps and kickoff prompt. |
| [docs/design/age_themes.md](docs/design/age_themes.md) | Dashboard themes per voltage age (D81): which theme goes with each age, the rules every theme keeps, and the ship order. |
| [CLAUDE.md](CLAUDE.md) | Working rules for Claude Code. |

Dashboard mockups are designed in Claude Design (D79). Claude Code builds each view from the approved canvas through the Claude Design handoff, starting at WO-2.6.

## How work happens

- **Roles:** Claude Code builds, Clayton reviews, tests and merges, and a Claude PM agent maintains the plan.
- **Work orders:** one work order (WO) = one branch `wo/<id>-<slug>` + one PR with a summary, tests run, evidence, VERIFY items touched and open questions.
- **Gates:** each phase ends at a gate (G#). An independent reviewer agent reports, then Clayton signs off.
- **Phase 0 order:** WO-0.1 → WO-0.8 setup → WO-0.7 (build Play) → WO-0.3 → WO-0.8 tests → the rest.
- **Changing a decision:** append a `D##` entry to `docs/decisions.md` that names what it supersedes.
