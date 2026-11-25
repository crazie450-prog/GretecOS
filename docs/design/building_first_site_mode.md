> **Plan-free buildings (WO-10.17, D424)** reuse this design's floor and door rule without the machines: see [`plan_free_and_live_designer.md`](plan_free_and_live_designer.md) §3.

VERDICT: derived-interior wins. The final design below takes its core (the model only builds, GOS derives the floor and fits the machines) and grafts on three pieces: optional zones from rooms-placer, the `prior` pass and `extra_open` reach from rooms-placer, and the program/site split and compile-once gate from model-places. Model-placed pins are left out of this PR (Q6).

Checked in the repo for this review:
- docs/decisions.md ends at D421, and D389 is used twice (L299 and L301). The new entry is D422, and the PR should mention the clash. (Since renumbered: the WO-3.10 pair is D426 and D427, D428.)
- D385 limits the sandbox script to shell geometry.
- D386 records Clayton saying "don't worry about practicality", "the quicker we can [get to testing], the better", and that the procedural generator is being phased out.
- shell_bakeoff.REPAIR_ROUNDS = 2 (L120). shell_brief.PROMPT_VERSION = 5.
- layout_schema.json: corridors, chases and entrances all have minItems 1, and every placement requires a chase.
- Spec bounds start at y 0, so y 0 is already the first air layer and the world floor is below it.
- The viewer's ghosts are only machines and keep-outs (shell_versions L741). A fitted layout therefore needs no dashboard change.

=====================================================================
## 1. Scores (1-10, higher is better)

| Criterion | rooms-placer | model-places | derived-interior |
|---|---|---|---|
| 1 Creative freedom | 7 | 9 | 9 |
| 2 LLM reliability / cost | 6 | 3 | 8 |
| 3 Determinism + CLAUDE.md | 9 | 6 | 9 |
| 4 Implementation risk/size (10 = low risk) | 6 | 5 | 5 |
| 5 Backward compatibility | 9 | 8 | 9 |
| **Total** | **37** | **31** | **40** |

### rooms-placer
1. **Freedom 7.** The outline, number of halls and height are all free. But the model must express its building as up to 16 ground-floor axis boxes, and machines inside go in spine-and-row bands, so the interior still reads as rows in a box (its own risk 1).
2. **Reliability 6.**
   - The model must keep a second, redundant description (rooms) consistent with its blocks.
   - Script-built buildings must duplicate the room coordinates by hand in the JSON (its risk 7). That is the error-prone kind of output LLMs fumble.
   - A wall drawn inside a declared room becomes a fit refusal.
   - It raises REPAIR_ROUNDS to 3, so it costs more.
3. **Determinism and compliance 9.** Code places everything, so D389's reason holds.
4. **Risk 6.** About 1,850 core lines. Generalising the band packer around obstacles is fiddly.
5. **Back-compat 9.**

### model-places
1. **Freedom 9.** It is the only design with upper floors and exact model intent.
2. **Reliability 3.**
   - The model hand-packs 30-200 rotated inclusive boxes as literal JSON, because D385 forbids a script from computing placements.
   - Expect most first answers to fail on overlap or reach, at about 3x today's cost per design.
   - The design itself recommends an auto-fitter as a follow-up.
3. **Determinism and compliance 6.**
   - The validator is deterministic, but the design reverses D389 and runs into D385.
   - Its reach is 6-connected with no gravity (it admits that).
   - "Covered" capacity can be gamed by a flat slab.
4. **Risk 5.** About 2,100-2,500 lines, with heavy brief and fake work. The new east/west facings expose the D305 meta-rotation TODO in the build guide. That TODO affects every building-first design, but most acutely this one.
5. **Back-compat 8.** Marking the mode by an "arrange" op hidden inside ops is clever, but it couples mode detection to the op list.

### derived-interior
1. **Freedom 9.** The model just builds. Columns, arcades, inner walls, L, ring and courtyard plans become obstacles automatically, with no extra declarations. It is single-storey only for now.
2. **Reliability 8.**
   - The answer schema barely grows: an optional entrance.
   - The `covered()` sandbox helper lets a script size itself until it meets NEED, which is the cheapest guard against repair rounds.
   - Remaining failures (volume or packing shortfall) come back with concrete deltas.
3. **Determinism and compliance 9.**
   - fit() is a pure function of cell positions, site, program, options and hint.
   - The building hash covers positions only, so a palette change keeps machine positions.
4. **Risk 5.** It is the largest estimate (about 2,800-3,300 lines), with a 700-line fitter. Its "no reach search needed" claim holds: a keep-out's ring encircles its body inside R, so placing it cannot disconnect R. That removes the costliest part of rooms-placer's fallback.
5. **Back-compat 9.** It is mode-gated, old ids are unchanged, and the procedural path stays layout-first (with an optional `fit=True` for CI).

**Winner: derived-interior.**
- What it takes from the others:
  - from rooms-placer, `prior` (keep valid placements on revise or refit) and `extra_open` for reach;
  - from model-places, the `program()` split out of plan_layout and `design(compiled=)` so the script runs once.
- What it adds from rooms-placer in a safer, optional form: zones. They are not required to match blocks; they only steer the fitter.
- Scope cuts so it fits one PR:
  - no dashboard or TS work (the viewer shape is unchanged);
  - no new API route;
  - no pins;
  - no procedural building mode beyond the CI `fit=True` switch;
  - REPAIR_ROUNDS stays 2 until it is measured.

=====================================================================
## 2. Synthesized design: "site mode" (D422)

**Principle.** The model, or `procedural(fit=True)`, designs any building inside a SITE box sized from the machine program. GOS compiles it once, derives the sheltered floor, checks capacity, and fits every machine deterministically in any of 4 facings around the building's own obstacles. It emits a normal gos-layout v1, then runs the existing gate against it, where zero breaches is an invariant. Machine positions stay code-owned (D389, CLAUDE.md); the building drives them. Layout-first stays the default for every existing caller and every saved build.

### 2.1 Files, in implementation order (one PR, branch `wo/10.16-site-mode`, with suggested commit boundaries)

**Commit 1: engine, no wiring**
1. `core/gregtec/build/layout.py`
   - Extract `program(conn, plans, opts) -> Program` (the `_Footprints` + `instances()` part of plan_layout L415-457). plan_layout calls it, and its output must stay byte-identical.
   - Extract `keepout_of(body, c, ct)` from L477-485.
   - `service_cells` / `reach_all` gain `extra_open: frozenset[Cell] = frozenset()`. The default keeps old ids.
   - `Options` gains `buffer` (LIMITS 25, range 0-100), left out of the JSON and id while it is 25 (like `_NEW`).
2. `core/gregtec/build/footprints.py`: `Footprint.from_json` (the inverse of `to_json`, L269), for program.json.
3. `core/gregtec/build/layout_schema.json` (additive):
   - `corridors[].kind` gains "aisle";
   - optional top-level `fit` (object, below).
4. NEW `core/gregtec/build/floor.py`, about 120 lines, pure with no I/O. It holds `derive(cells, site) -> Floor` and `measure(cells, site, need) -> dict`. Both fit.py and the sandbox `covered()` helper import it.
5. NEW `core/gregtec/build/fit.py`, about 550 lines: `fit(program, cells, site, opts, hint=None, zones=(), prior=None) -> FitResult(layout | None, reasons: list[str], stats: dict)`.
6. NEW `tests/unit/test_build_floor.py`, `tests/unit/test_build_fit.py`, and updates to `tests/unit/test_build_layout.py`.

**Commit 2: gate and store**
7. `core/gregtec/build/shell.py`:
   - `Spec.mode: Literal["layout","site"] = "layout"`, plus `site` and `need`;
   - `spec_for_site(program, site, swatches, opts)` with clear = ();
   - `site_of(program, opts)`;
   - `design(..., compiled=None)` (the palette and max_blocks checks reuse the compiled cells);
   - `spec_from_layout(..., bounds=None)` (no second MARGIN or relief);
   - `script_facts` in site mode;
   - `build_id` gets conditional keys;
   - `procedural(..., fit=True)` composes on the site spec.
8. `core/gregtec/build/sandbox_child.py`:
   - the `covered()` helper (imports gregtec.build.floor before lockdown; check the import allowlist);
   - L55 wording: "the layout" becomes "the site".
9. `core/gregtec/build/shell_store.py`:
   - `make(mode="site")` branch;
   - save program.json;
   - `notes()` fit line;
   - `rebuild`/`cells_of` branch;
   - `check_layout` site branch.

**Commit 3: model-facing**
10. `core/gregtec/build/shell_brief.py`:
    - PROMPT_VERSION 6 (site mode; v5 text kept for layout mode);
    - SCHEMA adds the optional `entrance` and `zones`;
    - `parse()` returns them;
    - `_body` prints the PROGRAM, NEED and services;
    - `repair_brief` repeats NEED.
11. `core/gregtec/build/shell_fakes.py`:
    - existing fakes become site-aware;
    - new `fake-cramped` and `fake-tower`.
12. `core/gregtec/build/shell_bakeoff.py`:
    - Case gains `mode` (new runs default to site; saved runs load as layout);
    - `prepare` and `_preview` use the program and site;
    - new score columns.
13. `core/gregtec/agents/tools/shells.py` and `catalog.py`:
    - GATE text;
    - `build_request(entrance?, zones?)`;
    - result head carries the fit line;
    - `build_revise` gets a site branch.

**Commit 4: versions and CLI**
14. `core/gregtec/build/shell_versions.py`:
    - `revise` site branch (program.json + parent layout.json as prior; never structure rows, D367);
    - `refit(build)` = a new version with the same ops, by "procedural", asked "refit".
15. `core/gregtec/build/shell_revise.py`: in site mode, the D368 "never lower than the layout's top" rule is dropped and the fit gate enforces height.
16. `core/gregtec/build/shell_cli.py`:
    - `gos shell refit BUILD`;
    - `gos shell design --mode site|layout`;
    - help text.
17. `docs/decisions.md`: D422, partly superseding D316 (interior/origin), D317 (repair does not apply to fit refusals), D368 and D389. Note the duplicate D389 id (since renumbered, D428). Also the plan §8 WO line.

No dashboard or TS changes: layout.json and the ghost shapes are unchanged. The Designer copy and a `/api/shells/{build}/layout` route are a follow-up.

### 2.2 Data contracts

**Answer (SCHEMA v6, site mode).** The mode comes from the request or the saved build, never from the answer's keys.
```
{"script"?: str, "ops": [...],
 "entrance"?: {"side": "north|east|south|west", "at": [x, z]},
 "zones"?: [{"kind": "machines"|"open", "lo": [x,y,z], "hi": [x,y,z]}]  // <=12, inside SITE
}
```
- **zones** are optional steering. "open" means no keep-out may touch the zone (a grand foyer, an atrium, a throne-room nave). If any "machines" zones exist, keep-outs must lie inside their union.
- Zones need not match the blocks. The fitter simply masks R with them.
- In layout mode, parse() still accepts only {"ops"} and {"script","ops"}, as today.

**Program (`program.json`, saved per site build; lets revise and refit run without plans or structure rows):**
```
{"v":1, "options":{...fitter options...},
 "footprints":{key: Footprint.to_json()},           // distinct only
 "instances":[{"id":"m1","plan","node","role","mte","kind","name","tier","variant","source","footprint":key}],
 "services":[{"from":"plan#node#k","to":"plan#node#k","what":...}],   // from layout.services() at design time
 "need":{"keepout_volume":K,"required":M,"floor_area":F,"tallest":[kx,ky,kz],"widest":[kx,ky,kz],
         "door_height":ch,"aisle_height":ch+chh,"cap_height":H}}
```

**SITE** (`shell.site_of`):
- `side = max(ceil(sqrt(site_factor × 1.25 × Σ keep-out x·z)), widest kx/kz + 2)`, with site_factor 2.0 (LIMITS 1.25-6).
- `height = max(H, 12) + headroom`.
- `lo = (0,0,0)`. Relief and MARGIN sit outside it, as today, so BOUNDS = site + relief.

**Script facts in site mode:**
- Unchanged: BOUNDS, WALLS (= site), RELIEF, HEADROOM, MAX_BLOCKS, PALETTE, ROLES.
- `KEEP_CLEAR = []` and `DOORWAYS = []`, so old scripts still run.
- INTERIOR and WALL_TOP come from the site.
- New: `SITE`, `NEED`, `PROGRAM` (one row per distinct keep-out × count), `FLOOR_Y = 0`, and `covered()`, which returns `{"volume", "floor", "need_volume", "short_by"}` for the cells placed so far. It is geometry only, so D385 holds.

**Fitted layout.json.** gos-layout v1, same shape, plus:
```
"fit": {"mode":"site","site":Box,"building":"sha256[:12] of sorted solid positions",
        "entrance_from":"answer"|"auto","pass":"prior"|"flow"|"area",
        "need":{"keepout_volume":K,"required":M},"have":{"volume":V,"volume_raw":Vr,"floor":A},
        "spare_pct":p,"placed":n,"of":n,"zones":sha|null}
```
- bounds = the site.
- Entrances: e1 = the main door. `services()` hard-codes e1, and that still holds.
- The c1 "hall" lobby is just inside the door. Aisles are a2...
- Chases h1... sit over the aisles.
- `max_run` stays in options (the schema requires it); the fitter ignores it.
- id = `_layout_id(body)` with `fit` in the body. Old layouts have no `fit`, so their ids are unchanged.

**design.json** adds `"mode":"site"`, site, need, entrance, zones and fit stats. A missing mode means layout.

**build_id**: in site mode the payload adds "mode", "site", "program" (sha of program.json), "entrance" and "zones". "layout" is left out, because it is derived. Layout-mode payloads are unchanged.

### 2.3 Capacity rule (the 25% buffer, exactly)
- **Keep-out** of a machine instance = body grown by clearance c = 1 on x and z and by clearance_top ct = 1 on y (D200; footprint.py CLEARANCE_XZ / CLEARANCE_Y). Its volume is (sx+2)(sy+1)(sz+2).
- **Required** M = ceil((1 + buffer/100) × Σ keep-out volumes), with buffer 25.
- **Usable region R**: the y-0 columns (x,z) inside the site that meet all of the following:
  - y 0 is air;
  - the column has a solid block somewhere above (sheltered; any block counts);
  - h(x,z) ≥ corridor_height, where h(x,z) = the run of air from y 0 up to the first solid;
  - not in an "open" zone, and inside the "machines" zones if any exist;
  - 4-connected to the door lobby.
- **Measured internal volume** V = Σ over R of min(h(x,z), H), with H = cap_height = max(tallest keep-out y, corridor_height + chase_height).
  - The cap stops a 40-high vault counting air no machine can use.
  - Uncapped Vr is reported alongside for Clayton's literal reading (Q2).
- **Gate**: V ≥ M. The volume rule is necessary but not sufficient; the packing is the proof. Aisles, the lobby and chases come out of the 25%.
- **Floor gate (added in build, D422 (2)):** |R| ≥ F = ceil((1 + buffer/100) × Σ keep-out x·z). H is usually one block taller than a keep-out (corridor 3 + chase 2 = 5 against a 4-high keep-out), so air counted up to H can make up for a floor smaller than the keep-outs themselves: a probe of plain halls sized just past V ≥ M was refused for packing about half the time. The machines stand on the floor (ground floor only), so the 25% is asked of the floor too. NEED and `covered()` carry `floor_required` / `need_floor`.

### 2.4 Algorithm

```
floor.derive(cells, site):                      # O(N)
  for (x,z) in site columns, sorted:
    solid_ys = sorted y of cells at (x,z)
    air0 = 0 not in solid_ys
    h = (first solid y) if air0 and solid_ys else 0       # no solid above -> h=0 (unsheltered)
  return Floor(h: dict[(x,z)]->int)

fit.fit(program, cells, site, opts, hint, zones, prior):
  F = derive(cells, site)
  door = choose_door(F, hint)                    # §2.5; refuse "door" reasons
  lobby = c1 rect (cw x cw) inside door; R = flood4 over cols with h>=ch from lobby, masked by zones
  V, Vr = Σ_R min(h,H), Σ_R h ; if V < M: refuse VOLUME
  sizes = {inst: [(kx,ky,kz) for facing N,E,S,W]}  # via Footprint.rotated, keepout_of
  heights = sorted distinct ky ∪ {ch+chh}
  for ky in heights: ok[ky](x,z) = (x,z) in R and h>=ky and not lobby; SAT[ky] = summed-area(ok[ky])
  if no rect for tallest/widest in any facing: refuse HEIGHT / WIDTH
  placed = {}
  PASS "prior": for inst in sorted(prior): if its keep-out rect is still all-ok at its ky and in R: occupy
  PASS "flow":  for inst in instances order (plan, flow order) not placed:
       dist = BFS4 over R from front of previous placed machine of same plan (else lobby)  # O(A)
       best = min over (facing f, x, z) with SAT[ky_f] rect(x..x+kx-1+s, z..z+kz-1+s) all-ok
              key = (dist[front_cell(f,x,z)], z, x, f_index)
       if best: occupy(best) (clear rect grown by spacing in every SAT; rebuild O(A·|heights|))
  if unplaced: reset to after-prior, PASS "area": first-fit decreasing by kx·kz (ties layout order),
       key = (dist_from_lobby[front], z, x, f_index)
  if still unplaced: refuse PACKING (largest free rect left via max-rect scan)
  bodies = ∪ body cells; open = {(x,y,z): (x,z) in R, 0<=y<h(x,z)} - bodies
  aisles = greedy maximal rectangles (row-major, extend x then z) of R minus keep-out columns,
           y 0..min(ch, min h)-1, kind "aisle", ids a2..; lobby is c1 "hall"
  chases = for each aisle, sub-rects where h >= ch+chh, y ch..ch+chh-1; none anywhere -> refuse CHASE
  for each placement: corridor = nearest aisle/hall to its front by multi-source BFS (ties lowest id);
                      chase = chase over that corridor else nearest chase
  placements = [_placement(pid, kid, cid, hid, inst, fp.rotated(f), body)]
  reach_all(..., extra_open=open)  -> any problem: refuse REACH (should be rare: enclosed hatches)
  layout = {..., services(program-backed), _variant_warnings, card unchanged, id=_layout_id(body)}
```
- **Why no reach search.** A keep-out lies wholly in R and its 1-block ring encircles the body, so removing a body never disconnects R. `reach_all` still runs as a check.
- **Determinism.** No randomness. Every dict and set is iterated sorted. Tie-breaks are explicit (dist, z, x, facing index, id). There is a test with shuffled cell insertion order.
- **Cost.** About 70 machines × (4 facings × A + |heights| × A), with A ≤ 8.1k columns: roughly 6M operations, about 2-4 s in CPython at the largest real case. Unit fixtures stay ≤ 40×40.

### 2.5 Door choice
- A candidate is an edge column of the sheltered region with h ≥ ch, whose outward neighbour has a straight air path at y 0..ch-1 to the site edge.
- **With a hint**: validate it or refuse.
- **Without one**: take the widest run of candidates. Ties go west, south, east, north, then the lowest coordinate.
- The doorway volume = that straight path, so the gate never removes porch blocks.
- Origin and `to_schematic` use e1's lo (D318 semantics move from "first layout entrance" to "e1").

### 2.6 Gate order (shell_store.make, site branch) and messages
1. `program()`. If it is empty: "no placements" (as today).
2. `site_of`, then `spec_for_site`. Compose (answer or procedural).
3. `compile_ops` once. Palette and max_blocks refusals as today.
4. `fit()`. Refusals, cheapest first:
   - DOOR: "no doorway: no ground opening at least {ch} high has a straight way out of the site; open one on an outer face" / "entrance at [x,z] on the {side}: {why}".
   - VOLUME: "the roofed floor holds {V} usable blocks (air up to {H} high under your roof; {Vr} counting all headroom); the machines need {M} (keep-outs {K} + {buffer}%): add at least {M-V}". Optionally add "y 0 is solid in {n} columns under your roof, e.g. [x,0,z]: the machine floor is y 0; leave it air".
   - HEIGHT: "m3 Electric Blast Furnace needs a {kx}×{kz} floor at least {ky} high; the tallest such floor is {best}".
   - WIDTH: "m5 {name} needs {kx}×{kz} free floor in some facing; the widest roofed run is {w}×{l}".
   - PACKING: "placed {n} of {N}: m4, m7, m11 did not fit (keep-outs ...); largest free floor left is {w}×{l} (h {h}) at [x,z]; widen a hall or add one at least {W}×{L}×{H} joined to the door".
   - CHASE: "no walkway has a ceiling at least {ch+chh} high for the cable chase; raise part of the roof over open floor".
   - REACH: the `_reach_problems` text.
5. `spec_from_layout(fitted, bounds=site)`, then `design(compiled=cells)`.
   - Zero breaches is asserted: any breach raises, because it is a fitter bug.
   - D317's 25% repair does not apply in site mode.
   - The gate never deletes the model's blocks to make machines fit.

Accepted outcome note: "12 machines fitted in the building (pass flow), interior {V} of {M} needed (+25%), {p}% spare, entrance {auto|answer}". All refusal reasons go into Outcome.reasons, so repair_brief, the ToolError and the bake-off rounds carry them unchanged.

### 2.7 Store, export, revise
- **rebuild / cells_of, site mode:**
  - compile with `spec_for_site` (the design-time facts: KEEP_CLEAR [] and NEED);
  - re-run fit with prior = the saved layout.json;
  - require the same layout id;
  - inspect against spec_from_layout(saved).
  - This avoids the trap of compiling with the post-fit spec.
- **check_layout (export), site mode:**
  - build today's program from footprints, and refit into the saved cells with prior = the saved layout;
  - same id: export;
  - fit succeeds with a different id: "the machines changed since this shell was designed; the building still holds them: `gos shell refit <build>` makes a version with them fitted again";
  - fit fails: the fit reason plus "revise the building".
  - Export never mutates.
- **revise, site mode:** compile the new ops, fit against the parent's program.json with prior = the parent's layout.json, gate. A "shorter" revision is refused by the fit's HEIGHT or VOLUME reason. revise_brief shows the current placements as context, and says they may move.
- **Layout mode:** every path is unchanged.

### 2.8 Prompt and bake-off
- **Prompt v6 (site)**, replacing the KEEP_CLEAR/interior text:
  - "Design the building freely inside SITE. GOS fits the machines inside afterwards, wherever they fit; you never place or avoid machines."
  - The rules: y 0 is the machine floor (keep it air under the roof); roofed floor must hold NEED.volume up to NEED.cap_height; give at least one ground doorway ≥ door_height with a straight way out; some ceilings over open floor ≥ aisle_height; columns and inner walls are fine (machines go around them); aim for ~40% spare, since packing is not perfect.
  - An optional entrance and zones (for example an "open" foyer).
  - An example script that grows a hall until `covered()["short_by"] <= 0`.
  - The PROGRAM table and services as "mX → mY item" lines. No coordinates, so the `_fit` size limit is relieved.
- **Fakes**:
  - existing fakes draw a roofed hall sized to NEED×1.5;
  - `fake-cramped`: the first answer is at 70% of NEED and the repair grows it (refusal → repair → accept);
  - `fake-tower`: enough volume on a 3-wide floor; it ends refused, which tests the scoring of refused cases.
- **REPAIR_ROUNDS stays 2.** Each fit refusal is a full re-answer (about $0.70 per Astra call on D387's numbers). Measure `rounds_to_fit` first.
- **Score columns**: fit_ok, rounds_to_fit, placed/of, spare_pct, entrance_from, service_len (the Manhattan sum of service pairs; informational only).

### 2.9 Tests

**test_build_floor.py**
- roofed hall, open courtyard, floating roof block, solid y-0 floor, arcade without walls;
- `measure` agrees with fit's V.

**test_build_fit.py**
- Door: auto pick and tie order; valid hint; invalid hint; porch in the straight path; lobby.
- Volume boundary: exactly M passes, M-1 refuses (exact numbers in the text); cap H vs Vr.
- Height and width refusals; chase refusal.
- Pack:
  - all four facings used in a z-long hall;
  - inner columns avoided;
  - L-shaped and courtyard ring plans;
  - flow → area fallback;
  - spacing honoured;
  - packing refusal lists the unfit ids and the largest free rect.
- Zones: "open" stays empty; "machines" restricts placement; a zone outside the site is refused.
- Prior: valid placements kept when the building changes elsewhere; invalidated ones re-placed.
- Invariants on every fixture:
  - no overlap;
  - every box inside the site and R;
  - keep-out = body ± c;
  - no keep-out, corridor or chase cell is solid;
  - an independent flood matches `reach`.
- The result validates against layout_schema.json, and the old fixture layouts still validate.
- Determinism: run twice with shuffled cell order, same id. A different palette at the same positions gives the same id.
- `reach_all` with the default `extra_open` gives the old ids.
- A perf guard: 60 machines on a 64×64 site under a generous bound, marked slow.

**test_build_layout.py**
- plan_layout byte-identical and golden id after the `program()` / `keepout_of` split;
- `check()` split into common rules and layout-only rules (lo.y == 0, west entrance).

**test_shell.py**
- site make with procedural `fit=True` (no model);
- `design(compiled=)` compiles once (count sandbox calls);
- zero breaches on the fitted spec;
- build_id unchanged for the layout golden ids and new for site;
- origin from e1;
- fit reasons reach Outcome.

**test_build_sandbox.py**: site facts present; KEEP_CLEAR == []; `covered()` numbers and limits.

**test_shell_brief.py**: v6 parse accepts and rejects entrance and zones; the layout-mode key sets are unchanged; the brief size is independent of machine count.

**test_shell_versions.py**
- site revise refits from program.json with no structure query;
- "shorter" refused by HEIGHT;
- refit makes a new version;
- a layout-mode revise is unchanged.

**test_shell_store / export**
- an old build exports as today;
- a site build exports;
- a footprint change refuses with the refit message;
- a fit failure refuses.

**test_shell_bakeoff.py**: fake-cramped repaired in round 2; fake-tower refused and scored; old case files and runs load as layout.

**tools/test_shell_architect.py**: the entrance and zones arguments; the result head has the fit line.

**Integration**
- test_shell_harness: a site make → export;
- test_real_dump_harness: the 69-machine steel plan fits into a procedural site building in under 10 s.

### 2.10 Size
About 1,900 core lines and about 1,100 test lines. The largest pieces:
- fit.py ~550;
- floor.py ~120;
- shell.py +200;
- shell_store +180;
- shell_brief +200;
- shell_fakes +110;
- shell_bakeoff +120;
- shell_versions + revise +130;
- layout.py +90;
- agents +80;
- CLI +40.

It is large for one PR, so review it commit by commit (§2.1).

### 2.11 Risks to state in the PR
1. Volume passes but packing fails on thin wings or column forests. The greedy packer is not optimal, so some buildings a human could fill will be refused. The specific reasons and `covered()` mitigate this.
2. Ground floor only. A tall landmark's height counts only up to H for machines.
3. Machines can scatter across wings, giving long service runs. The flow ordering limits this but does not optimise it, and it is scored, not refused.
4. East/west facings become common, so the D305 block-meta rotation TODO can show wrong orientation in the WO-10.8 build guide. Verify this before Clayton builds.
5. "Sheltered = any block above" counts overhangs and arcades as interior. That is a judgement call (Q2).
6. A footprint data change now needs `refit` before export.
7. Two modes double the branches: set a sunset for architect layout mode in D422.

=====================================================================
## 3. Open questions only Clayton can answer
1. Is the 25% buffer over keep-outs (body + 1-block walkway ring + 1 above; proposed) or over bare machine bodies? Do aisles and chases come out of the 25% (proposed; expect refusals near the limit) or on top of it?
2. For "internal volume", may GOS count only air up to the tallest machine's keep-out plus chase height (proposed; a cathedral vault doesn't inflate it), or all roofed air? And does any block above (arcades, overhangs) count as a roof?
3. Is it OK that a build with enough volume is still refused when the tallest or widest machine has no spot, or the packing fails?
4. Site size: is a box about 2× the needed floor area (plus headroom and relief) enough freedom, or larger?
5. Should every machine sit at y 0, ground floor only for now? Are mezzanines or multi-storey machine floors wanted soon?
6. Should models be allowed to optionally pin specific machines (id, position, facing) in a later PR? That amends D389, and D385 if a script computes them.
7. When a revise or new footprint data would move machines already built: keep valid old positions (proposed `prior`), and should export refuse and offer `gos shell refit` (proposed) or refit automatically?
8. Low-ceiling buildings: refuse when no ceiling over open floor is chase-high (proposed), or make the cable chase optional (a schema change)?
9. Should the No-AI procedural shell stay layout-first (proposed; D386 phases it out), and should new AI designs default to site mode immediately?
10. Approve D422 superseding parts of D316, D317, D368 and D389, and renumbering or noting the duplicate D389 (renumbered by D428).

Paths:
- /home/user/GretecOS/core/gregtec/build/layout.py
- /home/user/GretecOS/core/gregtec/build/shell.py
- /home/user/GretecOS/core/gregtec/build/shell_store.py
- /home/user/GretecOS/core/gregtec/build/shell_brief.py
- /home/user/GretecOS/core/gregtec/build/shell_bakeoff.py
- /home/user/GretecOS/core/gregtec/build/sandbox_child.py
- /home/user/GretecOS/core/gregtec/build/layout_schema.json
- /home/user/GretecOS/docs/decisions.md
- new: /home/user/GretecOS/core/gregtec/build/fit.py
- new: /home/user/GretecOS/core/gregtec/build/floor.py
- new: /home/user/GretecOS/tests/unit/test_build_fit.py
- new: /home/user/GretecOS/tests/unit/test_build_floor.py
=====================================================================
## 4. Clayton's answers (2026-11-24, in the session)
1. Volume rule: **keep-outs + usable air** (as proposed in §2.3: keep-out = body + 1-block ring + 1 above; usable air capped at H; aisles and chases come out of the 25%).
2. Floors: **ground floor only for now** (y 0); mezzanines later.
3. Pins: **later, if needed**. This PR: optional zones only.
4. Rollout: **all new AI designs** (bake-off and the architect's build_request/build_revise) use site mode; saved shells and the procedural (No-AI) generator stay layout-first.
Earlier standing answers: practicality doesn't matter (WorldEdit pastes it, D386); spend no API money without asking.
