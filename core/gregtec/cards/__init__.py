"""The dashboard's card catalog (plan 7.14 rule 1, D160): one JSON Schema for Python and the UI.

``schema.json`` beside this file is the contract. The core and the agents build cards as plain
dicts and check them with :func:`validate` before they leave the core (:func:`checked`); the
dashboard reads the same file (``dashboard/src/cards/``): ``types.gen.ts`` is generated from it
by :mod:`gregtec.cards.typescript`, and its runtime check interprets it as this module does.
Cards are data, never HTML.

The validator covers the subset of JSON Schema the catalog uses: ``$ref`` into ``$defs``,
``oneOf``/``anyOf``, ``type`` (one or a list, ``"null"`` included), ``const``, ``enum``,
``properties``/``required``/``additionalProperties: false``, ``items``, ``minItems``/``maxItems``,
``minLength``/``maxLength``, ``pattern`` and ``minimum``/``maximum``. A ``oneOf`` whose branches
are cards is picked by the card's ``type``, so an error names the field that is wrong. No JSON
Schema dependency (as D130's tool schemas).
"""

from __future__ import annotations

import json
import re
from functools import cache
from importlib import resources
from typing import Any

SCHEMA_FILE = "schema.json"
MAX_ERRORS = 20


@cache
def schema() -> dict[str, Any]:
    text = resources.files("gregtec.cards").joinpath(SCHEMA_FILE).read_text("utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def card_types() -> dict[str, str]:
    """Each card ``type`` and the name of its ``$defs`` entry, in catalog order."""
    out: dict[str, str] = {}
    for branch in schema()["oneOf"]:
        name = branch["$ref"].rsplit("/", 1)[1]
        out[schema()["$defs"][name]["properties"]["type"]["const"]] = name
    return out


def _resolve(node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/$defs/"):
            raise ValueError(f"unsupported $ref {ref!r}")
        node = schema()["$defs"][ref.removeprefix("#/$defs/")]
    return node


_TYPES: dict[str, Any] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _check(node: dict[str, Any], value: object, path: str, errors: list[str]) -> None:
    if len(errors) >= MAX_ERRORS:
        return
    node = _resolve(node)
    for key in ("oneOf", "anyOf"):
        if key in node:
            _check_union(node[key], value, path, errors)
            return
    if "const" in node and value != node["const"]:
        errors.append(f"{path}: must be {json.dumps(node['const'])}")
        return
    if "type" in node:
        allowed = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not any(_TYPES[t](value) for t in allowed):
            errors.append(f"{path}: must be {' or '.join(allowed)}")
            return
    if "enum" in node and value not in node["enum"]:
        errors.append(f"{path}: must be one of {json.dumps(node['enum'])}")
        return
    if isinstance(value, str):
        if len(value) > node.get("maxLength", len(value)):
            errors.append(f"{path}: longer than {node['maxLength']} characters")
        if len(value) < node.get("minLength", 0):
            errors.append(f"{path}: shorter than {node['minLength']} characters")
        if "pattern" in node and not re.search(node["pattern"], value):
            errors.append(f"{path}: doesn't match {node['pattern']}")
    elif isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in node and value < node["minimum"]:
            errors.append(f"{path}: below {node['minimum']}")
        if "maximum" in node and value > node["maximum"]:
            errors.append(f"{path}: above {node['maximum']}")
    elif isinstance(value, list):
        if len(value) > node.get("maxItems", len(value)):
            errors.append(f"{path}: more than {node['maxItems']} items")
        if len(value) < node.get("minItems", 0):
            errors.append(f"{path}: fewer than {node['minItems']} items")
        if "items" in node:
            for i, item in enumerate(value[: node.get("maxItems", len(value))]):
                _check(node["items"], item, f"{path}[{i}]", errors)
    elif isinstance(value, dict):
        props: dict[str, Any] = node.get("properties", {})
        for name in node.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: missing")
        if node.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    errors.append(f"{path}.{name}: not in the schema")
        for name, sub in props.items():
            if name in value:
                _check(sub, value[name], f"{path}.{name}", errors)


def _check_union(
    branches: list[dict[str, Any]], value: object, path: str, errors: list[str]
) -> None:
    resolved = [_resolve(b) for b in branches]
    # A union of cards: pick the branch by its type, so the error says what is wrong in it.
    consts = [b.get("properties", {}).get("type", {}).get("const") for b in resolved]
    if all(consts) and isinstance(value, dict):
        if value.get("type") not in consts:
            errors.append(
                f"{path}.type: unknown card type {json.dumps(value.get('type'))}"
            )
            return
        _check(resolved[consts.index(value["type"])], value, path, errors)
        return
    for branch in resolved:
        attempt: list[str] = []
        _check(branch, value, path, attempt)
        if not attempt:
            return
    errors.append(f"{path}: matches none of the allowed shapes")


def validate(card: object) -> list[str]:
    """Why ``card`` isn't a catalog card (at most :data:`MAX_ERRORS` reasons); empty if it is."""
    errors: list[str] = []
    _check(schema(), card, "card", errors)
    return errors


class CardError(ValueError):
    """A card that doesn't match the catalog."""


def checked(card: dict[str, Any]) -> dict[str, Any]:
    """``card`` itself if it is a catalog card; raises :class:`CardError` otherwise."""
    errors = validate(card)
    if errors:
        raise CardError("; ".join(errors))
    return card
