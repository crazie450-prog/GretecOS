# Recipe card, version 0 (WO-2.7, D172)

The dashboard's recipe card (plan §7.3, §7.14 rule 1) as data, for WO-2.6 to adopt or replace with its own card schema. Agents return cards, never HTML; this one is built by code (`gregtec.agents.cards.recipe_card`) from a `recipe_find` or `recipe_uses` result, the same rows the model read and cited, so the card's numbers are the DB's (D131): nothing is rounded, converted or added up here.

## Where it is

`POST /api/ask` and `gos ask --json` return a reply whose `card` is the pre-retrieved tool result (`type: tool_result`, WO-2.3). When that tool is `recipe_find` or `recipe_uses` and it answered with recipes, the card also has a `recipe` key holding the recipe card below. An ambiguous item (the reply asks instead), an error and every other tool have no `recipe` key. The deep lane's answers carry no card yet; their detail gives each cited recipe as one `key: value` line (the recipe-lookup skill's section 4).

## The card

| Field | Type | Meaning |
|---|---|---|
| `type` | string | `recipe` |
| `v` | integer | `0`, this version |
| `tool` | string | `recipe_find` (recipes that make the item) or `recipe_uses` (recipes that take it) |
| `item` | object | `key` (`registry@meta[#nbt8]` or `fluid:<name>`) and `name`, as the tool resolved them |
| `run` | string | the export run the rows are from, with its GT version |
| `filters` | string or null | the tool's filters (`tier_max=LV machine=EBF`), as its head line says |
| `total` | integer or null | how many recipes match in all; the page holds the first ones (50 at most), so "how many alternatives exist" is `total` |
| `recipes` | list | one entry per recipe row on the page, in the tool's order (simplest first; recycling last) |

Each entry of `recipes`:

| Field | Type | Meaning |
|---|---|---|
| `ref` | string | the citation `r:<map>/<sig12>`; the card's citation chip |
| `machine` | string | the NEI name of the map or handler, else the first machine that runs it |
| `tier` | string or null | GT voltage tier name (`LV` ...); null for crafting, smelting and the like |
| `eut` | integer or null | EU/t, amperage included, as GT stores it |
| `amps` | integer or null | amperage |
| `dur_t` | integer or null | duration in ticks |
| `dur_s` | string or null | duration in seconds, exactly `dur_t / 20` as the tool writes it (`40.3`) |
| `inputs`, `outputs` | list of slots | in slot order |
| `conditions` | list | `{key, value}` per recipe condition (`coil_heat`, `cleanroom`, ...), values as the DB holds them |
| `flags` | list of strings | `recycling`, `fake?` (the NEI handler doesn't say whether it is real), `disabled`, `fake`, `hidden` |

Each slot:

| Field | Type | Meaning |
|---|---|---|
| `key` | string | the item or fluid key |
| `name` | string | its English name (may be empty) |
| `amount` | integer | items, or L (= mB) for a fluid; 0 when NEI shows the stack without an amount |
| `unit` | string or null | `item`, `L`, or null when there is no amount |
| `chance_pct` | string | the chance in percent, exactly basis points / 100 (`11.11`); `100` when certain |
| `consumed` | boolean | false for a programmed circuit or a mold that stays in the machine |
| `circuit` | integer or null | the programmed circuit's number |
| `ore` | string or null | the oredict name when any item of it fits the slot (`plankWood`) |
| `alternatives` | integer | how many other stacks the slot accepts (0 for an oredict slot: `ore` says it) |

A slot the parser doesn't recognise is `{text: <the tool's text>}`, so nothing is dropped silently.

## Not in version 0

The stale-data badge (plan §0.1 principle 4), spoiler state and machine facts: `machine_info` results stay generic `tool_result` cards.
