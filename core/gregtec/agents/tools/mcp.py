"""The tool registry as the deep lane's in-process MCP server (WO-2.4; plan 4.4, 7.0; [D§1.4]).

:func:`server` wraps the deep lane's tools (``Registry.tools("deep")``) in one SDK MCP server
named :data:`SERVER`, so Claude sees each as ``mcp__gos__<name>``. Every call goes through
:meth:`Registry.call` with ``lane="deep"``, exactly as the fast lane and ``gos tool`` call it:
the same argument validation, read-only DB connection, time limits, TSV results and the vault
path guard behind the note tools (D130, D138). ``read_only`` becomes MCP's ``readOnlyHint``
(read-only tools may run in parallel); a tool's error comes back as an ``is_error`` result, the
text the model reads. The registry is synchronous (SQLite, file I/O), so a call runs in a worker
thread and the SDK's event loop keeps serving the CLI.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import SdkMcpTool, ToolAnnotations, create_sdk_mcp_server
from claude_agent_sdk.types import McpSdkServerConfig

from gregtec.agents.tools.registry import DEEP, Registry, Tool

SERVER = "gos"
PREFIX = f"mcp__{SERVER}__"


def qualified(name: str) -> str:
    """The name Claude Code gives a registry tool: ``mcp__gos__<name>``."""
    return PREFIX + name


def _tool(registry: Registry, tool: Tool) -> SdkMcpTool[Any]:
    name = tool.name

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        out = await asyncio.to_thread(registry.call, name, args, lane=DEEP)
        result: dict[str, Any] = {"content": [{"type": "text", "text": out.text}]}
        if out.is_error:
            result["is_error"] = True
        return result

    return SdkMcpTool(
        name=name,
        description=tool.description,
        input_schema=tool.input_schema,
        handler=handler,
        annotations=ToolAnnotations(
            readOnlyHint=tool.read_only, destructiveHint=False, openWorldHint=False
        ),
    )


def tools(registry: Registry) -> list[Tool]:
    """The registry's deep-lane tools, in its fixed order."""
    return registry.tools(DEEP)


def server(registry: Registry) -> McpSdkServerConfig:
    """The in-process ``gos`` MCP server over every deep-lane tool of ``registry``."""
    return create_sdk_mcp_server(
        SERVER, tools=[_tool(registry, t) for t in tools(registry)]
    )
