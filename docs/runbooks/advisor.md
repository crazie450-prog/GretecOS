# Advisor and proactive notices: PC steps (WO-6.5)

Clayton runs these on the gaming PC after the WO-6.5 PR is merged. It takes about 30 minutes. The build container has already checked the plumbing:

- `gos eval advisor --fake` passes all three scenarios (early, trouble, stocked) through the real router and the real Claude Code CLI, with an adversarial fake model that asks for full spoilers on every quest it sees and repeats every tool result: GOS's steps in GOS's order, every number cited, every id real, nothing past the frontier;
- both D71 default notices fire in a fixture Lab scenario (`tests/unit/test_advisor.py::test_both_default_notices_fire_in_the_lab_scenario`): the OC contract's own telemetry goes through the real `oc-lab` ingest, the plans through the real plan store and EU ledger, the stock through My base; each notice fires once, with a spoken line, the 10-minute cooldown and the priority gate hold;
- the Now view draws the advisor's steps (vitest) and the voice client speaks a notice (pytest).

What is left needs the PC: the real model on the three scenarios (you rate them), and the notices in a real Lab world. Decisions: D266 (the advisor), D267 (the notices).

`gos` below means the release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91), in a normal PowerShell window. Nothing here touches Play: the eval builds its scenarios in temporary folders, and part C uses the Lab only.

## A. Deploy

1. In the profile clone: `git pull`, then `uv run gos deploy`. This puts `vault\_gos\skills\advisor\SKILL.md` into the vault.
2. Check it arrived:
   ```powershell
   Test-Path "$env:USERPROFILE\GregtecOS\vault\_gos\skills\advisor\SKILL.md"
   ```
   Expect `True`.

## B. The three scenarios with the real model (plan §8: "Clayton rates 3 scenarios useful")

3. This needs the key (`gos key status` says `anthropic: stored`). Run:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe eval advisor | Tee-Object -FilePath "$env:USERPROFILE\GregtecOS\data\reports\advisor-eval.txt"
   ```
   **Cost.** Three deep-lane tasks (the orchestrator loads the skill, calls `advisor_next` and perhaps one or two detail tools). WO-2.4's measured deep-lane tasks put this at a few cents each; the whole run should stay under $0.50. Each call is logged in `llm_usage` under task `eval:advisor`. A task that reaches `[budget] task_max_usd` pauses and the eval stops it; nothing is approved.
   Expect `advisor: 3 of 3 scenarios pass`. A `# structure`, `# citations` or `# spoilers` line under a scenario says what failed: paste it.
4. Open `GregtecOS\data\reports\advisor-<date>.tsv` (Excel or Notepad). One row per scenario: the automatic checks, an empty `useful` column, and the answer. Read each answer as if it were your own world:
   - **early**: day one, nothing built. Does it point you at your goals' quests (the Electric Blast Furnace, then oxygen) and the steel line you accepted?
   - **trouble**: a running macerator line on too small a power line, an EBF with a job that isn't running, a battery buffer draining. Does it put the problems first and say what is wrong?
   - **stocked**: both macerators for an accepted line are in stock, and you just finished "Logs". Does it tell you to build it?
   Write `yes` or `no` in `useful` for each, and a few words on any `no`. Paste the sheet into the PR.

## C. The notices in the Lab (plan §8: "both default notices fire in a Lab scenario")

Needs WO-7.2's OC agent on an OC computer in `lab-ops`, reporting as `oc-lab` (runbook `oc_agent.md`), the dashboard open, and the voice client running (it starts with the Lab, D259). Leave `[policy] notices` out of gos.toml (the D71 defaults are on).

5. `& gos launch lab`, open `lab-ops`, and check the dashboard's OC chip shows the computer heard.
6. **Power deficit.** Put a GT battery buffer (any tier, with a few charged batteries) on the OC adapter the agent watches. Feed it nothing and hang a load on it larger than its input (any powered machine running a long recipe). Within about 70 s of the buffer's average output going above its input (one OC reading, then the 60 s hold), expect:
   - the voice line "Heads up: power deficit. A power store is draining.";
   - an alert on Now, "Power deficit: <buffer name> is draining", its body naming the EU/t in and out with the `tel:` id.
   Fix it (power the buffer). The alert goes within 10 s.
7. **Machine stalled** (WO-7.5's alert engine, D281). Give a GT single-block machine on the adapter a job, then take away what it needs to keep running (break its power cable or let its buffer run dry) so it holds the job without running. About a minute after it stops (`[alerts] stalled_s`), expect **one** voice line "Lab: <name> has stalled." and **one** alert "Machine stalled: <name>" on Now (source OC telemetry), and the machine as a step on Now's Next steps. There must be no second card or line for it (no "Heads up: a machine has stalled.").
   - If step 6 fired in the last 10 minutes this one still fires (it is the alert engine's, with its own cooldown).
   - If OC never reports the machine with `work` true and `active` false (check with `gos tool telemetry_get '{\"scope\": \"machines\"}'`), note which way you stopped it: that is a PR question for WO-7.4 (the sensor "Problems" count).
   - **A multiblock (VERIFY V34).** Give an EBF on the adapter a job, then cut its power mid-recipe so GT shuts it down. Run `gos tool telemetry_get '{\"scope\": \"machines\"}'` and note the EBF's `allowed` (and `work`, `active`). If `allowed` reads false, expect "Machine stopped: <name>" (WO-7.4's `stopped`, counted by `[alerts] stalled_stopped`) once, and the step on Now; paste the row into the PR so V34 can be closed either way.
8. **A plan buildable from stock.** In the Designer, design a small LV line (for example, a macerator line at 0.5/s) and **Accept** it. In Base, add each machine its BOM lists to the stock with at least its count (the machine items, by key: `gregtech:gt.blockmachines@<mte>`; a multiblock's casings too, which the Base view's stock form takes by item). Within 10 s expect "You have everything to build <name> in stock." and the line as step 1 of Next steps on Now, linking to it in the Designer.
   - If a problem notice or alert (step 6 or 7) fired less than 10 minutes ago, this one waits until the 10 minutes are up: an opportunity never talks over a problem (D267). Wait, or run step 8 first.
9. Say "what should I do next?" Expect a short spoken answer in GOS's order (problems first, then the buildable line, then your goals' quests), and the steps with their reasons and citations on the dashboard.
10. Paste the notices' log lines into the PR:
    ```powershell
    Select-String -Path "$env:USERPROFILE\GregtecOS\logs\core*.log" -Pattern '"notice"' | Select-Object -Last 10
    ```

## D. Turning notices on and off

- `[policy] notices` in gos.toml lists the kinds that are on. Leave it out for D71's defaults. `notices = []` turns them all off; add `"quest_opened"` for the opt-in "a quest just opened up" notice:
  ```toml
  [policy]
  notices = ["power_deficit", "machine_stalled", "plan_buildable", "quest_opened"]
  ```
- A wrong value stops the core at start with `gos.toml: [policy] notices must be a list of some of ...` (a service key, like `spoiler_default`), so `gos doctor`, deploy and sync still run.
