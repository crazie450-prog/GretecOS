"""The tool registry: one Python tool layer for both lanes (plan sections 4.4, 4.6 and 7.0).

- A :class:`Tool` is a function with a JSON schema (Anthropic's ``input_schema`` shape), a
  read-only flag and the lanes it is offered in. The fast lane gets the read-only tools plus
  ``handoff`` (WO-2.3); :meth:`Registry.add` refuses anything else there.
- :meth:`Registry.call` validates the arguments, opens the DB read-only (URI ``mode=ro`` plus
  ``PRAGMA query_only``, no ATTACH, a cap on value sizes and a progress-handler time limit),
  runs the tool and renders its result as TSV (:mod:`.tsv`): at most 50 rows a page, then a
  ``more`` cursor (:mod:`.cursor`) that the ``more`` tool turns into the next page. Errors come
  back as text with ``is_error`` set, the way Anthropic's ``tool_result`` carries them.
- Only read-only tools run here. A tool that writes needs its own gate first: the vault path
  guard (WO-2.4, WO-2.5) or the world-action confirm flow (WO-7.1).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.agents.tools.cursor import MAX_LENGTH, CursorError, Cursors
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.schema import ArgumentError, check_schema, validate
from gregtec.agents.tools.tsv import PAGE, Result, render
from gregtec.db import access
from gregtec.db.quests import KV_SOURCE  # its record changes on every quest load

FAST, DEEP = "fast", "deep"
LANES = (FAST, DEEP)
# The one fast-lane tool that isn't read-only (WO-2.3); it still needs its own gate to run.
FAST_EXTRA = frozenset({"handoff"})
MORE = "more"
NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
DEADLINE_S = 5.0  # a named query that runs longer is stopped
# SQLITE_LIMIT_LENGTH: the longest string or blob a query can make or read (the DB's longest
# value, a quest's raw JSON, is about 21 kB in 2.9.0).
MAX_VALUE_BYTES = 1_000_000
MAX_SQL_BYTES = 100_000


class ToolError(Exception):
    """A message for the model: the call failed, nothing was changed."""


@dataclass(frozen=True)
class Env:
    """What every tool call can read."""

    db: Path  # GOS_HOME/data/gos.sqlite
    glossary: Glossary = field(default_factory=Glossary)


@dataclass
class Call:
    """One tool call: a read-only connection, the environment and the page size."""

    conn: sqlite3.Connection
    env: Env
    page: int = PAGE

    def query(
        self, name: str, params: dict[str, object] | None = None
    ) -> list[sqlite3.Row]:
        return access.query(self.conn, name, params)


Handler = Callable[[Call, dict[str, Any], int], Result]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    read_only: bool = True
    lanes: frozenset[str] = frozenset(LANES)
    deadline_s: float = DEADLINE_S

    def spec(self) -> dict[str, Any]:
        """The tool as Anthropic's Messages API takes it."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True)
class Output:
    text: str
    is_error: bool = False


def _more(call: Call, args: dict[str, Any], offset: int) -> Result:
    raise AssertionError("Registry.call handles more itself")


MORE_TOOL = Tool(
    MORE,
    "The next page of a tool result: pass the cursor from its `more:` line. Results come 50 "
    "rows a page.",
    {
        "type": "object",
        "properties": {
            "cursor": {
                "type": "string",
                "description": "the text after `more:` in the last result",
                "maxLength": MAX_LENGTH,
            }
        },
        "required": ["cursor"],
        "additionalProperties": False,
    },
    _more,
)


def guard(conn: sqlite3.Connection, seconds: float) -> None:
    """Make ``conn`` (opened ``mode=ro``) refuse writes and ATTACH, cap the size of any value
    a query makes, and stop any statement still running ``seconds`` from now."""
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA trusted_schema = OFF")
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
    conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_BYTES)
    deadline = time.monotonic() + seconds
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)


def epoch(conn: sqlite3.Connection) -> str:
    """The DB's state as far as paging cares: the newest export run and the last quest load
    (whose record has its load time). A rebuild of the same run that changes nothing else is
    not seen; its recipes keep their keys and order."""
    run = access.query(conn, "current_run")
    quests = conn.execute("SELECT value FROM kv WHERE key = ?", (KV_SOURCE,)).fetchone()
    loaded = hashlib.sha256(quests[0].encode()).hexdigest()[:12] if quests else "-"
    newest = (
        f"{run[0]['id']}:{run[0]['run_key']}:{run[0]['created_at']}" if run else "-"
    )
    return f"{newest}|{loaded}"


class Registry:
    def __init__(self, env: Env, *, key: bytes | None = None, page: int = PAGE) -> None:
        self.env = env
        self.page = page
        self._tools: dict[str, Tool] = {}
        self._cursors = Cursors(key)
        self.add(MORE_TOOL)

    def add(self, tool: Tool) -> None:
        if not NAME.fullmatch(tool.name):
            raise ValueError(f"bad tool name {tool.name!r}")
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name} is already registered")
        if not tool.lanes or tool.lanes - set(LANES):
            raise ValueError(f"{tool.name}: lanes must be some of {LANES}")
        if FAST in tool.lanes and not tool.read_only and tool.name not in FAST_EXTRA:
            raise ValueError(
                f"{tool.name}: the fast lane gets the read-only tools plus handoff (plan 7.0)"
            )
        check_schema(tool.input_schema)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def tools(self, lane: str | None = None) -> list[Tool]:
        return [t for t in self._tools.values() if lane is None or lane in t.lanes]

    def specs(self, lane: str) -> list[dict[str, Any]]:
        """The lane's tools as the Messages API's ``tools`` list."""
        return [t.spec() for t in self.tools(lane)]

    def call(self, name: str, args: object, *, lane: str | None = None) -> Output:
        """Run tool ``name`` (``more`` for a cursor's next page); ``lane`` None is the shell."""
        try:
            return Output(self._call(name, args, lane))
        except (ToolError, ArgumentError, CursorError) as exc:
            return Output(f"error: {exc}\n", is_error=True)

    def _tool(self, name: str, lane: str | None) -> Tool:
        if lane is not None and lane not in LANES:
            raise ToolError(f"unknown lane {lane!r}")
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"no tool named {name!r}")
        if lane is not None and lane not in tool.lanes:
            raise ToolError(f"{name} isn't offered in the {lane} lane")
        if not tool.read_only:
            raise ToolError(f"{name} changes things and has no gate here yet")
        return tool

    def _call(self, name: str, args: object, lane: str | None) -> str:
        offset, issued = 0, None
        if name == MORE:
            self._tool(MORE, lane)
            cursor = validate(MORE_TOOL.input_schema, args)["cursor"]
            page = self._cursors.open(cursor)
            name, args, offset, issued = page.tool, page.args, page.offset, page.epoch
        tool = self._tool(name, lane)
        if tool is MORE_TOOL:
            raise ToolError("a cursor never points at more itself")
        values = validate(tool.input_schema, args)
        with self._connect(tool) as conn:
            if issued is not None and issued != epoch(conn):
                raise ToolError(
                    "the DB changed since this cursor was issued; call the tool again"
                )
            result = tool.handler(Call(conn, self.env, self.page), values, offset)
            cursor = None
            if result.more:
                cursor = self._cursors.issue(
                    tool.name, values, result.offset + len(result.rows), epoch(conn)
                )
                if cursor is None:
                    result.notes.append(
                        "more rows exist, but these arguments are too long for a more "
                        "cursor: narrow the call"
                    )
        return render(result, cursor)

    @contextmanager
    def _connect(self, tool: Tool) -> Iterator[sqlite3.Connection]:
        path = self.env.db
        if not path.is_file():
            raise ToolError(
                f"there is no DB at {path} yet: run `gos db build` (recipes) or "
                "`gos quests load` (quests)"
            )
        try:
            conn = access.connect(path, readonly=True)
        except (access.SchemaError, sqlite3.Error) as exc:
            raise ToolError(f"can't open the DB: {exc}") from None
        try:
            guard(conn, tool.deadline_s)
            yield conn
        except sqlite3.OperationalError as exc:
            if str(exc) == "interrupted":
                raise ToolError(
                    f"{tool.name} ran past its {tool.deadline_s:g} s limit and was stopped"
                ) from None
            raise ToolError(f"database error: {exc}") from None
        except sqlite3.Error as exc:
            raise ToolError(f"database error: {exc}") from None
        finally:
            conn.close()
