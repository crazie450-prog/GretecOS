# Gregtec OS: G0 gate review (Phase 0)

2026-11-05 · independent gate reviewer (D41) · reviewed: `origin/main` at `a4b904c` (PRs #2–#17), plus PR #18 (`origin/plan/d93-phase0-answers`, open)

## Verdict

**G0 is not ready for sign-off yet, but it is close.** Two of the three G0 criteria are met: doctor reads Play correctly (pack, Java build, mods), though its exit code is not yet shown, and the live isolation suite passes 9/9 as gos-dev. The third, "path/version VERIFYs resolved", is not met: V01 and V02 are resolved, but V06 is open and no decision re-assigns it. Six open items in the Phase 0 rows block sign-off. None needs new design work:

1. **CI on `main` is red on Windows.** 30 `test_deploy.py` test cases fail on `windows-latest` on every run since `gos deploy` was added. PR #16 was merged over its red run 41995762082, three seconds after its final run started, and PR #18's runs fail the same way.
2. **The world backup job has not been tested on the PC** (WO-0.8 acceptance; runbook step I). No decision defers it.
3. **No refusal of a Claude Code edit of a Play file is in the evidence** (WO-0.8 acceptance). The deny rules are shown to be in force, but the check asks for an observed refusal.
4. **WO-0.5's acceptance check ("Opens in Obsidian") is not in the evidence.**
5. **WO-0.3's "Worlds load" is shown for `lab-ops` only.** `lab-structures` and `lab-progress` are shown to exist, not to load.
6. **V06's `world.log` path is still open** and still assigned to WO-0.6 in §13.2. Only PR #6's text moves it to "WO-6.x"; no decision does.

PR #18 also needs to merge before sign-off: `main` has lost D92 (see the risks section).

Evidence tags used below:
- **[repo]:** this reviewer checked it in the repo, the git history or the GitHub CI logs.
- **[PC]:** Clayton's pasted output from 2026-11-05, as relayed to this review. It is treated as true, but its exact wording was not seen.
- **[PR]:** the implementer's PR description, not re-checked here.
- **Not confirmed:** nothing seen supports it either way.

This reviewer's own run on `a4b904c` (Linux): `uv run --locked ruff check .` gave all checks passed, `ruff format --check .` gave 30 files already formatted, and `uv run --locked pytest -q` gave **226 passed, 40 skipped** [repo].

---

## Phase 0 work orders

### WO-0.1: Toolchain

> **Acceptance:** "CI green on an empty test suite"

**Verdict: met with notes.**

**Evidence**
- PR #2 (merge `c527972`): `pyproject.toml`, `uv.lock`, `.python-version`, `.github/workflows/ci.yml` [repo].
- CI run 41396050785 on `56be86a` succeeded on both OSes [repo].
- PC: uv 0.13.6, Node 26, pnpm 12, Temurin JDK 25.0.5 ZIP with machine `JAVA_HOME` only. The bootstrap clone's pytest gave 192 passed, 25 skipped (A0–A6) [PC].

**Gaps and notes**
- The acceptance was met at the time. **CI on `main` is red today**, though: run 42102085031 on `a4b904c` has `checks (windows-latest)` failing with **30 failed, 221 passed, 15 skipped** [repo]. The cause is under WO-0.8.
- Clayton's 192/25 pytest result predates `gos deploy`. Today's suite has 266 tests. No Windows run of the deploy tests has passed, on CI or on the PC.
- **PR #2 Question 1 (repo visibility) has no recorded answer.** PM05 and PM10 still say the repo is private. CLAUDE.md says it is public, and GitHub reports `visibility: public` [repo]. A D## should settle this, because CI minutes and PM10's licence rationale both depend on it.

### WO-0.2: `gos doctor`

> **Acceptance:** "Doctor output reviewed by Clayton"

**Verdict: met.**

**Evidence**
- PR #4 (merge `4c216b2`): `core/gregtec/doctor.py` and `core/gregtec/gamestate/safe_open.py`. Doctor writes `<GOS_HOME>/data/env.json` and prints the VERIFY list from `core/gregtec/verify.tsv`. `tests/unit/test_doctor.py` has 17 tests, and `test_verify_list.py` keeps `verify.tsv` equal to §13.2 [repo].
- PC: `GTNH-Play pack=2.9.0 java=java17+ game=.minecraft mods=246`. Later, Play, Lab and Export each show 247 mods (D94). Doctor runs from GOS_APP as both `gos.exe doctor` and `python -m gregtec.cli doctor` [PC].
- Clayton ran doctor and relayed the output [PC]. The implementer recorded V01 and V02 as **Resolved** in §13.2 and `verify.tsv` in `3098b27` (PR #14, which reached `main` through PRs #15 and #16) [repo].

**Gaps and notes**
- The exit code and whether any `problem:` lines appeared are not in the relayed output. Doctor exits 0 only when it finds instances and none has a problem (`doctor.py:485`) [repo]. See the G0 row.
- D87 moves the exact stock-pack check to WO-1.9.

### WO-0.3: GTNH-Export and GTNH-Lab, `sync_instances.py`, Lab worlds

> **Acceptance:** "Worlds load; sync is idempotent and respects `sync_exclude`"

**Verdict: partly evidenced.** Sync is shown to be idempotent, but only one of the three worlds is shown to load. Clayton confirming that `lab-structures` and `lab-progress` load closes it.

**Evidence**
- PR #11 (merge `ce4f406`): `core/gregtec/sync.py`, `core/gregtec/config.py`, `scripts/sync_instances.py`.
- `tests/unit/test_sync.py` has 14 tests, including `test_first_sync_copies_and_a_second_changes_nothing` and `test_excludes_are_never_copied_over_or_deleted` [repo].
- PC: Export and Lab were made with Prism Copy. A dry run showed no `Refused`. The F3 setup re-run was all `ok` (GTNH-Play read only, Export and Lab modify). A second sync printed `nothing to do`. Worlds `lab-structures`, `lab-progress` and `lab-ops` exist, and `lab-ops` loaded and ran the D94 smoke test [PC].

**Gaps and notes**
- **Only `lab-ops` is shown to load.** For `lab-structures` and `lab-progress`, the evidence says only that they exist. This is the same gap as WO-0.5's: the acceptance names a check that is not in the evidence. Their game modes are also not confirmed: superflat creative, and survival with cheats on. D88 records `lab-ops` as Creative with cheats.
- **`sync_exclude` on the PC:** the relayed output is `nothing to do`, without the `(N excluded)` count. The exclude behaviour is shown by unit tests only. Today Export's default NEI exclude is a no-op anyway: Export still runs Play's NEI, because the 2.8.172 swap comes in WO-1.2.

### WO-0.4: Anthropic setup and smoke tests

> **Acceptance:** "Haiku with thinking disabled and the SDK on Sonnet both answer; Haiku time to first token measured (V21)"

**Verdict: met with notes.**

**Evidence**
- PR #12 (merge `bb0e705`): `core/gregtec/llm/keys.py` stores the key in the keyring only, and `core/gregtec/llm/smoke.py` runs both lanes. `tests/unit/test_llm.py` has 19 test functions and needs no network [repo].
- PC:
  - `gos key status` printed `WinVaultKeyring, anthropic: stored`.
  - `gos smoke`, fast lane: median first token 917 ms over 5 runs (525–1592 ms), median total 1162 ms.
  - Deep lane through the Agent SDK: `apiKeySource ANTHROPIC_API_KEY`, `tools []`, `MCP servers []`.
  - `Result: every lane answered.` [PC]
- V21 is marked Resolved in §13.2 [repo].

**Gaps and notes**
- **The relayed deep-lane output doesn't name the model.** `smoke.py` pins the deep lane to the Sonnet model (`DEEP_MODEL`, line 44), and the fast lane disables thinking (`test_fast_lane_request_timing_and_median`) [repo].
- **The Console side of the deliverable is not confirmed:** Max linked to the Console org, credits claimed, and workspace `gregtec` with D83's $150 backstop limit. A passing smoke test shows that the key works, not that the workspace limit exists.
- **The price re-check is done** (PR #12) [PR]. Plan §4.4 still shows Fable 5.1's cache read as "—", though PR #12 found $0.25 [repo]. This is doc drift for the PM.

### WO-0.5: Vault template

> **Acceptance:** "Opens in Obsidian"

**Verdict: not met (not evidenced).** One line from Clayton closes it.

**Evidence**
- PR #5 (merged into `wo/0.2-doctor`, reaching `main` through #4):
  - `vault-template/` with `_gos/schema.md` v0 and `glossary.tsv`, including the `say` column (the `platline` alias was added later, in `579aeff`, PR #8);
  - `core/gregtec/vault/deploy.py` and `schema.py`;
  - `tests/unit/test_vault.py` with 13 tests. [repo]
- PC: `gos vault deploy` printed `created`, then `unchanged` on a second run (E). The live test `test_vault_and_logs_are_read_only` is among the 9/9 [PC].

**Gaps and notes**
- No evidence says that the deployed vault was opened in Obsidian.

### WO-0.6: Config tweaks and `check_tweaks.py`

> **Acceptance:** "Script reports all tweaks present"

**Verdict: met with notes.**

**Evidence**
- PR #6 (merged into `wo/0.2-doctor`, reaching `main` through #4; review fixes in `9e0996d`): `core/gregtec/tweaks.py`, `scripts/check_tweaks.py`, and `tests/unit/test_tweaks.py` with 21 tests [repo].
- PC: on Play, `check_tweaks --apply`, then `All tweaks present.` (Clayton ran it). On Lab, all present, plus `Successfully applied 8 Internet Card filtering rules.` after a world load [PC].

**Gaps and notes**
- **V04** is closed by D85, but §13.2 and `verify.tsv` still list it as open, so doctor prints it as open [repo]. D85 says "The Lab alt-tab check still runs in WO-0.6 step 1", and that check is not in the evidence.
- **V06** (ServerUtilities logging keys and the single-player `world.log` path) is assigned to WO 0.6 in §13.2. PR #6 confirms the keys and says the path "is still open; WO-6.x finds it" [PR], but **no decision re-assigns it**. See the G0 row.

### WO-0.7: Build Play

> **Acceptance:** "Play launches to the main menu; `gos doctor` lists its mods and hashes"

**Verdict: met with notes.**

**Evidence**
- PR #10 (merge `d2eefac`) added `scripts/fetch_play_files.ps1` with pinned SHA256s and D88. PR #15 (`06f42c1`, reaching `main` through #16) added the 123Technology 2.2.1.1 and PlanNH 0.2.0 pins and D90 [repo].
- PC:
  - Play was built from `GT_New_Horizons_2.9.0_Java_17-26.zip` on portable Prism 11.1.1 with Prism's Java 25.0.1. The first launch was fine.
  - Doctor printed `mods=246`, then 247 after 123Tech and the PlanNH swap. The jars' SHA256s were checked against the pins (D94). [PC]
- D89 sets Play's maximum memory to 8192 MiB [repo]. Prism's memory setting was not seen.
- The count is consistent with PR #4's "stock 2.9.0 is 242 jars on the Java 17 build". Adding TST, PH, worldedit-gtnh and PlanNH gives 246, and 123Tech makes 247. This is arithmetic only.

**Gaps and notes**
- **Hashes:** doctor hashes by default (`--no-hash` turns it off), but the relayed output shows counts only. `env.json` was not seen.
- **Crash:** one intermittent SDL3.dll crash at the loading screen with the addons in (0xC0000005, then 0xC000041D). The next launch was fine. It is documented in `docs/runbooks/pc_setup_notes.md` with a Lab-first fallback. It doesn't block the acceptance, but it is a risk.
- **GT-Not-Leisure** is left out by D88 and D90, which matches "the addons that load".

### WO-0.8: Claude Code isolation

> **Acceptance:** "From `gos-dev`: writing a test file in Play, `GOS_APP` or `backups\world\` fails, and in Lab succeeds; Claude Code's own edit of a Play file is refused; `gos launch lab` starts Lab; the backup job copies a Lab backup zip"

**Verdict: not met.** One of the four checks passes. The Claude Code refusal is not evidenced, the `gos launch` check is deferred by D80, and the backup-job check is not done. The `gos deploy` deliverable also has red Windows CI.

| Part | Verdict | Evidence |
|---|---|---|
| Writes fail in Play, `GOS_APP` and `backups\world\`, and succeed in Lab | **met** | PC: 8/8 live tests after PR #13's fix (`85c6097`), then 9/9 after `gos deploy`, including `test_deployed_release_is_read_only` [PC]. The tests are in `tests/security/test_isolation_live.py`: `test_play_is_read_only`, `test_gos_app_is_read_only`, `test_world_backups_are_closed`, `test_lab_and_export_are_writable` and others [repo]. The quick wall check (C) gave: dev write ok, GOS_APP denied, Prism root denied, logs readable but not writable [PC]. On CI, the real setup runs as a fresh standard user (`tests/security/test_gos_dev_setup.py`, 11 tests). They pass on `windows-latest` in run 42102085031 [repo]. |
| Claude Code's own edit of a Play file is refused | **not evidenced** | `/status` lists "Enterprise managed settings (drop-ins)", and `/permissions` shows the six deny rules [PC]. `test_claude_code_deny_rules` checks the drop-in's contents on CI [repo]. **No attempted Claude Code edit of a Play file is in the evidence.** The rules are shown to be in force, but the acceptance asks for an observed refusal. Under PM15 the deny rules bind Claude Code's built-in file tools on Windows, so an Edit of a Play file as gos-dev tests it directly. |
| `gos launch lab` starts Lab | **deferred by decision** | D80 moves `gos launch` to WO-2.1. §13.2 V24 is "Partly resolved", with the remainder on WO 2.1. |
| The backup job copies a Lab backup zip | **not met** | Runbook step I (after 30+ minutes of Lab play) is not done [PC]. On CI only, `test_backup_task_copies_play_backup` starts the real task and copies a Play backup zip on the runner, and `tests/unit/test_backup_world.py` covers the script [repo]. No decision defers the PC check. |

**Deliverables outside the acceptance line**

**Setup.** PR #3 (`0f0ebc4`) added `scripts/setup_gos_dev.ps1` and `scripts/backup_world.ps1`. PR #9 (`777e736`) added the D82 read access. On the PC, every B1 check line was `ok`, with owner `DESKTOP-SCIFTF5\linvi`. Claude Code is installed and signed in as gos-dev, and uv, pnpm, node and git run as gos-dev after the `icacls /reset` of the winget packages [PC].

**`gos deploy`.** D80 moved it after WO-0.2, and D91 and D93 define it. PR #16 (`a4b904c`) added `core/gregtec/deploy.py` and `tests/unit/test_deploy.py` (24 test functions, 48 test cases) [repo]. On the PC it deployed commit `a4b904c` into `C:\GregtecOS-app\current`, and the tools run from GOS_APP [PC]. However:
- **Windows CI fails 30 of the 48 test cases in `test_deploy.py`** (in 16 of the 24 test functions, some parametrized) [repo]. The failing runs are 41995762082 and 42102068554 (PR #16's own runs), 42102085031 (`main`, `a4b904c`), and 42103263862 and 42118404688 (PR #18).
- **The error** is `GregtecOS-app lets S-1-3-4 change it, so it isn't the folder setup_gos_dev.ps1 makes`. S-1-3-4 is OWNER RIGHTS. The test folder that the `protect()` fixture builds under pytest's temp directory carries an OWNER RIGHTS allow ACE with full control (mask `0x1F01FF`), and `acl_problem` counts it as an extra writer.
- **Root cause not determined by this review.** It may be in the test fixture (the temp-directory ACL on the runner) or in `acl_problem` itself: an OWNER RIGHTS ACE on a folder owned by Administrators grants nothing beyond what Administrators already have. D91, though, says GOS_APP must be "writable by no one but SYSTEM, Administrators and the user deploying", so the check matches the spec as written.
- **A lead, not verified:** CI runs CPython 3.12.16 and pytest 9.2.0 [repo]. pytest creates its temp directories with `mkdir(mode=0o700)` (checked in pytest 9.2.0's `_pytest/tmpdir.py`), and since CPython 3.12.4 that mode on Windows gives the new directory a protected DACL that includes an OWNER RIGHTS entry (not checked here). That would put the ACE in the fixture, not in Clayton's GOS_APP, which `setup_gos_dev.ps1` made. It does not yet explain how the ACE survives the fixture's `icacls /inheritance:r`, which removes inherited ACEs.
- **Clayton's real GOS_APP passed the same check** [PC], so the wall is not affected. The defect is that the Windows test suite for deploy has never passed anywhere.
- **PR #16's description said** "This PR's Windows CI is their first run". It was merged at 20:17:57Z over the red run 41995762082 (`06f42c1`, failed 08:37Z), three seconds after its final run, 42102068554 (`d4528da`, started 20:17:54Z), started. That run also failed.

**Gaps**
1. Run step I: the Lab backup copy on the PC. Alternatively, record a D## that defers it, with a date before Play's world starts.
2. Make Windows CI green on `main`.
3. Try a Claude Code Edit of a Play file as gos-dev and paste the refusal.

---

## G0 row

> **G0:** "Doctor green; path/version VERIFYs resolved; isolation tests pass" (Reviewer report; Clayton sign-off)

| Criterion | Verdict | Evidence and gaps |
|---|---|---|
| Doctor green | **met with notes** | Doctor reads Play as `pack=2.9.0 java=java17+ game=.minecraft`, with 246 and then 247 mods, and runs from GOS_APP [PC]. The relayed output doesn't state exit code 0 or the absence of `problem:` lines. Not confirmed, but nothing suggests a problem. Paste the exit code (`$LASTEXITCODE`) from the GOS_APP doctor run to close it. |
| Path/version VERIFYs resolved | **not met** until V06 is resolved or re-assigned by decision | **V01** (Java build) and **V02** (instance paths) are resolved in §13.2 and `verify.tsv` [repo]. **V24** (portable Prism and permissions) is partly resolved; its `gos launch` remainder moves to WO-2.1 under D80. **V21** is resolved. **V04** is closed by D85, but the list still shows it open. **V06** is still open and still assigned to WO 0.6: its keys are confirmed, but the single-player `world.log` **path** isn't, and no decision moves it. Strictly, that is a path VERIFY in a Phase 0 WO, so either resolve it in a Lab session or re-assign it by decision (most likely to WO-6.1, whose watchers tail that log). |
| Isolation tests pass | **met** | 9/9 live tests as gos-dev, including `test_deployed_release_is_read_only` [PC]. The CI setup tests pass on Windows [repo]. |

**G0 verdict: not met yet.** Two G0 criteria are met (doctor green with notes, isolation tests), and the VERIFY criterion is not met until V06 is settled. The gate also checks the whole phase against its acceptance criteria (§0), and six Phase 0 items are open. Required before sign-off:

1. **Windows CI green on `main`** (WO-0.1, WO-0.8). Fix the `test_deploy.py` failures. Don't merge further PRs over a red Windows job.
2. **Backup job on the PC** (WO-0.8): do runbook step I, or record a decision that defers it.
3. **Claude Code refusal** (WO-0.8): as gos-dev, try a Claude Code Edit of a Play file and paste the refusal.
4. **Obsidian check** (WO-0.5): Clayton confirms that the vault opens.
5. **Lab worlds load** (WO-0.3): Clayton confirms that `lab-structures` and `lab-progress` load.
6. **V06 path**: resolve it, or re-assign it by decision.
7. **Merge PR #18** so that D92 is back in `docs/decisions.md`. Its CI shows the same Windows failure, so fix CI first or merge knowingly.

Should do (not blocking):
- Update §13.2 and `verify.tsv`: mark V04 resolved (D85), and change V24's "8/8" to 9/9.
- Fix the Fable 5.1 cache-read price in §4.4.
- Answer PR #2's repo-visibility question with a D##.
- Answer PR #14's open questions, which no decision records: Q2 (is 525–1592 ms the per-run first-token range? V21 is already marked Resolved on that reading) and Q4 (run the gotcha-3 PATH command as gos-dev; evidence C, Claude Code signed in as gos-dev, makes it likely moot). D93 answers Q1 and Q3.
- Confirm the Lab worlds' modes.
- Confirm the Console workspace limit (D83).

Clayton's sign-off: pending

---

## Carry-overs into Phase 1 and later

| Item | Goes to | Deferred by |
|---|---|---|
| `gos launch lab\|export` and the rest of V24 | WO-2.1 (supervisor) | D80 |
| Exact stock-2.9.0 mod check against DAXXL's manifest | WO-1.9 | D87 |
| NEI 2.8.172 swap in Export (V03) | WO-1.2 | Plan §8 and §13.2 (not a Phase 0 item) |
| V27: read-safety on 2.9.0's replace-style saves | WO-6.1 save loop | D84 |
| D02 world gate: a versioned GTNL release with `28007498`, GTNL #575, and the PH #323 re-test once GTNL is in | before Play's survival world (D02) | D93, D94 |
| SDL3 / BetterLoadingScreen crash fallback, if it recurs | Lab trial with a temporary `[sync.lab] exclude` | D93 (5); `pc_setup_notes.md` |
| V06 `world.log` path | WO-6.x per PR #6 | **No decision yet**: needs one, or resolve it now |
| Lab backup-job check (runbook step I) | not assigned | **No decision yet**: needs one, or do it now |
| Windows CI for `test_deploy.py` | not assigned | **No decision**: fix it before Phase 1 work merges |

---

## Risks

1. **Red Windows CI is becoming normal.** GOS runs only on Windows, and only the Windows job exercises the ACL, junction and sharing code. With that job red on `main` and on PR #18, a real Windows regression would look like the known failure. PR #16 was merged over its red run 41995762082, three seconds after its final run started. (Confirmed)
2. **The append-only decision log was edited by a merge.** Resolving a conflict in `d4528da` dropped D92 from `docs/decisions.md`: it is present at `aca5b30` and missing at `a4b904c` (Confirmed). PR #18 restores it. Nothing in CI checks the log. A check that IDs stay contiguous, like `test_verify_list.py` does for §13.2, would catch this. That is a suggestion, not a requirement.
3. **No Lab backup copy on the PC yet.** The backup job has copied a real zip only on the CI runner. Play has no world yet, so the exposure starts with day one, but the safety net (§4.6 residual risk) must be proven before then. (Confirmed: not done)
4. **The wall has known residual paths, each accepted by decision.**
   - gos-dev runs on Clayton's desktop through `runas` and can send input to his windows (D82, accepted).
   - Mods and configs that Claude Code puts in Lab or Export run as Clayton when those instances launch (§4.6).
   - Claude Code's deny rules don't bind subprocesses on Windows, so the ACLs are the wall (PM15). The 9/9 live result covers the ACLs, not those residual paths.
5. **The deploy ACL check is strict.** It refused a temp folder with an OWNER RIGHTS ACE on CI. If a real GOS_APP ever gets such an ACE (an admin tool, a restore), deploy will refuse until the setup is re-run. That fails safe, but it would block redeploys. (Likely; depends on the CI root cause)
6. **Addons.** GTNL #575 is a memory leak when loaded with TST, not rechecked on 2026-11-05 (§13.1; last checked 2026-11-01). It matters for long AFK sessions (D46) once GTNL is added. The intermittent SDL3 crash was seen with the addons in, but it happens at startup, when BetterLoadingScreen (not one of the D88/D90 addons, so part of the base pack) opens its window (`pc_setup_notes.md`), so it is a launch-time risk. (Verify)
7. **Some evidence is reported, not seen.** The PC results above are Clayton's relayed outputs. Exit codes, the excluded counts, the mod hashes and the Lab worlds' modes were not in what this review saw. Pasting the raw outputs into PR #18 or a G0 PR comment would make the record complete.
