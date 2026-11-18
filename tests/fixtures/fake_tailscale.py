"""A fake ``tailscale`` CLI for the WO-9.3 tests: the subcommands ``gos tailnet`` uses, over a
JSON state file (``FAKE_TAILSCALE_STATE``). Every call is appended to the state's ``calls``.

State keys: ``status`` (what ``status --json`` prints), ``serve`` (the ServeConfig ``serve status
--json`` prints; ``{}`` prints nothing, as the real CLI does with no config), ``funnel_on_serve``
(the next ``serve`` also turns Funnel on: a CLI that misbehaves), ``fail`` (a subcommand that exits 1).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    path = Path(os.environ["FAKE_TAILSCALE_STATE"])
    state = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("calls", []).append(argv)
    code = run(state, argv)
    path.write_text(json.dumps(state), encoding="utf-8")
    return code


def run(state: dict, argv: list[str]) -> int:
    if argv and argv[0] == state.get("fail"):
        print(f"{argv[0]}: failed (fake)", file=sys.stderr)
        return 1
    serve = state.setdefault("serve", {})
    name = state["status"]["Self"]["DNSName"].rstrip(".")
    if argv == ["status", "--json"]:
        print(json.dumps(state["status"]))
        return 0
    if argv == ["serve", "status", "--json"]:
        if serve:
            print(json.dumps(serve))
        return 0
    if argv[:1] == ["funnel"]:
        # The real CLI would put the service on the public internet; GOS must never get here.
        hp = f"{name}:443"
        serve.setdefault("AllowFunnel", {})[hp] = True
        return 0
    if argv[:1] == ["serve"] and argv[-1] == "off":
        port = next(a.split("=")[1] for a in argv if a.startswith("--https="))
        serve.get("Web", {}).pop(f"{name}:{port}", None)
        serve.get("TCP", {}).pop(port, None)
        serve.get("AllowFunnel", {}).pop(f"{name}:{port}", None)
        return 0
    if argv[:1] == ["serve"] and "--bg" in argv:
        port = next(a.split("=")[1] for a in argv if a.startswith("--https="))
        hp = f"{name}:{port}"
        serve.setdefault("TCP", {})[port] = {"HTTPS": True}
        serve.setdefault("Web", {})[hp] = {"Handlers": {"/": {"Proxy": argv[-1]}}}
        if state.get("funnel_on_serve"):
            serve.setdefault("AllowFunnel", {})[hp] = True
        return 0
    print(f"unknown command: {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
