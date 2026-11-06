"""The tools WO-2.2 implements, and the plan 7.0 tools that arrive with later work orders."""

from __future__ import annotations

from pathlib import Path

from gregtec.agents.tools import items, machines, notes, quests, recipes, sql
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import DEEP, Env, Registry, Tool
from gregtec.agents.tools.tsv import PAGE
from gregtec.db import access
from gregtec.vault import fts
from gregtec.vault.io import VaultIO

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
        "One quest: state, quest lines, prerequisites, tasks and rewards. A quest past the "
        "frontier is shown nudge-first (spoil 0: where to go, not what it is); spoil 1 adds "
        "its tasks and 2 everything, only when Clayton asks for more.",
        quests.QUEST_GET_SCHEMA,
        quests.quest_get,
    ),
    Tool(
        "quest_frontier",
        "The quests Clayton can work on now (the frontier), in quest-book order, with his tier.",
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
        "Create or replace a note in base/, plans/, quests/, themes/ or personas/ in the note "
        "grammar; the tool writes the frontmatter. me/, _gos/, INDEX.md and the generated "
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
)

# Plan 7.0's other tools: none is registered, and no stub stands in for one.
LATER: dict[str, str] = {
    **dict.fromkeys(("handoff", "timer_set"), "WO-2.3 (router, instant commands)"),
    "export_diff": "WO-1.4 (fingerprint and diff report)",
    "ore_find": "WO-1.6 (ore mixes and veins; ore-intel WO-6.6)",
    "structure_get": "WO-1.8 (structure table, D123; its data reached this base late, so the "
    "tool is a follow-up)",
    "machine_fact_get": "machine_info covers reading facts (WO-2.2); a fact-by-key tool comes with machine_fact_set",
    "machine_fact_set": "WO-1.9 (pack-update playbook: curation)",
    **dict.fromkeys(
        ("crop_find", "crop_plan", "crop_step", "crop_status"),
        "WO-1.10 (crop data), WO-6.7 (planner); crop_status with WO-7.6",
    ),
    **dict.fromkeys(
        (
            "design_production",
            "plan_get",
            "plan_export",
            "plan_set_status",
            "plan_list",
            "plan_pin",
            "plan_rename",
            "effort_estimate",
            "layout_estimate",
        ),
        "Phase 3 (WO-3.4 to WO-3.7)",
    ),
    "base_get": "WO-3.8 (My base)",
    "design_power": "WO-4.2 (power objective modes)",
    **dict.fromkeys(("ledger_get", "ledger_update"), "WO-4.4 (EU ledger)"),
    "speak": "WO-5.3 (TTS)",
    "persona_set": "WO-5.6 (persona packs)",
    "player_pos": "WO-6.1 (watchers)",
    **dict.fromkeys(
        ("events_recent", "session_summary"), "WO-6.3 (event store, sessions)"
    ),
    **dict.fromkeys(
        ("telemetry_get", "action_propose", "action_status"),
        "WO-7.1 (bridge, confirm flow)",
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
) -> Registry:
    """Every tool over the DB at ``db`` and ``vault`` (``key``: the cursor key, default
    random)."""
    reg = Registry(Env(db, glossary or Glossary(), vault), key=key, page=page)
    for tool in TOOLS:
        reg.add(tool)
    return reg


def default_registry(gos_home: Path) -> Registry:
    """The tools over ``GOS_HOME/data/gos.sqlite`` and the live vault (its glossary too); a
    note an agent writes is mirrored into ``note_fts`` at once."""
    db, root = access.db_path(gos_home), gos_home / "vault"

    def mirror(rel: str) -> None:
        fts.mirror_one(db, root, rel)

    return build_registry(db, Glossary.find(root), vault=VaultIO(root, on_write=mirror))
