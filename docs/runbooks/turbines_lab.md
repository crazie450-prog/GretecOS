# Turbine output in the Lab (WO-4.1, §8 row 4.1, D230-D232)

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
