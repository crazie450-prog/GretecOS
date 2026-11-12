"""The flow diagram as a PNG, for Discord (WO-3.6, plan §7.1 step 9, D53, D201).

Drawn with Pillow (wheels for Windows and Linux, its own default font, no system install; the
lightest renderer that needs nothing else on Clayton's PC: Graphviz and a browser both need one).
The boxes and links are :mod:`.diagram`'s, in its columns, so the PNG, the vault's Mermaid and
the dashboard's card show the same diagram. Colours are the design system's (``tokens.json``):
the Machine GUI theme's panel, border and text, and each node's GT tier colour as a band on its
left, always beside the tier's name (D51). A node shows its product, ``<count>x <machine> · <tier>``
and its ``r:`` id; a boundary box its item and the plan's rate; a link its item and rate.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from gregtec.planner.outputs.book import Book
from gregtec.planner.outputs.diagram import Box, flow, layers, rate

# docs/design/system/tokens.json: the Machine GUI theme (2 · LV) and the GT tier colours.
BG = "#1F1F1F"  # machine-bg
PANEL = "#C6C6C6"  # machine-panel
BORDER = "#000000"  # machine-border
TEXT = "#3F3F3F"  # machine-text
TEXT_2 = "#4E4E4E"  # machine-text-2
RULE = "#8B8B8B"  # machine-rule
SHELL_TEXT = "#E0E0E0"  # machine-shell-text
TIER = {
    "STEAM": "#B5893B",
    "ULV": "#FF5555",
    "LV": "#00AA00",
    "MV": "#FFAA00",
    "HV": "#FFFF55",
    "EV": "#555555",
    "IV": "#5555FF",
    "LUV": "#FF55FF",
    "ZPM": "#55FFFF",
    "UV": "#00AA00",
    "UHV": "#AA0000",
    "UEV": "#AA00AA",
    "UIV": "#0000AA",
}
TIER_EDGE = "#8A8A8A"  # tier-edge

BOX_W, BOX_H = 300, 84
GAP_X, GAP_Y = 120, 36
PAD = 32
BAND = 12
MAX_SIDE = 16000  # pixels: a bigger plan is drawn smaller rather than refused


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size)


def _fit(draw: ImageDraw.ImageDraw, text: str, font: Any, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _lines(b: Box) -> tuple[str, str, str]:
    if b.kind == "node":
        sub = " · ".join(
            x
            for x in (
                f"{b.count}x" if b.count is not None else None,
                b.machine or "no machine",
                b.tier,
            )
            if x
        )
        return b.label, sub, b.ref or ""
    side = {"in": "input", "out": "byproduct", "target": "target"}[b.kind]
    return b.label, f"{side} · {rate(b.rate_s)}" if b.rate_s is not None else side, ""


def render(plan: Mapping[str, Any], book: Book) -> bytes:
    """The PNG's bytes."""
    f = flow(plan, book)
    place = layers([b.id for b in f.boxes], [(x.src, x.dst) for x in f.links])
    cols = max((c for c, _ in place.values()), default=0) + 1
    rows = max((r for _, r in place.values()), default=0) + 1
    width = PAD * 2 + cols * BOX_W + (cols - 1) * GAP_X
    height = PAD * 2 + 40 + rows * BOX_H + (rows - 1) * GAP_Y
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)
    title, big, small = _font(20), _font(16), _font(13)
    draw.text((PAD, PAD - 8), _fit(draw, f"{plan['name']} · plan:{plan['id']}", title, width - 2 * PAD), fill=SHELL_TEXT, font=title)

    def xy(bid: str) -> tuple[int, int]:
        c, r = place[bid]
        return PAD + c * (BOX_W + GAP_X), PAD + 40 + r * (BOX_H + GAP_Y)

    for link in f.links:
        x1, y1 = xy(link.src)
        x2, y2 = xy(link.dst)
        a = (x1 + BOX_W, y1 + BOX_H // 2)
        b = (x2, y2 + BOX_H // 2)
        if b[0] <= a[0]:  # a loop back: route above the boxes
            top = min(y1, y2) - GAP_Y // 2
            pts = [a, (a[0] + 16, a[1]), (a[0] + 16, top), (b[0] - 16, top), (b[0] - 16, b[1]), b]
        else:
            mid = a[0] + GAP_X // 2
            pts = [a, (mid, a[1]), (mid, b[1]), b]
        draw.line(pts, fill=RULE, width=2)
        draw.polygon([b, (b[0] - 8, b[1] - 5), (b[0] - 8, b[1] + 5)], fill=RULE)
        label = link.label + (f" {rate(link.rate_s)}" if link.rate_s is not None else "")
        label = _fit(draw, label, small, GAP_X + BOX_W // 2)
        draw.text((pts[1][0] + 4, (a[1] + b[1]) // 2 - 16), label, fill=SHELL_TEXT, font=small)
    for box in f.boxes:
        x, y = xy(box.id)
        draw.rectangle([x, y, x + BOX_W, y + BOX_H], fill=PANEL, outline=BORDER, width=2)
        if box.tier:
            colour = TIER.get(box.tier.upper(), TIER_EDGE)
            draw.rectangle([x + 2, y + 2, x + BAND, y + BOX_H - 2], fill=colour, outline=TIER_EDGE)
        head, sub, ref = _lines(box)
        tx = x + BAND + 10
        draw.text((tx, y + 8), _fit(draw, head, big, BOX_W - BAND - 18), fill=TEXT, font=big)
        draw.text((tx, y + 34), _fit(draw, sub, small, BOX_W - BAND - 18), fill=TEXT_2, font=small)
        if ref:
            draw.text((tx, y + 56), _fit(draw, ref, small, BOX_W - BAND - 18), fill=TEXT_2, font=small)
    if max(width, height) > MAX_SIDE:
        scale = MAX_SIDE / max(width, height)
        img = img.resize((max(1, int(width * scale)), max(1, int(height * scale))))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
