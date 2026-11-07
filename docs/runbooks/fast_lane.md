# Router and fast lane: PC steps (WO-2.3)

Clayton runs these on the gaming PC after the WO-2.3 PR is merged: about 10 minutes and about one US cent of API credits. They measure what the build container can't: the real Haiku 5.5's time to first token (plan §8: P50 ≤ 1.2 s on the 50-question set) and its answers on Clayton's own export. Decisions: D150 (router and instant commands), D151 (pre-retrieval and unsure-match questions), D152 (the fast lane, its number check, `/api/ask`, `gos ask`), D153 (the eval).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91), in a normal PowerShell window (not "Run as administrator"). It needs a DB (`gos db build`, Phase 1 runbook) and, for the quest rows, a quest load (`gos quests load`); without quests those rows still pass but say no quests are loaded.

## A. Deploy

1. In the profile clone: `git pull`, then `uv run gos deploy`. GOS restarts with the new code.
2. Check the key is stored:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe key status
   ```
   Expect `anthropic: stored`.

## B. A few questions by hand

3. An instant command (no model call, works with no key):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe ask "recipe for steel"
   ```
   Expect `Steel Ingot: <N> recipes, lowest tier first. The first is ... (r:...)`, then `lane instant, ... 0 model calls, numbers checked`.
4. A fast-lane answer:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe ask "how do I make steel"
   ```
   Expect one or two sentences that start with `Steel Ingot`, cite at least one `r:` id, and end `lane fast, first token <ms>, ... 1 model calls, numbers checked`. A line `NOT checked` means the model's numbers weren't in the tool results twice in a row; paste it into the PR.
5. An unsure match (asks, no model call):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe ask "how do I make copper wire"
   ```
   Expect `Which copper wire did you mean: ...?` and the three candidates with their keys. (`gos ask` starts afresh each time; answering "the second one" works through the core, as the dashboard and voice will use it.)
6. A deep-lane request: `gos ask "design me a steel line at MV"` says it is handed to the deep lane (recorded only, until WO-2.4).

## C. The eval (plan §8 acceptance)

7. Run the 50 questions and the 10 ambiguous items with the real model:
   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\GregtecOS\data\reports" | Out-Null
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe eval fast-lane | Tee-Object -FilePath "$env:USERPROFILE\GregtecOS\data\reports\fast-lane-eval.txt"
   ```
   It pre-warms the prompt cache once, then asks each row; about 40 Haiku calls. The last lines are the result:
   ```text
   passed: 60 of 60
   p50 ttft (core rows answered by the model): <ms> ms over 40 turns (target <= 1200 ms)
   p50 total (same rows; checked text is spoken only when complete): <ms> ms
   ambiguous rows that asked: 10 of 10
   turns with cache reads: <n> of 40
   cost: $0.0...
   Result: PASS
   ```
   Paste the whole file into the PR (or a chat with the PM). Rows that fail say why in the `why` column: `resolved <key>, want <key>` means the full pack's names resolve an item differently from the dev runtime's (a question-set fix, not a lane bug); `numbers refused` means the model's numbers weren't in the tool results. `turns with cache reads` should be 39 or 40 of 40: fewer means the cached prefix changed between calls. `p50 total` is not part of the pass mark: it is how long the whole checked answer takes, which is what voice waits for before speaking (a question in the PR).
8. To time only the model's part again later (each run costs about a cent): `gos eval fast-lane --limit 20`.

## What to send back

- The report from step 7 (P50, ambiguous 10 of 10, any failed rows).
- Any step whose output differed from what is written here.
