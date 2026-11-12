# Power lines by objective mode (WO-4.2)

`gos power design` designs 2-3 ranked power lines for one of plan §7.2's objective modes, on the
newest export run in `GOS_HOME/data/gos.sqlite` (read-only). Decisions: D233 (lines, generators,
fuels, farms), D234 (validator, bootstrap kit), D235 (modes, ranking, scale bounds).

```
uv run gos power design best --tier HV
uv run gos power design max_output --tier IV --scale generators --bound 4
uv run gos power design output_per_effort --tier HV
uv run gos power design self_sustaining --tier EV --kind boiler_steam --fuel fluid:creosote
```

Options: `--free KEY ...` (the free set; default water, air, cobblestone, stone, sunlight),
`--kind single_block|gas_turbine|plasma_turbine|boiler_steam`, `--fuel KEY ...`,
`--rotor MATERIAL SIZE` (turbines; default Steel large), `--candidates K` (fuels per generator),
`--options N` (1-3), `--scale unit|generators|eut_cap|raw_budget --bound N`, `--no-effort`,
`--json` (the plans as canonical JSON lines).

Each line prints its net EU/t (generator out minus what its own chain draws), the fuel and its
`r:` row, the validator's verdict (`closed: no import outside the free set`, or the imports it
found), its nodes and, for `self_sustaining`, the bootstrap kit (saplings, tools, kept circuits,
rotor and dynamo, throttle circuit, starter fuel as an upper bound) and its sinks: items the line
makes more of than it uses, which need a void or storage. The notes say how many generator x fuel
candidates and farms were pruned before solving.

## The effort breakdown (WO-4.3)

Each line (and each `gos plan` route) prints its effort and the breakdown table under its score
line (D236, D237):

```
   effort 4.2 min = build 4.2 min + unlock 0 s (crafting-table step 2 s; 24 items not counted, see the notes)
   part   map                     machine  recipe                                 crafts  machines  hours
   build  gt.recipe.alloysmelter  mte:204  r:gt.recipe.alloysmelter/5c89d98542b3  73      1         43.8 s
```

- `effort_h` = `build` (the processing time to make every build component from boundary items
  at the tier) + `unlock` (the components of each machine type My base doesn't own that the
  steps need, recursive to 3 levels, each once). Gathering and hands-on building are left out;
  scarcity is its own term. Times print as seconds under a minute, minutes under an hour.
- Build components: the line's machine items, a multiblock's structure blocks (where the DB has
  a structure row), and the parts the line chooses itself: the turbine rotor (the request's
  material and size, found by its NBT material) and the dynamo hatch at the line's tier. Other
  hatches, pipes, throttle circuits and distribution cables are listed in the notes, not
  counted (D236).
- **Items not counted**: what the model can't make (no item or structure in the DB, no recipe or
  route from boundary items at the tier, gated more than 3 levels down) is counted in
  `uncounted` and shown on the effort line and in the card's head; the notes list them. Such an
  effort is a lower bound, so `output_per_effort` ranks those lines after the lines whose
  effort is complete (D236). On the server harness DB every EV large gas turbine line has some:
  the turbine controller itself has no route at EV yet (a WO-3.5 follow-up).
- Each row is one recipe (`crafting` map for crafting-table and furnace steps at `craft_s`,
  2 s by default, `craft_s:` in `me/prefs.md`): its map, the machine the map's steps are sized
  on, its whole crafts, the machines My base owns on that map (at least 1) and its hours.
- `--json` carries the breakdown under `metrics.effort`: `rows` per map (`crafts`, `machines`,
  `h`, and `recipes`, each `{id, crafts, h}`), `components`, `unlocks`, `uncounted`, `notes`;
  `output_per_effort` ranks by net EU/t / `eff_h` (`rank.eut_per_effort_h`).
- The dashboard gets the breakdown as a `table` card (`tool: effort_estimate`) from
  `GET /api/plans/{id}/cards` (`effort`), next to the flow and BOM cards.

After a GT pin bump, regenerate the boiler and tree-farm vectors with
`uv run python tests/unit/gtmath/oracle/make_boiler_vectors.py` (a JDK and the mod's Gradle build).

## Checking the D65 tree-farm boiler line in the Lab (Clayton, optional, G4)

1. On Clayton's DB: `uv run gos power design self_sustaining --tier EV --kind boiler_steam
   --fuel fluid:creosote` and note option 1's nodes, counts, net EU/t and bootstrap kit.
2. Build it in the Lab world (never Play): the Tree Growth Simulator on an LV hatch with the
   sapling in the controller and an LV buzzsaw in the input bus, the pyrolyse ovens (circuit 1),
   the large boilers with the throttle circuit the kit names, the large steam turbine with the
   rotor and dynamo hatch the kit names, water from an infinite source.
3. Start it with the kit's starter creosote. After the boilers and turbine settle (about a
   minute), read the turbine's output with a scanner: it should equal the line's `gross_eut`;
   the line keeps running with no input but water. A mismatch is a finding for the reviewer
   (the boiler and tree-farm ports are checked against GT's own classes by
   `tests/unit/gtmath/oracle/GtBoilerOracle.java`, but not yet in a running world, D233).
