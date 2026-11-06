"""``db_query`` (named queries) and ``db_sql`` (one guarded SELECT) (plan 4.6 and 5.3, [D§3.6]).

**db_query(name, params)** runs ``core/gregtec/db/queries/<name>.sql`` with named parameters:
each ``:param`` the query uses must be given, as a JSON scalar, and nothing else. ``:limit`` and
``:offset`` belong to the tool (50 rows a page, then the ``more`` cursor).

**db_sql(select)** runs one ad-hoc statement, read-only in four independent ways:

1. the connection is opened ``mode=ro`` with ``PRAGMA query_only`` on, ATTACH capped at 0
   databases, values capped at 1 MB and a progress-handler time limit (registry.guard);
2. the statement must be one SELECT (``WITH`` and ``VALUES`` too); sqlite3 runs only one
   statement per call;
3. while it is prepared, an authorizer allows only SELECT, READ of a table in
   :data:`READABLE` (game data and GOS's own tables, not :data:`PRIVATE` ones such as ``kv``
   and ``device``) and the functions in :data:`FUNCTIONS`, plus the one read-only pragma FTS5
   runs itself (``data_version``, no argument). Everything else is denied: writes, ATTACH,
   every other PRAGMA, ``load_extension``, ``randomblob``/``zeroblob``, recursive CTEs, temp
   tables and views, transactions;
4. rows are capped (50 a page); deeper pages re-run it, and the time limit holds every run.

Rows are raw DB values: cite the ``ref`` (r:) and quest (q:) ids in them. A page whose rows
carry no such id says so, because its numbers can't be quoted as game facts.
"""

from __future__ import annotations

import functools
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from gregtec.agents.tools.registry import Call, ToolError
from gregtec.agents.tools.tsv import Result
from gregtec.db import access

MANAGED = ("limit", "offset")
SQL_MAX = 4000
DEADLINE_S = 2.0
_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:[^']|'')*'")
_PARAM = re.compile(r"(?<![\w:]):([A-Za-z_][A-Za-z0-9_]*)")
_FIRST_WORD = re.compile(r"\s*([A-Za-z]+)")
# A citation id in a cell: r:/q:/mte: (plan:, crop: and w: later), or a bare quest id.
_CITE = re.compile(r"(?:r|q|mte|plan|crop|w):\S+|[A-Za-z0-9_-]{22}")
RAW_NOTE = (
    "raw DB values: cite the r: (recipe.ref) and q: (quest id) ids of the rows you use"
)
UNCITED_NOTE = (
    "no r:, q: or mte: id in these rows: their numbers can't be quoted as game facts; "
    "select the recipe's ref or the quest's id with them"
)

# Tables db_sql may read. Every table schema.sql creates is here or in PRIVATE (a unit test
# checks), so a new table is unreadable until someone decides which it is.
GAME_TABLES = frozenset(
    {
        "export_run", "mod", "item", "fluid", "ore", "ore_member", "recipe_map", "nei_handler",
        "recipe", "recipe_input", "recipe_input_alt", "recipe_output", "recipe_meta",
        "assembly_line_research", "machine", "machine_map", "machine_fact", "structure",
        "name_fts", "quest_line", "quest_line_entry", "quest", "quest_prereq", "quest_task",
        "quest_reward", "quest_item", "quest_progress", "quest_state", "quest_fts", "session",
        "event", "tel_machine", "tel_sample", "action", "plan", "base_machine", "base_stock",
        "ore_mix", "vein", "wiki_page", "wiki_fts", "note_fts", "llm_usage", "alert",
    }
)  # fmt: skip
PRIVATE = frozenset({"kv", "device"})  # app state and paired devices: not for agents
FTS = ("name_fts", "quest_fts", "wiki_fts", "note_fts")
FTS_SHADOW = frozenset(
    f"{t}_{s}" for t in FTS for s in ("data", "idx", "content", "docsize", "config")
)
READABLE = (
    GAME_TABLES
    | FTS_SHADOW
    | {"sqlite_master", "sqlite_schema", "json_each", "json_tree"}
)
FUNCTIONS = frozenset(
    {
        # scalar
        "abs", "char", "coalesce", "concat", "concat_ws", "format", "glob", "hex", "ifnull",
        "iif", "if", "instr", "length", "like", "likelihood", "likely", "lower", "ltrim", "max",
        "min", "nullif", "octet_length", "printf", "quote", "replace", "round", "rtrim", "sign",
        "substr", "substring", "trim", "typeof", "unhex", "unicode", "unlikely", "upper",
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


def db_sql(call: Call, args: dict[str, Any], offset: int) -> Result:
    sql = check_select(args["select"])
    conn = call.conn
    conn.set_authorizer(authorize)
    try:
        cur = conn.execute(sql)
        columns = tuple(d[0] for d in cur.description or ())
        rows: list[tuple[object, ...]] = []
        for n, row in enumerate(cur):
            if n >= offset:
                rows.append(tuple(row))
                if len(rows) > call.page:
                    break
    # DatabaseError covers ProgrammingError too: more than one statement.
    except sqlite3.DatabaseError as exc:
        if "not authorized" in str(exc) or "prohibited" in str(exc):
            raise ToolError(f"{REFUSED} ({exc})") from None
        if str(exc) == "interrupted":
            raise  # the registry reports the time limit
        raise ToolError(f"SQL error: {exc}") from None
    finally:
        conn.set_authorizer(None)
    page = rows[: call.page]
    notes = [RAW_NOTE]
    if not cited(page):
        notes.append(UNCITED_NOTE)
    return Result(columns, page, notes=notes, offset=offset, more=len(rows) > call.page)


def cited(rows: list[tuple[object, ...]]) -> bool:
    """Whether every row that holds a number also holds a citation id."""
    for row in rows:
        numbers = any(
            isinstance(v, int | float) and not isinstance(v, bool) for v in row
        )
        if numbers and not any(isinstance(v, str) and _CITE.fullmatch(v) for v in row):
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
    found: dict[str, Named] = {}
    for path in sorted(access.QUERIES.glob("*.sql")):
        try:
            sql = access.query_text(path.stem)
        except ValueError:
            continue  # not a query name
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
    cur = call.conn.execute(access.query_text(q.name), bind)
    columns = tuple(d[0] for d in cur.description or ())
    rows: list[tuple[object, ...]] = []
    for n, row in enumerate(cur):
        if n >= skip:
            rows.append(tuple(row))
            if len(rows) > call.page:
                break
    return Result(
        columns,
        rows[: call.page],
        head=[("query", q.name)],
        notes=[RAW_NOTE],
        offset=offset,
        more=len(rows) > call.page,
    )
