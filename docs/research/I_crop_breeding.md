# I. Crop breeding in GTNH 2.9.0 (CropsNH)

`2026-11-01 · for D78 and plan §7.17 · method: read-only shallow clones at the 2.9.0 pins (CropsNH 2.0.140, GT5U 5.09.54.265, OpenComputers 1.12.68-GTNH, NewHorizonsCoreMod 2.9.99, modpack tag 2.9.0), the 2.9.0 quest book and the official changelogs. wiki.gtnewhorizons.com was unreachable, so this report makes no wiki claims. Nothing was run from the repos.`

**Headline (CONFIRMED):** GTNH 2.9.0 replaced IC2 crops with **CropsNH**, and CropsNH removed OpenComputers support on purpose. The IC2-era OC breeding bots therefore don't work on 2.9. Automation in 2.9 is meant to come from CropsNH's own machines.

## 1. Which crop system

- **CONFIRMED:** 2.9.0 swapped IC2 crops for CropsNH 2.0.140. Crops++, CropLoadCore and the IC2 Crop-Breeding Plugin were removed; IC2 2.2.2.828 stays for its other content. CropsNH has been in every 2.9 pre-release since beta-1 (2026-06-07). [M][C9]
- **CONFIRMED:** CropsNH calls itself a "complete replacement for the IC2 crop system" (it began as an Agricraft fork). It keeps crop sticks and Growth/Gain/Resistance, now 1–31 each. IC2 crop blocks and crop-stick items in old worlds are converted automatically. [A][P]
- **CONFIRMED:** The Extreme Industrial Greenhouse lost its IC2 mode; CropsNH seeds aren't the item type it accepts. The replacement is CropsNH's **Industrial Farm** multiblock, MV and up. [G][A]
- **CONFIRMED:** 2.9 also removed the IC2 Cropnalyzer and Weeding Trowel recipes. 2.8 had only small crop changes. [C8][C9]

## 2. How breeding works in CropsNH

- **CONFIRMED:** Right-click a crop stick onto an empty one to make a double stick. Only north/south/east/west neighbours count, and parents must be at least 80% grown. [Q][A]
- **CONFIRMED:** Crops update every 256 ticks; a double stick tries to breed with a 1/3 chance per update (pack config). On each attempt: 50% it copies one neighbour's species; otherwise a matching parent-combination recipe (these take priority); otherwise a random member of a mutation pool shared by two or more parents. **There is no IC2-style tier or attribute similarity rule.** The child appears only if it can grow on that stick (soil and requirements). [A][P][Q]
- **CONFIRMED:** A child's stat is the parents' average (rounded down) plus a random step, set by the lowest fertilizer level among the contributing parents [P][Q][A]:

  | Fertilizer | Several parents | One parent (copying) |
  |---|---|---|
  | 0–49 | −1 to +1 | −1 or 0 |
  | 50–100 | ±2 | no change |
  | 101+ | 0 to +4 | 0 to +4 |

  Fertilizing by hand caps at 100 (bone meal gives 30), so 101+ needs a Crop Manager (cap 200).
- **CONFIRMED (penalties):** a mature crop with high Growth and Resistance below Growth spreads weeds and sickness to neighbours; Weed-EX or a Resistance roll protects the target. Resistance has no downside: seed drop chance without a spade is Resistance/31, and Resistance 31 makes a crop immune to sickness. Empty single or double sticks grow weeds at 1/100 per update.
- **UNCERTAIN (sources disagree):** the Growth threshold for weeds. The quest says above 23 (Necrobloom above 7); the code says above 24 (Necrobloom above 8). [Q][A]
- **CONFIRMED (requirements):** a soil type, plus any required sub-soil block directly beneath the soil block (natural ores count); minimum or maximum light for some crops, and some crops breed only in the Crop Breeder; nutrients (base 5, up to +10 from water, up to +10 from fertilizer, +2 for sky access, +14 per liked biome tag up to 2, or up to +14 from humidity; higher tiers need more, and too little makes the crop sick); water only keeps farmland wet, while crops get water from rain, a watering can or a Crop Manager; no height requirement. [A][Q][C9]
- **CONFIRMED (tools):**
  - Reading stats: the Plant Lens (early) reads planted crops; the Portable Scanner also shows the nutrient score; the GT Scanner (LV, 8 s) reads seed items.
  - Tending: the Spade (steel) and Reinforced Spade (MV) clear weeds and give bonus seeds; Weed-EX and Plant Cure sprays; the Crop Manager (LV and up, radius 3 + 2×tier) supplies water, fertilizer and Weed-EX and can harvest.
  - Machines: the Crop Breeder sets stats to the parents' average, succeeds 40% of the time at LV (+10% per tier), destroys the parents on failure, and needs HV for 31/31/31. Material crops at MV and above breed only in the Crop Breeder. Also the Seed Generator, Gene Extractor and Crop Synthesizer. [Q][A]
- **CONFIRMED:** NEI has Crop Breeding and Mutation Pool tabs, plus CSV dumpers for crops, parent-combination recipes, mutation pools and soils. They are a ready data feed for the planner. [A]

## 3. OpenComputers crop-bot programs

- **CONFIRMED:** The known program is DylanTaylor1/GTNH-CropAutomation (MIT, 2023; no releases; last commit 2026-01-22). It descends from xyqyear/auto-crossbreeding (Unlicense) via huchenlei/auto-crossbreeding (MIT); the forks by mderoulou, Mozzg and iamgqr are MIT. [R][F]
- **CONFIRMED: none of them work on 2.9.0.** The bot's scanner only recognises IC2 crop blocks and reads the stats OC's geolyzer reports for them. OC 1.12.68-GTNH reports stats only for IC2 crops, and the CropsNH README says OC integration was "intentionally removed". All 6 repos checked handle IC2 crops only. [R][O][A][F]
- **UNCERTAIN:** whether any fork has been ported to CropsNH (GitHub blocked listing all forks).
- **CONFIRMED (IC2 era, for reference):** the bot needed a Tier 2 robot (Tier 2 case and memory, Tier 1 APU, internet card, geolyzer, inventory and inventory-controller upgrades), an Electronics Assembler and Charger, a spade, a Transvector Binder and Dislocator, a crop-stick chest, a 6×6 working farm and a 9×9 storage farm. Modes: autoTier, autoStat (Growth + Gain − Resistance ≥ 52), autoSpread. README warnings include teleport accidents with the dislocator, crops over the stat limits being destroyed, and no detection of running out of crop sticks. [R]
- **UNCERTAIN (idea only):** the 2.9.0 OC config sets `allowItemStackNBTTags=true`, so a custom program might read stats from seed items in a chest. It still can't scan planted crops. [P]

## 4. Progression tier for an OC robot

- **CONFIRMED:** the bot's robot is a mid-to-late HV build: the Tier 2 case, Electronics Assembler and Charger need an HV assembler and an advanced epoxy board; the Tier 1 APU needs an EV circuit; the geolyzer and inventory upgrades are MV. [N][R]
- **CONFIRMED:** the 2.9.0 quest book has no robot, geolyzer or charger quest (3,814 quests searched). [Q]

## 5. Breeding by hand in 2.9

1. Craft plenty of crop sticks. Till farmland with the spade, keep water nearby, and put any needed sub-soil block directly under the farmland.
2. Alternate parent crops on single sticks with empty double sticks. Each double stick should touch only the parents you want: one species to copy it, or all 2–4 parents of a recipe.
3. Keep parents at 50+ fertilizer; use a Crop Manager to push past 100 for stat gains.
4. Use Weed-EX, pull weeds at once, and spray sick crops with Plant Cure.
5. Check every child with the Plant Lens; keep the best and replace the weakest parent. Keep Growth at 23 or below unless Resistance is at least as high. Collect seeds with the spade.

## 6. CropsNH breeding machines (addendum)

Read from the CropsNH 2.0.140 source (`tileentity/singleblock/*.java`, `items/ItemGenericSeed.java`, `en_US.lang`) [A]:
- **CONFIRMED: Crop Breeder** (from LV): its tooltip says it "breeds crops deterministically".
  - **Recipe choice:** it looks up the deterministic mutations for the loaded parents, trying recipes with more parents first.
  - **Inputs:** it uses 1 of each parent, any catalyst, and enriched fertilizer (144 mB × output tier + 72 mB × (Growth + Gain + Resistance)).
  - **Success:** 40% at LV, +10% per tier, capped at 100% (ZPM). On a failure the inputs are used up and nothing comes out.
  - **Output:** the child's stats are the parents' average.
  - **Limits:** 3 input slots below HV, 6 from HV. At LV and MV, the tooltip says it "cannot breed high-stat seeds".
- **CONFIRMED: Seed Generator** (from LV): "generates more seeds from an existing seed".
  - It outputs a copy with the same stats and costs enriched fertilizer per stat point.
  - Some crops need a duplication catalyst; it skips seeds that need the synthesizer.
  - At LV and MV it can't copy high-stat seeds.
- **CONFIRMED: Crop Gene Extractor** (from EV): extracts one gene to a data orb. Programmed circuit 1 = species, 2 = Growth, 3 = Gain, 4 = Resistance.
- **CONFIRMED: Crop Synthesizer** (from EV): makes seeds from data orbs, with stats taken from the orbs, paid in UUM per tier and per stat point. The tooltip on crops that can't spread to other sticks points to it for improving or copying them.
- **CONFIRMED:** a seed item's display name carries its species, so OpenComputers can identify seeds by label without reading NBT.
- **UNCERTAIN:** the exact "high-stat" cap at LV and MV, and whether OC's GT machine driver reads CropsNH machines. They are GT MetaTileEntities, so it should.

## VERIFY in game

1. Whether Growth 24 spreads weeds, and whether Necrobloom spreads at 8.
2. That the geolyzer returns no crop data on a CropsNH stick, and whether seed-item NBT exposes stats to OC.
3. The LV Crop Manager's range, and that parents really reach 101+ fertilizer.
4. Whether extra neighbouring species block a parent-combination recipe.
5. Whether crossing two identical seeds can trigger pool mutations (the quest and code say yes; one code comment says no).
6. The OC recipes and tiers in NEI.
7. That the EIG rejects CropsNH seeds.

## Sources (opened; repos read by shallow clone at the tags shown)

- [M] 2.9.0 manifest: https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/releases/manifests/2.9.0.json
- [C9], [C8] Changelogs (2.8.4 → 2.9.0, 2.7.4 → 2.8.0, 2.8.x): https://github.com/GTNewHorizons/DreamAssemblerXXL/tree/master/releases/changelogs
- [A] CropsNH 2.0.140: https://github.com/GTNewHorizons/CropsNH/tree/2.0.140
- [P] Modpack 2.9.0 config: https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/tree/2.9.0/config
- [Q] 2.9.0 quest book ("The Green Revolution"): https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/tree/2.9.0/config/betterquesting/DefaultQuests/Quests
- [G] GT5U kubatech (EIG): https://github.com/GTNewHorizons/GT5-Unofficial/tree/5.09.54.265/src/main/java/kubatech
- [O] OpenComputers IC2 integration: https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/integration/ic2/EventHandlerIndustrialCraft2.scala
- [N] NewHorizonsCoreMod 2.9.99 recipes: https://github.com/GTNewHorizons/NewHorizonsCoreMod/tree/2.9.99/src/main/java/com/dreammaster/gthandler/recipes
- [R] GTNH-CropAutomation: https://github.com/DylanTaylor1/GTNH-CropAutomation
- [F] Ancestors and forks: https://github.com/huchenlei/auto-crossbreeding, https://github.com/xyqyear/auto-crossbreeding
