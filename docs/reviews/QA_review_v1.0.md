# Gregtec OS — QA Review of Implementation Plan v1.0

Nov 1, 2026 · @Clayton

## Verdict

**Do not approve as written.** The engineering underneath (data
pipeline, MILP planner, read-safety, OC bridge) is strong and well
researched. The plan fails on three fronts you care about most: the
dashboard has no UX spec at all, the scope rule "everything before day
one" pushes your start date to roughly February 2027 or later, and
several features break in real use (phone links that can't open, alerts
from a world that isn't running, an export that relaunches GTNH every
time you quit).

Confidence tags used below: **Confirmed** (checked against source or the
plan's own text), **Likely** (strong reasoning, not tested), **Verify**
(needs a test or your answer).

### Top 10 must-fix before approval

1.  **Dashboard has no design.** §7.14 is an 11-row list of view names.
    No layout, states, interaction model or design system, and dashboard
    work is scattered across 8 WOs. Add a design WO with approved
    mockups before WO-2.6. (Confirmed)

2.  **D05 "everything first" is the wrong gate.** OC needs MV, the HTTP
    bridge needs HV/EV, themed buildings matter mid-game. None of it is
    usable on day one. Gate day one on P0–P3 (+P5 voice) and deliver the
    rest before you reach the tier that needs it. (Confirmed from §7.9
    unlock table)

3.  **The 2.9.0 pin will be stale before day one.** Hotfixes (2.9.1+)
    will almost certainly ship in the 3–4 month build window. The
    pack-update playbook is in Phase 11; it must move to Phase 1.
    (Likely)

4.  **No-LLM fallback.** If the Anthropic API is down or credits run
    out, the whole assistant dies even though recipe lookups are pure
    database work. Per Anthropic's docs, requests simply stop when
    credits run out. (Confirmed)

5.  **Phone "plan link" can't open.** §7.12 says /plan replies with a
    plan link, but the dashboard is bound to 127.0.0.1 and never
    exposed. (Confirmed, internal contradiction)

6.  **Phone alerts are mostly meaningless in single-player.** The world
    doesn't tick when the game is closed. Unless you AFK with the game
    running, "power low" alerts can only fire while you're already at
    the PC. (Confirmed logic; depends on Q6)

7.  **Post-exit auto-export runs on every quit.** §7.13 launches a
    second GTNH instance (minutes of load, many GB of RAM) after every
    session, and nothing stops you relaunching Play into a two-client
    collision. Make it conditional on a fingerprint change and add a
    lock. (Confirmed from §7.13 wording)

8.  **"Machine built" detection likely can't identify machines.** All GT
    machines share one block (gt.blockmachines); the machine identity
    lives in the tile entity, which a block-placed log line probably
    doesn't carry. Use a server-side event listener in GregtecLink
    instead. (Likely, Verify)

9.  **You can't override a sub-step recipe.** Alternatives only branch
    at "primary choices." Deep choices (how you get hydrogen, sulfuric
    acid) are fixed by heuristic with no way to pin or swap a node and
    re-solve. That's the feature you'll use most. (Confirmed)

10. **Route ranking is done by a model.** Principle 1 says numbers come
    from code, but §7.1 lets the Opus planner rank routes. Same request,
    different order on different days. Rank with a deterministic,
    weighted score (as power best mode already does); let the model only
    explain. (Confirmed)

## Scope and schedule

The 85–125 hour estimate counts only your review and test time, and it
assumes Claude Code delivers each work order right the first time. Both
assumptions are optimistic.

| \#  | Severity | Finding                                                                                                                                                                                             | Fix                                                                                                                                                  |
|-----|----------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| S1  | Critical | D05 makes every phase a day-one blocker, including features that unlock at MV, HV/EV or later. A fresh GTNH world spends many hours in stone/steam age before any of it matters.                    | Day one = P0–P3 + P5. P4, P6–P10 ship "just in time" against your actual tier, still Lab-validated first. Playing becomes real-world testing.        |
| S2  | High     | The hours estimate ignores rework. Every Java change to GregtecLink means you launch GTNH (minutes per load) and test by hand. The Lab, the mod and the voice stack need you at the keyboard.       | Add a 1.5× rework buffer. Make V22 (unattended export) and a headless runServer test harness Phase 1 priorities so Claude Code can test without you. |
| S3  | High     | Pin drift. Target is GT 5.09.54.265 / GTNH 2.9.0, released 2026-10-31. A hotfix in the build window is near-certain and forces a re-export, re-port check and re-validation.                        | Move the pack-update playbook and pin-bump procedure from WO-11.2 to Phase 1. Decide now: stay on 2.9.0 or follow 2.9.x (Q4).                        |
| S4  | High     | Claude Code usage limits aren't in the risk table. Development runs on your Max subscription limits (the API credits don't cover Claude Code). A project this size can hit weekly limits and stall. | Add R20: plan limit throttling. Track Claude Code usage per phase; sequence heavy WOs across limit windows.                                          |
| S5  | Medium   | Your rotating 8-on/6-off night schedule makes "5–10 h/week" an average of very uneven weeks. Gates that need you in game will cluster on off-weeks.                                                 | Schedule gates on your off-blocks; let streams queue PRs during on-weeks.                                                                            |
| S6  | Medium   | P10 (building design) is 12–18 h and depends on img2schem. It isn't needed until you build permanent mid-game factories.                                                                            | Move P10 to post-day-one, target "before HV."                                                                                                        |
| S7  | Medium   | WO-6.2 (custom EndlessIDs region decoder) is high effort for a backup signal.                                                                                                                       | Move to backlog; replace with the GregtecLink event listener (D6 below).                                                                             |
| S8  | Low      | Who "the team" is is ambiguous. The plan says Claude Code builds and you review; your message says it goes to a PM and dev team.                                                                    | Answer Q1. If the PM is a Claude agent, add a WO acceptance-review step that isn't the same agent grading itself.                                    |

**Bottom line:** with S1 adopted, the day-one scope is P0–P3 + P5 =
38–54 of your hours by the plan's own table, about 8–11 weeks at 7.5
h/week with a 1.5× rework buffer. The full plan with the same buffer is
128–188 hours, 4–6 months. The remaining features arrive while you're in
steam age.

## Architecture, billing and reliability

The biggest gap is that nothing degrades gracefully: any API, credit or
process failure takes the whole assistant down. I checked the pricing
and credit claims against Anthropic's docs; the prices are correct, the
credit amount isn't quite.

| \#  | Severity | Finding                                                                                                                                                                                                                           | Fix                                                                                                                                            |
|-----|----------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------|
| A1  | Critical | **No offline / no-LLM mode.** Credits exhausted, spend limit hit, or API outage = no answers at all. Anthropic's docs say requests stop until the next credits arrive if the organization has none left.                          | Degraded mode: instant commands, the Search view, recipe cards and BOMs work straight from SQLite with no model. Show a "model offline" badge. |
| A2  | High     | **Credit amount depends on your tier.** The plan says Max includes \$100/month. That's Max 5x; Max 20x gets \$200. (Confirmed)                                                                                                    | Answer Q7; set monthly_alert_usd to match.                                                                                                     |
| A3  | High     | **D16 "uncapped" collides with the workspace spend limit.** A spend limit is a hard stop for both lanes, not a "pause and ask."                                                                                                   | Enable auto-reload of purchased credits, or set the limit well above expected use and alert at 50/80/100%. Your call (Q8).                     |
| A4  | High     | **Deep-lane cost estimate looks low.** \$0.12 per Sonnet task assumes a short loop. Orchestrator + Opus planner + tool-search round trips + vision can run several times that. Labelled an estimate, but budgets are set from it. | Measure in WO-2.4 with 10 real design tasks before setting task_max_usd.                                                                       |
| A5  | High     | **No process supervisor or health view.** Core, voice and Discord are separate processes. Auto-start and crash recovery arrive in P11; until then a crashed voice process fails silently mid-game.                                | Move a minimal supervisor + /health endpoint + dashboard health strip to P2. Voice says "core offline" if it loses the core.                   |
| A6  | High     | **Play doesn't exist yet, but everything is "a copy of Play."** WO-0.3 copies Play to make Export and Lab; the fingerprint is computed on Play; yet G11 says "the Play instance is built" at the end.                             | Build Play in P0 with base 2.9.0 + whatever addons load, no world. Treat addon updates as normal pack updates.                                 |
| A7  | Medium   | **RAM.** GTNH plus a second GTNH (Export) plus Whisper, Kokoro, browser, Obsidian on 32 GB is tight, and the post-exit export can overlap a relaunch.                                                                             | Export only when stale (fix \#7), take a lock, and refuse to launch Play while Export runs. Measure peak RAM in WO-5.7.                        |
| A8  | Medium   | **Localhost security holes.** A token on every client is good, but a web page in your browser can still reach 127.0.0.1 (DNS rebinding, CSRF). The plan doesn't say how the dashboard gets its token.                             | Check the Host header, refuse non-local Origins, keep the token in an HttpOnly cookie set by the core. Add it to WO-2.1 tests.                 |
| A9  | Medium   | **Nightly maintenance on a clock.** You work nights, and the core only runs when the PC is on.                                                                                                                                    | Run maintenance on idle (game closed + 30 min no input), not at a fixed hour.                                                                  |
| A10 | Medium   | **Obsidian may be dead weight.** The vault is "agents-first," no prose, TSV; you only edit me/ and personas/. The web dashboard plus \_dash.md is two human dashboards.                                                           | If you won't browse Obsidian (Q14), keep the vault as plain folders + git and drop \_dash.md.                                                  |
| A11 | Low      | **Licence contradiction.** PM10 talks about keeping "the repo's licence open," but PM05 makes it a private repo.                                                                                                                  | Pick one; it's personal use either way.                                                                                                        |
| A12 | Low      | **Model claims to re-check at build time.** Haiku 5.5 thinking-off behaviour and TTFT are flagged V21, which is good. Prices (\$0.10/\$0.50 under 100k, 5× above) match the docs. (Confirmed)                                     | Keep V21; re-check prices in WO-0.4.                                                                                                           |

## Data, planner and game integration

The planner math is sound; the problems are in what it assumes it knows
about your base and in what you can control.

| \#  | Severity | Finding                                                                                                                                                                                                                                                                                                    | Fix                                                                                                                                                                                                |
|-----|----------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| D1  | Critical | **No manual override of sub-steps** (must-fix \#9).                                                                                                                                                                                                                                                        | Designer: click any node, see its alternative recipes, pin one, re-solve. Pins are saved in the plan JSON.                                                                                         |
| D2  | High     | **Model-ranked routes** (must-fix \#10).                                                                                                                                                                                                                                                                   | Deterministic score with weights in me/prefs.md, shown with every result, as power best mode already does.                                                                                         |
| D3  | High     | **The "boundary" assumes data that doesn't exist early.** "Stocked" items come from AE2 (only visible via OC at HV/EV) or base/storage.md, which is agent-owned and has no input path. Chests aren't readable from saves cheaply. Your tier (base/tier.md) and owned machines also have no defined source. | Dashboard "My base" panel: set tier, tick owned machines, add stock manually; auto-fill from quests and placement events where possible (Q23, Q24).                                                |
| D4  | High     | **The numeric validator will false-reject.** "About 2k EU/t" vs 1920, per-second vs per-minute, Roman tiers (IV), item names with digits ("Tier 2 circuit"), counts derived from metrics. No rule for what happens after a rejection.                                                                      | Spec normalisation (units, rounding ±1%, tier names, item-name masking), retry once, then fall back to a templated explanation.                                                                    |
| D5  | High     | **"Cite or don't claim" is too strict for general advice.** "What's a good early power option?" has no r: reference, so the agent must refuse. Wiki pages aren't in the citation list either.                                                                                                              | Add w:\<page\>@\<revid\> citations and an explicit "general advice, uncited" label for opinions. Numbers stay strictly cited.                                                                      |
| D6  | High     | **Machine identity on placement** (must-fix \#8). gt.blockmachines is one block for all machines; the specific machine is in the tile entity. ServerUtilities' block log probably can't tell an EBF from a macerator. (Likely, Verify V06)                                                                 | GregtecLink gets a server-side BlockEvent.PlaceEvent listener that reads the placed ItemStack's damage (the machine ID). No items, blocks or mixins, so it fits D32. Region scan moves to backlog. |
| D7  | Medium   | **Windows file locking has a direct fix.** §5.5 relies on "read fast, retry" because Python's open() blocks the game's save-by-rename. Opening with FILE_SHARE_DELETE (CreateFile via ctypes or pywin32) removes the conflict outright. (Likely)                                                           | Add a safe_open() helper with share-delete; keep the retries as backup. Applies to the OC mailbox too.                                                                                             |
| D8  | Medium   | **Recipe signature may collide or churn.** "sha256 of sorted ids and amounts" omits EU/t, duration and output chances. Two recipes in one map that differ only in those collide.                                                                                                                           | Include eut, duration, chance_bp and special conditions in the signature; add a collision test over the full export.                                                                               |
| D9  | Medium   | **Accepted plans silently go stale after a pack update.** The addon watcher diffs recipes but never cross-references accepted plans.                                                                                                                                                                       | Flag any plan whose r: keys changed or vanished; show "affected by update" on the plan and in the diff note.                                                                                       |
| D10 | Medium   | **Effort ignores raw-material scarcity** (D17 excludes gathering). A route needing thousands of platinum can rank as "low effort."                                                                                                                                                                         | Optional scarcity term in the weighted score (Q22). Raw inputs per hour are already reported.                                                                                                      |
| D11 | Medium   | **STT mistakes become confident wrong answers.** Pre-retrieval resolves the item before the model sees it. A misheard item yields a well-cited answer about the wrong thing.                                                                                                                               | On a low-confidence match, say "Steel or stainless steel?" and show the top 3 on the dashboard.                                                                                                    |
| D12 | Medium   | **Benchmark references may disagree with each other.** G3 checks against gtnhplanner.com "or" PlanNH, which may run on different data versions.                                                                                                                                                            | The Lab single-machine run is the tie-breaker; record which reference was used.                                                                                                                    |
| D13 | Low      | **Plan IDs aren't human.** pl-0007 is fine in data, bad in speech and UI.                                                                                                                                                                                                                                  | Auto-name plans ("Steel line · 2/s · HV"), editable.                                                                                                                                               |
| D14 | Low      | **Golden-set ownership.** G3 lists "others Clayton picks" for the 10 benchmark lines.                                                                                                                                                                                                                      | Pick them now (Q25) so fixtures can be built in parallel.                                                                                                                                          |

## Voice, Discord and safety

The voice stack is well chosen; what's missing is how GTNH vocabulary
sounds coming out, and how the phone channel behaves in single-player.

| \#  | Severity | Finding                                                                                                                                                                                                                          | Fix                                                                                                                                                                                |
|-----|----------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| V1  | High     | **TTS will mangle GTNH words and read citations aloud.** Kokoro won't know "LuV," "UHV," "EBF," "naquadah," and nothing strips r:gt.recipe.blastfurnace/3fa2… from speech.                                                       | Add a say column to glossary.tsv (pronunciation lexicon); a speech filter strips citations and formats numbers ("nineteen-twenty EU per tick"). Test in WO-5.3.                    |
| V2  | High     | **WER is the wrong STT metric.** 8% WER can still miss the one word that matters (the item).                                                                                                                                     | Acceptance = entity accuracy: item/machine resolved correctly in ≥ 95% of 50 utterances. Keep WER as secondary.                                                                    |
| V3  | High     | **Voice "confirm" needs a guard.** If "confirm" is an instant command, saying it casually in Discord while an action is pending could fire it. With two pending actions it's ambiguous.                                          | "Confirm" counts only after the talk key or wake word, and the voice reads back the action first ("Craft 64 steel. Confirm?"). One pending action at a time, or confirm by number. |
| V4  | High     | **The Discord plan link is unreachable** (must-fix \#5).                                                                                                                                                                         | Either attach everything (PNG + BOM file) and drop the link, or add a private read-only remote view through Tailscale (Q13).                                                       |
| V5  | High     | **Phone alerts with no running world** (must-fix \#6).                                                                                                                                                                           | If you don't AFK, scope phone alerts to job-done and system-health; base alerts go to voice/HUD only.                                                                              |
| V6  | Medium   | **!gos chat commands from other players.** On the possible future server, anyone could type !gos into chat; chat, wiki text and quest text all reach the agent. Actions are confirm-gated, but you'd still get injected answers. | Accept !gos only from your username; treat chat, wiki and screen text as untrusted data in prompts.                                                                                |
| V7  | Medium   | **Headset model.** Your earlier notes say SteelSeries Arctis "Elite"; that's probably the Arctis Nova Elite. Game/Chat routing depends on it (V11).                                                                              | Confirm the model (Q16).                                                                                                                                                           |
| V8  | Medium   | **The mouse X2 talk key may already be bound** in your GTNH controls, and a pynput hook that doesn't suppress it will also trigger the game bind.                                                                                | Pick a key unbound in both GTNH and Discord (Q17).                                                                                                                                 |
| V9  | Low      | **Discord bot needs Clayton-only guards on buttons too.** Confirm buttons in a shared channel can be clicked by anyone with access.                                                                                              | Check the clicker's user ID on every interaction, not just slash commands.                                                                                                         |
| V10 | Low      | **Personas.** The plan already says the "dungeon announcer" pack must be original. Good; keep that rule in the template.                                                                                                         | No change.                                                                                                                                                                         |

## Dashboard: what it should be

Design the dashboard as two modes of one shell: a **glance surface while
you play** and a **workbench when you don't**. Your mouse is captured by
GTNH, so anything that needs a click on monitor 2 means leaving the
game. The plan treats the dashboard as a page of views; it needs to be
designed around that constraint.

### What the "agentic OS" look actually is

The demos you've been seeing share a handful of patterns, and they're
well documented:

- **Persistent shell.** Sidebar views on number-key shortcuts, an
  agent-status panel and a voice strip with a glowing standby ring, as
  in the open-source [Mission
  Control](https://21st.dev/@jacobharris0505/components/mission-control)
  personal-agent shell.

- **Ambient presence.** Agent state shown through low-attention signals
  (colour, shape, a pulse): quiet when idle, louder when active,
  readable from the corner of your eye ([AI UX
  Playground](https://aiuxplayground.com/pattern/ambient-presence-displays)).

- **Visible approval checkpoints.** Progress states that show what the
  agent is doing, plus a stop point before any irreversible action
  ([dev.to, 2026 UI
  patterns](https://dev.to/zdeb_fouc_f95089367652fc5/from-agent-checkpoints-to-spatial-widgets-five-uiux-patterns-that-look-poised-to-define-2026-m3i)).

- **Controlled generative UI.** The agent picks and fills components
  from a fixed catalog the developers built, rather than generating
  screens ([AI Accelerator
  Institute](https://www.aiacceleratorinstitute.com/nobody-wants-another-chatbot-7-interface-architectures-worth-building/)).

- **Step-level override with confidence.** You see what the agent
  proposed, how sure it is, and can change one step without redoing the
  rest (same source).

*[Wireframe of the workbench-mode shell: see the Word version of this review.]*

Workbench-mode shell · wireframe, not a visual design

The right rail never goes away: whatever the agent is doing, waiting on,
or asking you to approve is always visible, whichever view is open.

### Two modes, switched automatically

| Mode          | When                                                | What shows                                                                                                                                                          |
|---------------|-----------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Glance**    | GTNH has focus                                      | Large type. Latest answer card, pending approval (with countdown), active alerts, power net per tier, current goal + next step, voice orb. No interaction required. |
| **Workbench** | Mouse enters monitor 2, game paused, or game closed | Full shell above: views, Designer, tables, 3D viewer, settings.                                                                                                     |

In-game control of the dashboard goes through voice ("show route 2,"
"open BOM") and two or three global hotkeys (confirm, cancel, next
card). Ctrl+K opens a command palette in workbench mode for items,
recipes, quests, plans and settings.

### Rules the dev team should build to

1.  **Fixed component catalog.** The agent returns structured cards
    (recipe card, route comparison, BOM table, flow diagram, quest card,
    ore list, power ledger, approval card, alert). It never returns
    HTML. This keeps every answer consistent and testable.

2.  **Provenance on every number.** A small citation chip beside each
    number; hover opens the recipe card. A "data stale" badge shows when
    the pack fingerprint doesn't match.

3.  **One colour language.** Dark theme by default. GT voltage-tier
    colours used for tiers everywhere (cards, flow nodes, ledger bars).
    One accent for "needs you." Status colours only for status. Tabular
    figures for numbers.

4.  **Every state designed.** Loading, empty, stale data, model offline
    (degraded mode), game not running, OC disconnected, export running.
    No blank panels.

5.  **Spoilers stay nudge-first visually.** Content beyond your frontier
    is blurred until you click or say "spoil it."

6.  **A performance budget.** Glassmorphism blur, particles and constant
    glows cost GPU while GTNH is running. Animations only on state
    change; the 3D viewer renders only when visible; 30 fps cap in
    glance mode. L7 must measure FPS with the dashboard open.

7.  **A dedicated window, not a browser tab.** Launch it as an app
    window (Chrome --app mode) that opens on monitor 2 at startup. No
    tabs or URL bar, so it reads as an OS panel, and background-tab
    throttling doesn't apply.

8.  **A first-run setup wizard.** Paths, API key, Discord token, headset
    endpoints, talk key and wake phrase in the GUI, with gos doctor
    results shown as a checklist. Replaces the CLI-only setup.

### View-by-view

| View               | Primary job                                                                     | Must-have interaction                                                            |
|--------------------|---------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| **Now** (new home) | What should I do next? Goal, advisor's 3 steps, power net, alerts, running jobs | One click from a step to its plan or quest                                       |
| Chat               | Conversation as cards, not raw text                                             | Re-open any past card full-size                                                  |
| Designer           | Compare 2–3 routes, then build one                                              | Side-by-side route cards with differences highlighted; swap/pin any node; accept |
| Power              | Am I in deficit, at which tier?                                                 | Generation vs use bars per tier; click a line to see its draw                    |
| Base               | What's broken right now?                                                        | Stalled machines sorted first; pending actions                                   |
| Activity           | What happened this session?                                                     | Session timeline; jump to journal                                                |
| Quests             | What can I do next?                                                             | Frontier cards, spoiler blur, link to items                                      |
| Ores               | Where's the nearest X?                                                          | Distance-sorted list + simple map; waypoint (confirmed)                          |
| Build              | How do I build this multi?                                                      | 3D viewer with layer slider and per-layer materials                              |
| Costs              | Am I within credits?                                                            | Month-to-date vs credits, by lane                                                |
| Settings + Health  | Is everything running?                                                          | Process status, last export, VERIFY list, re-run doctor                          |

The exact look (sci-fi HUD vs clean modern vs GregTech industrial) is
your call; see Q11. A clickable mockup should be approved before any
view is built.

## Required plan changes

These are the edits I'd require in v1.1 before sign-off. Each maps to a
finding above.

| Change                                                                                                                                                  | Where         | Finding          |
|---------------------------------------------------------------------------------------------------------------------------------------------------------|---------------|------------------|
| Replace D05 with a phased readiness bar: day one = G0–G3 + G5; later gates tied to your in-game tier                                                    | §2.1, §8, §10 | S1               |
| **New WO-0.7:** build Play (base 2.9.0 + loadable addons, no world) before copying Export/Lab                                                           | Phase 0       | A6               |
| **Move** pack-update playbook + pin-bump procedure from WO-11.2 to **new WO-1.9**                                                                       | Phase 1       | S3               |
| **Move** minimal supervisor, /health, health strip from WO-11.2 to **WO-2.1**                                                                           | Phase 2       | A5               |
| **New WO-2.0:** dashboard design system + information architecture + clickable mockups of Now, Designer and glance mode; Clayton approves before WO-2.6 | Phase 2       | Must-fix \#1     |
| **New WO-2.8:** degraded no-LLM mode (Search, recipe cards, BOMs, instant commands work without the API)                                                | Phase 2       | A1               |
| **New WO-2.9:** first-run setup wizard in the dashboard                                                                                                 | Phase 2       | Dashboard rule 8 |
| WO-2.1 acceptance adds Host/Origin checks and cookie-based token                                                                                        | Phase 2       | A8               |
| WO-2.4 acceptance adds measured cost of 10 real design tasks; set task_max_usd from it                                                                  | Phase 2       | A4               |
| §7.1 step 8: deterministic weighted ranking; model explains only                                                                                        | Phase 3       | D2               |
| WO-3.7 acceptance adds node swap/pin + re-solve in the Designer                                                                                         | Phase 3       | D1               |
| WO-3.5 numeric validator: normalisation spec + fallback behaviour                                                                                       | Phase 3       | D4               |
| §5.2 step 3: signature includes eut, duration, chances, special conditions; collision test                                                              | Phase 1       | D8               |
| §7.13: export only on fingerprint change; instance lock; addon diff flags affected plans                                                                | Phase 1 / 11  | Must-fix \#7, D9 |
| **New WO-3.8:** "My base" panel (tier, owned machines, manual stock) feeding the boundary                                                               | Phase 3       | D3               |
| WO-5.2/5.3: entity-accuracy metric; pronunciation lexicon; speech filter for citations                                                                  | Phase 5       | V1, V2           |
| WO-5.6/7.1: voice confirm only after talk key/wake, with read-back                                                                                      | Phase 5 / 7   | V3               |
| **Replace WO-6.2** (region decoder) with a GregtecLink server-side placement listener; region scan to backlog                                           | Phase 6       | D6               |
| §7.12: drop the plan link or add a Tailscale read-only view; scope phone alerts to your AFK answer                                                      | Phase 9       | V4, V5           |
| Move Phase 10 to post-day-one (target: before HV)                                                                                                       | §8            | S6               |
| Add R20 (Claude Code plan limits), R21 (API outage), R22 (pin drift) to §12                                                                             | §12           | S4, A1, S3       |
| Add a safe_open() share-delete helper to the read protocol                                                                                              | §5.5          | D7               |
| Add w: wiki citations and an "uncited general advice" label                                                                                             | §0.1          | D5               |

## Questions for Clayton

The eight marked **Blocking** change the plan's structure; answer those
first. Short answers are fine; reply inline under each.

### Scope and schedule

1.  **Blocking.** Who are the "project manager" and "development team":
    people, Claude agents, or both? Is the PM the same agent that wrote
    this plan?

2.  **Blocking.** Will you accept a phased day one: start your world
    after the designer, Q&A and voice pass (about 8–11 weeks), with OC,
    building and the rest delivered before you reach the tier that needs
    them?

3.  Do you have a target start date, or friends waiting to start with
    you?

4.  **Blocking.** When 2.9.1 or later ships during development, do you
    stay on 2.9.0 or follow hotfixes?

5.  **Blocking.** Server or single-player (D06)? Will friends ever play
    in your world? This changes the watchers, !gos, spoilers and alerts.

6.  **Blocking.** Do you leave the game running unattended (AFK,
    chunk-loaded, overnight)? If not, phone base alerts have almost
    nothing to report.

### Billing

7.  **Blocking.** Max 5x or Max 20x? That's \$100 vs \$200 of monthly
    API credits.

8.  When credits run out mid-month: auto-buy more, hard stop, or
    degraded no-LLM mode? What monthly amount would make you
    uncomfortable, even with D16 "uncapped"?

### Dashboard

9.  While playing, will you ever move the mouse to monitor 2, or should
    the dashboard be fully hands-off in game (voice + hotkeys only)?

10. Monitor 2: size, resolution, landscape or portrait? Touchscreen?

11. **Blocking.** Look and feel: (a) sci-fi HUD, Jarvis style; (b) clean
    modern, like Linear or Raycast; (c) GregTech industrial, with tier
    colours and machine textures; or a mix? Please send 2–3 links to the
    YouTube videos you mean.

12. Is a dedicated app window (Chrome app mode, opens on monitor 2 at
    startup) fine, or do you want a real installed desktop app?

13. Do you want to view plans and the dashboard from your phone (private
    Tailscale link, read-only), or is Discord enough?

14. Will you actually open Obsidian? If not, the vault becomes a hidden
    backend.

15. What should the "Now" home screen lead with: next steps, power,
    alerts, or current goal?

### Voice

16. Headset: is it the SteelSeries Arctis Nova Elite? Do you use its
    Game/Chat mix dial?

17. Which key or mouse button is free in both GTNH and Discord for
    talk-to-GOS? Hold or toggle by default?

18. OK that "confirm" by voice only works right after the talk key or
    wake word, with a read-back first?

19. In the wake-word path your friends hear the question. Acceptable, or
    talk-key only while you're in voice chat?

20. Which persona is the default, and do you want any premium (cloud)
    voice at all?

### Designer and power

21. Do you want to swap any sub-step recipe by hand and re-solve?
    (Recommended: yes.)

22. Should raw-material scarcity count against a route in ranking, or
    stay excluded per D17?

23. Before OC is online, how should GOS know your stock: manual entry,
    ignore stock, or read from a chest you designate?

24. How should your tier be set: auto from quest progress, or you set
    it?

25. Name the 10 benchmark lines now (four are listed; six are "Clayton
    picks").

26. Do you use gtnhplanner.com, PlanNH or gtnh-flow today? Which export
    matters most?

27. For best power mode, rough priorities: more EU, less effort, fewer
    upkeep inputs, or simpler builds?

### Game integration

28. Is 2.9.0 installed yet? Java 8 or Java 17+ build?

29. Is it OK for GOS to launch a second GTNH instance after you quit,
    only when mods changed?

30. OK for GregtecLink to add a server-side placement listener (no
    items, blocks or mixins) for exact "machine built" events?

31. Which proactive notices do you want on from day one, if any?

32. Spoiler level: one global setting, or per questline?

### Building

33. Is themed building a day-one need, or fine by the time you reach HV?

34. Where is img2schem now? Can the team read its SOW or repo (V17), and
    who owns shell design?

### Operations

35. Where will Claude Code run: directly on your gaming PC, and with
    what permissions?

36. When something breaks mid-session, how do you want to hear about it:
    voice, a dashboard banner, Discord, or all three?

37. Should GOS auto-start at Windows login, or start when you launch the
    Play instance?

## Clayton's answers (2026-11-01)

These answers are binding for v1.1 and should be logged as new D##
entries. Q3 (target date) is still open; Q33 is settled by "everything
first," so building design ships before day one.

| Topic                | Answer                                                     | Plan impact                                                                                                                        |
|----------------------|------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| Team (Q1)            | Claude PM + Claude Code; Clayton reviews                   | Add an independent reviewer agent per gate so the PM isn't grading its own work                                                    |
| Day one (Q2)         | **Everything first** (D05 stands)                          | S1 rejected; schedule stays ~4–6 months with buffer. Still sequence work by tier so early features get the most testing            |
| Pack pin (Q4)        | Decide per update                                          | Playbook in Phase 1; every new 2.9.x shows a diff + "adopt?" prompt                                                                |
| Hosting (Q5)         | Single-player now; keep it portable to a home server       | Keep the §4.2 relocation design; no server work before day one                                                                     |
| AFK (Q6)             | Often                                                      | Phone base alerts stay in scope (V5 withdrawn)                                                                                     |
| Max tier (Q7)        | Max 5x, \$100 credits                                      | monthly_alert_usd = 100                                                                                                            |
| Credits out (Q8)     | Message me and let me decide                               | Discord DM at 80% and 100% with buttons: buy more · no-AI mode · stop. Run in no-AI mode until answered                            |
| In-game use (Q9)     | Sometimes uses the mouse on monitor 2                      | Glance mode must also be clickable; switch on mouse entry                                                                          |
| Monitor 2 (Q10)      | 1440p landscape                                            | Design target 2560×1440                                                                                                            |
| Look (Q11)           | Clean modern + GT tier colours                             | Design system WO-2.0 uses this                                                                                                     |
| Window (Q12)         | Chrome app window on monitor 2                             | As recommended                                                                                                                     |
| Phone (Q13)          | **Full control from phone**                                | Tailscale-only remote dashboard with confirm buttons; Discord stays for alerts                                                     |
| Obsidian (Q14)       | Rarely; keep as backend                                    | Drop \_dash.md                                                                                                                     |
| Home screen (Q15)    | Dynamic mix of next steps, base status and last answer     | "Now" view reprioritises by context: alerts first when something's wrong, last answer right after a question, next steps otherwise |
| Headset (Q16)        | Arctis Nova Elite, uses the Game/Chat dial                 | TTS to the Chat endpoint (V11 closed)                                                                                              |
| Talk trigger (Q17)   | **Wake word mostly**                                       | Wake-word accuracy becomes a top-priority acceptance test                                                                          |
| Discord mic (Q19)    | Friends hear everything                                    | Skip Discord auto-mute (WO-5.5 shrinks)                                                                                            |
| Confirm (Q18)        | **Plain "confirm" anytime**                                | See pushback below                                                                                                                 |
| Persona (Q20)        | Gruff GT veteran default; premium voice optional           | persona.default = gt-veteran                                                                                                       |
| Swap steps (Q21)     | Yes, any node                                              | D1 accepted                                                                                                                        |
| Scarcity (Q22)       | Adjustable weight                                          | D10 accepted                                                                                                                       |
| Stock (Q23)          | Manual entry; assume nothing stocked by default            | "My base" panel, WO-3.8                                                                                                            |
| Tier (Q24)           | Auto from quests, overridable                              | As recommended                                                                                                                     |
| Benchmarks (Q25)     | All four groups: early, mid, power, late                   | Benchmark set grows to about 18 lines; G3 hours go up                                                                              |
| Export target (Q26)  | gtnhplanner.com                                            | Primary export; PlanNH spike can be dropped                                                                                        |
| Power priority (Q27) | Balanced                                                   | Equal default weights                                                                                                              |
| Install (Q28)        | Not sure                                                   | gos doctor detects; recommend the Java 17+ build                                                                                   |
| Auto-export (Q29)    | Ask each time                                              | Prompt on quit (dashboard + voice) when mods changed                                                                               |
| Mod listener (Q30)   | Yes                                                        | D6 accepted                                                                                                                        |
| Notices (Q31)        | Power deficit / machine stalled; plan buildable from stock | Those two on by default                                                                                                            |
| Spoilers (Q32)       | Global nudge-first                                         | As planned                                                                                                                         |
| Shells (Q34)         | **GOS owns shell design**                                  | V17 resolved; see pushback                                                                                                         |
| Claude Code (Q35)    | **Gaming PC, full access**                                 | See pushback                                                                                                                       |
| Failures (Q36)       | Dashboard banner, voice line, in-game chat/HUD             | No Discord for failures                                                                                                            |
| Startup (Q37)        | Manually                                                   | See pushback                                                                                                                       |

### Pushback on four answers

1.  **Plain "confirm" anytime, plus a wake word and an open mic.**
    You'll say "confirm" in normal conversation, and with friends
    hearing everything you'll be talking a lot. A false confirm could
    run an AE2 craft that eats a stack of rare materials. Minimum guard
    that costs you nothing: GOS always reads the action back, only one
    action can be pending, and it expires after 60 s. I'd still require
    the wake word before "confirm."

2.  **Claude Code with full access on the PC that holds your real
    world.** That breaks the plan's own rule that the play world is
    sacred. One bad script or path mix-up can corrupt a survival save
    you've put hundreds of hours into. Give it full access to the repo,
    Export and Lab, and add deny rules for the Play instance and saves/.
    It costs no speed.

3.  **GOS owns shells.** That pulls img2schem's whole design job into
    GOS and makes Phase 10 the biggest phase. It also leaves two
    projects maintaining the same schematic and palette code. If
    img2schem is still alive, keep the shared package and let one of
    them be the engine, not both.

4.  **Manual start vs AFK alerts and phone control.** If you forget to
    start GOS, the AFK alerts and phone control you asked for silently
    don't exist. Start on Play launch via the Prism hook, with a manual
    off switch.

**Resolved after pushback (same day):**

- **Confirm:** plain "confirm" stays, but GOS always reads the action
  back, allows one pending action, and expires it after 60 s.

- **Claude Code:** full access on the gaming PC, with deny rules for the
  Play instance and its saves/.

- **img2schem:** merged into GOS. Phase 10 absorbs the img2schem SOW
  (Claude-designed builds, palette from the client); the separate
  project retires. The PM must re-estimate Phase 10.

- **Startup:** GOS auto-starts on Play launch via the Prism pre-launch
  hook, with a manual off switch.

## Sources

- [API credits for Max and Team
  plans](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers):
  \$100 (Max 5x) / \$200 (Max 20x); no rollover; requests stop when
  credits run out

- [Claude Haiku 5.5 pricing and the 100K
  tier](https://letsdatascience.com/blog/claude-haiku-5-5-cut-prices-90-prompts-over-100k-pay-5x):
  \$0.10/\$0.50 under 100K, \$0.50/\$2.50 above

- [GT-New-Horizons-Modpack
  releases](https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/releases):
  2.9.0 released 2026-10-31

- [Mission Control personal-agent dashboard
  shell](https://21st.dev/@jacobharris0505/components/mission-control)

- [Ambient presence
  displays](https://aiuxplayground.com/pattern/ambient-presence-displays)

- [Five UI/UX patterns poised to define
  2026](https://dev.to/zdeb_fouc_f95089367652fc5/from-agent-checkpoints-to-spatial-widgets-five-uiux-patterns-that-look-poised-to-define-2026-m3i)

- [Seven AI agent interface
  architectures](https://www.aiacceleratorinstitute.com/nobody-wants-another-chatbot-7-interface-architectures-worth-building/)

- The plan itself: implementation_plan.md v1.0, 2026-11-01 (section
  references throughout)

Not independently verified: the plan's GTNH-internal claims (OC config
keys, VisualProspecting paths, worldedit-gtnh issues, addon status).
They rely on its research reports A–H, which I didn't have.
