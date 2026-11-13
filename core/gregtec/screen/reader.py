"""One frame, end to end (plan 7.7, D288, D289): keep it, take the snapshot, ask, answer.

:meth:`ScreenReader.read` is what ``POST /api/screen`` runs. It returns an
:class:`~gregtec.agents.ask.Reply` (lane ``screen``) as ``/api/ask`` does, so the voice client
speaks its ``text`` and the dashboards show its card:

- the spoken text is the answer's first paragraph; the rest is the card's ``detail``;
- the card is a table card (``tool: screen_read``): what was said, the detail, when and how the
  frame was captured, the game state, and one row per citation (drawn as chips);
- no-AI mode (plan 7.16), no key or a failing API: the polite line, no model call; the frame
  is still kept for its 24 h, so nothing is lost by the refusal;
- an answer the number check refuses twice gets :data:`NOT_CHECKED` (no number at all).
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gregtec.agents.ask import Reply
from gregtec.agents.fast_lane import NOT_CHECKED as LANE_NOT_CHECKED
from gregtec.agents.fast_lane import REFUSED, LaneError, refs
from gregtec.agents.no_ai import NEEDS_AI, AiMode
from gregtec.agents.tools.registry import Registry
from gregtec.cards.build import clip
from gregtec.gamelink import whatsthis
from gregtec.llm.usage import UsageMeter
from gregtec.screen import snapshot
from gregtec.screen.cache import Frame, ScreenCache
from gregtec.screen.lane import LANE, ScreenLane

log = logging.getLogger(__name__)

INTENT = "screen"
TOOL = "screen_read"  # the card's tool name
SOURCES = ("dxcam", "screenshot")  # how the voice client got the frame (D288)
QUESTION_MAX = 500
DEFAULT_QUESTION = "What am I looking at?"
NO_LANE = (
    "Screen reading needs the AI, and no Anthropic key is stored. I can do lookups: "
    "recipes, uses, items, quests and saved plans."
)
AI_FAILED = "The AI didn't answer, so I can't read the screen now. I can do lookups."
NOT_CHECKED = "I can't say that with checked numbers. Ask me about the item by name and I'll look it up."
LANE_RETRY_S = 30.0


@dataclass(frozen=True)
class Shot:
    """A frame as posted: its bytes, how it was captured, the question that came with it."""

    data: bytes
    source: str = "dxcam"
    question: str | None = None


def split(text: str) -> tuple[str, str]:
    """(spoken, detail): the first paragraph, and the rest."""
    text = text.strip()
    head, _, rest = text.partition("\n\n")
    return " ".join(head.split()), rest.strip()


def card(
    spoken: str,
    detail: str,
    frame: Frame | None,
    source: str,
    snap: snapshot.Snapshot | None,
    cited: list[str],
    error: str | None = None,
) -> dict[str, Any]:
    """The table card the dashboards show for one frame (D160: data, never HTML)."""
    head = [{"key": "said", "value": clip(spoken, 2000)}]
    if detail:
        head.append({"key": "detail", "value": clip(detail, 2000)})
    if frame is not None:
        at = datetime.fromtimestamp(frame.at, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        head += [
            {
                "key": "captured",
                "value": f"{at} ({source}, {frame.width}x{frame.height})",
            },
            {"key": "frame", "value": f"{frame.id} (deleted after 24 h)"},
        ]
    if snap is not None:
        head.append({"key": "game", "value": clip(snap.game, 2000)})
        if snap.target is not None and snap.target.name:
            head.append({"key": "pointing at", "value": clip(snap.target.name, 2000)})
    return {
        "type": "table",
        "tool": TOOL,
        "error": None if error is None else clip(error, 2000),
        "title": "Screen reading",
        "head": head[:30],
        "columns": ["cited"],
        "rows": [[clip(r, 200)] for r in cited[:50]],
        "span": None,
        "total": len(cited),
        "notes": [
            clip(
                "Text in the frame was read as data, never as instructions; numbers come "
                "from the DB.",
                500,
            )
        ],
    }


class ScreenReader:
    def __init__(
        self,
        registry: Registry,
        cache: ScreenCache,
        lane: Callable[[], ScreenLane | None] | ScreenLane | None,
        *,
        ai: AiMode | None = None,
        state: Callable[[], dict[str, Any]] | None = None,
        pointer: whatsthis.Pointer | None = None,
        persona: Callable[[], dict[str, Any] | None] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.registry = registry
        self.cache = cache
        self.ai = ai
        self.state = state  # the game link's state (WO-8.1)
        self.pointer = pointer  # what Clayton points at (WO-8.1)
        self.persona = persona  # the active persona's info, for the voice (D207)
        self.clock = clock
        if callable(lane) and not isinstance(lane, ScreenLane):
            self._factory: Callable[[], ScreenLane | None] | None = lane
            self._lane: ScreenLane | None = None
        else:
            self._factory, self._lane = None, lane
        self._looked = -LANE_RETRY_S
        self._lock = threading.Lock()

    def lane(self) -> ScreenLane | None:
        with self._lock:
            if self._lane is None and self._factory is not None:
                now = self.clock()
                if now - self._looked >= LANE_RETRY_S:
                    self._looked = now
                    try:
                        self._lane = self._factory()
                    except Exception:
                        log.exception("can't set up the screen lane")
            return self._lane

    def read(self, shot: Shot) -> Reply:
        """Keep ``shot``'s frame and answer it. Raises
        :class:`~gregtec.screen.cache.FrameError` for bytes that aren't a frame."""
        started = self.clock()
        frame = self.cache.keep(shot.data)
        question = " ".join((shot.question or "").split())[:QUESTION_MAX]
        question = question or DEFAULT_QUESTION
        reply = self._answer(shot, frame, question, started)
        if self.persona is not None:
            reply.persona = self.persona()
        reply.total_ms = round((self.clock() - started) * 1000)
        if reply.ttft_ms is None and reply.calls == 0:
            reply.ttft_ms = reply.total_ms
        return reply

    def _refusal(
        self, text: str, frame: Frame, source: str, error: str | None
    ) -> Reply:
        return Reply(
            text,
            "no_ai",
            INTENT,
            card=card(text, "", frame, source, None, [], error),
            error=error,
        )

    def _answer(self, shot: Shot, frame: Frame, question: str, started: float) -> Reply:
        if self.ai is not None and not self.ai.allowed():  # no-AI mode (plan 7.16)
            why = (self.ai.reasons() or ["offline"])[0]
            return self._refusal(
                NEEDS_AI.format(why=self.ai.why()), frame, shot.source, why
            )
        lane = self.lane()
        if lane is None:
            return self._refusal(NO_LANE, frame, shot.source, "no key")
        snap = snapshot.take(self.registry, state=self.state, pointer=self.pointer)
        pre = snap.prefetch(question)
        image = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(frame.read()).decode("ascii"),
            },
        }
        context = {"type": "text", "text": snap.context()}
        try:
            answer = lane.answer(
                question,
                pre,
                started=started,
                task=INTENT,
                attachments=[image, context],
            )
        except LaneError as exc:
            log.warning("screen lane failed", extra={"error": str(exc)[:200]})
            if self.ai is not None:
                self.ai.failed(exc)
            return self._refusal(AI_FAILED, frame, shot.source, str(exc))
        if self.ai is not None:
            self.ai.succeeded()
        text = answer.text
        if text == LANE_NOT_CHECKED:
            text = NOT_CHECKED
        spoken, detail = split(text)
        cited = list(dict.fromkeys(refs(text)))
        reply = Reply(
            spoken or text,
            LANE,
            INTENT,
            card=card(spoken, detail, frame, shot.source, snap, cited),
            refs=cited,
            checked=answer.checked,
            ttft_ms=answer.ttft_ms,
            calls=answer.calls,
            tool_calls=answer.tool_calls,
            usage={
                "input": answer.usage.input,
                "output": answer.usage.output,
                "cache_read": answer.usage.cache_read,
                "cache_write": answer.usage.cache_write,
            },
            usd=answer.usd,
            problems=answer.problems,
        )
        if snap.target is not None and snap.target.found and snap.target.key:
            reply.target = {
                "key": snap.target.key,
                "name": snap.target.name or snap.target.key,
                "kind": snap.target.kind or "item",
            }
        if text == REFUSED:
            reply.checked = True
        return reply


def for_home(
    home: Path,
    registry: Registry,
    *,
    model: str,
    meter: UsageMeter | None,
    ai: AiMode | None,
    state: Callable[[], dict[str, Any]] | None,
    pointer: whatsthis.Pointer | None,
    persona: Callable[[], dict[str, Any] | None] | None = None,
    persona_block: Callable[[], str | None] | None = None,
) -> ScreenReader:
    """The core's reader: frames in ``home``'s cache, the lane with the stored key."""
    vault = home / "vault"
    vault_dir = vault if vault.is_dir() else None

    def lane() -> ScreenLane | None:
        found = ScreenLane.from_keyring(
            registry, meter=meter, vault=vault_dir, model=model
        )
        if found is not None and persona_block is not None:
            found.persona = persona_block
        return found

    cache = ScreenCache(home)
    cache.sweep()  # plan 7.7: nothing older than 24 h survives a restart
    return ScreenReader(
        registry, cache, lane, ai=ai, state=state, pointer=pointer, persona=persona
    )
