"""The tool registry: one Python tool layer for both lanes (plan sections 4.4, 4.6 and 7.0).

- A :class:`Tool` is a function with a JSON schema (Anthropic's ``input_schema`` shape), a
  read-only flag and the lanes it is offered in. The fast lane gets the read-only tools plus
  ``handoff`` (WO-2.3); :meth:`Registry.add` refuses anything else there.
- :meth:`Registry.call` validates the arguments, opens the DB read-only (URI ``mode=ro`` plus
  ``PRAGMA query_only``, no ATTACH, a cap on value sizes and a progress-handler time limit),
  runs the tool and renders its result as TSV (:mod:`.tsv`): at most 50 rows a page, then a
  ``more`` cursor (:mod:`.cursor`) that the ``more`` tool turns into the next page. Errors come
  back as text with ``is_error`` set, the way Anthropic's ``tool_result`` carries them; so
  does a tool's own bug (logged), so a lane's turn never ends on an exception.
- A tool that writes runs only with its own gate, named in ``Tool.gate``: the note tools'
  is the vault path guard (:mod:`gregtec.vault.io`, WO-2.5); ``action_propose``'s is the confirm
  flow (WO-7.1: it only proposes; only Clayton confirms). :meth:`Registry.add` refuses a writing tool without one. A tool with
  ``needs_db=False`` (the note tools but ``vault_search``) runs without opening the DB, so it
  works before the first ``gos db build``.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gregtec.agents.tools.cursor import MAX_LENGTH, CursorError, Cursors
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.schema import ArgumentError, check_schema, validate
from gregtec.agents.tools.tsv import PAGE, Result, render
from gregtec.db import access
from gregtec.db.quests import KV_SOURCE  # its record changes on every quest load
from gregtec.quests import spoilers
from gregtec.vault.io import VaultIO

if TYPE_CHECKING:
    from gregtec.agents.deep_lane import DeepLane
    from gregtec.bridge.ops import Ops
    from gregtec.planner.explain import Writer

log = logging.getLogger(__name__)

FAST, DEEP = "fast", "deep"
LANES = (FAST, DEEP)
# The one fast-lane tool that isn't read-only (WO-2.3); it still needs its own gate to run.
FAST_EXTRA = frozenset({"handoff"})
MORE = "more"
NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
DEADLINE_S = 5.0  # a named query that runs longer is stopped
# SQLITE_LIMIT_LENGTH for the named queries: the longest string or blob a query can make or read
# (the DB's longest values, export_run.sources_json and a quest's raw JSON, are about 25 kB).
# db_sql sets its own, smaller limits (sql.LIMITS).
MAX_VALUE_BYTES = 1_000_000
MAX_SQL_BYTES = 100_000


class ToolError(Exception):
    """A message for the model: the call failed, nothing was changed."""


@dataclass(frozen=True)
class Env:
    """What every tool call can read."""

    db: Path  # GOS_HOME/data/gos.sqlite
    glossary: Glossary = field(default_factory=Glossary)
    vault: VaultIO | None = None  # GOS_HOME/vault, for the note tools
    deep: DeepLane | None = None  # where handoff sends a task (WO-2.3; WO-2.4 runs it)
    ops: Ops | None = None  # the OC bridge: telemetry and the action desk (WO-7.1)
    # the planner model for plan_explain (WO-3.7, D199): called per use, None when no key
    writer: Callable[[], Writer | None] | None = None
    spoiler_default: int = (
        0  # [policy] spoiler_default, the one global level (D72, WO-6.4)
    )


@dataclass
class Call:
    """One tool call: a read-only connection (None for a tool that doesn't need the DB), the
    environment, the page size and the ``time.monotonic()`` at which the call's statements
    are stopped."""

    connection: sqlite3.Connection | None
    env: Env
    page: int = PAGE
    deadline: float = math.inf
    # What Clayton asked, when a lane calls the tool for one of his requests (handoff passes
    # it on as the deep lane's instruction; the model's own words are only its data).
    request: str | None = None
    # Spoiler levels (WO-6.4, D268): the level a quest tool shows when its arguments name none
    # (the global setting), and the most it may show whatever they say (spoilers.allowed: the
    # global setting, raised only by Clayton's own words).
    spoil_default: int = 0
    spoil_max: int = spoilers.FULL

    def spoil(self, asked: int | None) -> int:
        """The level a quest tool shows: ``asked`` (its ``spoil`` argument) or the default,
        never above :attr:`spoil_max`."""
        return min(self.spoil_default if asked is None else asked, self.spoil_max)

    @property
    def conn(self) -> sqlite3.Connection:
        if self.connection is None:
            raise AssertionError("this tool was registered with needs_db=False")
        return self.connection

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
    needs_db: bool = True
    gate: str = (
        ""  # what checks a writing tool's every call (required when not read_only)
    )

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


def guard(conn: sqlite3.Connection, seconds: float) -> float:
    """Make ``conn`` (opened ``mode=ro``) refuse writes and ATTACH, cap the size of any value
    a query makes, and stop any statement still running ``seconds`` from now (returned, as a
    ``time.monotonic()``). SQLite checks the progress handler only between steps of a query
    (its loops), never inside one row's expressions; db_sql bounds those itself."""
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA trusted_schema = OFF")
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
    conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_BYTES)
    deadline = time.monotonic() + seconds
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    return deadline


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
        if not tool.read_only and not tool.gate:
            raise ValueError(f"{tool.name} writes, so it needs a gate")
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

    def call(
        self,
        name: str,
        args: object,
        *,
        lane: str | None = None,
        request: str | None = None,
        spoil_max: int | None = None,
    ) -> Output:
        """Run tool ``name`` (``more`` for a cursor's next page); ``lane`` None is the shell.
        ``request``: Clayton's words the call is made for (see :attr:`Call.request`).
        ``spoil_max``: the most a quest tool may show when Clayton asked for it himself (a tap
        on the dashboard); otherwise a lane's call gets :func:`spoilers.allowed` of
        ``request``, and the shell (``gos tool``, his own command) up to full."""
        try:
            return Output(self._call(name, args, lane, request, spoil_max))
        except (ToolError, ArgumentError, CursorError) as exc:
            return Output(f"error: {exc}\n", is_error=True)
        except Exception:
            log.exception("tool %s failed", name)
            return Output(
                f"error: {name} failed on a bug in the tool (logged); nothing was changed. "
                "Answer without it, or try another tool\n",
                is_error=True,
            )

    def _tool(self, name: str, lane: str | None) -> Tool:
        if lane is not None and lane not in LANES:
            raise ToolError(f"unknown lane {lane!r}")
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"no tool named {name!r}")
        if lane is not None and lane not in tool.lanes:
            raise ToolError(f"{name} isn't offered in the {lane} lane")
        return tool

    def _spoils(
        self, lane: str | None, request: str | None, spoil_max: int | None
    ) -> tuple[int, int]:
        """(default level, highest level) for one call (D268). The default is always the global
        setting: going higher takes a ``spoil`` argument within the highest level, so a
        request's words raise what one quest may show, never every quest a list names."""
        default = self.env.spoiler_default
        if spoil_max is not None:
            return default, max(default, spoilers.check_level(spoil_max))
        if lane is None:
            return default, spoilers.FULL
        return default, spoilers.allowed(request, default)

    def _call(
        self,
        name: str,
        args: object,
        lane: str | None,
        request: str | None,
        spoil_max: int | None = None,
    ) -> str:
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
        spoil_default, spoil_top = self._spoils(lane, request, spoil_max)
        if not tool.needs_db:
            result = tool.handler(
                Call(
                    None,
                    self.env,
                    self.page,
                    request=request,
                    spoil_default=spoil_default,
                    spoil_max=spoil_top,
                ),
                values,
                offset,
            )
            if result.more:
                raise AssertionError(f"{tool.name} has no DB to page from")
            return render(result)
        with self._connect(tool) as (conn, deadline):
            if issued is not None and issued != epoch(conn):
                raise ToolError(
                    "the DB changed since this cursor was issued; call the tool again"
                )
            call = Call(
                conn,
                self.env,
                self.page,
                deadline,
                request,
                spoil_default=spoil_default,
                spoil_max=spoil_top,
            )
            result = tool.handler(call, values, offset)
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
    def _connect(self, tool: Tool) -> Iterator[tuple[sqlite3.Connection, float]]:
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
            yield conn, guard(conn, tool.deadline_s)
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
