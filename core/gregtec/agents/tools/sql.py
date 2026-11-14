"""``db_query`` (named queries) and ``db_sql`` (one guarded SELECT) (plan 4.6 and 5.3, [D§3.6]).

**db_query(name, params)** runs one of the named queries in :data:`DB_QUERY`
(``core/gregtec/db/queries/<name>.sql``) with named parameters: each ``:param`` the query uses
must be given, as a JSON scalar, and nothing else. ``:limit`` and ``:offset`` belong to the tool
(50 rows a page, then the ``more`` cursor). The other named queries are the tools' own: the
quest ones, which only the quest tools run because those show a quest past the frontier
nudge-first (D26), and any query not added to :data:`DB_QUERY` yet. An authorizer refuses reads
of the :data:`PRIVATE` tables, whatever a named query says.

**db_sql(select)** runs one ad-hoc statement, read-only in four independent ways:

1. the connection is opened ``mode=ro`` with ``PRAGMA query_only`` on, ATTACH capped at 0
   databases and a progress-handler time limit (registry.guard);
2. the statement must be one SELECT (``WITH`` and ``VALUES`` too); sqlite3 runs only one
   statement per call;
3. while it is prepared, an authorizer allows only SELECT, READ of a table in
   :data:`READABLE` (game data and GOS's own tables, not :data:`PRIVATE` ones such as ``kv``
   and ``device``) and the functions in :data:`FUNCTIONS`, plus the one read-only pragma FTS5
   runs itself (``data_version``, no argument). Everything else is denied: writes, ATTACH,
   every other PRAGMA, ``load_extension``, ``randomblob``/``zeroblob``, recursive CTEs, temp
   tables and views, transactions;
4. rows are capped (50 a page, fewer once a page's values pass :data:`PAGE_BYTES`); deeper
   pages re-run it, and the time limit holds every run.

**The time limit** (2 s). SQLite checks the progress handler between the steps of a query, never
inside one row's expressions, and it copies a flattened subquery's expression wherever its
column is used, so one short statement can make a single row call a function hundreds of times.
db_sql keeps every call short instead: values of at most 64 KiB, LIKE and GLOB patterns of at
most 100 bytes and at most 100 result columns (:data:`LIMITS`); no ``trim``, ``ltrim`` or
``rtrim`` (the two-argument form takes time that grows with the product of its lengths, and the
authorizer can't tell it from the other); and the functions in :data:`COSTLY`, whose time grows
the same way, run through :class:`Costly`, which checks the deadline before each call. A
statement then stops within a fraction of a second of its limit.

Rows of both tools are raw DB values: cite the ``ref`` (r:) and quest (q:) ids in them. A page
whose rows hold numbers but no such id says so, because its numbers can't be quoted as game
facts (:func:`cited`, a heuristic). A db_sql page that read quest-book text says that the text
is data and that a quest past the frontier is a spoiler.

**Spoilers (WO-6.4, D268).** Below the full level (any lane's call at a global setting under 2:
a spoiler command raises single quests through the quest tools, never db_sql), db_sql also refuses to read what a quest is: the
:data:`SPOILER_COLUMNS` of ``quest`` (name, description, raw JSON, task logic, repeat time) and the
:data:`SPOILER_TABLES` (tasks, rewards, their items, the quest text index). Those go through
the quest tools, which show a quest past the frontier only at his level; the quest graph,
states, lines and progress stay readable.
"""

from __future__ import annotations

import functools
import re
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from gregtec.agents.tools.quests import TEXT_NOTE
from gregtec.agents.tools.registry import Call, ToolError
from gregtec.agents.tools.tsv import Result
from gregtec.db import access
from gregtec.quests import spoilers

MANAGED = ("limit", "offset")
SQL_MAX = 4000
DEADLINE_S = 2.0
# db_sql's limits, for its statement only (the named queries keep registry.guard's).
VALUE_MAX = 64 * 1024  # the DB's longest values are about 25 kB
LIMITS = {
    sqlite3.SQLITE_LIMIT_LENGTH: VALUE_MAX,  # any string or blob read or made
    sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH: 100,
    sqlite3.SQLITE_LIMIT_COLUMN: 100,  # result columns, GROUP BY and ORDER BY terms
}
PAGE_BYTES = 1 << 20  # text and blob bytes in a page's rows, after which the page stops
# (name, arguments): SQLite's functions whose time grows with the product of two lengths.
COSTLY = (("instr", 2), ("replace", 3), ("like", 2), ("like", 3), ("glob", 2))
_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:[^']|'')*'")
_PARAM = re.compile(r"(?<![\w:]):([A-Za-z_][A-Za-z0-9_]*)")
_FIRST_WORD = re.compile(r"\s*([A-Za-z]+)")
# A citation id in a cell: r:/q:/mte: (plan:, crop: and w: later), or a bare quest id in a
# column of QUEST_ID_COLUMNS.
_CITE = re.compile(r"(?:r|q|mte|plan|crop|w):\S+")
_QUEST_ID = re.compile(r"[A-Za-z0-9_-]{22}")
QUEST_ID_COLUMNS = frozenset({"id", "quest_id"})
RAW_NOTE = (
    "raw DB values: cite the r: (recipe.ref) and q: (quest id) ids of the rows you use"
)
UNCITED_NOTE = (
    "no r:, q: or mte: id in these rows: their numbers can't be quoted as game facts; "
    "select the recipe's ref or the quest's id with them"
)
SPOILER_NOTE = (
    "a quest past the frontier is a spoiler: say where to go (its prerequisites), not what it "
    "is, unless Clayton asks for more (D26)"
)
TOO_BIG = (
    "db_sql reads and makes values of at most 64 KiB, 100 columns and LIKE or GLOB patterns "
    "of at most 100 bytes: leave out a long column (export_run.sources_json, say)"
)

# Tables db_sql may read. Every table schema.sql (and, until WO-1.3's schema absorbs it,
# gregtec.db.structures, D123) creates is here or in PRIVATE (a unit test checks), so a new table
# is unreadable until someone decides which it is.
GAME_TABLES = frozenset(
    {
        "export_run", "mod", "item", "fluid", "ore", "ore_member", "recipe_map", "nei_handler",
        "recipe", "recipe_input", "recipe_input_alt", "recipe_output", "recipe_meta",
        "assembly_line_research", "machine", "machine_map", "machine_fact", "structure",
        "structure_run",
        "name_fts", "quest_line", "quest_line_entry", "quest", "quest_prereq", "quest_task",
        "quest_reward", "quest_item", "quest_progress", "quest_state", "quest_fts", "session",
        "event", "tel_machine", "tel_sample", "action", "plan", "base_machine", "base_stock",
        "ore_mix", "vein", "wiki_page", "wiki_fts", "note_fts", "llm_usage", "alert",
        "crop", "crop_parent_recipe", "crop_parent", "crop_mutation", "crop_drop", "crop_seed",
        "crop_soil",
        "my_base_machine", "my_base_stock",  # the core's (store.py): My base, WO-3.8 (D210)
        "rotor_size", "rotor_material", "rotor_gt", "generator", "generator_gt",
        "fuel_container", "fuel",
        "cable", "battery", "battery_buffer", "transformer", "lsc_cell",  # WO-4.5 (D240)
        "reactor_boost", "dynamo_hatch",  # WO-4.8 (D326)
        "block_state", "block_texture",  # the block catalog, WO-10.3 (D307)
    }
)  # fmt: skip
PRIVATE = frozenset({"kv", "device"})  # app state and paired devices: not for agents
FTS = ("name_fts", "quest_fts", "wiki_fts", "note_fts")
SHADOW = ("data", "idx", "content", "docsize", "config")
FTS_SHADOW = frozenset(f"{t}_{s}" for t in FTS for s in SHADOW)
# The tables that hold quest-book text (names, descriptions, raw JSON).
QUEST_TEXT = frozenset(
    {"quest", "quest_line", "quest_fts", *(f"quest_fts_{s}" for s in SHADOW)}
)
# What a quest is (D268): read only through the quest tools below the full spoiler level.
SPOILER_TABLES = frozenset(
    {
        "quest_task",
        "quest_reward",
        "quest_item",
        "quest_fts",
        *(f"quest_fts_{s}" for s in SHADOW),
    }
)
SPOILER_COLUMNS = {
    # task_logic and repeat_time: what quest_get shows only from level 1
    "quest": frozenset({"name", "descr", "raw_json", "task_logic", "repeat_time"})
}
SPOILER_REFUSED = (
    "refused: below the full spoiler level, quest names, descriptions, tasks and rewards are "
    "read with quest_find, quest_get, quest_frontier and quest_for_item, which show a quest "
    "past his frontier only at the level he asked for (D26, D268); quest ids, states, lines, "
    "prerequisites and progress stay readable here"
)
READABLE = (
    GAME_TABLES
    | FTS_SHADOW
    | {"sqlite_master", "sqlite_schema", "json_each", "json_tree"}
)
FUNCTIONS = frozenset(
    {
        # scalar (instr, replace, like and glob run through Costly; no trim, ltrim or rtrim)
        "abs", "char", "coalesce", "concat", "concat_ws", "format", "glob", "hex", "ifnull",
        "iif", "if", "instr", "length", "like", "likelihood", "likely", "lower", "max", "min",
        "nullif", "octet_length", "printf", "quote", "replace", "round", "sign", "substr",
        "substring", "typeof", "unhex", "unicode", "unlikely", "upper",
        # math
        "acos", "asin", "atan", "atan2", "ceil", "ceiling", "cos", "degrees", "exp", "floor",
        "ln", "log", "log10", "log2", "mod", "pi", "pow", "power", "radians", "sin", "sqrt",
        "tan", "trunc",
        # date and time
        "date", "time", "datetime", "julianday", "unixepoch", "strftime", "timediff",
        # aggregate and window
        "avg", "count", "group_concat", "string_agg", "sum", "total", "row_number", "rank",
        "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead", "first_value",
        "last_value", "nth_value",
        # JSON
        "json", "json_array", "json_array_length", "json_extract", "json_object", "json_quote",
        "json_type", "json_valid", "json_group_array", "json_group_object", "->", "->>",
        # FTS5
        "match", "bm25", "highlight", "snippet",
    }
)  # fmt: skip

Authorizer = Callable[[int, str | None, str | None, str | None, str | None], int]


def authorize(
    action: int, arg1: str | None, arg2: str | None, db: str | None, trigger: str | None
) -> int:
    """db_sql's sqlite3 authorizer (see the module docstring)."""
    allowed = (
        action == sqlite3.SQLITE_SELECT
        or (
            action == sqlite3.SQLITE_READ
            and (arg1 or "").lower() in READABLE
            and db in (None, "main")
        )
        or (action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in FUNCTIONS)
        # FTS5 reads PRAGMA data_version itself the first time a connection uses a table.
        or (
            action == sqlite3.SQLITE_PRAGMA
            and (arg1 or "").lower() == "data_version"
            and arg2 is None
        )
    )
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def spoiler(table: str | None, column: str | None) -> bool:
    """True when reading ``column`` of ``table`` tells what a quest is (D268)."""
    t = (table or "").lower()
    return t in SPOILER_TABLES or (column or "").lower() in SPOILER_COLUMNS.get(t, ())


def watching(seen: set[str], guard_spoilers: bool = False) -> Authorizer:
    """:func:`authorize`, adding each table a statement reads to ``seen``; with
    ``guard_spoilers`` also denying :func:`spoiler` reads (``"spoiler"`` joins ``seen``)."""

    def check(
        action: int, arg1: str | None, arg2: str | None, db: str | None, t: str | None
    ) -> int:
        if guard_spoilers and action == sqlite3.SQLITE_READ and spoiler(arg1, arg2):
            seen.add("spoiler")
            return sqlite3.SQLITE_DENY
        verdict = authorize(action, arg1, arg2, db, t)
        if verdict == sqlite3.SQLITE_OK and action == sqlite3.SQLITE_READ:
            seen.add((arg1 or "").lower())
        return verdict

    return check


def authorize_named(
    action: int, arg1: str | None, arg2: str | None, db: str | None, trigger: str | None
) -> int:
    """db_query's authorizer: a named query is ours, but it never reads a PRIVATE table."""
    if action == sqlite3.SQLITE_READ and (arg1 or "").lower() in PRIVATE:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class Costly:
    """The :data:`COSTLY` functions of one db_sql statement. Each call checks the deadline
    first, then runs SQLite's own function on a scratch connection with the same
    :data:`LIMITS`, so no call takes more than about 25 ms (``instr`` over two 64 KiB strings)
    and the statement stops at its first call past the deadline."""

    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self.expired = False
        self.error: sqlite3.Error | None = None
        self.scratch = sqlite3.connect(":memory:")
        for limit, value in LIMITS.items():
            self.scratch.setlimit(limit, value)

    def install(self, conn: sqlite3.Connection) -> None:
        """Put the wrappers in place of SQLite's functions on ``conn``, until it closes."""
        for name, n in COSTLY:
            sql = f"SELECT {name}({', '.join('?' * n)})"
            conn.create_function(name, n, self._wrap(sql), deterministic=True)

    def _wrap(self, sql: str) -> Callable[..., object]:
        def run(*args: object) -> object:
            if time.monotonic() > self.deadline:
                self.expired = True
                raise TimeoutError(sql)
            try:
                return self.scratch.execute(sql, args).fetchone()[0]
            except sqlite3.Error as exc:
                self.error = exc
                raise

        return run

    def close(self) -> None:
        self.scratch.close()


def check_select(sql: str) -> str:
    """``sql`` if it is one SELECT, WITH or VALUES statement; else ToolError."""
    if "\x00" in sql:
        raise ToolError("the statement has a NUL character")
    bare = _COMMENT.sub(" ", sql)
    m = _FIRST_WORD.match(bare)
    if m is None or m[1].lower() not in ("select", "with", "values"):
        raise ToolError(
            "db_sql runs one SELECT (or WITH ... SELECT) statement, nothing else"
        )
    return sql


REFUSED = (
    "refused: db_sql reads game-data tables with one SELECT and a list of safe functions; it "
    "never writes, attaches, sets pragmas, loads extensions, recurses or reads private tables"
)


def _bytes(row: Sequence[object]) -> int:
    return sum(len(v) for v in row if isinstance(v, str | bytes))


def db_sql(call: Call, args: dict[str, Any], offset: int) -> Result:
    sql = check_select(args["select"])
    conn = call.conn
    seen: set[str] = set()
    costly = Costly(call.deadline)
    before = {limit: conn.setlimit(limit, value) for limit, value in LIMITS.items()}
    costly.install(conn)  # the registry closes this connection after the call
    conn.set_authorizer(watching(seen, call.spoil_max < spoilers.FULL))
    rows: list[tuple[object, ...]] = []
    more = False
    try:
        cur = conn.execute(sql)
        columns = tuple(d[0] for d in cur.description or ())
        size = 0
        for n, row in enumerate(cur):
            if n < offset:
                continue
            if len(rows) == call.page or size >= PAGE_BYTES:
                more = True
                break
            rows.append(tuple(row))
            size += _bytes(row)
    # DatabaseError covers ProgrammingError too: more than one statement.
    except sqlite3.DatabaseError as exc:
        if costly.expired or str(exc) == "interrupted":
            # The registry reports the time limit.
            raise sqlite3.OperationalError("interrupted") from None
        error: sqlite3.Error = costly.error or exc
        if "spoiler" in seen:
            raise ToolError(SPOILER_REFUSED) from None
        if "not authorized" in str(error) or "prohibited" in str(error):
            raise ToolError(f"{REFUSED} ({error})") from None
        if any(w in str(error) for w in ("too big", "too many columns", "too complex")):
            raise ToolError(f"SQL error: {error} ({TOO_BIG})") from None
        raise ToolError(f"SQL error: {error}") from None
    finally:
        conn.set_authorizer(None)
        for limit, value in before.items():
            conn.setlimit(limit, value)
        costly.close()
    notes = [RAW_NOTE]
    if not cited(columns, rows):
        notes.append(UNCITED_NOTE)
    if seen & QUEST_TEXT:
        notes += [TEXT_NOTE, SPOILER_NOTE]
    if more and len(rows) < call.page:
        notes.append(
            f"this page stops at {len(rows)} rows: its values passed {PAGE_BYTES:,} bytes "
            "(more has the rest; or select fewer or shorter columns)"
        )
    return Result(columns, rows, notes=notes, offset=offset, more=more)


def cited(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> bool:
    """Whether every row that holds a number also holds something shaped like a citation id:
    ``r:``, ``q:`` or ``mte:`` (``plan:``, ``crop:`` and ``w:`` later), or a bare quest id in
    a column named ``id`` or ``quest_id``. A heuristic: it can't tell which of a row's numbers
    an id is for (a join can bring numbers from another table), nor whether an id-shaped value
    really is one; it catches numbers with no id at all."""
    quest_ids = [c.lower() in QUEST_ID_COLUMNS for c in columns]
    for row in rows:
        numbers = any(
            isinstance(v, int | float) and not isinstance(v, bool) for v in row
        )
        if numbers and not any(
            isinstance(v, str)
            and (_CITE.fullmatch(v) or (bare and _QUEST_ID.fullmatch(v)))
            for v, bare in zip(row, quest_ids, strict=True)
        ):
            return False
    return True


DB_SQL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "select": {
            "type": "string",
            "description": "one read-only SELECT over the DB (schema: SELECT sql FROM sqlite_schema)",
            "minLength": 1,
            "maxLength": SQL_MAX,
        }
    },
    "required": ["select"],
    "additionalProperties": False,
}

# The named queries db_query offers: game data. The quest queries are the quest tools' own
# (nudge-first spoilers, D26), and a new query is offered only once it is added here.
DB_QUERY = frozenset(
    {
        "current_run", "find_names", "fluid_by_name", "item_by_key", "item_names", "item_ores",
        "machine_by_mte", "machine_facts", "machine_maps", "recipe_by_ref", "recipe_conditions",
        "recipe_maps_matching", "recipe_maps_named", "recipe_slots", "recipes_for_item",
        "recipes_making", "recipes_using_item",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Named:
    name: str
    params: tuple[str, ...]  # in order of first use, :limit and :offset included
    summary: str  # the query's leading comment


def params_of(sql: str) -> tuple[str, ...]:
    bare = _STRING.sub("''", _COMMENT.sub(" ", sql))
    return tuple(dict.fromkeys(_PARAM.findall(bare)))


@functools.cache
def named_queries() -> dict[str, Named]:
    """The named queries db_query offers (:data:`DB_QUERY`), parsed."""
    found: dict[str, Named] = {}
    for path in sorted(access.QUERIES.glob("*.sql")):
        if path.stem not in DB_QUERY:
            continue
        sql = access.query_text(path.stem)
        lead = []
        for line in sql.splitlines():
            if not line.startswith("--"):
                break
            lead.append(line.removeprefix("--").strip())
        found[path.stem] = Named(path.stem, params_of(sql), " ".join(lead))
    return found


def db_query_schema() -> dict[str, Any]:
    listing = "; ".join(
        f"{q.name}({', '.join(p for p in q.params if p not in MANAGED)})"
        for q in named_queries().values()
    )
    return {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": f"the named query: {listing}",
                "enum": list(named_queries()),
            },
            "params": {
                "type": "object",
                "description": "its parameters by name (JSON scalars); limit and offset are set "
                "by the tool",
                "additionalProperties": {
                    "type": ["string", "integer", "number", "boolean", "null"]
                },
                "maxProperties": 20,
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }


def db_query(call: Call, args: dict[str, Any], offset: int) -> Result:
    q = named_queries().get(args["name"])
    if q is None:
        raise ToolError(f"no named query {args['name']!r}")
    params = dict(args.get("params") or {})
    for p in MANAGED:
        if p in params:
            raise ToolError(
                f":{p} is set by db_query (50 rows a page, then the more cursor)"
            )
    wanted = [p for p in q.params if p not in MANAGED]
    missing = [p for p in wanted if p not in params]
    extra = sorted(set(params) - set(wanted))
    if missing or extra:
        raise ToolError(
            f"{q.name} takes {', '.join(wanted) or 'no parameters'}"
            + (f"; missing {', '.join(missing)}" if missing else "")
            + (f"; unknown {', '.join(extra)}" if extra else "")
        )
    bind = {k: int(v) if isinstance(v, bool) else v for k, v in params.items()}
    skip = offset
    if "offset" in q.params:
        bind["offset"], skip = offset, 0
    if "limit" in q.params:
        bind["limit"] = skip + call.page + 1
    call.conn.set_authorizer(authorize_named)
    try:
        cur = call.conn.execute(access.query_text(q.name), bind)
        columns = tuple(d[0] for d in cur.description or ())
        rows: list[tuple[object, ...]] = []
        for n, row in enumerate(cur):
            if n >= skip:
                rows.append(tuple(row))
                if len(rows) > call.page:
                    break
    finally:
        call.conn.set_authorizer(None)
    page = rows[: call.page]
    return Result(
        columns,
        page,
        head=[("query", q.name)],
        notes=[RAW_NOTE] if cited(columns, page) else [RAW_NOTE, UNCITED_NOTE],
        offset=offset,
        more=len(rows) > call.page,
    )
