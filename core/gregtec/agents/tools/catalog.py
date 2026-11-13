"""The tools WO-2.2 implements, and the plan 7.0 tools that arrive with later work orders."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from gregtec.agents.deep_lane import HANDOFF_TOOL, DeepLane
from gregtec.agents.tools import (
    base,
    crops,
    designer,
    items,
    ledger,
    machines,
    notes,
    ops,
    ores,
    plans,
    power,
    quests,
    recipes,
    sql,
)
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import DEEP, Env, Registry, Tool
from gregtec.agents.tools.tsv import PAGE
from gregtec.db import access
from gregtec.vault import fts
from gregtec.vault.io import VaultIO

if TYPE_CHECKING:
    from gregtec.bridge.ops import Ops
    from gregtec.planner.explain import Writer

CITE = "Quote numbers only from the rows, each with the row's id."

TOOLS = (
    Tool(
        "item_resolve",
        "Find the item, fluid or machine a request names, from the DB's names and the "
        'glossary\'s aliases ("SS" -> stainless steel). Returns ranked candidates with their '
        "keys. `ambiguous: 1` means ask which one is meant (show the top three) instead of "
        "answering; a request with one clear match can use its key in the other tools.",
        items.ITEM_RESOLVE_SCHEMA,
        items.item_resolve,
    ),
    Tool(
        "recipe_find",
        "Recipes that make an item or fluid, from Clayton's own export (the newest run): "
        "machine, GT tier, EU/t, duration, inputs and outputs with chances, conditions (coil "
        "heat, cleanroom, ...), each cited by its r: id. Lowest tier first. Disabled and fake "
        "recipes are left out unless show_fake. If the item is ambiguous, returns the "
        f"candidates instead. {CITE}",
        recipes.RECIPE_FIND_SCHEMA,
        recipes.recipe_find,
    ),
    Tool(
        "recipe_uses",
        "Recipes that take an item or fluid as an input (shown, through its oredict or as an "
        f"alternative; consumed or not), each cited by its r: id, by machine. {CITE}",
        recipes.RECIPE_USES_SCHEMA,
        recipes.recipe_uses,
    ),
    Tool(
        "machine_info",
        "A GT machine by name or id: its item, the recipe maps it runs and the facts the planner "
        "uses (parallels, overclocks, speed, EU modifier, ...) with source and confidence, cited "
        f"mte:<id>. {CITE}",
        machines.MACHINE_INFO_SCHEMA,
        machines.machine_info,
    ),
    Tool(
        "quest_find",
        "Find quests by words in their name or description: q:<id>, name, Clayton's state "
        "(done, available, repeatable, locked) and quest lines.",
        quests.QUEST_FIND_SCHEMA,
        quests.quest_find,
    ),
    Tool(
        "quest_get",
        "One quest: state, quest lines, prerequisites, what it unlocks, tasks (consumed or "
        "not) and rewards. A quest past the frontier is shown at his spoiler level (0 nudge: "
        "where to go, not what it is; 1 adds its tasks, 2 everything); a higher spoil is "
        "shown only when Clayton asked for it.",
        quests.QUEST_GET_SCHEMA,
        quests.quest_get,
    ),
    Tool(
        "quest_frontier",
        "The quests Clayton can work on now (the frontier), ranked by his goals and active "
        "lines (why says how), with his tier.",
        quests.QUEST_FRONTIER_SCHEMA,
        quests.quest_frontier,
    ),
    Tool(
        "quest_for_item",
        "Quests whose tasks ask for an item or fluid (itself, any meta, or an oredict it is in) "
        "or whose rewards give it, with Clayton's state of each.",
        quests.QUEST_FOR_ITEM_SCHEMA,
        quests.quest_for_item,
    ),
    Tool(
        "base_get",
        "My base: Clayton's tier (from quests, or his override) with the key quest, the "
        "machines he has (mte: ids, counts and where each count came from) and the stock he "
        "entered (item and fluid keys). Read-only: he edits My base in the dashboard. Nothing "
        "listed means nothing owned or stocked.",
        base.BASE_GET_SCHEMA,
        base.base_get,
    ),
    Tool(
        "db_query",
        "Run a named read-only query over the game data (items, recipes, machines) with "
        f"its parameters, 50 rows a page; quests are the quest tools'. {CITE}",
        sql.db_query_schema(),
        sql.db_query,
    ),
    Tool(
        "db_sql",
        "One read-only SELECT over the DB (game data and GOS's tables), 2 s and 50 rows a "
        f"page. For questions no other tool answers. {CITE}",
        sql.DB_SQL_SCHEMA,
        sql.db_sql,
        # The db-analyst's tool (deep lane); the fast lane has the named queries.
        lanes=frozenset({DEEP}),
        deadline_s=sql.DEADLINE_S,
    ),
    Tool(
        "note_read",
        "Read one vault note by its path (INDEX.md lists them; vault_search finds more). Open "
        "only the notes a request needs; me/ is Clayton's (read only).",
        notes.NOTE_READ_SCHEMA,
        notes.note_read,
        needs_db=False,
    ),
    Tool(
        "vault_search",
        "Find vault notes by words in their text: path, type, date and the matching words, "
        "best first. For notes INDEX.md doesn't list one by one.",
        notes.VAULT_SEARCH_SCHEMA,
        notes.vault_search,
    ),
    Tool(
        "note_write",
        "Create or replace a note in base/, plans/, quests/, themes/ or personas/drafts/ in "
        "the note grammar; the tool writes the frontmatter. me/, _gos/, INDEX.md and the generated "
        "base notes are refused (propose with inbox_propose); logs take note_append.",
        notes.NOTE_WRITE_SCHEMA,
        notes.note_write,
        read_only=False,
        lanes=frozenset({DEEP}),
        needs_db=False,
        gate=notes.GATE,
    ),
    Tool(
        "note_append",
        "Add lines to the end of a note: today's journal log/YYYY-MM-DD.md (logs are "
        "append-only), or a note in an agent folder or inbox/.",
        notes.NOTE_APPEND_SCHEMA,
        notes.note_append,
        read_only=False,
        lanes=frozenset({DEEP}),
        needs_db=False,
        gate=notes.GATE,
    ),
    Tool(
        "inbox_propose",
        "Propose something to Clayton as a new inbox/ note: a change to me/ or a skill, a "
        "curation task, a question. Never replaces a proposal.",
        notes.INBOX_PROPOSE_SCHEMA,
        notes.inbox_propose,
        read_only=False,
        lanes=frozenset({DEEP}),
        needs_db=False,
        gate=notes.GATE,
    ),
    Tool(
        "plan_export",
        "A saved plan's outputs (WO-3.6): bom (machine, tier, count, OC, parallel, EU/t, "
        "utilisation, r: id per node, and the totals), footprint (each machine type's box from "
        "its structure plus clearance, floor area and volume), mermaid (the flow diagram), or "
        "the path of its gtnhplanner.com project, gtnh-flow YAML or PNG for Clayton. "
        f"{CITE}",
        plans.PLAN_EXPORT_SCHEMA,
        plans.plan_export,
        needs_db=False,
    ),
    Tool(
        "design_production",
        "Design a production line (WO-3.7): the target (his words or its key), a rate "
        '("2/s") or a batch, the tier cap, up to 3 routes, addons. GOS builds the recipe graph, '
        "solves the routes, sizes the machines and ranks them by code, then saves each as a "
        "plan (plan:<id>). Returns one row per route in rank order with its metrics, the "
        "differing choices and an explanation built from the metrics. Keep the order; "
        f"explain, never reorder. An unclear target returns the candidates to ask about. {CITE}",
        designer.DESIGN_PRODUCTION_SCHEMA,
        designer.design_production,
        read_only=False,
        lanes=frozenset({DEEP}),
        needs_db=False,
        deadline_s=120.0,
        gate=designer.GATE,
    ),
    Tool(
        "plan_get",
        "A saved plan: view summary (facts and metrics), nodes (each machine node: item, "
        "recipe r: id, machine, tier, count, pinned, primary), routes (its design's routes in "
        "rank order with the differing choices) or alternatives with node (that node's "
        f"candidate recipes for plan_pin). {CITE}",
        designer.PLAN_GET_SCHEMA,
        designer.plan_get,
        needs_db=False,
        deadline_s=60.0,
    ),
    Tool(
        "plan_explain",
        "The checked explanation of a saved plan's design (plan 7.1 step 11, D199), a "
        "production design (pl-...) or a power design (pw-..., design_power): the planner "
        "model writes it from the metrics only, GOS's numeric validator checks every number "
        "(and, for power, that the options keep code's order; one retry), else GOS's template "
        "is used. Returns the text and its source (model, model-retry or template). Say it as "
        "it is: it keeps code's rank order.",
        designer.PLAN_EXPLAIN_SCHEMA,
        designer.plan_explain,
        lanes=frozenset({DEEP}),
        needs_db=False,
        deadline_s=300.0,
    ),
    Tool(
        "plan_pin",
        "Pin one node of a saved plan to another candidate recipe (plan_get view "
        'alternatives), or unpin it, and solve again ("use the other sulfuric acid recipe", '
        "D61). The pin is kept in the plan. Returns the new routes in rank order, as "
        "design_production does.",
        designer.PLAN_PIN_SCHEMA,
        designer.plan_pin,
        read_only=False,
        lanes=frozenset({DEEP}),
        needs_db=False,
        deadline_s=120.0,
        gate=designer.GATE,
    ),
    Tool(
        "plan_list",
        "The saved plans, newest first: name, plan: id, kind and status (proposed, accepted, "
        "building, running, retired); status filters them.",
        designer.PLAN_LIST_SCHEMA,
        designer.plan_list,
        needs_db=False,
    ),
    Tool(
        "plan_rename",
        'Give a saved plan a short human name ("Steel line · 2/s · MV"); its id stays.',
        designer.PLAN_RENAME_SCHEMA,
        designer.plan_rename,
        read_only=False,
        lanes=frozenset({DEEP}),
        needs_db=False,
        gate=designer.GATE,
    ),
    Tool(
        "ledger_get",
        "The EU ledger (WO-4.4, base/power.md): per scope (all accepted, building and running "
        "plans; running only) and tier, generation, average and peak use, net EU/t and amps, "
        "the warnings (deficit, tier_mismatch, amp_overload) and OC's measured power when "
        f"fresh. Every number is an accepted plan's; cite it by its plan: ids. {CITE}",
        ledger.LEDGER_GET_SCHEMA,
        ledger.ledger_get,
    ),
    Tool(
        "design_power",
        "Design power lines (WO-4.6, plan 7.2): one of four objective modes (best, "
        "max_output, output_per_effort, self_sustaining), the tier cap, max_output's scale "
        "bound, generator kinds or fuels to consider. GOS sizes every generator and fuel "
        "chain, proves self-sustaining lines closed, and ranks 2-3 options by code with the "
        "weights printed, then saves each as a plan (plan:<id>). Returns one row per option in "
        "rank order: net EU/t (gross minus its chain's use), score, effort, upkeep, closed. "
        f"Keep the order; explain, never reorder. {CITE}",
        power.DESIGN_POWER_SCHEMA,
        power.design_power,
        read_only=False,
        lanes=frozenset({DEEP}),
        needs_db=False,
        deadline_s=300.0,
        gate=power.GATE,
    ),
    Tool(
        "effort_estimate",
        "A saved plan's effort breakdown (plan 7.2 effort model, D17): one row per recipe "
        "(part, recipe map, the machine it runs on, the recipe, crafts, machines, hours) with "
        "build, unlock and not-counted totals in the head. Effort is crafting and processing "
        f"time plus unlocking machines he doesn't own. {CITE}",
        power.EFFORT_ESTIMATE_SCHEMA,
        power.effort_estimate,
        needs_db=False,
    ),
    HANDOFF_TOOL,  # WO-2.3: the fast lane's one writing tool, to the deep lane (D152)
    Tool(
        "telemetry_get",
        "What Clayton's OC computers last reported (WO-7.1): the computers, and the GT machines "
        "and power they see with their last readings, each with its age and a stale flag; "
        "cite rows by their tel: ref. A stale reading is old data, never a current value.",
        ops.TELEMETRY_GET_SCHEMA,
        ops.telemetry_get,
        needs_db=False,
    ),
    Tool(
        "action_propose",
        "Propose one safe world action (an AE2 craft, pausing or resuming a GT machine, a "
        "chat-box line, a JourneyMap waypoint). It only proposes: GOS reads it back and Clayton "
        "confirms it himself (you can't), within 60 s. One action waits at a time; a new "
        "proposal cancels the last. Say the returned `say` line to Clayton word for word.",
        ops.ACTION_PROPOSE_SCHEMA,
        ops.action_propose,
        read_only=False,
        lanes=frozenset({DEEP}),
        needs_db=False,
        gate=ops.GATE,
    ),
    Tool(
        "ore_find",
        "Ore veins of a material in Clayton's world (WO-6.6): every generated vein GOS loaded "
        "(D27), ranked by code: live veins first, then the material's best layer (primary, "
        "secondary, between, sporadic), then nearest to where he was at the last save. Each "
        "row: its vein: id, mix, ores, dimension, centre x/z, the mix's heights, distance, "
        f"known, depleted. Keep the order. {CITE}",
        ores.ORE_FIND_SCHEMA,
        ores.ore_find,
    ),
    Tool(
        "player_pos",
        "Where Clayton was when his world last saved (x, y, z, dimension) and how long ago, "
        "from the world the veins were loaded from.",
        ores.PLAYER_POS_SCHEMA,
        ores.player_pos,
    ),
    Tool(
        "ore_waypoint",
        "Propose a JourneyMap waypoint at one vein, by its vein: id from ore_find. GOS builds "
        "it from the vein (name, centre, the middle of its heights), reads it back, and "
        "Clayton confirms it himself (you can't), within 60 s; a new proposal cancels the "
        "last. Say the returned `say` line to Clayton word for word.",
        ores.ORE_WAYPOINT_SCHEMA,
        ores.ore_waypoint,
        read_only=False,
        lanes=frozenset({DEEP}),
        gate=ops.GATE,
    ),
    Tool(
        "crop_find",
        "CropsNH crops by name or id (WO-6.7): tier, soil, whether a block must sit under the "
        "soil, the parents each is bred from (Crop Breeder-only marked), its mutation pools, "
        "and whether Clayton has it.",
        crops.CROP_FIND_SCHEMA,
        crops.crop_find,
    ),
    Tool(
        "crop_plan",
        "The breeding guide to a crop (WO-6.7, plan 7.17), computed by code from CropsNH's "
        "rules: each step in order (obtain, crop sticks, mutation pool, Crop Breeder, Crop "
        "Manager) with its chance per growth tick and expected time, then the stats plan. "
        f"Keep the order. {CITE}",
        crops.CROP_PLAN_SCHEMA,
        crops.crop_plan,
    ),
    Tool(
        "crop_step",
        "One step of a breeding guide in full (default: the next one): the layout around the "
        "double stick, requirements (soil, sub-soil block, light, nutrients), odds, times, "
        f"warnings and the keep rule. {CITE}",
        crops.CROP_STEP_SCHEMA,
        crops.crop_step,
    ),
    Tool(
        "action_status",
        "One world action by id (state, read-back, how it was confirmed, its result), or the "
        "pending one and the newest ten.",
        ops.ACTION_STATUS_SCHEMA,
        ops.action_status,
        needs_db=False,
    ),
)

# Plan 7.0's other tools: none is registered, and no stub stands in for one.
LATER: dict[str, str] = {
    "timer_set": "WO-5.3 follow-up, not yet a plan 8 row (a timer has to ring by voice and "
    "needs a core-to-voice push, D205; the router's instant commands came with WO-2.3, D150)",
    "export_diff": "WO-1.4 (fingerprint and diff report)",
    "structure_get": "WO-1.8 (structure table, D123; its data reached this base late, so the "
    "tool is a follow-up)",
    "machine_fact_get": "machine_info covers reading facts (WO-2.2); a fact-by-key tool comes with machine_fact_set",
    "machine_fact_set": "WO-1.9 (pack-update playbook: curation)",
    "crop_status": "WO-7.6 (breeding station status)",
    # Accepting and every later move are Clayton's (the Designer, gos plan --accept/--status,
    # POST /api/plans/{id}/status; D251, D238): no model moves a plan or writes the ledger.
    "plan_set_status": "WO-4.4 made lifecycle moves Clayton's (gos plan --status, the "
    "dashboard): not an agent tool (D238)",
    "layout_estimate": "WO-10.5 (corridor-aware layout; plan_export footprint has the "
    "bounding-box one)",
    "ledger_update": "WO-4.4: the ledger follows Clayton's accepts and lifecycle moves; "
    "not an agent tool (D238)",
    "speak": "WO-5.3 follow-up, not yet a plan 8 row (WO-5.3 built the speaker; the "
    "agent tool needs a core-to-voice push, D205)",
    "persona_set": "WO-5.6 follow-up, not yet a plan 8 row (switching is the router's "
    "instant command and POST /api/persona; the model tool waits for the persona subagent, "
    "D208)",
    **dict.fromkeys(
        ("events_recent", "session_summary"), "WO-6.3 (event store, sessions)"
    ),
    "screen_snapshot": "WO-8.2 (screen reading)",
    **dict.fromkeys(("wiki_search", "wiki_fetch"), "WO-8.3 (wiki cache)"),
    "notify": "WO-9.2 (alert routing)",
    "schematic_export": "WO-10.4 (schematic I/O)",
    **dict.fromkeys(("theme_get", "theme_list", "theme_save"), "WO-10.6 (themes)"),
    "build_request": "WO-10.7 (shell designer)",
}


def build_registry(
    db: Path,
    glossary: Glossary | None = None,
    *,
    key: bytes | None = None,
    page: int = PAGE,
    vault: VaultIO | None = None,
    deep: DeepLane | None = None,
    ops: Ops | None = None,
    writer: Callable[[], Writer | None] | None = None,
    spoiler_default: int = 0,
) -> Registry:
    """Every tool over the DB at ``db`` and ``vault``, handing off to ``deep``, with the OC
    bridge ``ops``, the planner model ``writer`` for ``plan_explain`` (``key``: the cursor
    key, default random) and the global spoiler level (``[policy] spoiler_default``, D72)."""
    reg = Registry(
        Env(db, glossary or Glossary(), vault, deep, ops, writer, spoiler_default),
        key=key,
        page=page,
    )
    for tool in TOOLS:
        reg.add(tool)
    return reg


def default_registry(
    gos_home: Path,
    deep: DeepLane | None = None,
    ops: Ops | None = None,
    writer: Callable[[], Writer | None] | None = None,
    spoiler_default: int = 0,
) -> Registry:
    """The tools over ``GOS_HOME/data/gos.sqlite`` and the live vault (its glossary too); a
    note an agent writes is mirrored into ``note_fts`` at once."""
    db, root = access.db_path(gos_home), gos_home / "vault"

    def mirror(rel: str) -> None:
        fts.mirror_one(db, root, rel)

    return build_registry(
        db,
        Glossary.find(root),
        vault=VaultIO(root, on_write=mirror),
        deep=deep,
        ops=ops,
        writer=writer,
        spoiler_default=spoiler_default,
    )
