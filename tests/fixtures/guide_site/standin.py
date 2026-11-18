"""A hand-made stand-in for a GuideNH ``/guidenhc exportsite`` folder (WO-10.1, D304).

:func:`make` writes, under ``dest``: ``standin/en_us/multiblock.html`` (one guide page with one
scene), ``_site/`` (a stand-in viewer, a style sheet and a blank placeholder) and
``_res/scenes/<sha256>.scene.gz`` (the scene, named by the SHA-256 of its uncompressed bytes as
GuideNH names its shared files). Nothing in it is GuideNH's code, text or data: the blocks are
made up (``standin:*``), keyed by registry name + meta, and the scene is JSON, not FlatBuffers.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE = "standin/en_us/multiblock.html"


def scene() -> dict[str, object]:
    """A 3 x 3 x 3 made-up multiblock: casings around a hollow middle, a coil ring in the
    middle layer, a controller on the front face."""
    blocks: list[dict[str, object]] = []
    for y in range(3):
        for x in range(3):
            for z in range(3):
                if (x, y, z) == (1, 1, 1):
                    continue
                if (x, y, z) == (1, 1, 0):
                    key = "standin:controller@0"
                elif y == 1:
                    key = "standin:coil@1"
                else:
                    key = "standin:casing@0"
                blocks.append({"pos": [x, y, z], "block": key})
    return {"format": "gos-guide-standin/1", "blocks": blocks}


def make(dest: Path) -> Path:
    """Write the stand-in site into ``dest`` (made if missing) and return the page's path."""
    raw = json.dumps(scene(), sort_keys=True).encode("utf-8")
    name = f"{hashlib.sha256(raw).hexdigest()}.scene.gz"
    scenes = dest / "_res" / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    (scenes / name).write_bytes(gzip.compress(raw, mtime=0))
    site = dest / "_site"
    site.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "viewer.js", site / "viewer.js")
    (site / "standin.css").write_text(
        "body{margin:0;padding:16px;background:#0b0d0c;color:#e4eae6;font:16px system-ui}\n"
        "canvas.game-scene{display:block;background:#151917}\n",
        "utf-8",
    )
    (site / "blank.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>\n', "utf-8"
    )
    page = dest / PAGE
    page.parent.mkdir(parents=True, exist_ok=True)
    html = (HERE / "page.html").read_text("utf-8").replace("__SCENE__", name)
    page.write_text(html, "utf-8")
    return page
