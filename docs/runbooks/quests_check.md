# Quest frontier and tier check (WO-1.5)

WO-1.5's acceptance, on Clayton's PC: **the frontier matches the in-game book in `lab-progress`**, and **the tier is right for 3 progression states**. Claude Code has checked both against a self-authored book and against GTNH 2.9.0's real DefaultQuests (`tests/unit/test_quests.py`, `tests/integration/test_quests_pack.py`), but only the game itself can say what the book shows. About 25 minutes.

**Where commands run.** Game commands (`/bq_admin ...`) go in Minecraft's chat in the Lab. The `gos` commands run as **gos-dev** in its dev clone, on branch `wo/1.5-quests`. They only read the Lab world and write to gos-dev's own dev `GOS_HOME`. Nothing here touches Play.

**Frontier**, as GOS defines it (D113): quests that are unlocked (their prerequisites are met under the quest's logic), shown in the book, in a chapter the book lists, and either not complete (`available`) or reset by their repeat timer so they can be done again (`repeatable`). In the book, those are the quests whose icons are lit but not ticked off, plus repeatable quests, which have their own frame. Locked quests the book only previews (dark icons, shown because their prerequisites are unlocked) are not on the frontier. `gos quests frontier` prints each quest's state in its last column.

## 0. Set up (once)

In gos-dev's PowerShell:

```powershell
cd C:\GregtecOS-dev\GretecOS
git fetch origin
git switch wo/1.5-quests
uv sync
```

Check the world's folder name: it is the folder in `GTNH-Lab\.minecraft\saves\` (WO-0.3 named it `lab-progress`). If it differs, use that name wherever `lab-progress` appears below.

**Read only while the game is paused.** BetterQuesting saves when the world saves, and Windows refuses its save while another program has the file open (D84). So before each `gos quests load`, press **Esc** in game. That saves the world (the log says `Saving and pausing game...`), and nothing saves again while the menu is open. Keep it open until the load has finished.

**Book settings.** GTNH's book has a **View Mode** button in the column of buttons on its left edge (hover it: the tooltip says *View Mode*). It must be **off** (icon dim, tooltip *Click to enable*): with it on, the book also lights quests that aren't unlocked yet, and nothing would match. Check it once when you first open the book.

**Quests completed by command** (`/bq_admin complete`) are marked done with completion time 0 and their reward unclaimed; `quest_progress.completed_ts` shows that 0. That is expected, and GOS still counts them done.

## 1. State A: a new world (expect the stone stage)

1. Launch **GTNH-Lab** and open `lab-progress` (survival, cheats on).
2. If anyone has already done quests in this world, reset them: `/bq_admin reset all`.
3. Empty your inventory: `/clear`. BetterQuesting completes item quests by itself when the items are in your inventory, so anything you carry could finish a quest between the load and the comparison. Keep the inventory empty until the end.
4. Press **Esc** and leave the menu open.
5. As gos-dev:

   ```powershell
   uv run gos quests load --instance lab --world lab-progress
   uv run gos quests frontier
   ```

6. **Expect.** `load` prints `Quest book: the world's QuestDatabase.json` with `3814 quests in 46 quest lines`, your player name and `0 quests complete`, and `Tier detected: stone (GT voltage tier 0)`. `frontier` lists one quest: `And So, It Begins  Your First Night  q:...  available`.
7. **Compare.** Close the menu and open the quest book (the **`** key). In **And So, It Begins**, *Your First Night* should be the only quest that is lit and not done, and no other chapter should have one.

## 2. State B: LV (expect LV)

1. In chat: `/bq_admin complete AAAAAAAAAAAAAAAAAAAARg` (*Steam-a-licious!*, the LV key quest). BetterQuesting may ask you to claim its reward; leave it.
2. Press **Esc**, then as gos-dev:

   ```powershell
   uv run gos quests load --instance lab --world lab-progress
   uv run gos quests frontier --line "Tier 1 - LV"
   uv run gos quests frontier --line "Tier 0.5 - Steam"
   ```

3. **Expect.** `Tier detected: LV (GT voltage tier 1) from q:AAAAAAAAAAAAAAAAAAAARg (Steam-a-licious!)` and, on 2.9.0's book (version 2188), `Frontier: 4 quests`. The LV list has *Welcome to Tier 1, LV* and one more quest; the Steam list has *Safety First*.
4. **Compare** both chapters in the book. Each quest GOS lists should be lit and not done there, and the chapter should have no other lit, unfinished quest. Order doesn't matter: GOS lists top to bottom, then left to right.

## 3. State C: HV (expect HV)

1. In chat: `/bq_admin complete AAAAAAAAAAAAAAAAAAAAoA` (*Tier 3 (HV)*, the HV key quest).
2. Press **Esc**, then:

   ```powershell
   uv run gos quests load --instance lab --world lab-progress
   uv run gos quests frontier --line "Tier 3 - HV"
   ```

3. **Expect.** `Tier detected: HV (GT voltage tier 3) from q:AAAAAAAAAAAAAAAAAAAAoA (Tier 3 (HV))` and, on 2.9.0's book, `Frontier: 17 quests`, 4 of them in **Tier 3 - HV**.
4. **Compare** the **Tier 3 - HV** chapter with the list, as in state B. The quests that need only *Tier 3 (HV)* are on it now.

## 4. The override (D64)

```powershell
uv run gos quests tier --set MV
uv run gos quests tier --clear
```

The first prints `Tier: MV (GT voltage tier 2) (override)` and `Detected from quests: HV`. The second prints `Tier: HV ... (quests)`. While an override is set, `gos quests load` also prints `Tier in use: MV ..., your override`, so run `--clear` before going on.

## 5. A repeatable quest (expect `repeatable`)

*Chef Training* (in **Feeding Yourself**) repeats as soon as its reward is claimed, so it shows the repeatable state without waiting for a timer.

1. In chat: `/bq_admin complete AAAAAAAAAAAAAAAAAAACmQ` (the quest it needs), then `/bq_admin complete AAAAAAAAAAAAAAAAAAADWg` (*Chef Training*).
2. Open the book, go to **Feeding Yourself**, open *Chef Training* and claim its reward (a loot bag), then `/clear` again so the bag isn't in your inventory. Within about ten seconds (BetterQuesting checks its timers that often) the quest's icon changes to the repeatable frame.
3. Press **Esc**, then:

   ```powershell
   uv run gos quests load --instance lab --world lab-progress
   uv run gos quests frontier --line "Feeding Yourself"
   ```

4. **Expect.** `load` says `4 quests complete, 1 of them repeatable again` and, on 2.9.0's book, `Frontier: 21 quests`. The list has four rows: *Pumpkin Pie*, *Potato Cakes* and *Fried Eggs* (`available`), and `Feeding Yourself  Chef Training  q:AAAAAAAAAAAAAAAAAAADWg  repeatable`.
5. **Compare** the **Feeding Yourself** chapter: each `available` quest lit and not done, *Chef Training* in the repeatable frame, and no other lit, unfinished or repeatable quest.

## 6. Optional: everything done

`/bq_admin complete all`, **Esc**, then load again. Expect `Tier detected: UXV (GT voltage tier 13)` and an empty frontier (`# 0 rows, 0 quests`): the command marks every quest's tasks done, so no repeatable quest is open again until you claim its reward. Finish with `/bq_admin reset all` if you want `lab-progress` clean for the next test.

## What to send back

- For each state, whether the tier line matched (stone, LV, HV), and whether each compared chapter matched (including **Feeding Yourself** in step 5).
- For any mismatch: the chapter, the quest's name, and what the book showed (locked, lit, done, repeatable, hidden). Also the `gos quests frontier --line "<chapter>"` output for it.

If a chapter has a quest that you can't see but GOS lists, or the other way round, the visibility rules in D113 are the suspect. Claude Code fixes them from your notes.
