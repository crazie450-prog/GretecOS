"""The vault's ``glossary.tsv`` as ``item_resolve``'s alias table (plan sections 6.1 and 6.4).

Columns ``term⇥aliases⇥kind⇥ref⇥say``; aliases are comma-separated. A term or alias in a request
(a whole request, or up to four words of it) is replaced by each of the entry's other forms, so
"SS plate" is also tried as "stainless steel plate" and "EBF" as "electric blast furnace". Only
entries of the kinds in :data:`EXPANDS` are used: a tier ("LV") is part of item names as it is.
The live vault's copy wins (agents may add to it); the repo's ``vault-template/glossary.tsv`` is
the fallback. Glossary text is data: it only ever becomes search words.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATE = REPO_ROOT / "vault-template" / "glossary.tsv"
FILE = "glossary.tsv"  # in the vault's root
HEADER = ("term", "aliases", "kind")
EXPANDS = frozenset({"material", "machine", "item", "line"})
MAX_BYTES = 1 << 20
MAX_WORDS = 4  # the longest term or alias matched inside a request
MAX_VARIANTS = 12


@dataclass(frozen=True)
class Entry:
    term: str
    aliases: tuple[str, ...]
    kind: str

    @property
    def forms(self) -> tuple[str, ...]:
        return (self.term, *self.aliases)


@dataclass(frozen=True)
class Glossary:
    entries: tuple[Entry, ...] = ()
    path: Path | None = None
    problem: str | None = None  # why the file was not used

    @classmethod
    def load(cls, path: Path) -> Glossary:
        """``path`` read; a missing, oversized or headerless file gives an empty glossary."""
        try:
            if path.stat().st_size > MAX_BYTES:
                return cls(path=path, problem=f"{path} is over {MAX_BYTES} bytes")
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(path=path, problem=f"{path} doesn't exist")
        except (OSError, UnicodeDecodeError) as exc:
            return cls(path=path, problem=f"{path}: {exc}")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines or tuple(lines[0].split("\t")[:3]) != HEADER:
            return cls(path=path, problem=f"{path} has no term, aliases, kind header")
        entries = []
        for line in lines[1:]:
            cols = line.split("\t")
            term = cols[0].strip()
            if not term:
                continue
            aliases = tuple(
                a.strip()
                for a in (cols[1] if len(cols) > 1 else "").split(",")
                if a.strip()
            )
            entries.append(
                Entry(term, aliases, cols[2].strip() if len(cols) > 2 else "")
            )
        return cls(tuple(entries), path)

    @classmethod
    def find(cls, vault: Path | None) -> Glossary:
        """The live vault's glossary, else the repo template's."""
        if vault is not None and (vault / FILE).is_file():
            return cls.load(vault / FILE)
        return cls.load(TEMPLATE)

    def variants(self, text: str) -> list[tuple[str, Entry | None]]:
        """``text`` first, then ``text`` with each glossary form it contains swapped for each
        of that entry's other forms (longest match first), without repeats."""
        index: dict[str, list[Entry]] = {}
        for e in self.entries:
            if e.kind in EXPANDS:
                for form in e.forms:
                    index.setdefault(" ".join(form.casefold().split()), []).append(e)
        words = text.split()
        folded = [w.casefold() for w in words]
        out: list[tuple[str, Entry | None]] = [(text, None)]
        seen = {" ".join(folded)}
        for size in range(min(len(words), MAX_WORDS), 0, -1):
            for i in range(len(words) - size + 1):
                phrase = " ".join(folded[i : i + size])
                for entry in index.get(phrase, []):
                    for form in entry.forms:
                        variant = " ".join([*words[:i], form, *words[i + size :]])
                        key = " ".join(variant.casefold().split())
                        if key not in seen:
                            seen.add(key)
                            out.append((variant, entry))
        return out[:MAX_VARIANTS]
