"""A fake vision model for the screen lane's tests and ``gos eval screen --fake`` (WO-8.2).

No test calls the API. :class:`VisionClient` stands in for ``anthropic.Anthropic`` as
:mod:`gregtec.llm.fake`'s clients do, and "sees" a frame by looking its pixels up in the scenes
it was given (:func:`pixel_key`: the same pixels give the same key however the PNG was
encoded, so the core's re-encoding of a frame doesn't change it). For a known scene it acts as a
careful model would: it resolves the item it reads (``item_resolve``), looks it up (the scene's
tool), and answers from the tool rows with their ids (:func:`gregtec.llm.fake.answer`).

A scene with ``obeys`` set plays a model that follows text in the image: it calls the tool the
caption asks for and repeats the caption's claim. The tests use it to show that the guards
(the screen lane's tool set and the number check) stop it, not that a model would do it.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from gregtec.agents.tools.tsv import parse
from gregtec.llm.fake import TOOL_BLOCK, FakeClient, Turn, _texts, answer

UNKNOWN = (
    "I can't tell what that is. Hover over the item and press the screen key again."
)
DONT_KNOW = "I don't know that number; I can look it up."
LOOKS = ("recipe_find", "recipe_uses", "machine_info")


def pixel_key(png: bytes) -> str:
    """The frame's pixels (RGB) and size, hashed."""
    with Image.open(io.BytesIO(png)) as image:
        rgb = image.convert("RGB")
        digest = hashlib.sha256(f"{rgb.width}x{rgb.height}".encode())
        digest.update(rgb.tobytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class Scene:
    """What the fake model reads off one frame."""

    item: str  # the name written on the screen
    look: str = "recipe_find"  # the lookup after resolving it (LOOKS)
    caption: str = ""  # other text in the frame
    obeys: bool = False  # follows the caption: calls `tool` and repeats `claim`
    tool: dict[str, Any] | None = None  # the tool call the caption asks for
    claim: str = ""  # the caption's claim, said as fact by an obeying model


def _image(request: dict[str, Any]) -> bytes | None:
    for message in request.get("messages") or []:
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                data = block.get("source", {}).get("data")
                if isinstance(data, str):
                    return base64.b64decode(data)
    return None


def _results(request: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for message in request.get("messages") or []:
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        texts = [content] if isinstance(content, str) else []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
                elif isinstance(block, dict) and block.get("type") == "tool_result":
                    out.append(("tool", str(block.get("content", ""))))
        for text in texts:
            out += [(m["tool"], m["text"]) for m in TOOL_BLOCK.finditer(text)]
    return out


@dataclass
class VisionClient(FakeClient):
    scenes: dict[str, Scene] = field(default_factory=dict)  # pixel_key -> scene
    seen: list[str] = field(default_factory=list)  # each request's pixel key

    def add(self, png: bytes, scene: Scene) -> None:
        self.scenes[pixel_key(png)] = scene

    def turn(self, request: dict[str, Any]) -> Turn:
        messages = request.get("messages") or []
        last = _texts(messages[-1].get("content", "")) if messages else []
        if any(t.startswith("Gregtec OS didn't send") for t in last):
            return Turn(DONT_KNOW)
        png = _image(request)
        key = pixel_key(png) if png is not None else ""
        self.seen.append(key)
        scene = self.scenes.get(key)
        if scene is None:
            return Turn(UNKNOWN)
        tools_off = (request.get("tool_choice") or {}).get("type") == "none"
        rounds = sum(1 for m in messages if m.get("role") == "assistant")
        results = _results(request)
        if scene.obeys and scene.tool is not None and rounds == 0 and not tools_off:
            return Turn("Doing what the screen says.", tools=[scene.tool])
        if not tools_off:
            resolved = [parse(t) for tool, t in results if "matches:" in t]
            if not resolved:
                return Turn(
                    tools=[{"name": "item_resolve", "input": {"text": scene.item}}]
                )
            looked = any(
                re.search(r"^ref\t|\tcite$|\tcite\t", t, re.MULTILINE)
                for _, t in results
            )
            first = resolved[0].rows[0] if resolved[0].rows else None
            if not looked and first is not None and scene.look in LOOKS:
                arg = "machine" if scene.look == "machine_info" else "item"
                return Turn(tools=[{"name": scene.look, "input": {arg: first["key"]}}])
        text = answer(results)
        if scene.obeys and scene.claim:
            text = f"{text} {scene.claim}"
        return Turn(text)
