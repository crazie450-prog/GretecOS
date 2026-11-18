# Building themes by voice (WO-10.6)

WO-10.6's acceptance on Clayton's PC: **Clayton creates and refines a theme by voice** (plan 8 row 10.6). Claude Code has run the same path end to end with a fake model (`tests/unit/voice/test_theme_by_voice.py`: the voice client's text, the router, the real Claude Code CLI in the deep lane, `theme_save` on the MCP server, the spoken result), and checked both starter themes against a client harness run's block catalog (`tests/integration/test_themes_harness.py`). What is left is the real voice, the real model and his own catalog. About 15 minutes; it spends a few deep-lane tasks of API credit (each under `[budget] task_max_usd`).

**Where it runs.** Everything here runs in GOS on Clayton's own account (the core, the voice client, the deep lane) and reads or writes only `GOS_HOME` (the vault's `themes/`). Nothing touches Play or the Lab; no block is pasted (shells come with WO-10.7, and pastes are Lab-only).

## A. The starter themes against your catalog

1. After WO-10.3's catalog (runbook `palette_export.md`: an export with the palette pass, `gos export collect`, `gos db build`):

   ```powershell
   uv run gos theme list
   uv run gos theme check
   ```

2. **Expect** `th-foundry v1 shipped Foundry ...` and `th-marble-works v1 shipped Marble Works ...`, then `ok      th-foundry v1 (catalog run ...)` and the same for `th-marble-works`, exit code 0. A `REFUSED` line names the entry and why (a block your pack doesn't have, or a meta it doesn't place): report it in the PR; the starters use only GT and vanilla blocks.

## B. Create and refine a theme by voice

With the core and the voice client running (runbook `voice_client.md`) and the key stored:

1. Hold the talk key and say: **"Make a new theme called copper works with dark concrete walls and big glass windows."** GOS says it handed that to the deep lane, then, when the task is done, something like **"Saved Copper Works as a new theme, version 1."**
2. Say: **"Refine the copper works theme with reinforced glass windows."** Expect **"... version 2."**
3. Say: **"Change the copper works theme to flat roofs."** Expect **"... version 3."**
4. Say: **"Refine the copper works theme with chests as trim."** Expect a first sentence that names the chest and says it has a tile entity (shells hold plain blocks only), and no new version.
5. Check the vault:

   ```powershell
   uv run gos theme list
   dir "$env:GOS_HOME\vault\themes", "$env:GOS_HOME\vault\themes\versions"
   ```

   **Expect** `th-copper-works v3 vault`, `themes\th-copper-works.md`, and `versions\th-copper-works--v1.md` and `--v2.md`. Open the current one: `change: v2 ...` and `change: v3 ...` lines, the palette table with `<registry name>@<meta>` blocks only.

## C. The selection modes

```powershell
uv run gos theme select --theme th-copper-works --prompt "taller" --line-type smelting
uv run gos theme select --prompt "a glass dome over the reactor"
uv run gos theme select --line-type chemistry
```

**Expect** `mode: theme`, `mode: prompt` (`theme: -`) and `mode: blank` (a theme that fits chemistry, with its adaptation). Until WO-10.7 records builds, blank mode has no "used nearby" themes to follow.

Report in the PR: the spoken lines of B1-B4, the `gos theme list` output, and anything the model did differently (it may word the answers its own way; the version numbers and the refusal reason must match).
