# WO-3.10: why the MV steel plan isn't buildable (diagnosis and fix plan)

Clayton, 2026-11-22: the steel plan GOS made (the one the shell bake-off builds around) uses an
ore from another planet, takes a roundabout route at MV (into gears, then back into ingots), and
is badly overbuilt. Steel at MV should be a short chain: iron plus coal, coke or carbon.

**Short answer.** The planner never considers iron + coal. It has no machine model for the
Bricked Blast Furnace, so all 36 of its iron + coal → steel recipes are rejected. It ranks recipes
by voltage tier before anything about where their inputs come from. And it treats any item that
no recipe makes as a free raw input, wherever in the game that item really comes from. Those three
together send the graph through the steel *material forms* (nuggets, molten steel, dusts of other
steels) down to whatever item has no recipe. On the harness DB that item is a Twilight Forest
material; on Clayton's full pack it is evidently a space ore. Ranking can't fix this: `rank.py`
only orders the 2-3 routes the solver hands it, and all of them come from the same bad graph.
The size comes from the default target of 1 ingot per second.

## 1. Reproduction

Clayton's DB and saved plans are on his PC (DB and save files are never committed). I reproduced
the plan on the **harness DB**: the dev server harness (`cd mod && ./gradlew runServer -Pharness`,
GT 5.09.54.265 plus bartworks and the other mods the dev runtime loads, but not GTNH's core mod
or Galacticraft), collected and built the way `tests/integration` builds it. The request was the
G3 steel line's: `Request("gregtech:gt.metaitem.01@11305", tier_cap=MV)`, the default 1/s,
`DefaultBaseInputs(MV)`.

The top-ranked plan (`plan.design`, rank 1 of 3, score 0.5465), **71 machines**:

| step | recipe | machine | count |
|---|---|---|---|
| Steeleaf Nugget ×9 → Steeleaf ingot | `r:gt.recipe.alloysmelter/1d5e67877281` | Advanced Alloy Smelter | 10 |
| Steeleaf ingot → Steeleaf dust | `r:gt.recipe.macerator/3a53ad155a71` | Advanced Macerator | 4 |
| Steeleaf dust ×2 → steel dust | `r:gt.recipe.centrifuge/1ca7a69f5307` | Advanced Centrifuge | 16 |
| steel dust → molten steel | `r:gt.recipe.alloyblastsmelter/7fb2ec802e91` | Alloy Blast Smelter | 40 |
| molten steel → steel ingot | `r:gt.recipe.fluidsolidifier/ac1899ccde1f` | Advanced Fluid Solidifier | 1 |

Its only raw input is **Steeleaf Nugget** (`gregtech:gt.metaitem.01@9339`), 18/s. Steeleaf is a
Twilight Forest material: no recipe in the run makes the nugget, so the graph calls it `raw`.
Routes 2 and 3 are the same chain with a polarizer, or with the nugget alloy-smelter step in
place of the solidifier (75 and 79 machines). There is no iron and no coal in any of the three.

This is the same failure Clayton saw, with a different "unreachable raw" at the bottom: the
harness has no Galacticraft, so a Twilight Forest material takes the space ore's place. The
"turn it into gears, then back into ingots" detour is the material-form shuffle above (ingot →
dust → other material's dust → steel dust → molten → ingot). On his pack it may run through
gears, because his DB holds recipes the harness lacks (see §5).

Evidence script: `steel_diag.py` (kept out of the repo, since it reads a DB). The same numbers
come from the CLI, run against any DB:

```
uv run gos plan gregtech:gt.metaitem.01@11305 --tier MV
uv run gos plan gregtech:gt.metaitem.01@11305 --tier MV --alternatives gregtech:gt.metaitem.01@11305 --all
```

## 2. Root causes

Bare file names below (`graph.py`, `plan.py`, `rank.py`, …) are under `core/gregtec/planner/`.
Each root cause was checked by an independent agent trying to refute it, on the same DB.

### R1. The early steel route can't be planned: the Bricked Blast Furnace has no machine model

`core/gregtec/planner/machines.py:883` (`options_for`, kind `None`): a machine class the model doesn't know
is rejected as "a single block with its own recipe check, not modelled (D176)". Checked one by
one, **every one of the 36 `gt.recipe.primitiveblastfurnace` recipes that make a steel ingot**
(for example `r:gt.recipe.primitiveblastfurnace/004d368834c4`: 10 iron dust + 4 blocks of coal)
is rejected with `no machine: Bricked Blast Furnace: MTEBrickedBlastFurnace: … not modelled`. 27 of them take iron (ingot, dust or block) plus a carbon fuel, and 9 remelt steel dust with one. They are the only iron + carbon route to steel in the harness DB: no EBF recipe there takes iron + oxygen or carbon, and no mixer makes steel dust.
Even with a model they would rank last: `graph.py:810-814` flags EU/t-0 recipes `eu0`, and
`_cheap` (`graph.py:657`) puts `eu0` with recycling and container maps, at the very end. That
rule was written for the Eye of Harmony and the neutron activator (D189), not for primitive
machines. In the graph they show as `unchecked` ("below the top 4"), because the machine check
never reaches them.

### R2. The heuristic ranks by voltage tier before it looks at where the inputs come from

`graph.py:650-664` (`_cheap`): after the "ranked last" and "reverse use" flags, the next keys are
*chanced only*, then **`rec.tier`**, then main product, cells and the input count. Nothing
measures how far a recipe's inputs are from something the player can get. So for steel ingot the
tier-0 alloy-smelter (9 nuggets + ingot mould) and solidifier (molten steel) recipes come before
the MV EBF, and only the top 4 are kept (`choose`, `graph.py:974-975`): the EBF magnetic-steel recipe
`r:gt.recipe.blastfurnace/d8d71e0067d5` is `pruned`. For steel dust the top picks are tier-0
centrifuge recipes that *decompose other steels* (Steeleaf, Damascus, Knightmetal).

On equal keys the last tie-break is the recipe id, `rec.id` (`graph.py:646`), `r:<map>/<hash>`:
the recipe map's name in alphabetical order first, then the hash. That is deterministic, but
arbitrary. At three of the five primary nodes the first-ranked recipe was tied with others on
every key but the id: 2 others at steel ingot and at molten steel, and 6 at steel dust (centrifuge,
macerator and multi-centrifuge recipes that take other steels apart; the map names alone kept 4 of
the 7 and pruned 3). At a primary node all of the top 4 expand, so there the order decides what
enters the graph. Off the primary nodes only the first recipe expands (`graph.py:1203`), so the
id order decides the whole branch below (Steeleaf dust from `r:gt.recipe.macerator/3a53ad155a71`).

### R3. A raw input is "anything no recipe makes", with no notion of where it comes from

`graph.py:1170`: an item with no makers is a `raw` boundary, and `plan.py:63` `FREE` makes every
`raw` boundary a free import for the solver (stage 2 weighs raw imports only by a 1e-6 tie-break,
`solve.py:77`, `:317`). A Twilight Forest nugget, a Moon or Mars ore and iron ore all look the
same. The only curated raw list, `data/curated/raw_inputs.tsv`, holds water, lava and air.

GOS *does* hold data that could gate this once it is loaded (`gos ores load`, the quest
watcher), and the planner reads none of it (`grep` finds no
`ore_mix`, `vein` or quest table read under `planner/` except the stage-to-tier map):

- `ore_mix.dims` from `gt_ore_mixes.json` (empty in the harness DB, which never ran `gos ores
  load`; the export file itself has it): every GT vein's dimensions, by name (the harness
  export lists Overworld, Nether, The End, Twilight Forest and space dimensions such as
  `tcetie`), with each layer's ore block (`registry@meta`);
- `vein`: the veins Clayton's world has generated or he has found (VisualProspecting);
- quest progress (`quest_progress`, the stage `gos` derives from it, D64);
- My base stock and line outputs (already boundaries, D63).

There is **no data** in GOS for non-vein sources: mob drops, dimension plants and blocks
(Steeleaf), meteors, space-only items that aren't GT veins. That part can't be gated
automatically without a curated table.

### R4. Items the player *can't* make are imported for free too

`plan.py:63` `FREE` also holds `locked` (every recipe for the item was rejected for this player:
above the tier cap, no machine) and `depth` (the graph stopped at depth 12). Up to now the plan
said nothing about it. Excluding the Steeleaf nugget (`Constraints(exclude=…)`) gives a
61-machine plan that just imports the Steeleaf *ingot* (locked). Excluding the Steeleaf nugget,
ingot and dust (`@9339`, `@11339`, `@2339`) gives a 4-machine "plan" that imports 144 L/s of
molten steel, the item it is meant to make (a gated import, not a locked one). Both looked like
ordinary plans. A third case stays unwarned even with this PR: excluding only the nugget and the
ingot gives a 61-machine plan importing the Steeleaf Buzzsaw Blade (`gregtech:gt.metaitem.02@15339`),
a `raw` item. Raw imports are only gated by F2.

### R5. Recipes that use the target up were treated as ways to make its inputs

`graph.py` `made_from` (the "reverse of one of its uses" rule, D189, review M1) only flags a
recipe when the item it consumes has no way of its own. In GT almost every material form has
one, so steel nuggets *from the steel ingot being planned*, magnetic steel *from steel ingot*,
and molten steel *from the fluid extractor on steel ingot* all ranked as normal ways to make the
target's inputs, and two of them were expanded as primary choices. They are loops back to the
target and can never be a source of it. The solver doesn't run them at the optimum, so in this
plan they only bloated the graph, but they are exactly the "into X and back again" shape.
**Fixed in this PR** (§4).

### R6. Ranking can't recover a route the graph never built

`rank.py` is doing what D198 says: it normalises six metrics among the routes it is given
(`rank.py:195`) and orders them. The three steel routes all came from R1-R3's graph, so the
"least bad Steeleaf route" won. No ranking weight would have produced iron + coal, because no
iron + coal route existed. The `scarcity` metric can't help either. Its table
(`data/curated/scarcity.tsv`) weights a few named materials (the platinum group, the naquadah
chain, rare earths, tungsten, neutronium, helium-3), some of them space-only ores, but it has no
Twilight Forest material and says nothing about where an item comes from. Steeleaf has no row,
so all three steel routes score scarcity 0, and an unlisted space ore would score 0 too. Only a
boundary input whose key or material has a weight counts (`metrics.py:121-146`).

### R7. Size: the default target is 1 ingot per second

`request.py:190` (`qty: float = 1.0`, `/s`) and `plancli.py:69` (`--qty` default `1/s`), and the
G3 steel line in the tests is requested at 1/s. That is 3,600 steel an hour. The machine maths is
GT's and checks out: the Alloy Blast Smelter recipe is 800 ticks at 120 EU/t, which at MV has no
overclock (an overclock needs 4× the EU/t), so 1/s needs 40 of them. The same route at **1/min
is 5 machines**. So the plan is overbuilt twice over: an absurd default rate, multiplied over a
five-step chain where one step would do. Parallels, overclocks and the ceil
(`sizing.py`, `count = ceil(rate × ticks / (20 × parallel))`) are not the problem.

## 3. How it maps to Clayton's plan

| What he saw | Cause |
|---|---|
| An ore that only exists on another planet | R3 (no source gate) + R2 (the hash and tier order picked that branch) + R1 (the iron + coal branch wasn't there to pick) |
| Into gears and back into ingots | R2 + R5: the material-form detour; on his DB, likely through gear or recycling recipes the harness doesn't have |
| Badly overbuilt | R7 (1/s default) × a long chain from R1-R3 |
| iron + coal not offered at all | R1 |

To confirm on his DB rather than mine, Clayton runs the two commands in §1 and pastes the routes
(recipe ids, machines, inputs) and the `--alternatives` list for steel ingot. Nothing in them is
secret: recipe ids and item keys only.

## 4. In this PR (small, preference-free)

1. **Loop guard (R5).** `graph.py:912-919`: for every node but the target, the request's target
   counts as one of the node's "uses". A recipe that consumes the target is ranked as a reversal
   and is a side route, so a primary node doesn't expand it (a pin still can). On the harness
   steel graph it drops two loop recipes (26 → 24: a steel-nugget alloy-smelter recipe and a
   fluid extractor, both using steel ingot up) and leaves the routes as they were: they were
   already loop-free (one steel-dust candidate is also demoted, 8 valid → 7). The reason shown
   is the existing one, "the reverse of one of its uses". An item with a single recipe keeps it, as before (magnetic steel ingot,
   polarised from steel ingot): with one candidate there is nothing to reorder. An ore-dict
   target (`ore:ingotSteel`) isn't covered either: its member items never equal the target key. The unit test `test_cells_container_and_reverse_use_flags` pinned
   the old set; its comment and assertion are updated (Widget is that request's target).
2. **Import warnings (R4).** `plan.py` `_import_warnings`: a plan that imports a `locked` or
   `depth` boundary, or a gated intermediate, says so in `warnings`, after the graph's own
   warnings, so views that show only the first few keep those ("imports X (locked: …); the
   plan doesn't make it"). Nothing is reordered or removed, and the plan JSON schema is
   unchanged (the warnings array already exists).
3. **Tests.** `tests/unit/planner/test_route_sanity.py` (fixture DB, no pack data) covers both.
   `tests/integration/test_steel_route_harness.py` checks the locked-import warning on the
   harness DB (Steeleaf nugget excluded) and holds the **steel-at-MV acceptance test**:
   iron in, carbon in, no Twilight Forest material, at most 10 machines. It is a strict `xfail`
   today, so CI fails the day it starts passing and the mark must come off in that PR.

Neither change touches `gtmath/`, ranking, the solver or the machine model.

## 5. Proposed fixes (approved in D389; F1 built, D390)

Each item is deterministic, uses only data GOS has (or a curated table, like `raw_inputs.tsv`
and `scarcity.tsv`), and comes with tests. The steel-at-MV acceptance test above is the shared
one: F1 + F2 + F3 together should make it pass.

**F1. Model the primitive machines (R1).** *Built (D390): the Bricked Blast Furnace and the
Coke Oven are a `primitive` kind; the harness graph offers the furnace beside the EBF for steel
at MV, with one extra top-4 slot for it.* Add a `primitive` kind to `machines.py` for the
Bricked Blast Furnace (and the Coke Oven and Primitive Pump family where they run recipe maps):
no energy, the recipe's own duration, no overclock, one craft at a time. That is my reading of
GT's behaviour, to be checked against the pinned 5.09.54.265 jar before writing it (no GT code
copied, as D176). Stop flagging those recipes `eu0`, since primitive is not "powers itself some
odd way". Tests: a sizing unit test, and the harness graph shows `primitiveblastfurnace` steel
recipes as valid candidates. *Question 1 decides whether the planner offers it at MV.*

**F2. A raw-source gate (R3).** At graph build, each `raw` item gets a *source*:

- `vein:<dims>` when it is an ore block in `ore_mix` (layers' `item`/`meta`): kept only if one
  of its dimensions is reachable;
- `curated` when it is in `data/curated/raw_inputs.tsv` (extended with a `source` column:
  `world`, `pump`, `mob`, `dimension:<name>`, `crop`…);
- `unknown` otherwise.

Reachable dimensions = a list in `me/prefs.md` (`dims: Overworld, Nether`), defaulting to what
the quests show (Question 2). A recipe whose consumed input is a raw item from an unreachable
dimension is **rejected** with its reason ("Steeleaf Nugget: Twilight Forest, not reachable"),
like a tier-capped one, so a pin can still bring it back and the alternatives list shows why.
`unknown` raws are kept but warned, and rank after a sourced one (F3). Tests: a fixture with a
two-dimension ore mix; the harness steel test.

**F3. Inputs closer to the world first (R2).** In `_cheap`, before `rec.tier`, add a key: whether
the recipe's consumed inputs are all *sourced*, meaning a sourced raw, stock, a line output, or
one recipe step from those. Then replace the hash as the last tie-break by the number of
consumed inputs that aren't sourced, before `rec.id`. This keeps the order deterministic and
needs no item values (§7.1 step 4's "no per-item cost values" holds). It is a ranking-shaped
change to the *graph's* heuristic, not to `rank.py`, so it needs Clayton's yes (Question 3).
Tests: unit fixtures where a tier-0 detour and a tier-1 direct recipe compete.

**F4. Imports the player can't make are not free (R4).** Make `locked` and `depth` boundaries
*gated* for the solver instead of free, so a route that makes everything beats one that imports
what can't be made. Where no such route exists, the plan still solves and keeps the PR's warning.
Tests: the fixture case in `test_route_sanity.py`, which then expects the non-importing route
first.

**F5. A "simplest route" mode (R2, R6).** `routes: simplest`: the graph keeps only each node's
first recipe after F1-F3, and the result is one route with the fewest steps among them. That is
the D11 "simplest at the tier" promise made literal. It is offered next to the 2-3 ranked routes
rather than replacing them, unless Clayton prefers it as the default (Question 4).

**F6. Sane sizing defaults (R7).** A missing rate becomes a **batch**: `qty` items, with the
ETA reported, instead of 1/s. Or the default rate becomes a smaller one, Clayton's choice
(Question 5). Either way the plan's name already shows the rate ("· 1/s ·"); the Designer should
put it in front of him before solving. Tests: request defaults, CLI default.

Order of work: F1 → F2 → F3 → F4 (each is its own PR with its tests), then F5 and F6, which are
small.

## 6. Clayton's idea: a model that reviews the recipes

A Sonnet or Haiku worker, with Opus as advisor, reads the routes and reduces the plan to "what
makes the most logical sense".

**What the rules allow today** (CLAUDE.md, D198): a model may *explain* routes, and may *flag*
one as suspicious. It may also ask the planner to re-run with constraints: exclude a recipe or
an item, pin a node, change the rate. Every number still comes from the planner. **Not allowed:**
the model picking or reordering the routes, or writing quantities.

| | Flag + constrained re-run (allowed now) | Model picks or edits the route (needs a new decision) |
|---|---|---|
| Catches "this needs Mars" | yes: it names the item, the planner re-runs with it excluded | yes |
| Numbers | always the planner's | at risk: the model edits a plan whose numbers it can't check (the §7.1 validator catches only numbers in text) |
| Same request, same answer | yes (the re-run is deterministic; the flag can be logged) | no: model output varies run to run |
| Fixes R1, R2 and R7 | no: it can only exclude, so it would exclude its way to the *next* bad route (R4's 4-machine molten-steel import is what excluding Steeleaf leads to today) | partly, by guessing recipes the graph never had, which is exactly what the rules forbid |
| Cost | API money per plan (Clayton's OK first) | same, or more with an Opus advisor |

My recommendation is the deterministic fixes (F1-F4) first. They make the right route *exist*,
which no reviewer can do. After that, an optional "sanity reviewer" in the allowed form is a
cheap safety net: it reads the plan card, flags any item it thinks is out of reach or any step
that loops, and proposes `exclude`/`pin` constraints. Clayton confirms, and the planner re-runs.
Going further (a model choosing routes) needs a new decision row, approved by him; I don't
recommend it.

## 7. Questions for Clayton

**Answered 2026-11-23: D389.** In short: Bricked Blast Furnace alongside the EBF; dimensions from the
quest book; sourcing before tier; simplest route as an extra option; a batch by default; unknown
raws warned and ranked last; a model route reviewer as a per-request toggle (Sonnet, escalating to
Opus) that may request re-runs and edit a route, and says when it did.

1. **Primitive machines at MV.** Should an MV steel plan offer the Bricked Blast Furnace route
   (iron + coal/coke, slow, no power) alongside the EBF, or only from the stone/steam stages,
   with the EBF route (iron + oxygen, or iron + carbon where your pack has it) at LV+?
2. **What counts as reachable.** Which dimensions can GOS assume today: Overworld and Nether
   only? Should it read the quest book for the rest (Moon after the tier-1 rocket quest, and so
   on), read the veins you've actually found (`vein`), or should you list them in
   `me/prefs.md`?
3. **Ranking.** May the graph's heuristic put "inputs closer to the world" before voltage tier
   (F3)? It changes which recipe every node starts with, across all lines, not just steel.
4. **Simplest route.** A separate `simplest` option next to the ranked routes, or the default?
5. **Default size.** With no amount given: a batch (how many?), or a small rate (say 1/min)?
6. **Unknown raws.** Items no recipe makes and GOS has no source for (mob drops, dimension
   plants, meteors): warn only, rank last, or reject until you add them to the curated table?
7. **The model reviewer.** Do you want the allowed "flag + constrained re-run" reviewer (it
   spends API money per plan), and on which lane? Anything beyond that needs a decision row from
   you.
8. **Your plan's routes.** Please run the two commands in §1 on your DB and paste the output,
   so I can confirm the space ore and the gear detour on your recipes.
