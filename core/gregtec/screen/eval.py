"""``gos eval screen`` (WO-8.2 acceptance, plan 8: "5 test screenshots answered correctly").

Each row of ``tests/evals/screen_cases.tsv`` (``id kind item look question caption names cites
absent lab``) is one screenshot case: what the frame shows (``kind`` and ``item``; ``caption`` is
other text in it, such as an injected instruction), the question asked with it (empty: the key
alone), and the expected facts: the reply's spoken text names each of ``names`` (default: the
item), cites an id with each prefix in ``cites``, contains none of ``absent`` (a number drawn
on the screen, an injected claim), has its numbers checked, and made no tool call outside the
screen lane's set. ``lab`` says how Clayton takes the real screenshot in his Lab.

- ``--fake`` (what CI and the tests run): the frames are synthetic, drawn here (:func:`draw`),
  and the model is :class:`gregtec.screen.fake.VisionClient`, which "sees" each drawn frame's
  scene. No key, no cost; it shows the pipeline and the scorer, not a model's eyes. The fake
  plays a careful model, so s05 (the injected caption) passes here without proving anything
  about injection: that proof is the unit test with a fake that obeys the caption (its handoff
  refused, its number caught by the check, and this scorer failing it). The paid run is what
  tests a real model against s05.
- Without ``--fake``: the real vision model (``[models] vision``) with the stored key. The
  frames are ``--images DIR``/``<id>.png`` (his Lab screenshots; never committed) or, without
  ``--images``, the synthetic ones. It costs money: the worst case (:func:`worst_case_usd`) is
  printed first and the run waits for "yes" unless ``--yes``.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from PIL import Image, ImageDraw, ImageFont

from gregtec import config
from gregtec.agents import fast_lane
from gregtec.agents.ask import Reply, setup
from gregtec.agents.fast_lane import MAX_ROUNDS
from gregtec.agents.tools.registry import Registry
from gregtec.llm.prices import PRICES
from gregtec.screen import fake
from gregtec.screen.cache import LONG_EDGE, ScreenCache
from gregtec.screen.lane import MAX_TOKENS, ScreenLane, system_prompt
from gregtec.screen.reader import ScreenReader, Shot

REPO_ROOT = Path(__file__).resolve().parents[3]
CASES = REPO_ROOT / "tests" / "evals" / "screen_cases.tsv"
COLUMNS = (
    "id",
    "kind",
    "item",
    "look",
    "question",
    "caption",
    "names",
    "cites",
    "absent",
    "lab",
)
KINDS = ("tooltip", "gui", "nei", "inventory", "sign")
TASK = "eval:screen"
SIZE = (1280, 720)
# The worst case per call beyond the cached prefix: the largest frame Claude reads (a square
# LONG_EDGE frame, ~w*h/750 tokens) plus a generous conversation of tool rows.
IMAGE_TOKENS = LONG_EDGE * LONG_EDGE // 750 + 1
CONVERSATION_TOKENS = 12_000


class EvalError(ValueError):
    """The case file is malformed."""


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    item: str
    look: str
    question: str
    caption: str
    names: tuple[str, ...]
    cites: tuple[str, ...]
    absent: tuple[str, ...]
    lab: str


@dataclass
class Row:
    case: Case
    reply: Reply
    passed: bool
    why: list[str] = field(default_factory=list)


def _list(value: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in value.split(";") if v.strip())


def load(path: Path = CASES) -> list[Case]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader, None)
        if header is None or tuple(header) != COLUMNS:
            raise EvalError(f"the header must be: {' '.join(COLUMNS)}")
        cases = []
        for n, row in enumerate(reader, start=2):
            if not row or not "".join(row).strip():
                continue
            if len(row) != len(COLUMNS):
                raise EvalError(f"line {n}: {len(row)} columns, not {len(COLUMNS)}")
            d = dict(zip(COLUMNS, row, strict=True))
            if d["kind"] not in KINDS:
                raise EvalError(f"line {n}: kind must be one of {', '.join(KINDS)}")
            if d["look"] not in fake.LOOKS:
                raise EvalError(
                    f"line {n}: look must be one of {', '.join(fake.LOOKS)}"
                )
            if not d["item"].strip() or not d["lab"].strip():
                raise EvalError(f"line {n}: item and lab can't be empty")
            cases.append(
                Case(
                    d["id"],
                    d["kind"],
                    d["item"].strip(),
                    d["look"],
                    d["question"].strip(),
                    d["caption"].strip(),
                    _list(d["names"]) or (d["item"].strip(),),
                    _list(d["cites"]),
                    _list(d["absent"]),
                    d["lab"].strip(),
                )
            )
    ids = [c.id for c in cases]
    if len(set(ids)) != len(ids):
        raise EvalError("case ids must be unique")
    return cases


# --- synthetic frames ------------------------------------------------------------------------


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width and line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return [*lines, line] if line else lines


def draw(case: Case) -> bytes:
    """A synthetic frame for ``case`` (deterministic PNG bytes): a crude game screen with the
    item's name where that kind of screen shows it, plus decoy numbers that no DB row backs."""
    image = Image.new("RGB", SIZE, (34, 40, 52))
    d = ImageDraw.Draw(image)
    big, small = _font(28), _font(20)
    for x in range(0, SIZE[0], 64):  # a blocky "world"
        d.rectangle((x, 600, x + 62, 718), fill=(70, 96, 54))
    if case.kind == "gui":
        d.rectangle((340, 120, 940, 560), fill=(198, 198, 198), outline=(20, 20, 20))
        d.text((360, 135), case.item, font=big, fill=(40, 40, 40))
        for i in range(9):
            x = 380 + (i % 3) * 70
            y = 200 + (i // 3) * 70
            d.rectangle((x, y, x + 60, y + 60), fill=(139, 139, 139))
        d.text((640, 220), "Running: 42%", font=small, fill=(40, 40, 40))
    elif case.kind == "nei":
        d.rectangle((300, 80, 980, 620), fill=(198, 198, 198), outline=(20, 20, 20))
        d.text((320, 95), "Shaped Crafting", font=big, fill=(40, 40, 40))
        d.text((320, 160), f"Output: {case.item}", font=small, fill=(40, 40, 40))
        d.text(
            (320, 200), "EU/t: 1234   Time: 1234 ticks", font=small, fill=(40, 40, 40)
        )
    elif case.kind == "inventory":
        d.rectangle((380, 300, 900, 560), fill=(198, 198, 198), outline=(20, 20, 20))
        for i in range(9):
            x = 400 + i * 54
            d.rectangle((x, 490, x + 48, 538), fill=(139, 139, 139))
        d.rectangle((400, 490, 448, 538), outline=(255, 255, 255), width=3)
        d.text((410, 500), "16", font=small, fill=(255, 255, 255))
    if case.kind in ("tooltip", "inventory", "sign"):
        d.rectangle(
            (520, 230, 920, 330), fill=(16, 0, 16), outline=(80, 0, 255), width=2
        )
        d.text((535, 242), case.item, font=big, fill=(255, 255, 255))
        d.text(
            (535, 285),
            "GregTech" if "Iron" not in case.item else "Minecraft",
            font=small,
            fill=(90, 90, 255),
        )
        if case.kind == "tooltip":
            d.text((760, 285), "x64", font=small, fill=(200, 200, 200))
    if case.caption:
        d.rectangle(
            (40, 40, 300, 220), fill=(160, 120, 70), outline=(60, 40, 20), width=4
        )
        for i, line in enumerate(_wrap(case.caption, 22)[:7]):
            d.text((52, 50 + i * 24), line, font=small, fill=(10, 10, 10))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


# --- scoring ---------------------------------------------------------------------------------


def score(case: Case, reply: Reply, allowed: frozenset[str]) -> list[str]:
    """Why ``reply`` fails ``case`` (empty: it passes)."""
    why = []
    detail = ""
    if reply.card:
        detail = " ".join(
            p["value"] for p in reply.card.get("head", []) if p.get("key") == "detail"
        )
    said = reply.text.casefold()
    if reply.lane != "screen":
        why.append(f"not answered by the screen lane ({reply.lane}: {reply.error})")
    for name in case.names:
        if name.casefold() not in said:
            why.append(f"doesn't name {name!r}")
    for prefix in case.cites:
        if not any(r.startswith(prefix) for r in reply.refs):
            why.append(f"cites no {prefix} id")
    # an absent number is a whole number of what was said: not the digits of a citation or
    # a DB key (r:...64...), nor part of a larger number (640) that a DB row backs
    shown = f"{reply.text} {detail}"
    said_numbers = fast_lane.numbers(shown)
    bare = fast_lane.REF.sub(" ", shown).casefold()
    for bad in case.absent:
        if fast_lane.NUMBER.fullmatch(bad):
            if fast_lane.value(bad) in said_numbers:
                why.append(f"says {bad!r}")
        elif bad.casefold() in bare:
            why.append(f"says {bad!r}")
    if not reply.checked:
        why.append("numbers not checked: " + "; ".join(reply.problems)[:200])
    if stray := sorted({t for t in reply.tool_calls if t not in allowed}):
        why.append(f"called {', '.join(stray)}")
    return why


def unresolved(registry: Registry, cases: Sequence[Case]) -> list[str]:
    """The cases whose item the DB doesn't know (all of them with no DB): a run without the
    game DB fails every case for reasons that have nothing to do with the model."""
    missing = []
    for case in cases:
        got = registry.call("item_resolve", {"text": case.item, "limit": 1})
        if got.is_error or "no item matches" in got.text:
            missing.append(case.id)
    return missing


def worst_case_usd(model: str, registry: Registry, cases: int) -> float:
    """An estimate of the most a real run costs, not a bound: every case takes the lane's every
    call (MAX_ROUNDS and the number check's retry), none reads the cache (the prefix written
    each time), each with the largest frame, a long conversation and the most output the lane
    allows. The prefix is counted as characters/3 and the conversation as a fixed size, so
    :func:`run` also stops once the metered cost passes it (review L7)."""
    price = PRICES[model]
    tools = json.dumps(registry.specs("fast"))
    prefix = (len(tools) + len(json.dumps(system_prompt(None)))) // 3 + 1
    per_call = (
        prefix * price.cache_write
        + (IMAGE_TOKENS + CONVERSATION_TOKENS) * price.input
        + MAX_TOKENS * price.output
    ) / 1_000_000
    return per_call * (MAX_ROUNDS + 1) * cases


def run(
    reader_for: Callable[[], ScreenReader],
    cases: Sequence[Case],
    frames: Callable[[Case], bytes],
    allowed: frozenset[str],
    out: TextIO,
    *,
    fake_model: bool,
    stop_usd: float | None = None,
) -> int:
    """Run ``cases``; with ``stop_usd``, the cases left once the metered cost reaches it are
    not run and the run fails."""
    print("id\tkind\tpass\tttft_ms\tcalls\tusd\twhy\ttext", file=out)
    rows = []
    spent = 0.0
    for case in cases:
        if stop_usd is not None and spent >= stop_usd:
            print(
                f"# stopped: ${spent:.4f} spent reached the stated ${stop_usd:.2f}; "
                f"{len(cases) - len(rows)} cases not run",
                file=out,
            )
            break
        reply = reader_for().read(
            Shot(frames(case), "screenshot", case.question or None)
        )
        spent += reply.usd
        why = score(case, reply, allowed)
        rows.append(Row(case, reply, not why, why))
        text = reply.text.replace("\t", " ").replace("\n", " ")[:160]
        print(
            f"{case.id}\t{case.kind}\t{int(not why)}\t"
            f"{'' if reply.ttft_ms is None else reply.ttft_ms}\t{reply.calls}\t"
            f"{reply.usd:.6f}\t{'; '.join(why)}\t{text}",
            file=out,
        )
    passed = sum(r.passed for r in rows)
    usd = sum(r.reply.usd for r in rows)
    print(file=out)
    print(f"passed: {passed} of {len(cases)} (target: all 5)", file=out)
    print(f"cost: ${usd:.6f}{' (simulated)' if fake_model else ''}", file=out)
    ok = passed == len(rows) == len(cases)
    print("Result: " + ("PASS" if ok else "FAIL"), file=out)
    return 0 if ok else 1


def fake_reader(
    registry: Registry, home: Path, cases: Sequence[Case]
) -> tuple[ScreenReader, ScreenLane]:
    """A reader whose lane is the fake vision model, knowing each case's drawn frame."""
    client = fake.VisionClient()
    for case in cases:
        client.add(draw(case), fake.Scene(case.item, case.look, case.caption))
    lane = ScreenLane(client, registry, meter=None)
    return ScreenReader(registry, ScreenCache(home), lane), lane


def main(
    config_path: Path | None,
    cases_path: Path | None,
    fake_model: bool,
    images: Path | None,
    yes: bool,
    limit: int | None,
    out: TextIO,
    answer: Callable[[], str] = lambda: sys.stdin.readline(),
) -> int:
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        cases = load(cases_path or CASES)
    except (OSError, EvalError) as exc:
        print(f"Can't read the cases: {exc}", file=out)
        return 2
    if limit is not None:
        cases = cases[:limit]
    if images is not None:
        missing = [c.id for c in cases if not (images / f"{c.id}.png").is_file()]
        if missing:
            print(f"No screenshot for {', '.join(missing)} in {images}.", file=out)
            return 2
    agents = setup(
        cfg, fake=True, deep_lane=False
    )  # the registry; no fast lane is used
    try:
        registry = agents.registry
        if missing := unresolved(registry, cases):  # before any cost is stated or spent
            print(
                f"Not run: the DB under {cfg.gos_home} doesn't know the items of "
                f"{', '.join(missing)}. Build the DB first (gos export collect, then "
                "gos db build).",
                file=out,
            )
            return 2
        with tempfile.TemporaryDirectory(prefix="gos-screen-eval-") as tmp:
            home = Path(tmp)  # frames go here, not into GOS_HOME's cache
            if fake_model:
                if images is not None:
                    print("--fake reads only the synthetic frames.", file=out)
                    return 2
                reader, lane = fake_reader(registry, home, cases)
                return run(
                    lambda: reader, cases, draw, lane.allowed, out, fake_model=True
                )
            model = cfg.model_vision
            worst = worst_case_usd(model, registry, len(cases))
            print(
                f"# {len(cases)} cases on {model}: worst case ${worst:.2f}, an estimate "
                "(every call, no cache, the largest frame); the run stops if it gets there.",
                file=out,
            )
            if not yes:
                print("Type yes to spend it: ", end="", file=out, flush=True)
                if answer().strip().casefold() != "yes":
                    print("Not run.", file=out)
                    return 1
            lane = ScreenLane.from_keyring(
                registry, meter=agents.meter, vault=agents.vault, model=model
            )
            if lane is None:
                print(
                    "Not run: no Anthropic key is stored in this account (uv run gos key "
                    "set anthropic), or use --fake.",
                    file=out,
                )
                return 1
            reader = ScreenReader(registry, ScreenCache(home), lane)

            def frames(case: Case) -> bytes:
                if images is None:
                    return draw(case)
                return (images / f"{case.id}.png").read_bytes()

            return run(
                lambda: reader,
                cases,
                frames,
                lane.allowed,
                out,
                fake_model=False,
                stop_usd=worst,
            )
    finally:
        agents.close()
