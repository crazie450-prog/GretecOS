"""Check, and with ``--apply`` set, the config tweaks of plan section 5.6 (WO-0.6, D32).

| tweak       | file                                | wanted                                        |
|-------------|-------------------------------------|-----------------------------------------------|
| oc_loopback | config/OpenComputers.cfg (HOCON)    | first internet.filteringRules entry is        |
|             |                                     | "allow ip:127.0.0.1"                          |
| oc_timeout  | config/OpenComputers.cfg            | internet.requestTimeout > 0 (set to 10)       |
| oc_rules    | config/OpenComputers.cfg            | every rule valid, blacklist/whitelist empty   |
|             |                                     | (one bad rule disables the internet card; a   |
|             |                                     | non-empty list stops the game starting)       |
| pause_focus | options.txt                         | pauseOnLostFocus anything but "true" (V04)    |
| su_logging  | serverutilities/serverutilities.cfg | world.logging enabled, block_placed and       |
|             |                                     | block_broken all true (V06)                   |
| borderless  | config/lwjgl3ify.cfg                | window.borderless=true (Java 17+ build only;  |
|             |                                     | it takes effect in fullscreen)                |

Formats are those of GTNH 2.9.0. OpenComputers and ServerUtilities rewrite their files on every
launch (sorted keys, CRLF on Windows), so nothing here depends on line positions. Reads go through
``safe_open``. ``--apply`` changes only the lines it must, keeps comments, layout and line endings,
checks the edited text before writing it, saves the original once as ``<file>.gos-bak`` and
replaces the file atomically. Run it with the game closed: a running game rewrites options.txt.
Play is changed only by Clayton running this himself (CLAUDE.md).
"""

from __future__ import annotations

import argparse
import ipaddress
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
OC_LOG = "logs/fml-client-latest.log"
LOOPBACK = "allow ip:127.0.0.1"
TIMEOUT_S = 10
SU_KEYS = ("enabled", "block_placed", "block_broken")
OC_FILTERS = {
    "default",
    "private",
    "bogon",
    "ipv4",
    "ipv6",
    "ipv4-embedded-ipv6",
    "all",
}


@dataclass
class Result:
    tweak: str
    file: str
    status: str  # ok | missing | invalid | no file | n/a
    detail: str


class Doc:
    """A text file as lines, remembering its newline style."""

    def __init__(self, text: str) -> None:
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.lines = text.splitlines()

    def text(self) -> str:
        return self.newline.join(self.lines) + self.newline


def _block(lines: list[str], path: Sequence[str]) -> tuple[int, int] | None:
    """Line range (first, last) inside the nested ``name {`` block ``path``, or None.

    Works for Forge cfg and for HOCON as GTNH ships and rewrites it; skips Forge ``<`` lists.
    """
    stack: list[str] = []
    start = None
    in_list = False
    for i, line in enumerate(lines):
        s = line.strip()
        if in_list:
            in_list = not s.startswith(">")
            continue
        if not s or s.startswith(("#", "//")):
            continue
        if s.endswith("<"):
            in_list = True
        elif m := re.fullmatch(r'"?([^"{}]+?)"?\s*\{', s):
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


def _items(text: str) -> list[str]:
    """HOCON list items from text between [ and ]: quoted or bare, comma or newline separated."""
    items = []
    for m in re.finditer(r'(?:#|//).*|"((?:[^"\\]|\\.)*)"|([^\s,"#\]]+)', text):
        if m[1] is not None:
            items.append(m[1])
        elif m[2] is not None:
            items.append(m[2])
    return items


@dataclass
class OcList:
    head: int  # line with "<key>=["
    end: int  # line with the closing "]" (== head for a one-line list)
    items: list[str]


def _oc_list(lines: list[str], span: tuple[int, int], key: str) -> OcList | None:
    found = _find(lines, span, rf"\s*{key}\s*[=:]\s*\[(.*)$")
    if found is None:
        return None
    head, m = found
    if "]" in m[1]:  # one line: key=["a", "b"]
        return OcList(head, head, _items(m[1].split("]", 1)[0]))
    body = []
    for i in range(head + 1, span[1] + 1):
        if lines[i].strip().startswith("]"):
            return OcList(head, i, _items("\n".join(body)))
        body.append(re.sub(r"(#|//).*$", "", lines[i]))
    return None


def valid_oc_rule(rule: str) -> bool:
    """OpenComputers' grammar: removeme, or 'allow|deny <filter>' with exactly one space."""
    if rule == "removeme":
        return True
    parts = rule.split(" ")
    if len(parts) != 2 or parts[0] not in ("allow", "deny"):
        return False
    target = parts[1]
    if target in OC_FILTERS:
        return True
    if target.startswith("domain:"):
        return len(target) > len("domain:")
    if target.startswith("ip:"):
        try:
            ipaddress.ip_network(target[3:], strict=False)
        except ValueError:
            return False
        return True
    return False


# ---------------------------------------------------------------- checks (read-only)


def check_oc(doc: Doc | None, log: str | None) -> list[Result]:
    names = ("oc_loopback", "oc_timeout", "oc_rules")
    if doc is None:
        return [Result(n, OC, "no file", "") for n in names]
    span = _block(doc.lines, ["opencomputers", "internet"])
    rules = _oc_list(doc.lines, span, "filteringRules") if span else None
    if span is None or rules is None:
        return [
            Result(n, OC, "missing", "no internet.filteringRules list") for n in names
        ]
    first = rules.items[0] if rules.items else "(empty)"
    loop = Result(
        "oc_loopback",
        OC,
        "ok" if first == LOOPBACK else "missing",
        f'first rule "{first}"',
    )

    timeout = _find(doc.lines, span, r"\s*requestTimeout\s*[=:]\s*(\d+)")
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

    bad = [r for r in rules.items if not valid_oc_rule(r)]
    nonempty = [
        k
        for k in ("blacklist", "whitelist")
        if (lst := _oc_list(doc.lines, span, k)) and lst.items
    ]
    detail = f"{sum(r != 'removeme' for r in rules.items)} rules"
    if bad:
        detail = f"invalid rules (OC disables the internet card): {bad}"
    if nonempty:
        detail = f"non-empty {', '.join(nonempty)} (OC refuses to start)"
    if log:
        detail += f"; last launch: {log}"
    status = "invalid" if bad or nonempty else "ok"
    return [loop, time, Result("oc_rules", OC, status, detail)]


def oc_log_line(game: Path) -> str | None:
    """What OpenComputers said about its rules at the last launch, if the log is there."""
    log = game / OC_LOG
    if not log.is_file():
        return None
    found = re.findall(
        r"Successfully applied \d+ Internet Card filtering rules\.|Could not parse Internet Card filtering rules!",
        read_text(log),
    )
    return found[-1] if found else None


def check_options(doc: Doc | None) -> list[Result]:
    if doc is None:
        return [
            Result(
                "pause_focus",
                OPTIONS,
                "no file",
                "the game writes it on F3+P or an options change",
            )
        ]
    values = dict(line.split(":", 1) for line in doc.lines if ":" in line)
    value = values.get("pauseOnLostFocus")
    # The game reads exactly "true" as true; anything else is false. A missing key means the default, true.
    status = "ok" if value is not None and value != "true" else "missing"
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
    if value == "true":
        return [
            Result(
                "borderless",
                LWJGL3IFY,
                "ok",
                "borderless=true (it applies in fullscreen: F11)",
            )
        ]
    return [Result("borderless", LWJGL3IFY, "missing", f"borderless={value}")]


# ---------------------------------------------------------------- fixes (used by --apply)


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def fix_oc(doc: Doc) -> None:
    span = _block(doc.lines, ["opencomputers", "internet"])
    rules = _oc_list(doc.lines, span, "filteringRules") if span else None
    if span is None or rules is None:
        raise ValueError(f"{OC}: no opencomputers.internet.filteringRules list")
    if rules.items[:1] != [LOOPBACK]:
        if rules.head == rules.end:  # one-line list: rebuild it with the rule first
            line = doc.lines[rules.head]
            items = [LOOPBACK] + [r for r in rules.items if r != LOOPBACK]
            quoted = ", ".join(r if r == "removeme" else f'"{r}"' for r in items)
            doc.lines[rules.head] = re.sub(
                r"\[.*\]", lambda _m: f"[{quoted}]", line, count=1
            )
        else:
            for i in range(rules.end - 1, rules.head, -1):
                if doc.lines[i].strip().rstrip(",").strip('"') == LOOPBACK:
                    del doc.lines[i]
                    rules.end -= 1
            entries = [
                i for i in range(rules.head + 1, rules.end) if doc.lines[i].strip()
            ]
            indent = (
                _indent_of(doc.lines[entries[0]])
                if entries
                else _indent_of(doc.lines[rules.head]) + "  "
            )
            comma = "," if entries else ""
            doc.lines.insert(rules.head + 1, f'{indent}"{LOOPBACK}"{comma}')
    span = _block(doc.lines, ["opencomputers", "internet"])
    assert span is not None
    found = _find(doc.lines, span, r"(\s*requestTimeout\s*[=:]\s*)(\d+)")
    if found is None:
        doc.lines.insert(
            span[1] + 1,
            f"{_indent_of(doc.lines[rules.head])}requestTimeout={TIMEOUT_S}",
        )
    elif int(found[1][2]) <= 0:
        doc.lines[found[0]] = f"{found[1][1]}{TIMEOUT_S}"
    # Never write a file OpenComputers would reject: re-check the edited text first.
    after = {r.tweak: r for r in check_oc(doc, None)}
    if after["oc_rules"].status != "ok" or after["oc_loopback"].status != "ok":
        raise ValueError(f"{OC}: not changed, {after['oc_rules'].detail}")


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
        *check_oc(_load(game, OC), oc_log_line(game)),
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
        if r.tweak not in FIXES:
            continue
        if r.status == "missing" or (r.status == "no file" and r.file == OPTIONS):
            rel = FIXES[r.tweak][0]
            if rel not in files:
                files.append(rel)
    return files


def apply(instance: Path, files: list[str]) -> None:
    """Edit every file in memory first, so one that can't be fixed changes nothing at all."""
    game = game_dir(instance)
    by_file = {rel: fix for rel, fix in FIXES.values()}
    edited = []
    for rel in files:
        target = game / rel
        doc = Doc(read_text(target)) if target.is_file() else Doc("")
        by_file[rel](doc)
        edited.append((target, doc))
    for target, doc in edited:
        backup = target.with_name(target.name + ".gos-bak")
        if target.is_file() and not backup.exists():
            shutil.copyfile(target, backup)
        tmp = target.with_name(target.name + ".gos-tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(doc.text().encode("utf-8"))
        os.replace(tmp, target)


def _print(results: list[Result]) -> None:
    for r in results:
        print(f"{r.status:<8} {r.tweak:<12} {r.file:<36} {r.detail}")


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
    _print(results)
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
        try:
            apply(args.instance, todo)
        except ValueError as exc:
            print(f"\nNothing changed: {exc}")
            return 1
        results = check(args.instance)
        print()
        _print(results)
    ok = all(r.status in ("ok", "n/a") for r in results)
    print("\nAll tweaks present." if ok else "\nSome tweaks are missing or invalid.")
    return 0 if ok else 1
