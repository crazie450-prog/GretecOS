# Breeding station: Lab steps (WO-7.6)

Clayton runs these in Lab's `lab-ops` world once WO-7.6 is merged and deployed, with the OC agent already working
there (docs/runbooks/oc_agent.md, sections A to D). About 1.5 hours, most of it the station running on its own. They
check what the build container can't: V31 in the real game (labels through a transposer, the machines' slots, the
breeder's tank and progress, the parts at MV) and plan §8 WO-7.6's acceptance: **at MV, it breeds a 3-step target
from starter seeds unattended and stocks 4 copies, never uses the last seed of a parent, and stops cleanly mid-run.**
Decisions: D282 (the station), D283 (the plan, start and stop), D284 (V31). Contract: docs/contracts/oc_bridge.md,
"The breeding station".

`gos` is Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91). Nothing here touches Play:
installing the station on Play later is Clayton's own copy of the same files to Play's drive, after this Lab run.

**One thing GT decides** (D282): automation can take items only from a GT machine's output slot. A seed the station
gives a Seed Generator stays in it for good as that generator's copier, and anything put into the Crop Breeder stays
until a run uses it. So use spare generators for the station, and take a parked seed out by hand when you want the
generator back.

## A. The plan, before the game

1. In the profile clone: `git pull`, `uv run gos deploy`. The station's file is then
   `C:\GregtecOS-app\current\oc\crop\station.lua`.
2. Your tier at MV, the crops you hold, and a 3-step target:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe quests tier --set MV   # Lab only; --clear after
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe crops have "<starter crop>" 1 1 1    # one line per starter
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe crops station "<target crop>"
   ```
   Pick a target whose plan says `in 3 step(s)` (try a few; `--json` shows the command it becomes). Write down the
   starters the plan's `Starter:` lines name, each step's catalyst, and the `Seed Generators copy, best first:` line.
3. The parts (V31): `gos crops station --parts`. On your full-pack DB each part shows its recipes with their `r:` ids
   and `craftable at MV` or `NOT craftable at MV`; note any `NOT` and any `not in this DB`.

## B. The station in `lab-ops` (creative)

4. Next to the OC computer (its network), build **at MV**: one **Crop Breeder (MV)** and **three Seed Generators
   (MV)** (one is enough to start; three let the station copy the target and two parents), each with an **adapter**
   on the network and **auto-output off** (default), power, and a tank or pipe of **enriched fertilizer** into each
   (creative: fill them full). One **transposer** on the network touching a **chest**, the breeder and the generators
   (no machine's front, its main facing, against the transposer: GT refuses items there). Write down which side of
   the transposer faces each (0 down, 1 up, 2 north, 3 south, 4 west, 5 east; F3 shows your facing).
5. Put in the chest: **5 analysed seeds of each starter** (scan them in a GT Scanner first: their name must read
   `<crop> Seeds`, not `Unknown Seeds`; a renamed seed keeps its custom name and the station ignores it), and 16 of each step's catalyst. Note the counts.

## C. Install and check (V31)

6. With the PowerShell window of oc_agent.md step 6 (`$gos` set), copy the station and edit the config:
   ```powershell
   $ProgressPreference = 'SilentlyContinue'
   New-Item -ItemType Directory -Force (Join-Path $gos 'crop') | Out-Null
   Copy-Item C:\GregtecOS-app\current\oc\crop\station.lua (Join-Path $gos 'crop\station.lua')
   notepad (Join-Path $gos 'gos-agent.cfg')
   ```
   Add before the last `}` (your sides from step 4; with several generators, each one's adapter address prefix, as
   `gos-agent --list` prints it):
   ```lua
   crops = {
     storage = 0, breeder = 1,
     generators = { { side = 2, adapter = "ab12" }, { side = 3, adapter = "cd34" }, { side = 4, adapter = "ef56" } },
   },
   ```
7. In the OC shell, the V31 reads (one line each; `<s>` is the chest's side, `<b>` the breeder's):
   ```
   lua -e "local t=require('component').transposer for i=1,9 do local s=t.getStackInSlot(<s>,i) if s then print(i,s.name,s.damage,s.label,s.size) end end"
   lua -e "local t=require('component').transposer print(t.getInventorySize(<b>), t.getTankLevel(<b>))"
   ```
   Expect each seed as `cropsnh:genericSeed 0 <Crop> Seeds`, an unscanned one as `Unknown Seeds`; the breeder's size
   10 at MV (13 from HV) and its tank level in mB. Then `gos-agent --list`: the breeder and generators show as
   `gt_machine` named `basicmachine.cropbreeder.tier.02` / `basicmachine.seedgenerator.tier.02`.
8. Restart the agent (`gos-agent`). The log says `breeding station ready`; the dashboard's `/api/crops/station`
   (or `gos crops station`) shows the computer with `state: idle`.

## D. Unattended, 4 stocked (about 1 hour)

9. **Unconfirmed never runs.** Ask GOS by voice "start the breeding station on <target>" (the deep lane's
   `crop_station`), and do **not** confirm: after 60 s it says it expired; nothing moved in the chest (check the
   counts). Then ask again and **confirm** (voice "confirm" after the read-back, or the dashboard's card).
10. Leave the game unfocused with no menu open (`pauseOnLostFocus` off) and do nothing for the run. Every few minutes
    note `crop_status` (or the dashboard): the step, runs and failures (MV is 50%: expect failures), and the chest.
11. **Never the last seed.** At no time may a parent's count in the chest drop to 0 because of the breeder; write down
    the lowest count you see of each parent. A parent the plan didn't give a copier can run short: the station then
    says `needs N more <crop> Seeds`; add some and it continues.
12. When the status says `done`: the chest holds **4 or more** of the target, the breeder is empty and idle, each
    generator holds at most one parked seed (its copier's crop, listed under `copiers`) and nothing in its output.
    Note the total time, runs and failures, and the enriched fertilizer used.

## E. Stop mid-run (about 20 minutes)

13. Start the same target again (step 9, confirmed). While the status says `step 2: breeding ...`, say "stop the
    breeding station" and confirm. The run under way finishes (the breeder's progress bar), its child (on a success)
    goes to the chest, the generators' last copies come out, and the status says `stopped; nothing of the station's
    is left in transit`. Check: the breeder empty, no generator output, the breeder's work switch on again (its GUI),
    the generators paused.
14. **Restart.** Start once more and confirm; during a run, stop the agent with Ctrl+Alt+C and run `gos-agent`
    again: the status says `interrupted` and nothing moves (a minute). Confirm a stop: it winds down as in step 13.

## What to send back

- Step 2's plan lines (target, steps, starters) and step 3's parts output.
- Step 7's lines (the labels as OC shows them, the breeder's size and tank level) and step 8's status line.
- Step 9: that the unconfirmed start expired with nothing moved.
- Steps 10 to 12: the status notes, the lowest count of each parent, the final chest counts, time, runs, failures.
- Steps 13 and 14's outcomes, and any step whose output differed from what is written here.
