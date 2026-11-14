# Turbine and naquadah reactor output in the Lab (WO-4.1, §8 row 4.1, D230-D232; WO-4.8, D326)

WO-4.1's acceptance is "turbine output matches the Lab for 3 rotor setups". In CI the port
(`core/gregtec/planner/gtmath/turbine.py`) already matches GT's own classes bit for bit:
`tests/unit/gtmath/vectors/turbine.tsv` holds GT 5.09.54.265's `fluidIntoPower` for the five large
turbines, new and legacy, with three rotor set-ups each (titanium large, tungstensteel huge,
neutronium normal; tight and loose; half, optimal, maximum and twice the maximum flow), and the
harness test checks the port's rotor stats against GT's own `TurbineStatCalculator` for every
rotor of the game. This runbook is the remaining half: the same numbers read off real turbines in
the Lab, with Clayton's own pack. It is a PC step (Clayton runs it in the Lab, never in Play).

## 1. Expected numbers, from your DB

After the next Export run with this GregtecLink (`gos export run`, then `gos db build`), print the
expected numbers. They come only from your DB (rotor stats and fuel values of your pack) and the
GT turbine port; nothing is typed in by hand:

```
uv run gos power turbine steam    --rotor Titanium      --size large  --dynamo IV
uv run gos power turbine steam    --rotor TungstenSteel --size huge   --dynamo IV
uv run gos power turbine steam    --rotor Neutronium    --size normal --dynamo IV
uv run gos power turbine hp_steam --rotor Titanium      --size large  --dynamo IV
uv run gos power turbine hp_steam --rotor TungstenSteel --size huge   --dynamo IV
uv run gos power turbine hp_steam --rotor Neutronium    --size normal --dynamo IV
uv run gos power turbine sc_steam --rotor Titanium      --size large  --dynamo LuV
uv run gos power turbine sc_steam --rotor TungstenSteel --size huge   --dynamo LuV
uv run gos power turbine sc_steam --rotor Neutronium    --size normal --dynamo LuV
uv run gos power turbine gas      --rotor Titanium      --size large  --dynamo IV  --fuel benzene
uv run gos power turbine gas      --rotor TungstenSteel --size huge   --dynamo IV  --fuel benzene
uv run gos power turbine gas      --rotor Neutronium    --size normal --dynamo IV  --fuel benzene
uv run gos power turbine plasma   --rotor Titanium      --size large  --dynamo UV  --fuel plasma.helium
uv run gos power turbine plasma   --rotor TungstenSteel --size huge   --dynamo UV  --fuel plasma.helium
uv run gos power turbine plasma   --rotor Neutronium    --size normal --dynamo UV  --fuel plasma.helium
```

Each prints the rotor's efficiency, optimal flow and overflow tier, the fuel's `FUEL_VALUE` and
three rows: `half optimal`, `optimal` and `maximum`, each with the flow the turbine burns, the
EU/t it makes and the optimal flow its scanner shows, plus the citations (`rotor:<material>/<size>`
and the fuel's `r:` key). Add `--legacy` for a pre-2.9 controller and `--loose` after a
screwdriver click. Save the output next to your Lab notes.

**Plasma is per second.** The steam, HP steam, supercritical and gas turbines burn their fluid
every tick, so their flows are **L/t**. Both plasma controllers burn once every 20 ticks (GT sets
their cycle to 20 ticks) and hold the EU/t for the whole second, so a plasma flow is **L/s**:
`gos power turbine plasma` prints the column as `L/s`, and GT's scanner shows the plasma optimal
flow with `/s`. A litre of plasma still makes `FUEL_VALUE` x the rotor's efficiency EU.

For reference, the dev runtime's numbers (server harness run `20261112T065356Z`, the mod's dev
pack, which may differ from yours wherever a config or addon changes a material or a fuel):

| turbine | rotor | dynamo | fuel | optimal flow shown | half optimal | optimal | maximum | refs |
|---|---|---|---|---|---|---|---|---|
| steam | Titanium large | IV | - | 1050 | 525 L/t: 176 | 1050 L/t: 708 | 2100 L/t: 944 | rotor:Titanium/large |
| steam | TungstenSteel huge | IV | - | 1600 | 800 L/t: 340 | 1600 L/t: 1360 | 3200 L/t: 1813 | rotor:TungstenSteel/huge |
| steam | Neutronium normal | IV | - | 2400 | 1200 L/t: 420 | 2400 L/t: 1680 | 6000 L/t: 2625 | rotor:Neutronium/normal |
| hp_steam | Titanium large | IV | - | 1050 | 525 L/t: 353 | 1050 L/t: 1417 | 2625 L/t: 2214 | rotor:Titanium/large |
| hp_steam | TungstenSteel huge | IV | - | 1600 | 800 L/t: 680 | 1600 L/t: 2720 | 4000 L/t: 4250 | rotor:TungstenSteel/huge |
| hp_steam | Neutronium normal | IV | - | 2400 | 1200 L/t: 840 | 2400 L/t: 3360 | 7200 L/t: 6048 | rotor:Neutronium/normal |
| sc_steam | Titanium large | LuV | - | 1050 | 525 L/t: 354 | 1050 L/t: 1417 | 1312 L/t: 1329 | rotor:Titanium/large |
| sc_steam | TungstenSteel huge | LuV | - | 1600 | 800 L/t: 680 | 1600 L/t: 2720 | 2000 L/t: 2550 | rotor:TungstenSteel/huge |
| sc_steam | Neutronium normal | LuV | - | 2400 | 1200 L/t: 840 | 2400 L/t: 3360 | 3000 L/t: 3150 | rotor:Neutronium/normal |
| gas | Titanium large | IV | benzene | 2 | 1 L/t: 243 | 2 L/t: 972 | 6 L/t: 1749 | rotor:Titanium/large r:gt.recipe.gasturbinefuel/28b9a71641df |
| gas | TungstenSteel huge | IV | benzene | 4 | 2 L/t: 612 | 4 L/t: 2448 | 12 L/t: 4406 | rotor:TungstenSteel/huge r:gt.recipe.gasturbinefuel/28b9a71641df |
| gas | Neutronium normal | IV | benzene | 6 | 3 L/t: 756 | 6 L/t: 3024 | 27 L/t: 7653 | rotor:Neutronium/normal r:gt.recipe.gasturbinefuel/28b9a71641df |
| plasma | Titanium large | UV | plasma.helium | 11 L/s | 5 L/s: 12567 | 11 L/s: 60825 | 44 L/s: 139029 | rotor:Titanium/large r:gt.recipe.plasmageneratorfuels/bbc21cccddaa |
| plasma | TungstenSteel huge | UV | plasma.helium | 17 L/s | 8 L/s: 26214 | 17 L/s: 118374 | 68 L/s: 270568 | rotor:TungstenSteel/huge r:gt.recipe.plasmageneratorfuels/bbc21cccddaa |
| plasma | Neutronium normal | UV | plasma.helium | 25 L/s | 12 L/s: 33028 | 25 L/s: 143360 | 137 L/s: 433657 | rotor:Neutronium/normal r:gt.recipe.plasmageneratorfuels/bbc21cccddaa |

## 2. Build each turbine in the Lab

In the GTNH-Lab instance, world `lab-structures` (creative), `gos launch lab`:

1. Place the **2.9 controller** (NEI: Large Steam / Large HP Steam / Large Supercritical Steam /
   Large Gas / Large Plasma Turbine; the "structure deprecated" tooltip marks the legacy ones) and
   build its structure with the multiblock projector or the in-world hologram (screwdriver-free
   placement is fine in creative).
2. Hatches: one maintenance hatch (fix it), one input hatch, one output hatch (the steam turbines
   output distilled water, HP steam outputs steam, SC steam outputs superheated steam; plasma
   outputs the depowered fluid), a muffler where the structure asks for one, and **exactly one
   dynamo hatch of the tier in the command, 1 A**. A second dynamo adds nothing (GT reads one
   dynamo's voltage x amps, D230); a bigger dynamo only raises the cap.
3. Put the rotor in the controller's slot: the material and size from the command (NEI search
   "Large Turbine" etc.; in creative, any rotor of that material and size, at full durability).
   Leave the fitting **tight** (the default; a screwdriver on the controller toggles loose).
4. Connect the dynamo to an energy sink that takes everything (a creative energy acceptor, or a
   battery buffer of the dynamo's tier with empty batteries and a cable to a void). The turbine
   must never be output-limited by a full buffer.

## 3. Feed it and read it

For each of the three rows:

- **maximum**: give it more than it can burn: a creative fluid source (or a Super Tank of the
  fluid behind a pump) on the input hatch with nothing limiting the flow.
- **optimal** and **half optimal**: put a **Fluid Regulator** cover on the pipe or tank feeding
  the input hatch, set to exactly the row's flow (a high enough tier for the rate):
  - steam, HP steam, supercritical steam, gas: the row's **L/t, per tick**;
  - plasma: the row's **L/s, per second** (per 20 ticks). Never set a plasma row per tick: the
    hatch would get 20 times the row's flow and the turbine would run at its maximum instead.

Then wait until the controller has settled: its efficiency reads **100 %** and its output has
**stopped changing** between two scanner reads a minute apart. GT ramps the efficiency from 0 to
100 % in 1000 ticks (10 per tick; plasma 200 per 20-tick cycle) and, each cycle, moves the
output toward the target by the larger of 10 EU/t and 1 % of the gap:
- steam, HP steam, supercritical steam, gas (a 1-tick cycle): wait **at least 90 s**;
- plasma (a 20-tick cycle, so the output ramp is 20 times slower): wait **about 10 to 12
  minutes** for the table's rows (over 8 minutes even for the smallest), and longer for outputs
  near a big dynamo's cap.

Read the controller with a **Portable Scanner** (or the tricorder): the lines are the turbine's
output in EU/t, its efficiency (must read 100 %), the optimal flow with its unit ("/t", or "/s"
for plasma) and the fitting, and the fuel stored. Record for each row:

| setup | flow fed (L/t, or L/s for plasma) | output shown (EU/t) | optimal flow shown | efficiency shown |
|---|---|---|---|---|

**Pass**: every row's output and optimal flow equal `gos power turbine`'s numbers exactly (they
are GT's integer arithmetic, so there is no tolerance; a difference of a few EU/t means a fluid
regulator that lets through a different flow). Repeat one set-up with `--legacy` on a legacy
controller and `--loose` after a screwdriver click if you want the extra checks.

If a row differs, note the turbine, rotor, fuel, the flow fed and both numbers, and file it:
the planner's number is wrong for your pack, and WO-4.2 must not use it until it is explained.

## 4. The Large Naquadah Reactor (WO-4.8, D326-D328)

GoodGenerator's Large Naquadah Reactor (`mte:15537`) is modelled from GT 5.09.54.265's own class,
and in CI the port equals that class run outside the game (`tests/unit/gtmath/vectors/naquadah.tsv`,
824 cases). What CI can't see is your pack's `config/GoodGenerator.cfg`: the export reads the
reactor's fuel values, liquid air and boost lists from the game, so your DB has your numbers, and
this step checks them on a real reactor. It is a PC step in Lab's `lab-ops` world (creative),
never in Play.

**Expected numbers, from your DB** (after an Export run with this GregtecLink and `gos db build`):

```
uv run gos power naquadah --dynamo IV
uv run gos power naquadah --fuel "naquadah based liquid fuel mki" --coolant ic2coolant
uv run gos power naquadah --fuel "naquadah based liquid fuel mki" --excited molten.naquadah
```

The first prints the reactor's liquid air and boost lists and, per fuel of its map, its EU/t, the
litres of fuel, liquid air, coolant and excited fluid one run takes and a second, the depleted
fuel a second, and the smallest dynamo hatch up to IV that holds it. The other two print the
boosted rows with no tier limit, each with the smallest hatch that holds it. Every number is for
a reactor with **exactly one** dynamo hatch (the planner doesn't model several). Save the output
next to your Lab notes.

**Build it** in `lab-ops` with creative: the controller and its casings (NEI's multiblock preview
or a blueprint), one maintenance hatch (fixed), one input hatch per fluid, one output hatch, and
**one** dynamo hatch: the one the `--excited` row names (review M2: the excited run is the
biggest output of the three, so this hatch holds all three runs; the hatch the first table names
for MkI is too small for it, and GT would stop the reactor). The reactor stops at once if a
tick's output is above the hatch's voltage x amps, which is worth seeing once with a smaller
hatch (the first table's MkI hatch on the excited run). Put a creative-energy sink or a big
battery buffer on the dynamo's laser or cable.

**Feed and read, three runs**, each with the tanks full (creative fluid tanks or super tanks):

| run | input hatches | expected (from the table) |
|---|---|---|
| MkI | MkI fuel, liquid air | the first table's MkI row: its `EU/t`, fuel L/s and air L/s |
| MkI + IC2 coolant | as above, plus IC2 coolant | the `--coolant ic2coolant` row as printed (its EU/t, coolant L/s) |
| MkI + molten naquadah | as above (no coolant), plus molten naquadah | the `--excited molten.naquadah` row as printed (its EU/t, fuel and excited L/s) |

Read the expected numbers from the printed rows, not from a multiplier: they come from your
`GoodGenerator.cfg` (D326). The liquid air you feed must be the fluid the first line of the output
names (`liquid air: ... of <fluid>`, the one the export read from GT's `Materials.LiquidAir`):
if the reactor takes none of it, or runs on another liquid air, note both fluids' names (NEI
shows them), since the planner would then be chaining the wrong one (review L3).

For each run, after a minute (the output is steady from its first second), read the controller
with a **Portable Scanner**: the output line (EU/t) and the efficiency line (100 %, or the
coolant's). Then measure what it burns over a timed minute: read the fuel, liquid air and
depleted-fuel amounts in their hatches (or tanks) at the start and after exactly 60 s
(1200 ticks), and divide by 60.

| run | EU/t shown | efficiency shown | fuel L/s | liquid air L/s | coolant L/s | excited L/s | depleted L/s |
|---|---|---|---|---|---|---|---|

**Pass**: the EU/t and efficiency equal the table's exactly (GT's integer arithmetic); each
litre rate equals the table's over a whole number of runs (MkI's run is 60 ticks, so a 60 s window
holds 20 runs and the rates are exact; for a fuel whose burn time doesn't divide 1200 ticks,
time the window to a whole number of runs). Without liquid air the reactor makes nothing: worth
seeing once too. If a number differs, note the run, both numbers and your `GoodGenerator.cfg`'s
`LargeNaquadahReactor` section, and file it: the planner's reactor numbers are wrong for your pack
until it is explained.
