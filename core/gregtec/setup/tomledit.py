"""Change a few keys of gos.toml and keep everything else as it was (WO-2.9, D167).

The standard library reads TOML but can't write it, and a whole-file rewrite would drop Clayton's
own comments and keys GOS doesn't know yet. So :func:`set_values` edits lines: a key's
``key = value`` line in its ``[table]`` is replaced (a comment after the value is kept), a missing
key goes after the table's last key, and a missing table goes at the end. Only bare keys in
``[table]`` sections are edited; anything else (a dotted key, an inline table, a value over
several lines) makes the result read back wrong, and then nothing is written
(:class:`TomlEditError`: edit gos.toml by hand).

:func:`write_atomic` replaces the file in one step (a temp file beside it, then ``os.replace``),
keeping the old one as ``gos.toml.bak``, so a crash never leaves half a file.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import time
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

Value = str | int | float | bool | list[str]
Updates = Mapping[
    tuple[str, str], Value | None
]  # (table, key) -> value; None removes it

_HEADER = re.compile(r"^\s*\[\s*([A-Za-z0-9_.-]+)\s*\]\s*(#.*)?$")
_ANY_HEADER = re.compile(r"^\s*\[")
_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_REPLACE_TRIES = (
    3  # Windows: a reader holding the file open makes os.replace fail at once
)


class TomlEditError(ValueError):
    """The edit can't be made safely; the message says why. Nothing was written."""


def _string(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def format_value(value: Value) -> str:
    """``value`` as TOML: a basic string, an integer, a float, true/false or a list of strings."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):  # [budget] task_max_usd (WO-3.7)
        if not math.isfinite(value):
            raise TomlEditError("can't write a value that isn't a finite number")
        return repr(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return "[" + ", ".join(_string(v) for v in value) + "]"
    raise TomlEditError(f"can't write {type(value).__name__} values")


def _comment(rest: str) -> str:
    """The comment after the value in ``rest`` (what follows ``=``), with its leading space,
    or "": a ``#`` where the text before it already reads as the whole value."""
    try:
        whole = tomllib.loads("k = " + rest)["k"]
    except tomllib.TOMLDecodeError:
        return ""
    for i, ch in enumerate(rest):
        if ch != "#":
            continue
        try:
            if tomllib.loads("k = " + rest[:i])["k"] == whole:
                j = i
                while j > 0 and rest[j - 1] in " \t":
                    j -= 1
                return rest[j:].rstrip("\r\n")
        except tomllib.TOMLDecodeError:
            continue
    return ""


def _sections(lines: list[str]) -> list[tuple[str | None, int, int]]:
    """(table name or None for the top, first line, end line) for each section."""
    out: list[tuple[str | None, int, int]] = []
    name: str | None = None
    start = 0
    for i, line in enumerate(lines):
        if _ANY_HEADER.match(line):
            out.append((name, start, i))
            m = _HEADER.match(line)
            name = m[1] if m else "\0other"  # [[array]] tables: never edited
            start = i + 1
    out.append((name, start, len(lines)))
    return out


def _key_of(line: str) -> str | None:
    key, sep, _ = line.partition("=")
    key = key.strip()
    if not sep or not _BARE.match(key) or line.lstrip().startswith("#"):
        return None
    return key


def set_values(text: str, updates: Updates) -> str:
    """``text`` with ``updates`` made. Raises :class:`TomlEditError` when ``text`` isn't TOML,
    or the edited text wouldn't read back as asked."""
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TomlEditError(
            f"gos.toml isn't valid TOML now ({exc}); fix it by hand"
        ) from None
    for table, key in updates:
        if not (_BARE.match(table) and _BARE.match(key)):
            raise TomlEditError(f"can't edit [{table}] {key}: only bare names")
    lines = text.splitlines()
    by_table: dict[str, dict[str, Value | None]] = {}
    for (table, key), value in updates.items():
        by_table.setdefault(table, {})[key] = value
    for table, wanted in by_table.items():
        found = [s for s in _sections(lines) if s[0] == table]
        if len(found) > 1:
            raise TomlEditError(f"[{table}] appears twice in gos.toml; fix it by hand")
        if not found:
            add = [
                f"{k} = {format_value(v)}" for k, v in wanted.items() if v is not None
            ]
            if add:
                while lines and not lines[-1].strip():
                    lines.pop()
                lines += ([""] if lines else []) + [f"[{table}]", *add]
            continue
        _, start, end = found[0]
        section = lines[start:end]
        done: set[str] = set()
        kept: list[str] = []
        for line in section:
            key = _key_of(line)
            if key is None or key not in wanted:
                kept.append(line)
                continue
            done.add(key)
            value = wanted[key]
            if value is None:
                continue
            indent = line[: len(line) - len(line.lstrip())]
            rest = line.partition("=")[2]
            kept.append(f"{indent}{key} = {format_value(value)}{_comment(rest)}")
        missing = [
            f"{k} = {format_value(v)}"
            for k, v in wanted.items()
            if k not in done and v is not None
        ]
        if missing:
            last = max(
                (i for i, line in enumerate(kept) if _key_of(line) is not None),
                default=-1,
            )
            kept[last + 1 : last + 1] = missing
        lines[start:end] = kept
    result = "\n".join(lines) + "\n"
    _check(result, updates, before)
    return result


def _expected(before: dict[str, Any], updates: Updates) -> dict[str, Any]:
    """``before`` with ``updates`` made, as the edited file must read back."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in before.items()}
    for (table, key), value in updates.items():
        section = out.get(table)
        if value is None:
            if isinstance(section, dict):
                section.pop(key, None)
        elif isinstance(section, dict):
            section[key] = value
        else:
            out[table] = {key: value}
    return out


def _check(result: str, updates: Updates, before: dict[str, Any]) -> None:
    try:
        data = tomllib.loads(result)
    except tomllib.TOMLDecodeError as exc:
        raise TomlEditError(
            f"the edit wouldn't be valid TOML ({exc}); edit gos.toml by hand"
        ) from None
    for (table, key), value in updates.items():
        section = data.get(table)
        got: Any = section.get(key) if isinstance(section, dict) else None
        if got != value:
            raise TomlEditError(
                f"[{table}] {key} wouldn't read back as written (is it a dotted key, an "
                "inline table or a value over several lines?); edit gos.toml by hand"
            )
    if data != _expected(
        before, updates
    ):  # e.g. a key = line inside a multi-line string
        raise TomlEditError(
            "the edit would change more than the keys asked for (a value over several "
            "lines?); edit gos.toml by hand"
        )


def write_atomic(
    path: Path, text: str, verify: Callable[[Path], None] | None = None
) -> None:
    """Replace ``path`` with ``text`` (UTF-8) in one step, the old file kept as ``<name>.bak``.

    ``verify`` reads the new file (written beside ``path`` first) and raises if it is wrong;
    then nothing is replaced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if verify is not None:
        try:
            verify(tmp)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    if path.is_file():
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_TRIES - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.2)
