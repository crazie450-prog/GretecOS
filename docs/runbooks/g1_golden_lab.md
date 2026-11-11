# G1: the 30 golden recipes against NEI in the Lab

G1's first criterion (plan §8) is "30 golden recipes checked against NEI in the Lab (100%)". It has two halves:

- **The cloud half** is automatic. `tests/integration/test_golden_recipes.py` checks each golden recipe in a DB built from a client harness run against NEI's own handler dump from the same run: EU/t, duration, every item and fluid amount, and every chance. CI's mod job runs it after every client harness run. The G1 review (`docs/reviews/G1_review.md`) has the result: 30 of 30.
- **The Lab half** is this page. You compare what GOS stored with what NEI shows in the game, on the full pack, including the values NEI's JSON dump doesn't carry: amperage, coil heat, cleanroom, special items and additives.

The golden set is `tests/fixtures/golden/g1_recipes.tsv`. It holds DB keys only. The selection rule is in the test's docstring and D222. Every number you compare comes from your DB through the `show` command below, never from this page.

**Where commands run.** Everything here runs as **gos-dev** in its dev clone (`C:\GregtecOS-dev\GretecOS`) against gos-dev's own GOS_HOME, plus the Lab for looking at NEI. Nothing touches Play. It needs [phase1_pc_checks](phase1_pc_checks.md) steps 1 to 4a first: a full-pack Export run, collected and built into gos-dev's DB.

## 1. The cloud half on the full pack (gos-dev, 2 min)

```powershell
cd C:\GregtecOS-dev\GretecOS
$h = if ($env:GOS_HOME) { $env:GOS_HOME } else { "$HOME\GregtecOS" }
uv run python tests/integration/test_golden_recipes.py report $h
```

- [ ] Expect `golden recipes: 30 of 30 match NEI (100%)`.
- [ ] If any line says `not in the DB's run`, the full pack doesn't have that dev-runtime recipe (another mod changed or removed it). Run the rule on your DB instead and use **that** set for the rest of this page:
  ```powershell
  uv run python tests/integration/test_golden_recipes.py report $h --reselect
  uv run python tests/integration/test_golden_recipes.py select $h
  ```
- [ ] Any `MISMATCH` line comes with both sides (DB and NEI) and the differing numbers. Paste it as is.

## 2. Print the 30 recipes (gos-dev, 1 min)

```powershell
uv run python tests/integration/test_golden_recipes.py show $h > "$HOME\g1_golden.txt"     # add --reselect if step 1 used it
notepad "$HOME\g1_golden.txt"
```

Each recipe has a heading line `gNN  <output name> (<map>, <mod>)`, its key and NEI tab, a line with `eut`, `ticks`, `amperage`, `special_value` and any Lab-only values, then one line per input (`in`) and output (`out`), each with its English name. A `0 x` input is not consumed (a circuit or a mold). Chances are in basis points: 10000 is 100%, 2500 is 25%.

## 3. Compare in the Lab (gos-dev's Lab session, about 45 min)

Launch GTNH-Lab (`uv run gos launch lab`) and open any Lab world. For each recipe, search NEI for the output's name and press **R**. Go to the NEI tab named after the map (for example the Assembler for `gt.recipe.assembler`), then page through it to the recipe whose inputs match.

NEI shows the duration in seconds (ticks ÷ 20) and chances in percent (basis points ÷ 100). Compare:

- EU/t, the duration, and amperage when NEI shows it;
- every input and output, with its amount (mB for fluids) and chance;
- the Lab-only values where NEI's page shows them: coil heat (`coil_heat`), cleanroom, low gravity, special items and additives. `special_value` is GT's own field (coil heat, fusion start energy and the like); NEI shows it on the page for the maps that use it.

For an NEI crafting recipe (g28 shaped, g29 shapeless), compare the grid's items and the output count. For the furnace (g30), the input and output.

| # | Stratum | Recipe | Match? | What differs (NEI next to `show`) |
|---|---|---|---|---|
| g01 | tier ULV | Osmiridium Ingot, alloy smelter (molding) | [ ] | |
| g02 | tier LV | Copper Ingot, arc furnace | [ ] | |
| g03 | tier MV | Saw, assembler | [ ] | |
| g04 | tier HV | Nether Star, autoclave | [ ] | |
| g05 | tier EV | Large Sunnarium Battery, canner | [ ] | |
| g06 | tier IV | Rare Earth (III) Dust, centrifuge | [ ] | |
| g07 | tier LuV | Hastelloy-N Dust, chemical dehydrator | [ ] | |
| g08 | tier ZPM | Firestone, chemical bath | [ ] | |
| g09 | tier UV | Biocells, chemical reactor | [ ] | |
| g10 | multiblock | Algae Biomass, algae pond | [ ] | |
| g11 | multiblock | Molten Laurenium, alloy blast smelter | [ ] | |
| g12 | multiblock | Stable Baryon Containment Unit, beamcrafter (its two particle inputs: NEI shows them as not-consumed stacks; the beam amounts are the Lab's) | [ ] | |
| g13 | multiblock | Iron Ingot, electric blast furnace (coil heat) | [ ] | |
| g14 | multiblock | Duranium Cable, cable map | [ ] | |
| g15 | fluids | Chilly Sauce, brewery | [ ] | |
| g16 | fluids | Fluorine Cell, cold trap | [ ] | |
| g17 | fluids | Moderately Steam-Cracked Propene, cracker | [ ] | |
| g18 | chanced | Neptunium 238 Dust, cyclotron | [ ] | |
| g19 | chanced | Zinc Dust, electrolyzer | [ ] | |
| g20 | chanced | Iron Dust, electromagnetic separator | [ ] | |
| g21 | oredict | Plantball, compressor | [ ] | |
| g22 | oredict | Raw Rubber Dust, extractor | [ ] | |
| g23 | oredict | Charcoal, industrial coke oven | [ ] | |
| g24 | addon | Emitter (UIV), component assembly line (GoodGenerator) | [ ] | |
| g25 | addon | Perfected Waveguide, condensate assembler (TecTech; its two condensate fluids) | [ ] | |
| g26 | addon | Flawless Jasper, electric implosion compressor (bartworks) | [ ] | |
| g27 | addon | Clay, space mining (gtnhintergalactic) | [ ] | |
| g28 | shaped | File (HV), crafting table | [ ] | |
| g29 | shapeless | Hypogen Wire, crafting table | [ ] | |
| g30 | furnace | Uranium 238 Nugget, furnace | [ ] | |

If step 1 used `--reselect`, use `show`'s headings instead of this table's names.

**Paste back:** step 1's first line (and any `MISMATCH` block, or the `--reselect` output), then "30 match" or, for each recipe that differs, its `gNN`, what NEI shows and what `show` printed. G1's "100%" is met when every row matches.
