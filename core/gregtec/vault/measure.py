"""``gos vault measure``: re-measure the note formats with Claude's own tokenizer (WO-2.5, plan 6.3).

Plan 6.3's numbers (TSV 64-66% smaller than pretty JSON, TOON 61-64%, YAML about 24% [D§3.2])
came from OpenAI tokenizers. This counts the same candidates with the Messages API's
``count_tokens`` endpoint (free; no message is sent to a model) for each model that reads notes,
on:

- every note in the live vault that follows the grammar (``INDEX.md``, ``_gos/schema.md``, ...);
- the plan 6.2 example line note;
- two tables from ``gos.sqlite`` when it has an export run: 60 recipes and 120 items, the shape
  of tool results and of bulk tables (their values are only counted, never written anywhere).

Each sample is parsed (:func:`schema.parse`) and rendered in every candidate format with the same
content: :data:`FORMATS`. A count includes the API's fixed overhead for one user message, which
is measured once (a one-character message, less one token) and subtracted.

The report (``GOS_HOME/data/reports/note-formats-YYYYMMDD.txt``) has one row per sample, format
and model, the totals per format and model, and the verdict of the rule D141 fixed in advance:
the grammar (``gos``: ``key: value`` records, TSV tables) is frozen if its total is within 10% of
the smallest total, else plan 6.3 is revisited.
"""

from __future__ import annotations

import csv
import datetime as dt
import io as textio
import json
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from gregtec.vault import io, schema

if TYPE_CHECKING:
    import anthropic

MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
BASELINE = "json"  # plan 6.3 compares against pretty JSON
KEEP_WITHIN = 0.10  # D141: the grammar stays if its total is within 10% of the smallest
REPORTS = Path("data", "reports")
# Plan 6.2's example line note, as the plan gives it.
PLAN_EXAMPLE = """---
id: ln-steel-01
t: line
upd: 2026-11-01
---
st: running
tgt: i:steel_ingot
tgt_rate_s: 2
plan: pl-0007
eut: 1920
tier: HV
deps: [[pw-main]], [[ln-o2-01]]
note: 4 EBF cupronickel; o2 from electrolyzer line
"""
DB_SAMPLES = {
    "db:recipes(60)": (
        "SELECT r.signature AS sig, m.unloc_name AS map, r.eut, r.duration_ticks AS dur_t, "
        "r.voltage_tier AS tier FROM recipe r LEFT JOIN recipe_map m ON m.id = r.recipe_map_id "
        "ORDER BY r.id LIMIT 60"
    ),
    "db:items(120)": (
        "SELECT registry_name AS registry, meta, name_en AS name, mod_id AS mod FROM item "
        "ORDER BY id LIMIT 120"
    ),
}

Counter = Callable[[str, str], int]  # (model, text) -> input tokens


# --- rendering ------------------------------------------------------------------------------


def _facts_dict(note: schema.Note) -> dict[str, Any]:
    out: dict[str, Any] = dict(note.meta)
    for key, value in note.facts:
        if key in out:
            out[key] = (
                [*out[key], value] if isinstance(out[key], list) else [out[key], value]
            )
        else:
            out[key] = value
    return out


def _records(table: list[list[str]]) -> list[dict[str, str]]:
    header, *rows = table
    return [dict(zip(header, row, strict=True)) for row in rows]


def _structure(note: schema.Note) -> dict[str, Any]:
    data = _facts_dict(note)
    if note.tables:
        data["tables"] = [_records(t) for t in note.tables]
    return data


def _frontmatter(note: schema.Note) -> str:
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in note.meta.items()) + "---\n"


def _kv(note: schema.Note) -> str:
    return "".join(f"{k}: {v}\n" for k, v in note.facts)


def render_gos(note: schema.Note) -> str:
    """The grammar (plan 6.2): frontmatter, ``key: value`` facts, TSV fences."""
    tables = "".join(
        "\n```tsv\n" + "".join("\t".join(r) + "\n" for r in t) + "```\n"
        for t in note.tables
    )
    return _frontmatter(note) + _kv(note) + tables


def render_csv(note: schema.Note) -> str:
    out = _frontmatter(note) + _kv(note)
    for t in note.tables:
        buf = textio.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(t)
        out += "\n```csv\n" + buf.getvalue() + "```\n"
    return out


def render_markdown(note: schema.Note) -> str:
    out = _frontmatter(note) + _kv(note)
    for t in note.tables:
        header, *rows = t
        out += "\n| " + " | ".join(header) + " |\n|" + "---|" * len(header) + "\n"
        out += "".join("| " + " | ".join(r) + " |\n" for r in rows)
    return out


def render_kv_rows(note: schema.Note) -> str:
    """Tables as one ``key: value`` line per record (M1's "key:value line per record")."""
    out = _frontmatter(note) + _kv(note)
    for t in note.tables:
        out += "\n" + "".join(
            "; ".join(f"{k}: {v}" for k, v in rec.items()) + "\n" for rec in _records(t)
        )
    return out


def render_json(note: schema.Note) -> str:
    return json.dumps(_structure(note), indent=2, ensure_ascii=False) + "\n"


def render_json_compact(note: schema.Note) -> str:
    return (
        json.dumps(_structure(note), separators=(",", ":"), ensure_ascii=False) + "\n"
    )


_YAML_PLAIN = re.compile(r"[A-Za-z0-9_./()+\-][A-Za-z0-9_ ./()+\-]*")


def _yaml_scalar(value: str) -> str:
    if (
        _YAML_PLAIN.fullmatch(value)
        and not value.endswith(" ")
        and value.lower()
        not in (
            "yes",
            "no",
            "true",
            "false",
            "null",
            "on",
            "off",
        )
    ):
        return value
    return json.dumps(value, ensure_ascii=False)


def render_yaml(note: schema.Note) -> str:
    lines = []
    for key, value in _facts_dict(note).items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines += [f"  - {_yaml_scalar(v)}" for v in value]
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    if note.tables:
        lines.append("tables:")
        for t in note.tables:
            lines.append("  -")
            for rec in _records(t):
                first = True
                for k, v in rec.items():
                    lines.append(f"    {'- ' if first else '  '}{k}: {_yaml_scalar(v)}")
                    first = False
    return "\n".join(lines) + "\n"


_TOON_SAFE = re.compile(r"[^,:\"\\\[\]{}\n\t]*")


def _toon_scalar(value: str) -> str:
    if value and _TOON_SAFE.fullmatch(value) and value == value.strip():
        return value
    return json.dumps(value, ensure_ascii=False)


def render_toon(note: schema.Note) -> str:
    """TOON (its README's format, re-implemented for this subset): ``key: value`` scalars,
    ``key[N]: a,b`` lists and ``name[N]{f1,f2}:`` uniform tables with comma rows."""
    lines = []
    for key, value in _facts_dict(note).items():
        if isinstance(value, list):
            lines.append(
                f"{key}[{len(value)}]: " + ",".join(_toon_scalar(v) for v in value)
            )
        else:
            lines.append(f"{key}: {_toon_scalar(value)}")
    for n, t in enumerate(note.tables, 1):
        header, *rows = t
        lines.append(f"table{n}[{len(rows)}]{{{','.join(header)}}}:")
        lines += ["  " + ",".join(_toon_scalar(v) for v in r) for r in rows]
    return "\n".join(lines) + "\n"


FORMATS: dict[str, Callable[[schema.Note], str]] = {
    "gos": render_gos,
    "csv": render_csv,
    "toon": render_toon,
    "markdown": render_markdown,
    "kv_rows": render_kv_rows,
    "json_compact": render_json_compact,
    "yaml": render_yaml,
    "json": render_json,
}


# --- samples --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    name: str
    note: schema.Note


def _table_note(
    name: str, columns: Sequence[str], rows: Sequence[Sequence[object]]
) -> schema.Note:
    def cell(v: object) -> str:
        return "" if v is None else " ".join(str(v).split())

    table = [list(columns), *[[cell(v) for v in r] for r in rows]]
    meta = {
        "id": name.split("(")[0].replace(":", "-"),
        "t": "sample",
        "upd": "2026-11-06",
    }
    return schema.Note(meta, [("src", name)], [table])


def samples(vault: Path | None, db: Path | None) -> list[Sample]:
    found = [Sample("plan 6.2 example", schema.parse(PLAN_EXAMPLE))]
    if vault is not None and vault.is_dir():
        for rel in io.notes(vault):
            try:
                found.append(
                    Sample(rel, schema.parse((vault / rel).read_text(encoding="utf-8")))
                )
            except (OSError, UnicodeDecodeError, ValueError):
                continue  # only notes that follow the grammar can be rendered faithfully
    if db is not None and db.is_file():
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
        try:
            for name, sql in DB_SAMPLES.items():
                try:
                    cur = conn.execute(sql)
                except sqlite3.Error:
                    continue
                rows = cur.fetchall()
                if rows:
                    columns = [d[0] for d in cur.description]
                    found.append(Sample(name, _table_note(name, columns, rows)))
        finally:
            conn.close()
    return found


# --- counting and the report ----------------------------------------------------------------


def api_counter(client: anthropic.Anthropic) -> Counter:
    def count(model: str, text: str) -> int:
        got = client.messages.count_tokens(
            model=model, messages=[{"role": "user", "content": text}]
        )
        return int(got.input_tokens)

    return count


@dataclass(frozen=True)
class Report:
    rows: list[tuple[str, str, str, int]]  # sample, format, model, tokens
    overhead: dict[str, int]  # model -> tokens of an empty user message

    def totals(self) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for _, fmt, model, n in self.rows:
            out[fmt, model] = out.get((fmt, model), 0) + n
        return out

    def verdict(self, model: str) -> tuple[str, float]:
        """(keep or revisit, gos's total over the smallest total) for one model."""
        totals = {f: n for (f, m), n in self.totals().items() if m == model}
        ratio = totals["gos"] / max(1, min(totals.values()))
        return ("keep" if ratio <= 1 + KEEP_WITHIN else "revisit"), ratio


def measure(
    count: Counter, items: Sequence[Sample], models: Sequence[str] = MODELS
) -> Report:
    overhead = {m: max(0, count(m, ".") - 1) for m in models}
    rows = []
    for sample in items:
        for fmt, render in FORMATS.items():
            text = render(sample.note)
            for model in models:
                rows.append(
                    (
                        sample.name,
                        fmt,
                        model,
                        max(0, count(model, text) - overhead[model]),
                    )
                )
    return Report(rows, overhead)


def render_report(report: Report, models: Sequence[str], today: dt.date) -> str:
    def clean(text: str) -> str:
        return " ".join(text.replace("\t", " ").split())

    lines = [
        f"measured: {today.isoformat()}",
        "counter: Messages API count_tokens, one user message, its fixed overhead subtracted",
        "overhead: " + ", ".join(f"{m} {n}" for m, n in report.overhead.items()),
        f"samples: {len({r[0] for r in report.rows})}",
        (
            f"rule: keep the grammar (gos) if its total is within {KEEP_WITHIN:.0%} of the "
            "smallest (D141)"
        ),
    ]
    for model in models:
        verdict, ratio = report.verdict(model)
        lines.append(
            f"verdict_{model.replace('-', '_')}: {verdict} (gos is {ratio:.3f}x the smallest)"
        )
    totals = report.totals()
    lines += ["", "format\tmodel\ttotal_tokens\tvs_json_pct"]
    for model in models:
        base = totals[BASELINE, model]
        for fmt in sorted(FORMATS, key=lambda f: totals[f, model]):
            pct = 100.0 * (totals[fmt, model] - base) / max(1, base)
            lines.append(f"{fmt}\t{model}\t{totals[fmt, model]}\t{pct:+.1f}")
    lines += ["", "sample\tformat\tmodel\ttokens"]
    lines += [f"{clean(s)}\t{f}\t{m}\t{n}" for s, f, m, n in report.rows]
    return "\n".join(lines) + "\n"


def main(
    gos_home: Path,
    models: Sequence[str],
    use_db: bool,
    out: TextIO,
    counter: Counter | None = None,
    today: dt.date | None = None,
) -> int:
    """0: measured and written; 1: the key or the API failed."""
    day = today or dt.date.today()
    items = samples(
        gos_home / "vault", gos_home / "data" / "gos.sqlite" if use_db else None
    )
    if counter is None:
        from gregtec.llm import keys

        try:
            key = keys.get_key(keys.ANTHROPIC)
        except keys.MissingKeyError as exc:
            print(exc, file=out)
            return 1
        import anthropic as sdk

        counter = api_counter(sdk.Anthropic(api_key=key, max_retries=8))
        try:
            report = measure(counter, items, models)
        except sdk.APIError as exc:
            print(f"count_tokens failed: {exc}", file=out)
            return 1
    else:
        report = measure(counter, items, models)
    text = render_report(report, models, day)
    target = gos_home / REPORTS / f"note-formats-{day:%Y%m%d}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    head = text.split("\n\nsample\t", 1)[0]
    print(head, file=out)
    print(f"\nreport: {target}", file=out)
    return 0
