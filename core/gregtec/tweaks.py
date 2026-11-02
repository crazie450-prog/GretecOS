"""Check, and with ``--apply`` set, the config tweaks of plan section 5.6 (WO-0.6, D32).

| tweak       | file                                | wanted                                        |
|-------------|-------------------------------------|-----------------------------------------------|
| oc_loopback | config/OpenComputers.cfg (HOCON)    | first internet.filteringRules entry is        |
|             |                                     | "allow ip:127.0.0.1"                          |
| oc_timeout  | config/OpenComputers.cfg            | internet.requestTimeout > 0 (set to 10)       |
| pause_focus | options.txt                         | pauseOnLostFocus:false (V04)                  |
| su_logging  | serverutilities/serverutilities.cfg | world.logging enabled, block_placed and       |
|             |                                     | block_broken all true (V06)                   |
| borderless  | config/lwjgl3ify.cfg                | window.borderless=true (Java 17+ build only)  |

Reads go through ``safe_open``. ``--apply`` changes only the lines it must, keeps comments, layout
and line endings, saves the original once as ``<file>.gos-bak`` and replaces the file atomically.
Run it with the game closed. Play is changed only by Clayton running this himself (CLAUDE.md).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from gregtec.doctor import JAVA17, detect_java_build, read_components
from gregtec.gamestate.safe_open import read_text

OC = "config/OpenComputers.cfg"
OPTIONS = "options.txt"
SU = "serverutilities/serverutilities.cfg"
LWJGL3IFY = "config/lwjgl3ify.cfg"
LOOPBACK = "allow ip:127.0.0.1"
TIMEOUT_S = 10
SU_KEYS = ("enabled", "block_placed", "block_broken")


@dataclass
class Result:
    tweak: str
    file: str
    status: str  # ok | missing | no file | n/a
    detail: str


class Doc:
    """A text file as lines, remembering its newline style."""

    def __init__(self, text: str) -> None:
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.lines = text.splitlines()

    def text(self) -> str:
        return self.newline.join(self.lines) + self.newline


def _block(lines: list[str], path: Sequence[str]) -> tuple[int, int] | None:
    """Line range (first, last) inside the nested ``name {`` block ``path``, or None."""
    stack: list[str] = []
    start = None
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith(("#", "//")):
            continue
        if m := re.fullmatch(r'"?([^"{}]+?)"?\s*\{', s):
            stack.append(m[1].strip())
            if stack == list(path):
                start = i + 1
        elif s == "}":
            if stack == list(path) and start is not None:
                return start, i - 1
            if stack:
                stack.pop()
    return None


def _find(
    lines: list[str], span: tuple[int, int], pattern: str
) -> tuple[int, re.Match[str]] | None:
    for i in range(span[0], span[1] + 1):
        if m := re.match(pattern, lines[i]):
            return i, m
    return None


def _rules(
    lines: list[str], span: tuple[int, int]
) -> tuple[int, list[tuple[int, str]]] | None:
    """The filteringRules list: (index of the 'filteringRules=[' line, [(line, entry)])."""
    found = _find(lines, span, r"\s*filteringRules\s*=\s*\[\s*$")
    if found is None:
        return None
    head, _ = found
    entries = []
    for i in range(head + 1, span[1] + 1):
        s = lines[i].strip()
        if s.startswith("]"):
            return head, entries
        if not s or s.startswith(("#", "//")):
            continue
        entries.append((i, s.rstrip(",").strip().strip('"')))
    return None


# ---------------------------------------------------------------- checks (read-only)


def check_oc(doc: Doc | None) -> list[Result]:
    if doc is None:
        return [
            Result("oc_loopback", OC, "no file", ""),
            Result("oc_timeout", OC, "no file", ""),
        ]
    span = _block(doc.lines, ["opencomputers", "internet"])
    rules = _rules(doc.lines, span) if span else None
    if span is None or rules is None:
        loop = Result("oc_loopback", OC, "missing", "no internet.filteringRules list")
    else:
        first = rules[1][0][1] if rules[1] else "(empty)"
        status = "ok" if first == LOOPBACK else "missing"
        loop = Result("oc_loopback", OC, status, f'first rule "{first}"')
    timeout = _find(doc.lines, span, r"\s*requestTimeout\s*=\s*(\d+)") if span else None
    if timeout is None:
        time = Result("oc_timeout", OC, "missing", "no internet.requestTimeout")
    else:
        value = int(timeout[1][1])
        time = Result(
            "oc_timeout",
            OC,
            "ok" if value > 0 else "missing",
            f"requestTimeout={value}",
        )
    return [loop, time]


def check_options(doc: Doc | None) -> list[Result]:
    if doc is None:
        return [Result("pause_focus", OPTIONS, "no file", "written on first launch")]
    values = dict(line.split(":", 1) for line in doc.lines if ":" in line)
    value = values.get("pauseOnLostFocus")
    status = "ok" if value == "false" else "missing"
    return [Result("pause_focus", OPTIONS, status, f"pauseOnLostFocus={value}")]


def _forge_bools(
    doc: Doc, path: Sequence[str], keys: Sequence[str]
) -> dict[str, str | None]:
    span = _block(doc.lines, path)
    out: dict[str, str | None] = dict.fromkeys(keys)
    if span:
        for key in keys:
            if found := _find(doc.lines, span, rf"\s*B:{key}=(\S+)\s*$"):
                out[key] = found[1][1]
    return out


def check_su(doc: Doc | None) -> list[Result]:
    if doc is None:
        return [Result("su_logging", SU, "no file", "")]
    values = _forge_bools(doc, ["world", "logging"], SU_KEYS)
    ok = all(v == "true" for v in values.values())
    detail = ", ".join(f"{k}={v}" for k, v in values.items())
    return [Result("su_logging", SU, "ok" if ok else "missing", detail)]


def check_borderless(doc: Doc | None, java17: bool) -> list[Result]:
    if not java17:
        return [
            Result(
                "borderless", LWJGL3IFY, "n/a", "Java 8 build: play windowed instead"
            )
        ]
    if doc is None:
        return [Result("borderless", LWJGL3IFY, "no file", "")]
    value = _forge_bools(doc, ["window"], ["borderless"])["borderless"]
    return [
        Result(
            "borderless",
            LWJGL3IFY,
            "ok" if value == "true" else "missing",
            f"borderless={value}",
        )
    ]


# ---------------------------------------------------------------- fixes (used by --apply)


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def fix_oc(doc: Doc) -> None:
    span = _block(doc.lines, ["opencomputers", "internet"])
    if span is None:
        raise ValueError(f"{OC}: no opencomputers.internet block")
    rules = _rules(doc.lines, span)
    if rules is None:
        raise ValueError(f"{OC}: no internet.filteringRules list")
    head, entries = rules
    if not entries or entries[0][1] != LOOPBACK:
        for i, entry in reversed(entries):
            if entry == LOOPBACK:
                del doc.lines[i]
        indent = _indent_of(doc.lines[head]) + "  "
        if entries:
            indent = _indent_of(doc.lines[entries[0][0]])
        doc.lines.insert(head + 1, f'{indent}"{LOOPBACK}"')
    span = _block(doc.lines, ["opencomputers", "internet"])
    assert span is not None
    found = _find(doc.lines, span, r"(\s*requestTimeout\s*=\s*)(\d+)")
    if found is None:
        doc.lines.insert(
            span[1] + 1, f"{_indent_of(doc.lines[head])}requestTimeout={TIMEOUT_S}"
        )
    elif int(found[1][2]) <= 0:
        doc.lines[found[0]] = f"{found[1][1]}{TIMEOUT_S}"


def fix_options(doc: Doc) -> None:
    for i, line in enumerate(doc.lines):
        if line.startswith("pauseOnLostFocus:"):
            doc.lines[i] = "pauseOnLostFocus:false"
            return
    doc.lines.append("pauseOnLostFocus:false")


def _set_forge_bool(doc: Doc, path: Sequence[str], key: str) -> None:
    span = _block(doc.lines, path)
    if span is None:
        raise ValueError(f"no {'.'.join(path)} section")
    found = _find(doc.lines, span, rf"(\s*B:{key}=)(\S+)\s*$")
    if found is None:
        indent = _indent_of(doc.lines[span[0] - 1]) + "    "
        doc.lines.insert(span[1] + 1, f"{indent}B:{key}=true")
    else:
        doc.lines[found[0]] = f"{found[1][1]}true"


def fix_su(doc: Doc) -> None:
    for key in SU_KEYS:
        _set_forge_bool(doc, ["world", "logging"], key)


def fix_borderless(doc: Doc) -> None:
    _set_forge_bool(doc, ["window"], "borderless")


# ---------------------------------------------------------------- instance level


def game_dir(path: Path) -> Path:
    """Accept an instance folder (with instance.cfg) or its minecraft/.minecraft folder."""
    for name in ("minecraft", ".minecraft"):
        if (path / name).is_dir():
            return path / name
    return path


def _load(game: Path, rel: str) -> Doc | None:
    f = game / rel
    return Doc(read_text(f)) if f.is_file() else None


def check(instance: Path) -> list[Result]:
    game = game_dir(instance)
    folder = game.parent if game != instance else instance
    java17 = detect_java_build(read_components(folder)) == JAVA17
    return [
        *check_oc(_load(game, OC)),
        *check_options(_load(game, OPTIONS)),
        *check_su(_load(game, SU)),
        *check_borderless(_load(game, LWJGL3IFY), java17),
    ]


FIXES: dict[str, tuple[str, Callable[[Doc], None]]] = {
    "oc_loopback": (OC, fix_oc),
    "oc_timeout": (OC, fix_oc),
    "pause_focus": (OPTIONS, fix_options),
    "su_logging": (SU, fix_su),
    "borderless": (LWJGL3IFY, fix_borderless),
}


def plan_fixes(results: list[Result]) -> list[str]:
    """Files --apply would change, in order. options.txt may be created; others must exist."""
    files: list[str] = []
    for r in results:
        if r.status == "missing" or (r.status == "no file" and r.file == OPTIONS):
            rel = FIXES[r.tweak][0]
            if rel not in files:
                files.append(rel)
    return files


def apply(instance: Path, files: list[str]) -> None:
    game = game_dir(instance)
    by_file = {rel: fix for rel, fix in FIXES.values()}
    for rel in files:
        target = game / rel
        doc = Doc(read_text(target)) if target.is_file() else Doc("")
        by_file[rel](doc)
        backup = target.with_name(target.name + ".gos-bak")
        if target.is_file() and not backup.exists():
            shutil.copyfile(target, backup)
        tmp = target.with_name(target.name + ".gos-tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(doc.text().encode("utf-8"))
        os.replace(tmp, target)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_tweaks.py",
        description="Check the plan 5.6 config tweaks on one instance.",
    )
    parser.add_argument(
        "instance", type=Path, help="the instance folder (or its minecraft folder)"
    )
    parser.add_argument(
        "--apply", action="store_true", help="set the missing tweaks (game closed)"
    )
    parser.add_argument("--yes", action="store_true", help="don't ask before --apply")
    args = parser.parse_args(argv)

    results = check(args.instance)
    for r in results:
        print(f"{r.status:<8} {r.tweak:<12} {r.file:<36} {r.detail}")
    todo = plan_fixes(results)
    if args.apply and todo:
        print("\nWill change (originals saved once as <file>.gos-bak):")
        for rel in todo:
            print(f"  {game_dir(args.instance) / rel}")
        if (
            not args.yes
            and input("Close the game first. Apply? [y/N] ").strip().lower() != "y"
        ):
            print("Nothing changed.")
            return 1
        apply(args.instance, todo)
        results = check(args.instance)
        print()
        for r in results:
            print(f"{r.status:<8} {r.tweak:<12} {r.file:<36} {r.detail}")
    ok = all(r.status in ("ok", "n/a") for r in results)
    print("\nAll tweaks present." if ok else "\nSome tweaks are missing.")
    return 0 if ok else 1
