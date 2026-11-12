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
