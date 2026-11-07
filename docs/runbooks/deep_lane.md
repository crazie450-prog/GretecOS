# Deep lane: PC steps (WO-2.4)

Clayton runs these on the gaming PC after the WO-2.4 PR is merged: about 30 minutes and, for the cost table, up to a few US dollars of API credits (10 tasks, each stopped at its `[budget] task_max_usd` cap of $2.00 at most; most should cost cents). They measure what the build container can't: the real models' behaviour and cost on Clayton's own export, and how long the Claude Code CLI takes to start on Windows [D§1.1]. Decisions: D154 (runtime and lock-down), D155 (subagents, models, skills), D156 (budgets), D157 (the cost eval).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91), in a normal PowerShell window (not "Run as administrator"). It needs the key (`gos key status` says `anthropic: stored`), a DB (`gos db build`) and the live vault (`gos vault deploy`).

## A. Deploy

1. In the profile clone: `git pull`, then `uv run gos deploy`. GOS restarts with the new code; the core now starts the deep lane, which deploys `vault-template\_gos\` into the vault (a commit "Deploy _gos/ from the repo" in the vault's git history) and connects its first session.
2. After about a minute, the dashboard health strip (or `gos status`) shows **Deep lane: ok, warm**. `warn, connecting` for longer than two minutes, or `warn, offline: ...`, goes into the PR with the line.

## B. One task by hand

3. ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe ask "plan a steel ingot line that makes 2 ingots per second at MV"
   ```
   Expect `That's one for the deep lane; I've handed it over.`, then after a while `deep lane h-...: done, $0.0..., (cap $2.00), N turns` and the answer: a short first sentence, then detail with `r:` ids, and a line saying the machine counts need the designer (Phase 3). The first run includes the CLI's cold start: note the wall time (Windows reports of 80 s exist [D§1.1]).
4. The guard, by hand:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe ask "change my goals to finishing the EBF this week"
   ```
   This request goes to the fast lane first, which hands it on (its `handoff` tool) or answers that goals are Clayton's to edit. Expect no change to `vault\me\goals.md` either way; a handed-off task ends with a new `vault\inbox\ib-<date>-....md` proposal instead. `git -C "$env:USERPROFILE\GregtecOS\vault" log --stat -3` shows what the lane wrote.

## C. The cost table (plan §8 acceptance, QA:A4)

5. ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe eval deep-lane | Tee-Object -FilePath "$env:USERPROFILE\GregtecOS\data\reports\deep-lane-eval.txt"
   ```
   It runs the 10 tasks of `tests\evals\deep_lane_tasks.tsv` one at a time (a fresh session each) and prints a TSV row per task: `id kind status cost_usd turns tools subagents seconds by_model`, then `# total $..., mean $..., median $... over 10 tasks; N reached the $2.00 cap` and `Result: PASS`. The table is also written to `GregtecOS\data\reports\deep-lane-costs-<date>.tsv`, and every call is in `llm_usage` (task `eval:deep-lane:<id>`; the dashboard's Costs view, WO-2.6). A `capped` row means the task reached the cap and the eval stopped it (it never approves).

   **The eval runs on the live vault.** Task d07 writes a `plans\` note and d09 may write an `inbox\` proposal (both through the guarded note tools; `me\` and `_gos\` stay untouched), and the lane's start may commit a `_gos` deploy. Afterwards `git -C "$env:USERPROFILE\GregtecOS\vault" log --stat -5` lists what it wrote; delete those notes (or `git revert` their commits) if you don't want them.
6. Paste both files into the PR (or a chat with the PM). The PM sets `[budget] task_max_usd` and plan 4.4's cost sketch from them; WO-3.7 measures again on real design tasks.

## D. Pause and ask, live (optional)

7. Set a tiny cap in `GregtecOS\config\gos.toml` (`gos ask` reads it at once; the core after a restart, docs/runbooks/core_service.md):
   ```toml
   [budget]
   task_max_usd = 0.05
   ```
   Ask `gos ask "compare the electric blast furnace with the bronze blast furnace for making steel at LV"`. When the task reaches $0.05 it pauses: `gos ask` asks `Approve another cap's worth? [y/N]` (the dashboard gets the same as `POST /api/deep/tasks/<id>/approve` in WO-2.6). `y` continues the task in the same session; `n` stops it. A task nobody answers is stopped after 30 minutes, so the next request isn't held up. The cap is checked as messages arrive, so a subagent's hidden thinking can carry a task a little past it before it pauses. Set the cap back afterwards.
8. A running task can be stopped too: `POST /api/deep/tasks/<id>/stop` (the dashboard's Stop button arrives with WO-2.6) interrupts it, and it ends as `stopped` with its cost so far recorded.

## What to send back

- Step 2's health line and step 3's wall time (the cold start).
- The two files from step 5.
- Any step whose output differed from what is written here.
