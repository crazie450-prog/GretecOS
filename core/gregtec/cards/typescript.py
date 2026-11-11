"""The dashboard's card types, generated from ``schema.json`` (D160).

``uv run python -m gregtec.cards.typescript --write`` rewrites ``dashboard/src/cards/types.gen.ts``;
``--check`` exits 1 when that file differs from what the schema gives. A unit test runs the same
check, so the TypeScript and the Python sides can't drift apart.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from gregtec.cards import card_types, schema

REPO_ROOT = Path(__file__).resolve().parents[3]
TARGET = REPO_ROOT / "dashboard" / "src" / "cards" / "types.gen.ts"
HEADER = (
    "// Generated from core/gregtec/cards/schema.json by gregtec.cards.typescript. Don't edit:\n"
    "// change the schema, then run `uv run python -m gregtec.cards.typescript --write`.\n"
)
_SCALARS = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
}


def _literal(value: object) -> str:
    return "null" if value is None else json.dumps(value)


def _type(node: dict[str, Any], indent: str) -> str:
    if "$ref" in node:
        return str(node["$ref"]).rsplit("/", 1)[1]
    for key in ("oneOf", "anyOf"):
        if key in node:
            return " | ".join(_type(b, indent) for b in node[key])
    if "const" in node:
        return _literal(node["const"])
    if "enum" in node:
        return " | ".join(_literal(v) for v in node["enum"])
    kinds = node["type"] if isinstance(node["type"], list) else [node["type"]]
    parts = []
    for kind in kinds:
        if kind == "array":
            item = _type(node["items"], indent)
            parts.append(f"({item})[]" if "|" in item else f"{item}[]")
        elif kind == "object":
            parts.append(_object(node, indent))
        else:
            parts.append(_SCALARS[kind])
    return " | ".join(parts)


def _object(node: dict[str, Any], indent: str) -> str:
    inner = indent + "  "
    required = set(node.get("required", []))
    lines = ["{"]
    for name, sub in node.get("properties", {}).items():
        optional = "" if name in required else "?"
        lines.append(f"{inner}{name}{optional}: {_type(sub, inner)};")
    lines.append(f"{indent}}}")
    return "\n".join(lines)


def generate() -> str:
    """The text of ``types.gen.ts``."""
    defs: dict[str, Any] = schema()["$defs"]
    out = [HEADER]
    for name, node in defs.items():
        if "description" in node:
            out.append(f"/** {node['description']} */")
        if node.get("type") == "object" and "properties" in node:
            out.append(f"export interface {name} {_object(node, '')}\n")
        else:
            out.append(f"export type {name} = {_type(node, '')};\n")
    members = " | ".join(card_types().values())
    out.append(f"export type Card = {members};\n")
    out.append('export type CardType = Card["type"];\n')
    listed = ", ".join(json.dumps(t) for t in card_types())
    out.append(f"export const CARD_TYPES: readonly CardType[] = [{listed}] as const;\n")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gregtec.cards.typescript")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help=f"rewrite {TARGET.name}")
    mode.add_argument(
        "--check", action="store_true", help="exit 1 if it is out of date"
    )
    args = parser.parse_args(argv)
    text = generate()
    if args.write:
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {TARGET}")
        return 0
    current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
    if current != text:
        print(f"{TARGET} is out of date: run with --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
