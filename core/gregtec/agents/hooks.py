"""The deep lane's ``PreToolUse`` hooks (WO-2.4; plan 4.4 Hooks, 4.6, 6.5; [D§1.5]).

Two hooks run on every tool call the Claude Code CLI is about to make, the orchestrator's and
every subagent's (the CLI tells them apart by ``agent_type``):

- :meth:`Gate.tool_gate` lets through only the caller's own tools (:class:`Policy`): the
  ``mcp__gos__*`` tools of its set, ``Skill`` for a deployed GOS skill, and for the orchestrator
  ``Agent`` with one of the five GOS subagents. Anything else is denied with the reason, so a
  built-in tool that a CLI update brings back (``Bash``, ``Write``, ``WebFetch`` ...) still never
  runs. An ``Agent`` call is rewritten to the subagent, its description and prompt and
  ``run_in_background: false`` (the orchestrator waits, plan 4.4), dropping any model, effort or
  isolation it asked for: a subagent's model is the one GOS gave it (D155, D16).
- :meth:`Gate.path_guard` is plan 6.5's path guard: a note tool's path, or the path of any SDK
  file tool that ever reaches it, goes through :meth:`gregtec.vault.io.VaultIO.refusal` (D138:
  ``me/`` is Clayton's, ``_gos/`` the repo's, generated notes and other logs read only, ...); a
  file outside the vault, and any shell command, is refused outright.

A hook answers ``{}`` when it has nothing against a call; the permission mode (``dontAsk`` with
the lane's tools pre-approved) decides the rest. Hook input is data from the CLI; a refusal's
reason is what the model reads.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from gregtec.agents.tools.mcp import qualified
from gregtec.vault.io import APPEND, WRITE, VaultError, VaultIO, clean

log = logging.getLogger(__name__)

AGENT_TOOLS = frozenset(
    {"Agent", "Task"}
)  # the subagent tool: "Agent" on the wire, "Task" in init
SKILL = "Skill"
# The note tools that write, and the guard operation each one is (inbox_propose names no path:
# VaultIO.propose makes a new inbox/ note itself).
NOTE_OPS = {qualified("note_write"): WRITE, qualified("note_append"): APPEND}
# Claude Code's own file tools, and the input key that holds the path they write.
FILE_TOOLS = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
SHELL_TOOLS = frozenset({"Bash", "PowerShell", "BashOutput", "KillShell", "Monitor"})
AGENT_KEYS = ("description", "prompt", "subagent_type")

Event = Callable[[str, str | None, dict[str, Any]], None]


def deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


@dataclass(frozen=True)
class Policy:
    """Who may call what: ``main`` is the orchestrator's set, ``agents`` each subagent's (by
    name), as qualified tool names; ``skills`` the deployed GOS skills (``gos:<name>``)."""

    main: frozenset[str]
    agents: Mapping[str, frozenset[str]]
    skills: frozenset[str] = frozenset()

    def allowed(self, agent: str | None) -> frozenset[str] | None:
        """The caller's tools; None for an agent GOS didn't define."""
        if agent is None:
            return self.main | {SKILL} | AGENT_TOOLS
        found = self.agents.get(agent)
        return None if found is None else found | {SKILL}


def vault_relative(path: str, vault: Path) -> str | None:
    """``path`` (an SDK file tool's, absolute or relative, ``/`` or ``\\``) as a vault-relative
    posix path, or None when it is outside the vault. A relative path is outside: the CLI's
    working directory is never the vault."""
    pure = (
        PureWindowsPath(path) if ("\\" in path or ":" in path) else PurePosixPath(path)
    )
    if not pure.is_absolute():
        return None
    try:
        root = vault.resolve()
        target = Path(os.path.normpath(str(pure))).resolve()
        rel = target.relative_to(root)
    except (OSError, ValueError):
        return None
    return rel.as_posix() if rel.parts else None


@dataclass
class Gate:
    """The two hooks over one policy and the live vault (None: no vault, every write refused)."""

    policy: Policy
    vault: VaultIO | None
    on_event: Event | None = None
    denied: list[tuple[str, str | None, str]] = field(default_factory=list)

    def _denied(self, tool: str, agent: str | None, why: str) -> dict[str, Any]:
        self.denied.append((tool, agent, why))
        log.info(
            "deep-lane tool call refused",
            extra={"tool": tool, "agent": agent or "orchestrator", "why": why[:200]},
        )
        return deny(why)

    async def tool_gate(
        self, data: Mapping[str, Any], tool_use_id: str | None, context: object
    ) -> dict[str, Any]:
        tool = str(data.get("tool_name", ""))
        agent = data.get("agent_type") or None
        args = data.get("tool_input") or {}
        allowed = self.policy.allowed(agent)
        if allowed is None:
            return self._denied(tool, agent, f"{agent} isn't one of GOS's subagents")
        if tool not in allowed:
            if tool in AGENT_TOOLS:
                return self._denied(tool, agent, "subagents can't start subagents")
            return self._denied(
                tool,
                agent,
                f"{tool} isn't one of this agent's tools; the deep lane has only GOS's "
                "tools (mcp__gos__*), its skills and its subagents",
            )
        if self.on_event is not None:
            self.on_event(tool, agent, args if isinstance(args, dict) else {})
        if tool == SKILL:
            name = args.get("skill") if isinstance(args, dict) else None
            known = self.policy.skills
            if not isinstance(name, str) or not any(
                name in (s, s.split(":", 1)[-1]) for s in known
            ):
                return self._denied(tool, agent, f"{name!r} isn't a deployed GOS skill")
            return {}
        if tool in AGENT_TOOLS:
            return self._agent(tool, agent, args)
        return {}

    def _agent(self, tool: str, agent: str | None, args: object) -> dict[str, Any]:
        if not isinstance(args, dict):
            return self._denied(tool, agent, "the Agent call has no input")
        kind = args.get("subagent_type")
        if kind not in self.policy.agents:
            names = ", ".join(sorted(self.policy.agents))
            return self._denied(tool, agent, f"subagent_type must be one of: {names}")
        if args.get("isolation"):
            return self._denied(tool, agent, "subagents run here, with no isolation")
        new = {k: args[k] for k in AGENT_KEYS if isinstance(args.get(k), str)}
        new["run_in_background"] = False
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": new,
            }
        }

    async def path_guard(
        self, data: Mapping[str, Any], tool_use_id: str | None, context: object
    ) -> dict[str, Any]:
        tool = str(data.get("tool_name", ""))
        agent = data.get("agent_type") or None
        args = data.get("tool_input") or {}
        if not isinstance(args, dict):
            args = {}
        why = self.refusal(tool, args)
        return {} if why is None else self._denied(tool, agent, why)

    def refusal(self, tool: str, args: Mapping[str, Any]) -> str | None:
        """Why plan 6.5's path guard refuses this call, or None."""
        if tool in SHELL_TOOLS:
            return "no shell commands in the deep lane: they would get around the path guard"
        if tool in NOTE_OPS:
            return self._vault_refusal(args.get("path"), NOTE_OPS[tool])
        if tool in FILE_TOOLS:
            path = args.get(FILE_TOOLS[tool])
            if not isinstance(path, str) or not path:
                return f"{tool} names no file"
            if self.vault is None:
                return "there is no vault here; nothing can be written"
            rel = vault_relative(path, self.vault.root)
            if rel is None:
                return f"{path} is outside the vault: the deep lane writes only vault notes"
            return self._vault_refusal(rel, WRITE)
        return None

    def _vault_refusal(self, rel: object, op: str) -> str | None:
        if self.vault is None:
            return "there is no vault here; nothing can be written"
        try:
            clean(rel)
        except VaultError as exc:
            return str(exc)
        return self.vault.refusal(rel, op)
