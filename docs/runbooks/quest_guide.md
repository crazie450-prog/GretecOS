# Quest guide: PC steps (WO-6.4)

Clayton runs these on the gaming PC after the WO-6.4 PR is merged. It takes about 20 minutes. The build container has already checked the plumbing:

- the 20-question eval passes 20 of 20 with no spoiler, using an adversarial fake model that asks for full spoilers on every question;
- "L0 never spoils" holds for every locked quest in the fixture pack, in the answers, the cards and the Quests view;
- the same checks pass over the server harness's real recipe DB;
- the Quests view passes its e2e tests at 2560x1440 and 390x844.

Four things are left that only the PC can do: the real model on the eval, GTNH's real quest book, the view on a real Lab world, and the board check. Decisions: D268 (spoiler levels), D269 (ranking, goals, summaries), D270 (the view and the eval).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91), run in a normal PowerShell window. Nothing here touches Play: the eval builds its own pack in a temporary folder, and step C reads only the Lab world.

## A. Deploy

1. In the profile clone, run `git pull` and then `uv run gos deploy`. This puts `vault\_gos\skills\quest-guide\SKILL.md` into the vault.
2. Check that it arrived:
   ```powershell
   Test-Path "$env:USERPROFILE\GregtecOS\vault\_gos\skills\quest-guide\SKILL.md"
   ```
   Expect `True`.

## B. The eval with the real model (plan §8 acceptance)

3. Run it. This needs the key: `gos key status` should say `anthropic: stored`.
   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\GregtecOS\data\reports" | Out-Null
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe eval quest-guide | Tee-Object -FilePath "$env:USERPROFILE\GregtecOS\data\reports\quest-guide-eval.txt"
   ```
   **Cost.** The fake run makes 36 fast-lane calls on Haiku, an estimated $0.005. A real model may make a few more calls, for example a retry or an extra lookup. Each prompt is about 10k tokens and mostly cached, so expect about $0.01. Even with no cache hits at all, 60 calls of 12k tokens come to under $0.10. Every call is logged in `llm_usage` under task `eval:quest-guide`.
   Expect these last lines:
   ```text
   passed: 20 of 20
   spoiled: 0
   model calls: <n>
   cost: $0.0...
   Result: PASS
   ```
   - `spoiled` must be 0. A row whose `why` starts `spoiled` names the quest and the text that leaked. Paste it: that is a guard bug, not a model miss.
   - A row whose `why` starts `doesn't name` means the model left out a quest or item the answer needed (a model miss). Paste the row.

## C. The Quests view on the Lab world

4. With the Lab running and paused (Esc), load its quests:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe quests load --instance lab --world lab-progress
   ```
5. Open the dashboard and press **6** (Quests). Check each of these:
   - **Next quests** lists the quests lit in the in-game book, ranked. If `me/goals.md` has `goal:` lines, the quests that lead to the first goal come first, and each one says why.
   - **Beyond the frontier** is blurred and says `spoilers: nudge`. Tap it: the names you see are only the ones the book already shows (dark icons). There are no descriptions, rewards or "needs" rows.
   - Press **Spoil it**: the label changes to `spoilers: full` and descriptions and rewards appear. Press **Back to nudge**: they disappear again.
   - In Chat, ask "tell me about the secret alloy quest" (or any locked quest), then say or type "a bit more" on its own while the view is open. Only that quest opens in **Beyond the frontier**, at the partial level (its name and what it asks for, no description). Then say "spoil it" on its own: the same quest goes to full; the other blurred quests stay at nudge.
   - Say "what comes after it? spoil it" in one breath: nothing opens, and the reply ends "Say 'spoil it' on its own to see more."
   - The commands (D268, whole utterance only): full: "spoil it/that/this/me", "spoil it fully", "(the) full answer", "give me / I want / I'd like the full answer", "full spoilers", "spoil <quest name or q: id> (fully)"; partial: "(a) partial answer", "give me / I want / I'd like a partial answer", "a bit more", "a little more", "(give me) a hint", "more of a hint", "more detail(s)". Fillers allowed around them: hey gos, please, ok, um, uh, so, now, then, pls, can you, could you.
   - Press an item chip under **Items for these quests**: Search opens on that item's quests.

## D. GTNH's real book (optional, as gos-dev)

6. This needs an extracted GTNH 2.9.0 pack, for example the zip's `GT New Horizons 2.9.0\.minecraft`. Run it in gos-dev's dev clone:
   ```powershell
   $env:GOS_QUESTS_PACK = "D:\GTNH-2.9.0\.minecraft"   # your extracted pack
   uv run pytest tests/integration/test_quest_guide_harness.py -k real_book
   ```
   Expect `1 passed`. At each stage's first key quest, the view's L0 payload holds no locked quest's spoiler fields.

## E. Board check (D92, D158 (5))

7. The Quests view was built from the design system without the canvas handoff. Compare it with the approved **Workbench views → Quests** board in Claude Design and note any differences in the PR. Screenshots from the e2e run are in `dashboard/test-results/` after `pnpm -C dashboard e2e`.

## What to send back

- `quest-guide-eval.txt` from step 3.
- Any check in step 5 that didn't look as described, with a screenshot.
- Board differences from step 7.
