# Gregtec OS

A local voice assistant and planning suite for **GT New Horizons 2.9**. It designs production and power lines from your own instance's recipe data, answers recipe, quest and wiki questions with cited numbers, watches the base live through OpenComputers, and shows everything on a second-monitor dashboard and a phone.

**Status:** under construction. Phases 0–2 are merged. Phase 3 (GT math, machine model, graph builder, `gos plan`), Phase 5 (voice: push-to-talk, speech-to-text, text-to-speech, wake word) and Phase 7 (OC agent, M1 mailbox, OC bridge) have landed. The remaining work orders are in [docs/implementation_plan.md](docs/implementation_plan.md) §8.

## Docs

| Doc | What it is |
|---|---|
| [docs/implementation_plan.md](docs/implementation_plan.md) | The spec. §2 decisions are binding; §8 holds the work orders and phase gates. |
| [docs/decisions.md](docs/decisions.md) | Append-only log of decision changes after v1.1 (D78 onward). |
| [docs/research/](docs/research/) | Research reports A–I. The plan cites them as `[A§5.4]`. |
| [docs/reviews/QA_review_v1.0.md](docs/reviews/QA_review_v1.0.md) | QA review of plan v1.0, with Clayton's answers (became D41–D77). |
| [docs/design/README.md](docs/design/README.md) | The approved dashboard design (D92): which boards are approved, the design system copy in `docs/design/system/`, and the WO-2.6 handoff steps and kickoff prompt. |
| [docs/design/age_themes.md](docs/design/age_themes.md) | Dashboard themes per voltage age (D81): which theme goes with each age, the rules every theme keeps, and the ship order. |
| [docs/runbooks/core_service.md](docs/runbooks/core_service.md) | Starting the core service at login, and the WO-2.1 checks on the PC: restarts, Host/Origin, pairing, `gos launch` (V24). |
| [docs/runbooks/pc_setup_notes.md](docs/runbooks/pc_setup_notes.md) | PC setup gotchas from the Phase 0 runbook, the SDL3 startup crash and its fallback, and when OpenComputers logs its filtering rules. |
| [docs/runbooks/dashboard.md](docs/runbooks/dashboard.md) | Dashboard install, first pairing, monitor 2 and the PC checks (WO-2.6). |
| [docs/runbooks/setup_wizard.md](docs/runbooks/setup_wizard.md) | The first-run setup wizard, which configures GOS from the dashboard alone (WO-2.9). |
| [docs/runbooks/recipe_lookup.md](docs/runbooks/recipe_lookup.md) | Recipe lookup PC steps and the 50-question `gos eval recipe-lookup` run (WO-2.7). |
| [docs/runbooks/voice_client.md](docs/runbooks/voice_client.md) | Voice client: talk key, microphone and acceptance checks (WO-5.1). |
| [docs/runbooks/stt.md](docs/runbooks/stt.md) | Speech-to-text: the GPU, the models and the entity-accuracy eval (WO-5.2). |
| [docs/runbooks/tts.md](docs/runbooks/tts.md) | Text-to-speech: engines, the latency bench, the listening check and ducking (WO-5.3). |
| [docs/runbooks/wake.md](docs/runbooks/wake.md) | The wake word: the phrase, false wakes and hits (WO-5.4). |
| [docs/runbooks/oc_agent.md](docs/runbooks/oc_agent.md) | OC agent Lab steps (WO-7.2, WO-7.3). |
| [CLAUDE.md](CLAUDE.md) | Working rules for Claude Code. |

Dashboard mockups are designed in Claude Design (D79). Claude Code builds each view from the approved canvas through the Claude Design handoff, starting at WO-2.6.

## How work happens

- **Roles:** Claude Code builds, Clayton reviews, tests and merges, and a Claude PM agent maintains the plan.
- **Work orders:** one work order (WO) = one branch `wo/<id>-<slug>` + one PR with a summary, tests run, evidence, VERIFY items touched and open questions.
- **Gates:** each phase ends at a gate (G#). An independent reviewer agent reports, then Clayton signs off.
- **Phase 0 order:** WO-0.1 → WO-0.8 setup → WO-0.7 (build Play) → WO-0.3 → WO-0.8 tests → the rest.
- **Changing a decision:** append a `D##` entry to `docs/decisions.md` that names what it supersedes.

## Licence

Gregtec OS's code is MIT (`LICENSE`); GregtecLink's is too (`mod/LICENSE`, with `mod/LICENSE-ExampleMod` for the template parts). Pack-derived data keeps its own licence where it says so, e.g. `data/curated/tier_quests.tsv` (CC BY-NC-SA 4.0, from GTNH's quest book).
