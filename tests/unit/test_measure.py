"""WO-2.5: the note-format token re-measurement (gos vault measure) and gos vault maintain,
with a fake token counter: no test calls the API."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gregtec import cli
from gregtec.vault import measure, schema

TEMPLATE = Path(__file__).resolve().parents[2] / "vault-template"
TODAY = dt.date(2026, 11, 6)
TABLE_NOTE = (
    "---\nid: pl-1\nt: plan\nupd: 2026-11-06\n---\nst: draft\nnote: a, b\nnote: c: d\n\n"
    "```tsv\nref\tmachine\teut\nr:a/1\tEBF, LV\t120\nr:b/2\tMacerator\t2\n```\n"
)


def fake_count(model: str, text: str) -> int:
    """About one token per 4 characters, +3 for the message; a bit more for Opus."""
    return 3 + (len(text) + 3) // 4 + (len(text) // 40 if "opus" in model else 0)


def test_every_format_keeps_the_content() -> None:
    note = schema.parse(TABLE_NOTE)
    out = {name: render(note) for name, render in measure.FORMATS.items()}
    assert out["gos"] == TABLE_NOTE
    assert schema.parse(out["gos"]).tables == note.tables
    data = json.loads(out["json"])
    assert data["note"] == ["a, b", "c: d"]
    assert data["tables"] == [
        [
            {"ref": "r:a/1", "machine": "EBF, LV", "eut": "120"},
            {"ref": "r:b/2", "machine": "Macerator", "eut": "2"},
        ]
    ]
    assert json.loads(out["json_compact"]) == data
    assert (
        "table1[2]{ref,machine,eut}:\n  r:a/1," not in out["toon"]
    )  # a colon is quoted
    assert 'table1[2]{ref,machine,eut}:\n  "r:a/1","EBF, LV",120\n' in out["toon"]
    assert 'note[2]: "a, b","c: d"' in out["toon"]
    assert '"EBF, LV"' in out["csv"]
    assert (
        "| ref | machine | eut |\n|---|---|---|\n| r:a/1 | EBF, LV | 120 |"
        in out["markdown"]
    )
    assert "ref: r:a/1; machine: EBF, LV; eut: 120\n" in out["kv_rows"]
    assert (
        '    - ref: "r:a/1"\n      machine: "EBF, LV"\n      eut: 120\n' in out["yaml"]
    )


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            "CREATE TABLE recipe_map(id INTEGER PRIMARY KEY, unloc_name TEXT);"
            "CREATE TABLE recipe(id INTEGER PRIMARY KEY, recipe_map_id INT, signature TEXT,"
            " eut INT, duration_ticks INT, voltage_tier INT);"
            "CREATE TABLE item(id INTEGER PRIMARY KEY, registry_name TEXT, meta INT,"
            " name_en TEXT, mod_id TEXT);"
            "INSERT INTO recipe_map VALUES (1, 'gt.recipe.blastfurnace');"
            "INSERT INTO recipe VALUES (1, 1, 'abc', 120, 1000, 2);"
            "INSERT INTO item VALUES (1, 'gregtech:gt.metaitem.01', 11305, 'Steel Ingot', 'gregtech');"
        )
        conn.commit()
    finally:
        conn.close()


def test_samples(tmp_path: Path) -> None:
    shutil.copytree(TEMPLATE, tmp_path / "vault")
    (tmp_path / "vault" / "plans" / "broken.md").write_text("prose", encoding="utf-8")
    make_db(tmp_path / "gos.sqlite")
    names = [
        s.name for s in measure.samples(tmp_path / "vault", tmp_path / "gos.sqlite")
    ]
    assert names == [  # never me/ (D142 (d))
        "plan 6.2 example",
        "INDEX.md",
        "_gos/schema.md",
        "db:recipes(60)",
        "db:items(120)",
    ]
    assert [s.name for s in measure.samples(None, tmp_path / "missing.sqlite")] == [
        "plan 6.2 example"
    ]


def test_measure_subtracts_the_overhead_and_gives_the_verdict() -> None:
    items = [measure.Sample("t", schema.parse(TABLE_NOTE))]
    report = measure.measure(fake_count, items, ["claude-haiku-5-5"])
    assert report.overhead == {
        "claude-haiku-5-5": fake_count("claude-haiku-5-5", ".") - 1
    }
    gos = next(n for s, f, m, n in report.rows if f == "gos")
    assert (
        gos
        == fake_count("x", measure.render_gos(items[0].note))
        - report.overhead["claude-haiku-5-5"]
    )
    totals = report.totals()
    assert totals["json", "claude-haiku-5-5"] > totals["gos", "claude-haiku-5-5"]
    verdict, ratio = report.verdict("claude-haiku-5-5")
    assert verdict == ("keep" if ratio <= 1.10 else "revisit")


def test_the_verdict_rule() -> None:
    def report(gos: int, best: int) -> measure.Report:
        rows = [("s", f, "m", 1000) for f in measure.FORMATS]
        rows = [
            (s, f, m, gos if f == "gos" else best if f == "toon" else n)
            for s, f, m, n in rows
        ]
        return measure.Report(rows, {"m": 0})

    assert report(110, 100).verdict("m") == ("keep", 1.1)
    assert report(111, 100).verdict("m")[0] == "revisit"
    assert report(90, 100).verdict("m") == ("keep", 1.0)


def test_gos_vault_measure_writes_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(TEMPLATE, tmp_path / "vault")
    code = measure.main(
        tmp_path,
        ["claude-haiku-5-5", "claude-opus-5-5"],
        True,
        sys.stdout,
        counter=fake_count,
        today=TODAY,
    )
    assert code == 0
    report = tmp_path / "data" / "reports" / "note-formats-20261106.txt"
    text = report.read_text(encoding="utf-8")
    assert text.startswith("measured: 2026-11-06\n")
    assert "verdict_claude_haiku_5_5: " in text and "verdict_claude_opus_5_5: " in text
    table = text.split("\nsample\tformat\tmodel\ttokens\n", 1)[1].splitlines()
    assert len(table) == 3 * len(measure.FORMATS) * 2  # 3 samples, 8 formats, 2 models
    out = capsys.readouterr().out
    assert "format\tmodel\ttotal_tokens\tvs_json_pct" in out and str(report) in out


def test_gos_vault_measure_without_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from gregtec.llm import keys

    def missing(name: str) -> str:
        raise keys.MissingKeyError(name)

    monkeypatch.setattr(keys, "get_key", missing)
    monkeypatch.setenv("GOS_HOME", str(tmp_path))
    assert cli.main(["vault", "measure", "--no-db"]) == 1
    assert "gos key set anthropic" in capsys.readouterr().out


def test_api_counter_uses_count_tokens() -> None:
    seen: dict[str, object] = {}

    def count_tokens(**kwargs: object) -> SimpleNamespace:
        seen.update(kwargs)
        return SimpleNamespace(input_tokens=17)

    client = SimpleNamespace(messages=SimpleNamespace(count_tokens=count_tokens))
    assert measure.api_counter(client)("claude-haiku-5-5", "st: x") == 17  # type: ignore[arg-type]
    assert seen == {
        "model": "claude-haiku-5-5",
        "messages": [{"role": "user", "content": "st: x"}],
    }


def test_gos_vault_maintain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from gregtec.vault import deploy

    deploy.deploy(tmp_path / "vault", TEMPLATE)
    monkeypatch.setenv("GOS_HOME", str(tmp_path))
    assert cli.main(["vault", "maintain", "--job", "index", "--job", "fts"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "index\tok\trebuilt",
        "fts\tok\t4 notes",
    ]
    assert cli.main(["vault", "maintain", "--job", "backup"]) == 0
    assert (tmp_path / "data" / "backups").is_dir()
    capsys.readouterr()

    from gregtec.store import Store

    store = Store(tmp_path / "data" / "gos.sqlite")
    try:
        store.rows(
            "INSERT INTO kv(key, value, upd) VALUES ('maintenance.last_day', 'x', 0)"
        )
        assert cli.main(["vault", "maintain", "--rerun-today"]) == 0
        assert "due again" in capsys.readouterr().out
        assert store.rows("SELECT * FROM kv WHERE key = 'maintenance.last_day'") == []
    finally:
        store.close()


# --- review fixes: retries, a partial report, each kind's share ----------------------------


def test_retrying_waits_then_gives_up() -> None:
    calls: list[str] = []
    slept: list[float] = []

    def flaky(model: str, text: str) -> int:
        calls.append(text)
        if len(calls) < 3:
            raise ConnectionError("429")
        return 7

    count = measure.retrying(
        flaky, lambda e: isinstance(e, ConnectionError), (1.0, 2.0, 4.0), slept.append
    )
    assert count("m", "x") == 7 and slept == [1.0, 2.0]

    def broken(model: str, text: str) -> int:
        raise ConnectionError("down")

    slept.clear()
    with pytest.raises(ConnectionError):
        measure.retrying(broken, lambda e: True, (1.0, 2.0), slept.append)("m", "x")
    assert slept == [1.0, 2.0]

    def bad(model: str, text: str) -> int:
        raise ValueError("bad request")

    slept.clear()
    with pytest.raises(ValueError):  # not retryable: raised at once
        measure.retrying(bad, lambda e: False, (1.0,), slept.append)("m", "x")
    assert slept == []


def test_a_failed_count_writes_the_rows_so_far(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(TEMPLATE, tmp_path / "vault")
    made = 0

    def fails_late(model: str, text: str) -> int:
        nonlocal made
        made += 1
        if made > 20:
            raise RuntimeError("429 rate limited")
        return fake_count(model, text)

    code = measure.main(
        tmp_path, ["claude-haiku-5-5"], False, sys.stdout, fails_late, TODAY
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "count_tokens failed: 429 rate limited" in out
    partial = tmp_path / "data" / "reports" / "note-formats-20261106.partial.txt"
    lines = partial.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("partial: 19 counts")  # 1 overhead call, then 19 rows
    assert len(lines) == 2 + 19
    assert not (tmp_path / "data" / "reports" / "note-formats-20261106.txt").exists()


def test_the_report_gives_each_kinds_share(tmp_path: Path) -> None:
    shutil.copytree(TEMPLATE, tmp_path / "vault")
    make_db(tmp_path / "gos.sqlite")
    items = measure.samples(tmp_path / "vault", tmp_path / "gos.sqlite")
    report = measure.measure(fake_count, items, ["claude-haiku-5-5"])
    text = measure.render_report(report, ["claude-haiku-5-5"], TODAY)
    head = text.split("\n\nformat\t", 1)[0]
    section = head.split("kind\tmodel\tsamples\t", 1)[1].splitlines()[1:]
    rows = {line.split("\t")[0]: line.split("\t") for line in section}
    assert {k: r[2] for k, r in rows.items()} == {
        "db tables": "2",
        "plan example": "1",
        "vault notes": "2",
    }
    assert round(sum(float(r[4]) for r in rows.values())) == 100


def test_measure_never_reads_or_sends_me(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D142 (d): no text of Clayton's me/ notes reaches the counter, not even the summary
    INDEX.md copies from them; me/ files are never opened."""
    from gregtec.vault import deploy, index

    root = tmp_path / "vault"
    deploy.deploy(root, TEMPLATE)
    secret = "zebra-quokka-secret"
    (root / "me" / "goals.md").write_text(
        f"---\nid: me-goals\nt: goals\nupd: 2026-11-06\n---\ngoal: {secret} one\n",
        encoding="utf-8",
    )
    (root / "me" / "notes").mkdir()
    (root / "me" / "notes" / "x.md").write_text(
        f"---\nid: x\nt: prefs\nupd: 2026-11-06\n---\nnote: {secret} two\n",
        encoding="utf-8",
    )
    index.build(root, TODAY)
    index_text = (root / "INDEX.md").read_text(encoding="utf-8")
    assert (
        f"{secret} one" in index_text
    )  # INDEX.md copies it: measure must leave it out
    opened: list[str] = []
    real_read = Path.read_text

    def read_text(self: Path, *args: object, **kwargs: object) -> str:
        opened.append(
            self.relative_to(root).as_posix() if self.is_relative_to(root) else ""
        )
        return real_read(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", read_text)
    sent: list[str] = []

    def counter(model: str, text: str) -> int:
        sent.append(text)
        return fake_count(model, text)

    code = measure.main(
        tmp_path, ["claude-haiku-5-5"], False, sys.stdout, counter=counter, today=TODAY
    )
    assert code == 0
    assert not [p for p in opened if p.startswith("me/")]
    assert sent and not [t for t in sent if secret in t or "me/" + "goals" in t]
    index_sent = [t for t in sent if "_gos/schema.md\tnote types" in t]
    assert index_sent  # INDEX.md itself is still measured, without its me/ rows
