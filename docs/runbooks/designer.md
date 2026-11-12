# Designer: PC steps (WO-3.7)

Clayton runs these on the gaming PC at the deployment test (D158): about 30 minutes and, for step C, up to **$24 of API credits in the worst case**: 10 design tasks, each stopped at the measuring cap of $2.00 ($20), plus each task's explanation (`plan_explain`, at most two planner-model calls, about $0.32 each at the most: about $3.20 more), plus a subagent's overrun past a pause. The fake run's shape puts a design task near $0.05 to $0.10 and an explanation near $0.05 to $0.07, so expect about $1 to $3. Spending is his call: step C is optional until he wants the cap set. Decisions: D250 (the view and its API), D251 (the skill and tools; accepting is his; the checked explanation), D252 (accept, notes, the ledger hook), D253 (the cost run and the cap rule).

`gos` below means the release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91), in a normal PowerShell window. It needs a DB (`gos db build`) and the live vault (`gos vault deploy`); the deep lane needs the key (`gos key status` says `anthropic: stored`).

## A. The Designer on monitor 2

1. After `uv run gos deploy` (from his clone), open the dashboard and press **4**. With no design yet it says "No design open".
2. In **New design**, type `steel ingot`, rate `2/s`, tier `MV`, and press **Design**. Up to three routes appear side by side, in the order GOS's code ranked them, with the differing choices marked "differs" and each route's flow diagram. The address is now `#/designer/pl-...`.
3. Click a machine in route 2's diagram (or its row under the diagram). **Swap or pin** lists the node's recipes; press **Pin** on another one. The design is solved again with the pin; reload the page (F5): the pin is still listed on the route. Press **Re-solve**: still pinned.
4. On the route he wants, **Accept this route**, then **Confirm**. The route shows "accepted"; `vault\base\lines\ln-<id>.md` and `vault\plans\pl-<id>.md` say `st: accepted`:
   ```powershell
   Get-Content "$env:USERPROFILE\GregtecOS\vault\base\lines\ln-*.md"
   ```
5. **Board check (D92, D158 (5)):** the Designer board on the Claude Design canvas is approved, but this view was built from the design system without the canvas handoff. Compare it with the board and note what differs, or send the canvas handoff so the view can be reconciled.

## B. A text request through the deep lane

6. ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe ask "plan a steel ingot line that makes 2 ingots per second at MV"
   ```
   Expect the hand-off, then the deep lane's answer: the routes in rank order with their `plan:` ids, each number cited, and a line saying he can compare, pin and accept in the Designer. Press **4**: the Designer opens on that design. The model never accepts; that is step 4's button (or `gos plan --accept pl-...`).

## C. The cost run and `task_max_usd` (optional; spends credits)

7. ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe eval design-cost --measure-cap 2.00 --out "$env:USERPROFILE\GregtecOS\data\reports\design-costs.tsv" | Tee-Object -FilePath "$env:USERPROFILE\GregtecOS\data\reports\design-cost.txt"
   ```
   It runs the 10 tasks of `tests\evals\design_tasks.tsv`, one fresh session each, and prints a row per task (`id kind status cost_usd est_usd explain_usd turns tools subagents seconds by_model mode`), then `# worst $..., mean $..., median $...` and `# task_max_usd = X.XX (measured: ...)`. `explain_usd` is the task's checked explanation (`plan_explain`), outside the lane's cap. A `capped` row means a task reached $2.00 and was stopped (never approved): then there is no recommendation; run again with `--measure-cap 4.00` (worst case about $44) only if he wants to. Every call is in `llm_usage` (task `eval:design-cost:<id>` and `plan_explain`, the Costs view). **It runs on a copy** of the DB and vault (`GregtecOS\data\eval\design-cost-home`, replaced by the next run): his plans, notes and the Designer's latest design are untouched; the copy's `vault\plans\` holds the tasks' plans to look at.
8. To set the cap from that run without running (or paying) again (it edits only `[budget] task_max_usd` in `GregtecOS\config\gos.toml`, keeping `gos.toml.bak`; refused unless the table holds all 10 default tasks from a real run):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe eval design-cost --apply-from "$env:USERPROFILE\GregtecOS\data\reports\design-costs.tsv"
   ```
   The core reads it after a restart (docs/runbooks/core_service.md). (`--apply` on step 7's command does the same in one go.)

## What to send back

- Step 3's and 4's screenshots, and anything in step 5 that differs from the board.
- Step 6's answer text and wall time.
- If step C ran: `data\reports\design-cost.txt` and `data\reports\design-costs.tsv`, and whether step 8 applied the value.
