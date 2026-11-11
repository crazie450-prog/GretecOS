# Chain templates

Curated recipe choices for chains whose GT recipe web would blow the designer's graph up or send
it down the wrong route (plan §7.1 step 2, WO-3.3, D189, D190). The graph builder
(`gregtec.planner.graph`) reads every `*.tsv` here through `gregtec.planner.chains`: for an item a
template lists, it expands the template's recipes instead of its heuristic's top four.

The file name is the chain's name (`platline.tsv` is `platline`). Lines starting with `#` are
comments. Then one header line and one row per (item, recipe), tab-separated:

| column | what |
|---|---|
| `item` | the item or fluid's stable key: `registry@meta` (`#<nbt8>` for an NBT variant) or `fluid:<name>`, never a numeric id |
| `recipe` | the recipe's citation key, `r:<map>/<sig12>` |
| `branch` | empty, or a branch-point id, the same on every row of the item |
| `note` | why, for people |

- **Branch point**: an item whose rows carry a branch id. WO-3.4 turns it into a primary choice
  (§7.1 step 3): its routes differ by which of these recipes they use.
- **No branch id**: every recipe listed for the item stays in the graph and the solver balances
  them. Use it for a chain's loops (the platline's metallic powder comes from ore and from its
  own refined salt).
- An item belongs to one template only; a recipe may make several of a template's items.
- Templates name recipes by key and carry no game numbers.

A template goes stale when a pack update changes or removes a recipe. `gos graph --check-chains`
validates every row against the newest export run in the DB (the recipe exists, is enabled, is
not a display-only recipe, and makes the item) and lists the stale ones; the pack-update
playbook runs it. A stale row is skipped with a warning on the graph, never guessed around.

Add a template only when a chain's graph blows up or picks the wrong route without one.
