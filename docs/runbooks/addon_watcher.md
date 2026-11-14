# The addon watcher end to end (WO-11.1)

Plan §7.13, D69, QA:D9; decisions D329–D331. When an addon jar in Play changes, GOS notices
before the game starts, asks after it quits, and on yes exports, rebuilds the recipe DB, writes
a signature diff per mod to the vault inbox, opens curation tasks for new controllers and flags
the plans the change affects, with a one-click re-solve in the Designer.

## How it works

1. **Pre-launch** (`gos hook pre-launch`, WO-1.4): Play's fingerprint against the last export.
   A mismatch sets the stale flag (`data\fingerprint\state.json`). The core then says once,
   by voice and on the dashboard, "Heads up: your mods changed since the last export, so recipe
   data is stale. I'll ask about exporting when you quit." The dashboard's "Recipe
   data is stale" alert stays while it holds. Once per change of Play's fingerprint, never on
   every launch.
2. **Post-exit** (`gos hook post-exit`): if Play's fingerprint still differs from the last
   export, the question "Mods changed. Export now?" goes to three places at once:
   - the dashboard (and the phone): an approval card with **Export now** and **Not now**, and
     a countdown;
   - the voice: "Mods changed. Export now? Say confirm or cancel." Plain **confirm** answers
     yes and **cancel** no (a pending world action, if any, takes "confirm" first, D59);
   - the Windows dialog, as before.
   The first answer wins; none in 5 minutes counts as No. The question lives in
   `GOS_HOME\run\export-question.json` while it waits.
3. **On yes**, `gos export run` (under the instance lock, so Play can't start meanwhile): sync
   Play to Export, launch Export (autoexport), record the run, **collect** it into
   `data\exports\`, **build** it into `data\gos.sqlite`, then:
   - the signature diff: `inbox/ib-YYYYMMDD-export-diff-YYYY-MM-DD.md` in the vault (counts,
     a table by mod: recipes added, removed, changed by content hash, machines added; the
     changed recipes with what moved) and the full report
     `data\reports\export-diff-<old>-<new>.recipes.txt`;
   - curation tasks: `data\curation\controllers-<run>.tsv` lists each new multiblock controller
     that has no curated machine facts or no structure yet (curated rows go in
     `data/curated/machine_facts.tsv`; the structure is a Lab capture, plan §7.10);
   - flags: every plan citing a recipe (or crop) whose content changed or that vanished is
     marked "affected by update" (`plan.affected_run`). The Designer shows a banner on it with
     the recipes it hit and a **Re-solve** button; Saved plans marks it too. A plan flagged by
     an earlier update keeps that flag; one that matches its own export again is cleared.
4. Nothing changed: nothing is asked and nothing runs.

The pack-update playbook (`gos pack update`, [pack_update.md](pack_update.md)) still loads its
own runs and flags at its own `flag` step.

## Clayton's PC steps for WO-11.1

After the PR is merged: `uv run gos deploy` from your clone (it installs into GOS_APP and
restarts GOS). `$gos` below is `GOS_APP`'s `gos.exe` as in the other runbooks.

1. **A real addon jar swap in Lab (diff and flags, no Play change).** Close every GTNH client.
   In Prism, in **GTNH-Lab** only, replace one addon jar with another release of it (keep the
   original to put back), e.g. Programmable Hatches or 123Technology from their GitHub releases,
   one that loads on 2.9.0. Then run the pack-update dry run on it, which builds Lab's export
   into a sandbox DB and diffs it against Play's:
   ```powershell
   & $gos pack update --start 2.9.1-sim --simulate
   & $gos pack update --confirm backup --note "addon dry run"
   & $gos pack update
   ```
   At "ADOPT?": `diff.txt` shows the addon's mod in the by-mod table with its recipes added,
   removed and changed, and the plans that cite them under "Plans". Answer
   `& $gos pack adopt no`, then put the original jar back in Lab.
2. **The post-exit chain on Play (only while Play has no survival world, WO-0.7; you change
   Play yourself in Prism; reversible).** Accept one Designer line first (any). Then, with every
   client closed, swap the same addon jar in **GTNH-Play** in Prism (keep the original).
   - Launch Play: within a few seconds the dashboard shows "Recipe data is stale" and the voice
     says the stale line **once**. Launch and quit again without changes to the jar: it isn't
     said again.
   - Quit Play: the dashboard card "Mods changed. Export now?" appears with a countdown, the voice
     asks, and the dialog shows. Say **confirm** (or click **Export now**). The dialog and the
     card go away; Export starts and quits by itself.
   - Then check: the vault has `inbox\ib-<date>-export-diff-<date>.md`; `data\reports\` has the
     `.recipes.txt` report; if the addon brought a new multiblock, `data\curation\controllers-*.tsv`
     lists it; any plan citing a changed or removed recipe shows "Affected by update" in the
     Designer, and **Re-solve** opens new routes on the new export.
   - Put the original jar back in Prism and repeat the quit: the next diff note lists the plans
     as no longer affected (`plans_cleared`).
3. **Not now** works too: on another quit with a change, click **Not now** (or say cancel):
   nothing exports, recipe data stays stale, and you're asked again next quit.

If the core isn't running, only the dialog asks (as WO-1.4 did) and the rest of the chain is the
same.

## Tests

- `tests/unit/test_addon_watcher.py` (every CI run): the whole chain on fake Prism instances and
  tiny exports: the jar swap, pre-launch's notice, post-exit's question answered on the
  dashboard, the export under the lock, the note, the curation task, the flags, a round where
  only a config changed (nothing new flagged) and a revert (flags cleared); the question, the
  notices, the routes and the card on their own.
- `tests/integration/test_addon_watcher_harness.py`: the same on two real client-harness
  exports (the second a "GoodGenerator 1.1" made from a copy of the first), with an accepted
  Designer line, its flag in the Designer view and its re-solve. About 4 minutes, so it runs on
  request, not in CI's mod job:
  `GOS_REQUIRE_HARNESS_RUN=1 GOS_ADDON_WATCHER_HARNESS=1 uv run pytest tests/integration/test_addon_watcher_harness.py`
  after `cd mod && ./gradlew runClient25 -Pharness` (mod/README.md).
