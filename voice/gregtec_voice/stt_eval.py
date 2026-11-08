"""The speech-to-text eval (plan 9 evals, plan 8 WO-5.2's acceptance, QA:V2; D183).

``tests/evals/stt_utterances.tsv`` (``id kind key text``) holds 50 GTNH questions, each naming
one item, machine or fluid; ``key`` is the item key the question must resolve to (GT's are
registry name + meta, so they hold on the full pack as on the dev runtime it was written over).

``gos voice eval`` transcribes ``<audio>/<id>.wav`` for each row with the speech-to-text
engine, then treats each transcript as the core treats a spoken question: the router finds the
item words (``router.route``) and ``item_resolve`` resolves them (pre-retrieval's own step,
D151, over GOS_HOME's DB and the vault's glossary). A row is **right** when its transcript
resolves to the row's key without a question ("Which steel did you mean?" counts as wrong).

- **Entity accuracy** = right rows over rows: the acceptance metric, target >= 95% (QA:V2).
- **WER** = word edits (substitutions, deletions, insertions) over the reference's words, all
  rows together, both sides in lower case with punctuation dropped and hyphens as spaces:
  secondary, target <= 8%.
- **The reference check**: each row's own text must resolve to its key on this DB first (a pack
  that names an item differently would otherwise be blamed on speech-to-text). A row that
  doesn't is reported as the set's fault and fails the run; ``--check`` runs only this.

The run passes when every row was transcribed and scored and both targets are met. Each row's
transcript, verdict and timing go to a TSV report (``GOS_HOME/data/reports``), never the audio.
Recordings come from ``gos voice record`` (:class:`Recording`: one talk-key segment per row,
saved as ``<id>.wav``); they are Clayton's voice and stay in his GOS_HOME.
"""

from __future__ import annotations

import csv
import math
import re
import threading
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from gregtec.agents import prefetch, router
from gregtec.agents.tools.registry import Registry
from gregtec_voice.audio import BYTES_PER_S, SAMPLE_BYTES, SAMPLE_RATE, Segment
from gregtec_voice.hooks import Transcriber

REPO_ROOT = Path(__file__).resolve().parents[2]
SET = REPO_ROOT / "tests" / "evals" / "stt_utterances.tsv"
HEADER = ("id", "kind", "key", "text")
KINDS = ("item", "machine", "fluid")
ROW_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")  # also the WAV's file name
ENTITY_TARGET = 0.95  # plan 8 WO-5.2
WER_TARGET = 0.08
RESOLVED, ASKED, NO_MATCH, NO_ITEM, ERROR = (
    "resolved",
    "asked",
    "no match",
    "no item named",
    "error",
)
# The 0.42 s of pre- and post-roll and the shortest line of the set ("Benzene recipe.") take
# about a second; a shorter segment is a slip of the key.
MIN_RECORDING_S = 1.0
QUIET_DBFS = -45.0  # a segment whose loudest sample is quieter holds no speech
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")


class EvalError(ValueError):
    """The set or an audio file can't be used (the message says which and why)."""


@dataclass(frozen=True)
class Utterance:
    id: str
    kind: str  # item, machine (a GT machine's item) or fluid
    key: str
    text: str


def load_set(path: Path = SET) -> list[Utterance]:
    """The eval set; raises :class:`EvalError` on a bad header, row, kind or repeated id."""
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
    if not rows or tuple(rows[0]) != HEADER:
        raise EvalError(f"{path} must start with the header {' '.join(HEADER)}")
    found: list[Utterance] = []
    seen: set[str] = set()
    for n, row in enumerate(rows[1:], 2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(HEADER):
            raise EvalError(f"{path}:{n}: {len(row)} columns, want {len(HEADER)}")
        item = Utterance(*(cell.strip() for cell in row))
        if not ROW_ID.fullmatch(item.id) or item.id in seen:
            raise EvalError(
                f"{path}:{n}: id {item.id!r} is not a new [A-Za-z0-9_-] name"
            )
        if item.kind not in KINDS or not item.key or not item.text:
            raise EvalError(
                f"{path}:{n}: kind must be one of {', '.join(KINDS)}, with a key and text"
            )
        seen.add(item.id)
        found.append(item)
    if not found:
        raise EvalError(f"{path} has no rows")
    return found


@dataclass(frozen=True)
class Resolved:
    key: str | None  # the item it resolved to, or None
    how: str  # resolved, asked, no match, no item named or error
    detail: str = (
        ""  # the item's name, the question asked, the words tried, or the error
    )


def resolve(reg: Registry, text: str) -> Resolved:
    """What the core resolves a question to before any model sees it (D150, D151)."""
    route = router.route(text)
    if not route.subject or route.intent == router.QUESTS:  # quest words name no item
        return Resolved(None, NO_ITEM, f"{route.kind}/{route.intent}")
    pre = prefetch.Prefetch()
    found = prefetch.resolve(reg, pre, route.subject)
    if found is not None:
        return Resolved(found.key, RESOLVED, found.name)
    if pre.clarify is not None:
        return Resolved(None, ASKED, pre.clarify.question)
    if pre.main is not None and pre.main.is_error:
        return Resolved(None, ERROR, pre.main.text.removeprefix("error: ").strip())
    return Resolved(None, NO_MATCH, route.subject)


def words(text: str) -> list[str]:
    """``text`` as WER compares it: lower case words, punctuation dropped, hyphens as spaces."""
    return _WORD.findall(text.casefold().replace("’", "'"))


def edits(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    """Word-level Levenshtein distance (substitutions, deletions, insertions)."""
    previous = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, 1):
        current = [i]
        for j, hyp in enumerate(hypothesis, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ref != hyp))
            )
        previous = current
    return previous[-1]


def read_wav(path: Path) -> bytes:
    """A 16 kHz mono 16-bit WAV's samples, as the voice client records them."""
    try:
        with wave.open(str(path), "rb") as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (
                SAMPLE_RATE,
                1,
                SAMPLE_BYTES,
            ):
                raise EvalError(f"{path.name} isn't 16 kHz mono 16-bit PCM")
            return w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise EvalError(f"{path.name} isn't a WAV file ({exc})") from None


def segment_of(pcm: bytes) -> Segment:
    """A recorded file as the voice client hands a closed segment to the transcriber."""
    seconds = len(pcm) / BYTES_PER_S
    return Segment(pcm, "key", 0.0, seconds, seconds, 0.0)


@dataclass
class Row:
    utterance: Utterance
    reference: (
        Resolved  # the row's own text through the same path (the reference check)
    )
    transcript: str | None = None  # None: not transcribed (see problem)
    got: Resolved | None = None
    stt_ms: int | None = None
    audio_s: float | None = None
    problem: str | None = None  # why it wasn't transcribed

    @property
    def scorable(self) -> bool:
        """The reference itself resolves to the row's key on this DB."""
        return self.reference.key == self.utterance.key

    @property
    def right(self) -> bool:
        return self.got is not None and self.got.key == self.utterance.key

    @property
    def ref_words(self) -> int:
        return len(words(self.utterance.text))

    @property
    def word_edits(self) -> int | None:
        if self.transcript is None:
            return None
        return edits(words(self.utterance.text), words(self.transcript))


def run(
    utterances: Sequence[Utterance],
    reg: Registry,
    transcriber: Transcriber | None,
    audio: Path,
    *,
    clock: Callable[[], float] = time.perf_counter,
    each: Callable[[Row], None] | None = None,
) -> list[Row]:
    """Every row: the reference check, then (with a transcriber) ``audio/<id>.wav``
    transcribed and resolved. ``each`` sees each row as it is done."""
    rows = []
    for item in utterances:
        row = Row(item, resolve(reg, item.text))
        if transcriber is not None:
            said = _transcribe(row, transcriber, audio / f"{item.id}.wav", clock)
            if said is not None:
                row.got = resolve(reg, said)
        rows.append(row)
        if each is not None:
            each(row)
    return rows


def _transcribe(
    row: Row, transcriber: Transcriber, path: Path, clock: Callable[[], float]
) -> str | None:
    """The row's transcript (also kept on the row), or None with ``row.problem`` set."""
    if not path.is_file():
        row.problem = "no audio"
        return None
    try:
        pcm = read_wav(path)
    except (EvalError, OSError) as exc:
        row.problem = str(exc)
        return None
    segment = segment_of(pcm)
    row.audio_s = segment.seconds
    started = clock()
    try:
        said = transcriber.transcribe(segment)
    except Exception as exc:  # noqa: BLE001 - a failing engine fails that row, not the run
        row.problem = f"speech-to-text failed ({type(exc).__name__}: {exc})"
        return None
    row.stt_ms = round((clock() - started) * 1000)
    row.transcript = said
    return said


@dataclass(frozen=True)
class Summary:
    rows: int
    scorable: int
    transcribed: int
    right: int
    word_edits: int
    ref_words: int
    stt_ms: tuple[int, ...] = field(default=())

    @property
    def entity_accuracy(self) -> float:
        return self.right / self.rows if self.rows else 0.0

    @property
    def wer(self) -> float:
        return self.word_edits / self.ref_words if self.ref_words else 0.0

    @property
    def complete(self) -> bool:
        return self.scorable == self.rows == self.transcribed

    @property
    def met(self) -> bool:
        return (
            self.complete
            and self.entity_accuracy >= ENTITY_TARGET
            and self.wer <= WER_TARGET
        )


def summarise(rows: Sequence[Row]) -> Summary:
    done = [r for r in rows if r.transcript is not None]
    return Summary(
        rows=len(rows),
        scorable=sum(r.scorable for r in rows),
        transcribed=len(done),
        right=sum(r.right for r in rows),
        word_edits=sum(r.word_edits or 0 for r in done),
        ref_words=sum(r.ref_words for r in done),
        stt_ms=tuple(sorted(r.stt_ms for r in done if r.stt_ms is not None)),
    )


def percentile(values: Sequence[int], q: float) -> int:
    """Nearest-rank percentile of sorted ``values``."""
    return values[max(0, math.ceil(q * len(values)) - 1)]


def outcome(row: Row) -> str:
    """One row as a line of the report."""
    item = row.utterance
    if not row.scorable:
        return (
            f"{item.id}  SET   the text resolves to {row.reference.key or row.reference.how} "
            f"({row.reference.detail}), not {item.key}: fix the row for this pack"
        )
    if row.transcript is None:
        return f"{item.id}  SKIP  {row.problem}"
    got = row.got
    assert got is not None
    verdict = "ok  " if row.right else "MISS"
    line = (
        f"{item.id}  {verdict}  {row.stt_ms:>5} ms  wer {row.word_edits}/{row.ref_words}  "
        f"{row.transcript!r}"
    )
    if not row.right:
        heard = got.key or f"{got.how}: {got.detail}"
        line += f" -> {heard}; want {row.reference.detail} ({item.key})"
    return line


def report(summary: Summary, engine: str, out: TextIO) -> None:
    print(f"engine: {engine}", file=out)
    print(
        f"entity accuracy: {summary.right} of {summary.rows} "
        f"({summary.entity_accuracy:.1%}), target >= {ENTITY_TARGET:.0%}",
        file=out,
    )
    print(
        f"word error rate: {summary.wer:.1%} ({summary.word_edits} edits in "
        f"{summary.ref_words} words), target <= {WER_TARGET:.0%}",
        file=out,
    )
    if summary.stt_ms:
        print(
            f"speech-to-text: P50 {percentile(summary.stt_ms, 0.5)} ms, "
            f"P90 {percentile(summary.stt_ms, 0.9)} ms per segment",
            file=out,
        )
    if summary.scorable < summary.rows:
        print(
            f"set: {summary.rows - summary.scorable} rows don't resolve to their key on this "
            "DB (SET lines): the set needs fixing for this pack",
            file=out,
        )
    if summary.transcribed < summary.rows:
        print(
            f"not transcribed: {summary.rows - summary.transcribed} rows (SKIP lines)",
            file=out,
        )
    print("verdict: " + ("met" if summary.met else "NOT MET"), file=out)


REPORT_COLUMNS = (
    "id",
    "kind",
    "key",
    "right",
    "got",
    "how",
    "edits",
    "ref_words",
    "stt_ms",
    "audio_s",
    "transcript",
    "problem",
)


def write_tsv(rows: Sequence[Row], path: Path) -> None:
    """The per-row report (the transcripts, never the audio)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def cell(value: object) -> str:
        text = "" if value is None else str(value)
        return " ".join(text.replace("\t", " ").split())

    lines = ["\t".join(REPORT_COLUMNS)]
    for row in rows:
        got = row.got
        lines.append(
            "\t".join(
                cell(v)
                for v in (
                    row.utterance.id,
                    row.utterance.kind,
                    row.utterance.key,
                    int(row.right),
                    got.key if got else None,
                    got.how if got else None,
                    row.word_edits,
                    row.ref_words,
                    row.stt_ms,
                    None if row.audio_s is None else f"{row.audio_s:.2f}",
                    row.transcript,
                    row.problem
                    or (
                        None
                        if row.scorable
                        else f"set: resolves to {row.reference.key}"
                    ),
                )
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- gos voice record -------------------------------------------------------------------------


@dataclass
class Recording:
    """``gos voice record``'s transcriber: each talk-key segment is the next row's WAV
    (``<folder>/<id>.wav``); a segment too short or too quiet to hold the question is asked
    again. ``done`` is set after the last row. Says nothing to the core."""

    rows: Sequence[Utterance]
    folder: Path
    out: TextIO
    done: threading.Event = field(default_factory=threading.Event)
    name: str = "record"
    index: int = 0

    def prompt(self) -> None:
        item = self.rows[self.index]
        print(
            f"[{self.index + 1}/{len(self.rows)}] {item.id}: {item.text}", file=self.out
        )

    def transcribe(self, segment: Segment) -> str:
        if self.index >= len(self.rows):
            return ""
        if segment.seconds < MIN_RECORDING_S or segment.peak_dbfs() < QUIET_DBFS:
            print(
                "  too short or too quiet: hold the talk key and say it again",
                file=self.out,
            )
            return ""
        item = self.rows[self.index]
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{item.id}.wav"
        path.write_bytes(segment.wav())
        print(f"  saved {path.name} ({segment.seconds:.1f} s)", file=self.out)
        self.index += 1
        if self.index == len(self.rows):
            print(f"All {len(self.rows)} recorded in {self.folder}.", file=self.out)
            self.done.set()
        else:
            self.prompt()
        return ""


def to_record(
    utterances: Sequence[Utterance], folder: Path, only: Sequence[str] = ()
) -> list[Utterance]:
    """The rows ``gos voice record`` asks for: ``only`` (ids, in the set's order), else every
    row with no WAV yet. Raises :class:`EvalError` for an id the set doesn't have."""
    if only:
        known = {u.id for u in utterances}
        unknown = [i for i in only if i not in known]
        if unknown:
            raise EvalError(f"not in the set: {', '.join(unknown)}")
        return [u for u in utterances if u.id in set(only)]
    return [u for u in utterances if not (folder / f"{u.id}.wav").is_file()]
