"""``gos tool``: run a read-only tool from the shell, as an agent would (WO-2.2, for testing).

``gos tool`` lists the tools (and says when the live vault's glossary can't be used);
``gos tool NAME '{"arg": ...}'`` runs one against
``GOS_HOME/data/gos.sqlite`` and prints its TSV; ``--all`` follows its ``more`` cursors (a
cursor only works in the process that issued it); ``--schema`` prints the tool as the Messages
API takes it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.agents.tools.catalog import default_registry
from gregtec.agents.tools.registry import MORE

MAX_PAGES = 200


def _cursor(text: str) -> str | None:
    """The cursor on the line after the result's last ``rows:`` line (a cell or a head line
    can start with "more: " too)."""
    lines = text.split("\n")
    rows = [i for i, line in enumerate(lines) if line.startswith("rows: ")]
    if not rows or rows[-1] + 1 >= len(lines):
        return None
    line = lines[rows[-1] + 1]
    return line.removeprefix("more: ") if line.startswith("more: ") else None


def main(
    config_path: Path | None,
    name: str | None,
    args_json: str,
    lane: str | None,
    follow: bool,
    schema: bool,
    out: TextIO,
) -> int:
    """0: the tool answered; 1: it returned an error; 2: bad arguments or gos.toml."""
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    reg = default_registry(cfg.gos_home)
    if name is None:
        print("tool\tlanes\tread_only\tdescription", file=out)
        for t in reg.tools(lane):
            first = t.description.split(". ")[0]
            print(
                f"{t.name}\t{','.join(sorted(t.lanes))}\t{int(t.read_only)}\t{first}",
                file=out,
            )
        if reg.env.glossary.problem is not None:
            print(f"note: glossary: {reg.env.glossary.problem}", file=out)
        return 0
    tool = reg.get(name)
    if tool is None:
        print(f"No tool named {name!r}; `gos tool` lists them.", file=out)
        return 2
    if schema:
        print(json.dumps(tool.spec(), indent=2), file=out)
        return 0
    if not tool.read_only:
        print(f"{name} changes things; gos tool runs read-only tools only.", file=out)
        return 2
    try:
        args = json.loads(args_json)
    except ValueError as exc:
        print(f"The arguments are not JSON: {exc}", file=out)
        return 2
    result = reg.call(name, args, lane=lane)
    print(result.text, end="", file=out)
    pages = 1
    while follow and not result.is_error and (cursor := _cursor(result.text)):
        if pages >= MAX_PAGES:
            print(f"(stopped after {MAX_PAGES} pages)", file=out)
            break
        result = reg.call(MORE, {"cursor": cursor}, lane=lane)
        print(result.text, end="", file=out)
        pages += 1
    return 1 if result.is_error else 0
