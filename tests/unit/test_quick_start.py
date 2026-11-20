"""docs/quick_start.md, the short version of docs/deployment_checklist.md, can't drift silently.

Every ``[n.n]`` reference in it names a row or section the checklist has, and every ``gos``
command it writes is one the CLI has (its subcommand too, for a command that has them).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import NoReturn

import pytest

from gregtec import cli

DOCS = Path(__file__).resolve().parents[2] / "docs"
QUICK = DOCS / "quick_start.md"
CHECKLIST = DOCS / "deployment_checklist.md"
REF = re.compile(r"\[(\d+(?:\.\d+)*(?:\s*[-,]\s*\d+(?:\.\d+)*)*)\]")
# `& $gos x y`, `uv run gos x y`, a bare `gos x` (not gos-dev, gos.toml, ...): its words
GOS = re.compile(
    r"(?:\\?\$gos|(?<![\w$.\\-])gos)[ \t]+([a-z][a-z0-9-]*)(?:[ \t]+([a-z][a-z0-9-]*))?"
)


def checklist_ids() -> set[str]:
    """The checklist's sections (``## 3.``, ``### 3.5``), table rows (``| 2.1 |``,
    ``| 5.1.4 |``) and section 3's numbered steps (step 5 of ``### 3.1`` is 3.1.5)."""
    ids: set[str] = set()
    section = None
    for line in CHECKLIST.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"#{2,3} (\d+(?:\.\d+)*)\.? ", line):
            section = m.group(1)
            ids.add(section)
        elif m := re.match(r"\| (\d+(?:\.\d+)+) \|", line):
            ids.add(m.group(1))
        elif (m := re.match(r"(\d+)\. ", line)) and section and "." in section:
            ids.add(f"{section}.{m.group(1)}")
    return ids


def references(text: str) -> list[str]:
    out = []
    for m in REF.finditer(text):
        out += [r.strip() for r in re.split(r"[-,]", m.group(1))]
    return out


def test_every_row_reference_is_in_the_checklist() -> None:
    refs = references(QUICK.read_text(encoding="utf-8"))
    assert len(refs) > 90  # one or more per step
    ids = checklist_ids()
    assert {"1", "2.1", "3.1.1", "3.5", "3.15.4", "4", "5", "6"} <= ids
    assert [r for r in refs if r not in ids] == []


class _Parsed(Exception):
    def __init__(self, parser: argparse.ArgumentParser) -> None:
        self.parser = parser


def _commands() -> dict[str, set[str] | None]:
    """gos's commands -> their subcommands (None for a command without any), read from the
    parser :func:`gregtec.cli.main` builds."""

    def stop(
        self: argparse.ArgumentParser, *args: object, **kwargs: object
    ) -> NoReturn:
        raise _Parsed(self)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(argparse.ArgumentParser, "parse_args", stop)
        with pytest.raises(_Parsed) as got:
            cli.main(["doctor"])

    def subs(p: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction):
                return dict(a.choices)
        return {}

    return {name: (set(subs(p)) or None) for name, p in subs(got.value.parser).items()}


def test_every_gos_command_exists() -> None:
    text = QUICK.read_text(encoding="utf-8")
    commands = _commands()
    assert {"deploy", "serve", "structures", "eval"} <= set(commands)
    found = GOS.findall(text)
    assert len(found) > 60
    bad = []
    for cmd, sub in found:
        if cmd not in commands:
            bad.append(cmd)
        elif commands[cmd] is not None and sub and sub not in commands[cmd]:  # type: ignore[operator]
            bad.append(f"{cmd} {sub}")
    assert bad == []


def test_the_quick_start_is_linked_from_the_checklist() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    how = text[text.index("**How to use it.**") :].split("\n", 1)[0]
    assert "](quick_start.md)" in how
