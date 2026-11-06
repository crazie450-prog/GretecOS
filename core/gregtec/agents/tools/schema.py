"""Tool input schemas: JSON Schema in the shape Anthropic's tool use takes as ``input_schema``,
and a checker for the subset the tools use (no dependency on a JSON Schema library).

A tool schema is ``{"type": "object", "properties": {...}, "required": [...],
"additionalProperties": false}``. A property is a string (``enum``, ``minLength``,
``maxLength``), an integer (``minimum``, ``maximum``), a boolean, or an object whose values are
JSON scalars (``additionalProperties`` with a list of types, ``maxProperties``). Each property
has a ``description`` and may have a ``default``, which :func:`validate` fills in.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

KEYWORDS = {
    "string": {"type", "description", "default", "enum", "minLength", "maxLength"},
    "integer": {"type", "description", "default", "minimum", "maximum"},
    "boolean": {"type", "description", "default"},
    "object": {"type", "description", "additionalProperties", "maxProperties"},
}
SCALARS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


class ArgumentError(ValueError):
    """The tool's arguments don't fit its schema."""


def check_schema(schema: Mapping[str, Any]) -> None:
    """Raise ValueError (TypeError) unless ``schema`` is a tool schema of the supported shape."""
    extra = set(schema) - {"type", "properties", "required", "additionalProperties"}
    if extra:
        raise ValueError(f"unsupported keys {sorted(extra)}")
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
    ):
        raise ValueError("a tool schema is an object with additionalProperties false")
    props = schema.get("properties")
    if not isinstance(props, dict):
        raise TypeError("properties must be an object")
    for name in schema.get("required", []):
        if name not in props:
            raise ValueError(f"required {name!r} is not a property")
    for name, spec in props.items():
        kind = spec.get("type")
        if kind not in KEYWORDS:
            raise ValueError(f"{name}: unsupported type {kind!r}")
        if set(spec) - KEYWORDS[kind]:
            raise ValueError(
                f"{name}: unsupported keys {sorted(set(spec) - KEYWORDS[kind])}"
            )
        if not spec.get("description"):
            raise ValueError(f"{name}: no description")
        if kind == "object":
            values = spec.get("additionalProperties", {}).get("type", [])
            if not values or set(values) - set(SCALARS):
                raise ValueError(
                    f"{name}: object values must be listed JSON scalar types"
                )
        if "default" in spec:
            _check(name, spec, spec["default"])


def _is(value: object, kind: str) -> bool:
    if kind in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, SCALARS[kind])


def _check(name: str, spec: Mapping[str, Any], value: object) -> object:
    kind = spec["type"]
    if kind == "object":
        if not isinstance(value, dict):
            raise ArgumentError(f"{name} must be an object")
        if len(value) > spec.get("maxProperties", 50):
            raise ArgumentError(
                f"{name} has more than {spec.get('maxProperties', 50)} keys"
            )
        allowed = spec["additionalProperties"]["type"]
        for key, each in value.items():
            if not any(_is(each, t) for t in allowed):
                raise ArgumentError(f"{name}.{key} must be one of {', '.join(allowed)}")
        return dict(value)
    if not _is(value, kind):
        raise ArgumentError(f"{name} must be a {kind}")
    if "enum" in spec and value not in spec["enum"]:
        raise ArgumentError(
            f"{name} must be one of {', '.join(map(str, spec['enum']))}"
        )
    if isinstance(value, str):
        if len(value) < spec.get("minLength", 0):
            raise ArgumentError(
                f"{name} is shorter than {spec['minLength']} characters"
            )
        if len(value) > spec.get("maxLength", len(value)):
            raise ArgumentError(f"{name} is longer than {spec['maxLength']} characters")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in spec and value < spec["minimum"]:
            raise ArgumentError(f"{name} must be at least {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise ArgumentError(f"{name} must be at most {spec['maximum']}")
    return value


def validate(schema: Mapping[str, Any], args: object) -> dict[str, Any]:
    """``args`` checked against ``schema``, with defaults filled in."""
    if not isinstance(args, dict):
        raise ArgumentError("the arguments must be a JSON object")
    props: Mapping[str, Any] = schema["properties"]
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ArgumentError(
            f"unknown argument {', '.join(unknown)} (this tool takes "
            f"{', '.join(props) or 'none'})"
        )
    for name in schema.get("required", []):
        if name not in args:
            raise ArgumentError(f"{name} is required")
    out: dict[str, Any] = {}
    for name, spec in props.items():
        if name in args:
            out[name] = _check(name, spec, args[name])
        elif "default" in spec:
            out[name] = spec["default"]
    return out
