# Decisions log

Append-only. The binding decisions D01–D77 and the PM defaults PM01–PM15 are in [implementation_plan.md §2](implementation_plan.md#2-decisions). This file records every change after plan v1.1 (2026-11-01).

- Never edit or delete an entry. To change one, append a new entry that names what it supersedes.
- IDs continue from **D78**. A change to a PM default also gets a D## and supersedes the PM##.
- Each entry has a date and a source: a PR, a gate review, or Clayton's message.
- The PM folds these entries into §2 at the next plan version.

| ID | Date | Topic | Decision | Supersedes | Source |
|---|---|---|---|---|---|
| D78 | 2026-11-01 | Crop breeding | Added: CropsNH breeding guides from the start, stat plans, and from MV an OC breeding station that runs the deterministic Crop Breeder and Seed Generator unattended. No field robot, because 2.9's CropsNH removed OC support for planted crops. Folded into plan v1.2 §2.4 and §7.17 | — (new feature) | Clayton, PM chat; research I |
| D79 | 2026-11-01 | Dashboard design | WO-2.0 is done in Claude Design (canvas + Design System artifact); Claude Code builds from the handoff from WO-2.6 on. Folded into plan v1.2 §2.4 | Refines WO-2.0 and WO-2.6 | Clayton, PM chat |
| D80 | 2026-11-02 | WO-0.8 scope | WO-0.8 setup is delivered as `scripts/setup_gos_dev.ps1` (gos-dev account, folder ACLs, portable Prism, Claude Code managed-settings deny rules, world backup task) plus `scripts/backup_world.ps1`. `gos deploy` moves to after WO-0.2 (it needs the `gos` CLI), and `gos launch lab\|export` to WO-2.1 (it needs the supervisor). Until `gos deploy` exists, the setup script copies `setup_gos_dev.ps1` and `backup_world.ps1` into `GOS_APP\scripts`, and Clayton launches Lab and Export himself | WO-0.8 row in §8 (`gos deploy`, `gos launch`) | Clayton, 2026-11-02 session; PR #3 |
| D81 | 2026-11-02 | Age themes | The dashboard's look follows his voltage age: ten themes from Stone/Steam to UHV and up, plus two comfort modes (Calm Ops, Field Manual) he can pick at any age. Only tokens, frames, textures and core art change; layout, components and the §7.14 rules stay fixed. The theme follows tier detection (D64). A tier-up re-themes once (1.5 s) and writes a recap of the finished age to the journal. Ages beyond his tier stay hidden like spoilers, and he can pin any age he has reached. WO-2.0 adds a token set and core art per theme; WO-2.6 adds switching, pinning and the comfort modes. Ages 1–3 and the comfort modes are needed by day one. Spec: [design/age_themes.md](design/age_themes.md) | Refines D51 and §7.14 rule 3; extends WO-2.0 and WO-2.6 | Clayton, PM chat 2026-11-02 |
