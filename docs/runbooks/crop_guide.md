# Crop guide: Lab checks and the followed guide (WO-6.7)

The crop guide (plan 7.17, D273-D275) computes odds and times from CropsNH 2.0.140's rules. The
rules are checked against the mod's own tooltips and NEI's Crop Breeder recipes in the harness
export; these steps check what only the game can show, and follow one guide to its target. All of
it happens in **Lab** (`lab-progress`); nothing here touches Play.

## 0. Before you start

```powershell
$ProgressPreference = 'SilentlyContinue'
uv run gos db build                      # a DB from a client Export run with CropsNH (parts.crops ok)
uv run gos crops check                   # the five picks: every number cited, "0 problems" each
```

The five picks are the PM's defaults, pending yours: Sticky Cane, Ferrofern, Coppon, Bauxia,
Fertilia. To change them, tell the PM; to check any crop: `uv run gos crops check "<crop name>"`.

## 1. Follow one guide to its target (acceptance)

1. Set your tier if the quests don't show it yet (My base), then pick the target:
   `uv run gos crops target ferrofern` (or "Follow" in the Crops view).
2. List what you have: `uv run gos crops have wheat 1 1 1` (stats are Growth Gain Resistance).
3. Say "next crop step" (or `uv run gos crops guide --next`) and do the step as laid out: parents
   north/south(/east/west) of the double stick, the named block under any stick that needs one.
4. When a step's crop appears, check it with the Plant Lens, add it with `gos crops have <crop> G Ga R`,
   and ask for the next step. Note the times you see against the guide's expected and 90% times.
5. Done when the target is in `gos crops have`. Put the notes (steps, times seen, anything that
   differed) in the PR or the G6 report.

## 2. V30 Lab checks (CropsNH on crop sticks)

- **Weeds at Growth 24 and 25**: plant a crop at Growth 24 / Resistance 1 next to an empty stick,
  then at Growth 25 / Resistance 1. Expected (code): 24 never spreads weeds, 25 does. Necrobloom: 8
  doesn't, 9 does.
- **Extra neighbours**: lay out a two-parent recipe and add a third species on the east side.
  Expected: the recipe can stop appearing (it depends on CropsNH's registration order).
- **Identical seeds**: two seeds of one crop that is in a mutation pool around a double stick.
  Expected: other members of the pool appear sometimes.
- **Crop Manager past 100**: an LV Crop Manager with fertilizer; the Plant Lens shows a stick above
  100 (up to 200), and children's stats then never drop.

## 3. V32 Lab checks (the Crop Breeder)

- In NEI, the Crop Breeder's page for Bauxia shows its parents, catalyst slot and MV tier.
- At LV, a run whose fertilizer (144 mB x child tier + 72 mB x Growth+Gain+Resistance) is over
  3,200 mB doesn't start; at MV the limit is 6,400 mB. `gos crops guide bauxia` prints the cap.
- Ten runs at LV succeed about 4 times (40%); a failure eats the parents and the fertilizer.
- A Crop Breeder-only crop (Bauxia) never appears on crop sticks from its parents.

Report each as passed or what you saw instead; the PM updates V30 and V32 in plan 13.2.
