# Recipe lookup: PC steps (WO-2.7)

Clayton runs these on the gaming PC after the WO-2.7 PR is merged: about 15 minutes and well under a US dollar of API credits (about 40 Haiku calls for the fast lane, cents; 3 deep-lane tasks on Sonnet, each stopped at its `[budget] task_max_usd` cap of $2.00 at most). They measure what the build container can't: the real models answering the 50 recipe and item questions on Clayton's own export (plan §8: at least 95% correct, 0 uncited numbers). In the container the same run passes 50 of 50 with oracle fakes, which checks the plumbing, not a model. Decisions: D170 (the skill), D171 (the question file and its scoring), D172 (the fake models and the recipe card).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91), in a normal PowerShell window (not "Run as administrator"). It needs the key (`gos key status` says `anthropic: stored`), a DB built from an Export run (`gos db build`) and the live vault (`gos vault deploy`).

## A. Deploy

1. In the profile clone: `git pull`, then `uv run gos deploy`. GOS restarts with the new code; the deep lane deploys `vault-template\_gos\` into the vault, so `vault\_gos\skills\recipe-lookup\SKILL.md` appears (a commit "Deploy _gos/ from the repo" in the vault's git history).
2. Check the skill reached both lanes:
   ```powershell
   Test-Path "$env:USERPROFILE\GregtecOS\vault\_gos\skills\recipe-lookup\SKILL.md"
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe ask "how do I make steel" --json | Select-String '"recipe"'
   ```
   Expect `True`, then a line with `"recipe": {` (the structured recipe card in the reply's card, for the dashboard).

## B. The eval (plan §8 acceptance)

3. Run the 50 questions with the real models:
   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\GregtecOS\data\reports" | Out-Null
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe eval recipe-lookup --out "$env:USERPROFILE\GregtecOS\data\reports\recipe-lookup.tsv" | Tee-Object -FilePath "$env:USERPROFILE\GregtecOS\data\reports\recipe-lookup-eval.txt"
   ```
   Each row goes through the router as a typed question would: instant commands, the fast lane, and the three rows the router hands to the deep lane (r12, r20, r38), which the eval waits for (a deep task that reaches its cap is stopped, never approved). The last lines are the result:
   ```text
   correct: 50 of 50 (100.0%; target >= 95%)
   uncited numbers: 0 (target 0)
   by kind: recipe 7/7, uses 4/4, machine 5/5, power 5/5, time 3/3, chance 5/5, fluid 5/5, oredict 4/4, multistep 4/4, ambiguous 6/6, none 2/2
   lanes: deep 3, fast 37, instant 10
   fast-lane model calls: <n>; cost: $0.0...
   Result: PASS
   ```
   Every model call is in `llm_usage` (task `eval:recipe-lookup`, the dashboard's Costs view). The deep-lane rows run in the live vault; they only look recipes up, but `git -C "$env:USERPROFILE\GregtecOS\vault" log --stat -3` shows anything the lane wrote.
4. Reading a failed row (`pass` 0; the `why` column says which check failed):
   - `cites no row of make <key> ...` or `uncited numbers: ...` from a model answer: a real miss (the wrong recipe, or a number no cited row holds). Keep the row as it is.
   - `the DB has no row for make <key> ...` or `not a none row here`: the references were written over the dev runtime's DB, and the full pack's export differs (a recipe GTNH changed or removed). That is a question-file fix, not a model miss; note the row id.
   - `answered instead of asking which item` on an ambiguous row: the full pack's names make that item clear. Also a question-file fix.
   - `handed to the deep lane` or `deep lane: failed: ...`: the deep lane didn't answer (offline, or its CLI failed to start); paste the line.

## What to send back

- `recipe-lookup-eval.txt` and `recipe-lookup.tsv` from step 3 (the result lines and any failed rows).
- Any step whose output differed from what is written here.
