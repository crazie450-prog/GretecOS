"""The rehearsal's core (WO-11.3): the real core (:func:`gregtec.api.server.run`) with the fake
model, a scripted deep lane and the fake desktop. The supervisor starts it as it starts
``python -m gregtec.api --supervised`` (same flags, same /health, same stdin stop).

What is fake, and nothing else:

- **the model**: the fast lane runs :class:`gregtec.llm.fake.EchoClient` (``ask.setup(fake=True)``,
  as the dashboard's e2e core); the deep lane is :class:`ScriptedDeep`, which records every
  handoff and, as a model would, calls the real ``action_propose`` tool for a request that asks
  for a craft or a chat line (the only way an action is ever proposed, plan 7.9);
- **the desktop**: the program in front and the input idle come from the control folder's files
  (``foreground``, ``idle_s``), as on the PC they come from Windows;
- **time**: the Anthropic-key check runs every second instead of 30 s, the dashboard's polls every
  second (L9 and the phone view see a change at once). Everything else runs at its real pace.

The keyring is the file keyring of :mod:`rfakes` (``PYTHON_KEYRING_BACKEND``), so removing the
Anthropic key there is the L9 revoke. Test-only: production code never reads these files.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import rfakes

from gregtec import config, desktop, presence, prism
from gregtec.agents import ask
from gregtec.agents.deep_lane import Handoff, HandoffRecorder
from gregtec.agents.tools.registry import DEEP, Registry
from gregtec.api import app as core_app
from gregtec.api import dashboard, server
from gregtec.bridge import ops as bridge_ops
from gregtec.store import Store

# The craft a deep-lane request asks for, as the AE2 network in the OC fixture lists it craftable:
# a steel ingot, by registry name and meta (never a numeric id).
CRAFT = {"name": "gregtech:gt.metaitem.01", "damage": 11305, "amount": 64}


class ScriptedDeep(HandoffRecorder):
    """The deep lane's stand-in: records each handoff like :class:`HandoffRecorder` and, for a
    request naming ``craft`` or ``say``, proposes the action through the registry's real
    ``action_propose`` (the deep lane's tool). Its outcome goes to the control folder's
    ``deep.jsonl``, so the driver can see what the "model" did."""

    def __init__(self, control: Path) -> None:
        super().__init__()
        self.control = control
        self.registry: Registry | None = None

    def handoff(self, task: str, request: str, source: str) -> Handoff:
        item = super().handoff(task, request, source)
        words = request.casefold()
        call = None
        if "craft" in words:
            call = {"kind": "craft", "args": dict(CRAFT), "instance": "lab"}
        elif "say" in words:
            call = {"kind": "say", "args": {"text": "GOS rehearsal"}, "instance": "lab"}
        if call is not None and self.registry is not None:
            out = self.registry.call("action_propose", call, lane=DEEP)
            with open(self.control / "deep.jsonl", "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {"handoff": item.id, "error": out.is_error, "text": out.text}
                    )
                    + "\n"
                )
        return item


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_core.py")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--supervised", action="store_true")
    args = parser.parse_args(argv)
    control = rfakes.control_dir()
    cfg = config.load(args.config)

    def foreground() -> str | None:
        return rfakes.read_json(control / "foreground", None)

    def running(c: config.Config, lines: Sequence[str] | None = None) -> list[str]:
        found = rfakes.read_json(control / "running", [])
        return [str(r) for r in found] if isinstance(found, list) else []

    desktop.foreground_program = foreground
    prism.running_clients = running
    presence.system_idle = lambda: rfakes.FileIdle(control)
    core_app.KEY_CHECK_S = 1.0
    dashboard.GAME_EVERY_S = 1.0
    dashboard.DATA_EVERY_S = 1.0

    def agents(
        c: config.Config, store: Store, ops: bridge_ops.Ops | None = None
    ) -> ask.Agents:
        a = ask.setup(c, store=store, fake=True, ops=ops)
        deep = ScriptedDeep(control)
        deep.registry = a.registry
        a.deep = deep
        return a

    server._default_agents = agents  # the ops tools need the core's bridge (WO-7.1)
    return server.run(cfg, supervised=args.supervised, config_path=args.config)


if __name__ == "__main__":
    sys.exit(main())
