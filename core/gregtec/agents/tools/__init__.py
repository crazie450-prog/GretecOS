"""The tool layer (WO-2.2; plan sections 4.4, 4.6, 5.3 and 7.0; [D§1.4], [D§3.6]).

One set of Python functions serves the fast lane (Messages API tool schemas,
``Registry.specs("fast")``), the deep lane (WO-2.4 wraps the same tools in the in-process MCP
server, ``read_only`` -> ``readOnlyHint``) and the shell (``gos tool``). Every tool is
read-only but three note tools (WO-2.5), which write behind the vault path guard, and
``action_propose`` (WO-7.1), which only records a proposal for Clayton to confirm; results are TSV, at most 50 rows a page with a ``more`` cursor, and every row that
states a game number carries the citation the DB gives it (``r:`` recipes, ``q:`` quests,
``mte:`` machines). The tools never compute game numbers; the only derived numbers are exact
unit changes (ticks to seconds, basis points to percent).

========================  =====  ==================================================
tool                      lanes  module
========================  =====  ==================================================
item_resolve(text, kind)  both   :mod:`.items` (FTS + glossary aliases, ambiguity)
recipe_find(item, ...)    both   :mod:`.recipes`
recipe_uses(item)         both   :mod:`.recipes`
machine_info(machine)     both   :mod:`.machines`
quest_find(text)          both   :mod:`.quests`
quest_get(id, spoil)      both   :mod:`.quests` (nudge-first spoilers, D26, D268)
quest_frontier(n)         both   :mod:`.quests` (ranked by goals and lines, WO-6.4)
quest_for_item(item)      both   :mod:`.quests`
base_get()                both   :mod:`.base` (WO-3.8; My base, read-only)
db_query(name, params)    both   :mod:`.sql` (the game-data ``db/queries/*.sql``)
db_sql(select)            deep   :mod:`.sql` (authorizer, read-only, time limit)
note_read(path)           both   :mod:`.notes` (WO-2.5; no DB needed)
vault_search(q, t)        both   :mod:`.notes` (``note_fts``)
note_write(path, body)    deep   :mod:`.notes` (writes, behind the vault path guard)
note_append(path, lines)  deep   :mod:`.notes` (writes; logs append-only)
inbox_propose(title, ...) deep   :mod:`.notes` (writes a new ``inbox/`` note)
handoff(task)             fast   :mod:`gregtec.agents.deep_lane` (WO-2.3; to the deep lane)
telemetry_get(scope)      both   :mod:`.ops` (WO-7.1; OC readings with age and stale flag)
action_propose(kind,args) deep   :mod:`.ops` (WO-7.1; proposes only: Clayton confirms, D59)
action_status(id)         both   :mod:`.ops` (WO-7.1)
ledger_get()              both   :mod:`.ledger` (WO-4.4; the EU ledger, read-only)
more(cursor)              both   :mod:`.registry` (the next page of any result)
========================  =====  ==================================================

The rest of plan 7.0's catalog arrives with later work orders (``catalog.LATER``): timer_set
with WO-5.3; export_diff with WO-1.4; ore_find with WO-1.6; structure_get after WO-1.8; machine_fact_set with
WO-1.9; crop_* with WO-1.10 and WO-6.7; plan_*, design_production, effort_estimate and
layout_estimate with Phase 3; design_power with WO-4.2; ledger_update and
plan_set_status stay Clayton's (D238); speak with WO-5.3; persona_set after WO-5.6 (D208); player_pos with WO-6.1; events_recent and
session_summary with WO-6.3; screen_snapshot with
WO-8.2; wiki_* with WO-8.3; notify with WO-9.2; schematic_export, theme_* and build_request
with Phase 10.
"""

from gregtec.agents.tools.catalog import LATER, TOOLS, build_registry, default_registry
from gregtec.agents.tools.registry import DEEP, FAST, Output, Registry, Tool, ToolError

__all__ = [
    "DEEP",
    "FAST",
    "LATER",
    "TOOLS",
    "Output",
    "Registry",
    "Tool",
    "ToolError",
    "build_registry",
    "default_registry",
]
