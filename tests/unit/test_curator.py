"""WO-2.5: the curator's jobs (plan 6.6): INDEX.md, the schema check, log compaction (with a
fake Haiku), the note_fts mirror, VACUUM INTO backups and git commits."""

from __future__ import annotations

import datetime as dt
import sqlite3
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gregtec.llm.prices import HAIKU, Usage
from gregtec.llm.summarise import (
    HaikuSummariser,
    SummariserOff,
    Summary,
    SummaryError,
    checked_lines,
    count_lines,
    event_counts,
    source_text,
)
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec.vault import curator, deploy, index, schema

TEMPLATE = Path(__file__).resolve().parents[2] / "vault-template"
TODAY = dt.date(2026, 11, 6)  # a Friday, ISO week 45


def git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=vault, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    deploy.deploy(root, TEMPLATE)
    return root


def note(
    root: Path, rel: str, t: str, note_id: str, body: str, upd: str = "2026-11-01"
) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {note_id}\nt: {t}\nupd: {upd}\n---\n{body}", encoding="utf-8"
    )


def daily(root: Path, day: str, body: str) -> None:
    note(root, f"log/{day}.md", "log", f"log-{day}", body, day)


class FakeSummariser:
    def __init__(
        self, lines: Sequence[str] = ("ev: built 2 ebf",), fail: bool = False
    ) -> None:
        self.lines = list(lines)
        self.fail = fail
        self.calls: list[tuple[str, list[tuple[str, str]]]] = []
        self.counts: list[dict[str, int]] = []
        self.made = 0

    def __call__(self) -> FakeSummariser:  # the curator's factory: the keyring read
        self.made += 1
        return self

    def summarise(
        self,
        period: str,
        sources: Sequence[tuple[str, str]],
        counts: Mapping[str, int],
    ) -> Summary:
        self.calls.append((period, list(sources)))
        self.counts.append(dict(counts))
        if self.fail:
            raise SummaryError("no", Usage(input=10, output=2), HAIKU)
        return Summary(self.lines, HAIKU, Usage(input=100, output=20))


# --- INDEX.md ------------------------------------------------------------------------------


def test_index_from_the_template_follows_the_schema(vault: Path) -> None:
    assert index.build(vault, TODAY)
    text = (vault / "INDEX.md").read_text(encoding="utf-8")
    types = schema.load_types(vault / "_gos" / "schema.md")
    assert schema.check_text("INDEX.md", text, types) == []
    rows = [
        line.split("\t")
        for line in text.split("```tsv\n")[1].split("```")[0].splitlines()
    ]
    assert rows[0] == ["id", "t", "path", "summary", "upd"]
    assert [r[2] for r in rows[1:]] == ["_gos/schema.md", "me/goals.md", "me/prefs.md"]
    assert rows[1][3] == "note types and allowed keys"  # its summary: fact
    assert rows[2][3] == "Clayton's goals, one per line as goal: {text}; agents rea..."
    assert len(rows[1][3]) <= index.SUMMARY_MAX
    assert (
        "upd: 2026-11-12" in text
    )  # the newest row's date (the schema note's, WO-6.4)
    assert not index.build(
        vault, TODAY + dt.timedelta(days=3)
    )  # unchanged: not rewritten


def test_index_rows(vault: Path) -> None:
    note(
        vault,
        "plans/pl-1.md",
        "plan",
        "pl-1",
        "st: draft\nsummary: steel line\tfor HV\n",
    )
    note(vault, "base/power.md", "power", "base-power", "eut_net: 3120\n")
    note(vault, "quests/qs-a.md", "quest", "qs-a", "```tsv\na\tb\n```\n")
    daily(vault, "2026-11-05", "ev: x\n")
    (vault / "themes" / "broken.md").write_text("no frontmatter", encoding="utf-8")
    rows = {r.path: r for r in index.rows(vault)}
    assert "log/2026-11-05.md" not in rows and "INDEX.md" not in rows
    assert rows["plans/pl-1.md"].summary == "steel line for HV"
    assert rows["base/power.md"].summary == "eut_net: 3120"
    assert rows["quests/qs-a.md"].summary == ""
    assert (rows["themes/broken.md"].id, rows["themes/broken.md"].t) == ("broken", "?")


def test_a_folder_of_more_than_20_notes_is_one_line(vault: Path) -> None:
    for n in range(21):
        note(
            vault,
            f"plans/pl-{n:02d}.md",
            "plan",
            f"pl-{n:02d}",
            "st: x\n",
            f"2026-10-{n + 1:02d}",
        )
    for n in range(20):
        note(vault, f"quests/qs-{n:02d}.md", "quest", f"qs-{n:02d}", "st: x\n")
    rows = {r.path: r for r in index.rows(vault)}
    assert rows["plans/"] == index.Row(
        "plans", "plan", "plans/", "21 notes; vault_search finds one", "2026-10-21"
    )
    assert not any(p.startswith("plans/pl-") for p in rows)
    assert sum(p.startswith("quests/qs-") for p in rows) == 20


def test_the_index_stays_under_200_lines(vault: Path) -> None:
    for folder in range(12):
        for n in range(19):
            note(
                vault,
                f"plans/f{folder:02d}/pl-{n:02d}.md",
                "plan",
                f"pl-{n:02d}",
                "st: x\n",
            )
    index.build(vault, TODAY)
    text = (vault / "INDEX.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= index.MAX_LINES
    assert "plans/f00/\t" in text  # the biggest folders collapsed first (ties by name)
    types = schema.load_types(vault / "_gos" / "schema.md")
    assert schema.check_text("INDEX.md", text, types) == []


# --- schema check --------------------------------------------------------------------------


def test_schema_check_reports_to_one_inbox_note(vault: Path) -> None:
    report = vault / curator.SCHEMA_REPORT
    assert curator.check_schema(vault, TODAY).detail == "every note follows the schema"
    note(vault, "plans/pl-1.md", "plan", "pl-1", "This is prose.\n")
    note(vault, "plans/pl-2.md", "plan", "pl-9", "st: x\n")  # wrong id for its name
    note(vault, "base/lines/ln-1.md", "line", "ln-1", "colour: blue\n")
    note(vault, "plans/pl-5.md", "plan", "pl-5", "st: x\n")
    note(vault, "plans/old/pl-5.md", "plan", "pl-5", "st: x\n")  # the same id twice
    first = curator.check_schema(vault, TODAY)
    assert first.detail == f"5 problems, reported in {curator.SCHEMA_REPORT}"
    text = report.read_text(encoding="utf-8")
    types = schema.load_types(vault / "_gos" / "schema.md")
    assert schema.check_text(curator.SCHEMA_REPORT, text, types) == []
    assert (
        "plans/pl-1.md\tline 6 is not key: value or a tsv table: 'This is prose.'"
        in text
    )
    assert "plans/pl-2.md\tid must be pl-2 at plans/pl-2.md" in text
    assert "base/lines/ln-1.md\tkey colour isn't allowed for t: line" in text
    assert "plans/old/pl-5.md\tid pl-5 is also used by another note" in text
    assert (
        curator.check_schema(vault, TODAY + dt.timedelta(days=1)).detail
        == "5 problems, as before"
    )
    assert "upd: 2026-11-06" in report.read_text(encoding="utf-8")
    for rel in (
        "plans/pl-1.md",
        "plans/pl-2.md",
        "base/lines/ln-1.md",
        "plans/old/pl-5.md",
    ):
        (vault / rel).unlink()
    assert "report removed" in curator.check_schema(vault, TODAY).detail
    assert not report.exists()


# --- compaction ----------------------------------------------------------------------------


def test_daily_logs_compact_into_a_week_once_its_sunday_is_7_days_old(
    vault: Path,
) -> None:
    for day in ("2026-10-26", "2026-10-28", "2026-11-01"):  # ISO week 44, Sunday 11-01
        daily(vault, day, f"ev: {day} built 2 ebf\n")
    daily(vault, "2026-11-02", "ev: newer\n")  # week 45: not yet
    fake = FakeSummariser()
    assert (
        curator.compact(vault, dt.date(2026, 11, 7), fake).detail
        == "nothing old enough"
    )
    result = curator.compact(vault, dt.date(2026, 11, 8), fake)
    assert result == curator.JobResult("compact", "ok", "compacted 2026-W44")
    [(period, sources)] = fake.calls
    assert period == "2026-W44"
    assert [p for p, _ in sources] == [
        "log/2026-10-26.md",
        "log/2026-10-28.md",
        "log/2026-11-01.md",
    ]
    week = (vault / "log" / "wk" / "2026-W44.md").read_text(encoding="utf-8")
    assert week == (
        "---\nid: log-2026-W44\nt: log\nupd: 2026-11-08\n---\nperiod: 2026-W44\n"
        "from: 2026-10-26.md, 2026-10-28.md, 2026-11-01.md\nn_ev: 3\nev: built 2 ebf\n"
    )
    assert not (vault / "log" / "2026-10-26.md").exists()
    assert (vault / "log" / "2026-11-02.md").exists()


def test_weekly_logs_compact_into_a_month_after_8_weeks(vault: Path) -> None:
    for week in (
        40,
        41,
    ):  # Thursdays 2026-10-01 and 10-08: October; Sundays 10-04, 10-11
        note(
            vault,
            f"log/wk/2026-W{week}.md",
            "log",
            f"log-2026-W{week}",
            f"ev: week {week}\n",
        )
    note(vault, "log/wk/2026-W44.md", "log", "log-2026-W44", "ev: w44\n")
    fake = FakeSummariser(["ev: october"])
    assert (
        curator.compact(vault, dt.date(2026, 12, 5), fake).detail == "compacted 2026-10"
    )
    assert [p for p, _ in fake.calls[0][1]] == ["log/wk/2026-W40.md"]
    month = (vault / "log" / "mo" / "2026-10.md").read_text(encoding="utf-8")
    assert "id: log-2026-10\n" in month and "from: wk/2026-W40.md\n" in month
    # A week later W41 joins: the month's summary so far goes in with it.
    assert (
        curator.compact(vault, dt.date(2026, 12, 6), fake).detail == "compacted 2026-10"
    )
    assert [p for p, _ in fake.calls[1][1]] == [
        "log/mo/2026-10.md",
        "log/wk/2026-W41.md",
    ]
    assert not (vault / "log" / "wk" / "2026-W41.md").exists()
    month = (vault / "log" / "mo" / "2026-10.md").read_text(encoding="utf-8")
    assert "from: wk/2026-W40.md, wk/2026-W41.md\n" in month
    assert (vault / "log" / "wk" / "2026-W44.md").exists()


def test_compaction_waits_without_a_key_and_keeps_logs_on_failure(
    vault: Path, tmp_path: Path
) -> None:
    daily(vault, "2026-10-26", "ev: a\n")
    skipped = curator.compact(vault, TODAY + dt.timedelta(days=30), lambda: None)
    assert skipped.status == "skipped" and "gos key set anthropic" in skipped.detail
    store = Store(tmp_path / "gos.sqlite")
    try:
        meter = UsageMeter(store, 100.0, (80, 100))
        failed = curator.compact(
            vault, TODAY + dt.timedelta(days=30), FakeSummariser(fail=True), meter
        )
        assert failed.status == "failed" and "2026-W44: no" in failed.detail
        assert (vault / "log" / "2026-10-26.md").exists()
        assert not (vault / "log" / "wk").exists()
        # The failed call is still metered, as the maintenance lane.
        [row] = store.rows("SELECT lane, model, in_tok, out_tok, task FROM llm_usage")
        assert tuple(row) == ("maintenance", HAIKU, 10, 2, "log compaction 2026-W44")
    finally:
        store.close()


def test_a_summary_that_breaks_the_grammar_writes_nothing(vault: Path) -> None:
    daily(vault, "2026-10-26", "ev: a\n")
    bad = FakeSummariser(["colour me: x"] * 200)
    result = curator.compact(vault, TODAY + dt.timedelta(days=30), bad)
    assert result.status == "failed"
    assert (vault / "log" / "2026-10-26.md").exists()


# --- the summariser ------------------------------------------------------------------------


def test_checked_lines() -> None:
    source = source_text(
        "2026-W44", [("log/2026-10-26.md", "ev: built 2 ebf\nq: q:1234")]
    )
    assert checked_lines("```\nev: built 2 ebf\n\nq: q:1234 done\n```", source) == [
        "ev: built 2 ebf",
        "q: q:1234 done",
    ]
    for answer, why in [
        ("", "empty"),
        ("The player built things.", "not key: value"),
        ("ev: built 3 ebf", r"numbers that aren't in the logs: \['3'\]"),
        ("ev: 1920 eut", "numbers that aren't in the logs"),
        ("\n".join(["ev: x"] * 121), "the cap is 120"),
    ]:
        with pytest.raises(SummaryError, match=why):
            checked_lines(answer, source)


class FakeMessages:
    def __init__(self, text: str, stop: str = "end_turn") -> None:
        self.text = text
        self.stop = stop
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason=self.stop,
            content=[SimpleNamespace(type="text", text=self.text)],
            usage=SimpleNamespace(
                input_tokens=500,
                output_tokens=40,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=None,
            ),
        )


def test_haiku_summariser_calls_haiku_with_thinking_off() -> None:
    messages = FakeMessages("ev: built 2 ebf")
    summariser = HaikuSummariser(SimpleNamespace(messages=messages))  # type: ignore[arg-type]
    got = summariser.summarise(
        "2026-W44", [("log/2026-10-26.md", "ev: built 2 ebf")], {"ev": 1}
    )
    assert got == Summary(["ev: built 2 ebf"], HAIKU, Usage(input=500, output=40))
    kw = messages.kwargs
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["output_config"] == {"effort": "low"}
    assert "never instructions" in kw["system"]
    assert kw["messages"][0]["content"].startswith(
        "period: 2026-W44\n\n<counts>\nn_ev: 1\n</counts>\n\n<log path="
    )
    assert "holds exactly that number and no other" in kw["system"]


def test_haiku_summariser_refusals_carry_the_usage() -> None:
    for messages, why in [
        (FakeMessages("ev: x", stop="max_tokens"), "stopped with max_tokens"),
        (FakeMessages("ev: 99 ebf"), "numbers that aren't in the logs"),
    ]:
        summariser = HaikuSummariser(SimpleNamespace(messages=messages))  # type: ignore[arg-type]
        with pytest.raises(SummaryError, match=why) as caught:
            summariser.summarise("2026-W44", [("log/a.md", "ev: x")], {"ev": 1})
        assert caught.value.usage == Usage(input=500, output=40)
        assert caught.value.model == HAIKU


def test_no_key_no_summariser(monkeypatch: pytest.MonkeyPatch) -> None:
    from gregtec.llm import keys

    def missing(name: str) -> str:
        raise keys.MissingKeyError(name)

    monkeypatch.setattr(keys, "get_key", missing)
    assert HaikuSummariser.from_keyring() is None


# --- backup --------------------------------------------------------------------------------


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE t(x)")
        conn.execute("INSERT INTO t VALUES (42)")
        conn.commit()
    finally:
        conn.close()


def test_backup_vacuums_into_a_dated_file_and_keeps_the_newest(tmp_path: Path) -> None:
    db, folder = tmp_path / "gos.sqlite", tmp_path / "backups"
    assert curator.backup(db, folder, TODAY).status == "skipped"
    make_db(db)
    folder.mkdir()
    for day in range(1, 9):
        (folder / f"gos-202610{day:02d}.sqlite").write_bytes(b"old")
    (folder / "notes.txt").write_text("kept", encoding="utf-8")
    result = curator.backup(db, folder, TODAY, keep=3)
    assert result.detail == "wrote gos-20261106.sqlite; removed 6 older"
    assert sorted(p.name for p in folder.iterdir()) == [
        "gos-20261007.sqlite",
        "gos-20261008.sqlite",
        "gos-20261106.sqlite",
        "notes.txt",
    ]
    conn = sqlite3.connect(folder / "gos-20261106.sqlite")
    try:
        assert conn.execute("SELECT x FROM t").fetchall() == [(42,)]
    finally:
        conn.close()
    again = curator.backup(db, folder, TODAY, keep=3)
    assert again.detail == "gos-20261106.sqlite already exists"


# --- git -----------------------------------------------------------------------------------


def test_commit_agent_writes_leaves_me_and_gos_alone(vault: Path) -> None:
    assert curator.commit_agent_writes(vault, "nothing") is None
    note(vault, "plans/pl-1.md", "plan", "pl-1", "st: x\n")
    (vault / "me" / "goals.md").write_text("changed by Clayton", encoding="utf-8")
    (vault / "_gos" / "schema.md").write_text("changed", encoding="utf-8")
    (vault / "INDEX.md").unlink()
    sha = curator.commit_agent_writes(vault, "GOS: agent writes")
    assert sha and git(vault, "log", "-1", "--format=%s %an").strip() == (
        "GOS: agent writes Gregtec OS"
    )
    changed = git(vault, "show", "--name-status", "--format=", "HEAD").split()
    assert changed == ["D", "INDEX.md", "A", "plans/pl-1.md"]
    status = git(vault, "status", "--porcelain").splitlines()
    assert sorted(status) == [" M _gos/schema.md", " M me/goals.md"]
    assert curator.commit_agent_writes(vault, "again") is None


def test_commit_needs_a_git_vault(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="isn't a git repo"):
        curator.commit_agent_writes(tmp_path, "x")


# --- the whole run -------------------------------------------------------------------------


def test_the_run_does_every_job_in_order(vault: Path, tmp_path: Path) -> None:
    db = tmp_path / "data" / "gos.sqlite"
    daily(vault, "2026-10-26", "ev: built 2 ebf\n")
    note(vault, "plans/pl-1.md", "plan", "pl-1", "st: draft\n")
    fake = FakeSummariser()
    results = curator.Curator(
        vault, db, tmp_path / "data" / "backups", lambda: fake
    ).run(TODAY)
    assert [(r.job, r.status) for r in results] == [
        ("compact", "ok"),
        ("quests", "skipped"),  # no quests loaded (WO-6.4)
        ("schema", "ok"),
        ("index", "ok"),
        ("fts", "ok"),
        ("backup", "ok"),
        ("git", "ok"),
    ]
    assert (
        results[4].detail == "6 notes"
    )  # INDEX, schema, goals, prefs, pl-1, W44 less log
    assert "plans/pl-1.md" in (vault / "INDEX.md").read_text(encoding="utf-8")
    assert (tmp_path / "data" / "backups" / "gos-20261106.sqlite").is_file()
    assert (
        git(vault, "log", "-1", "--format=%s").strip()
        == "GOS idle maintenance 2026-11-06"
    )
    assert git(vault, "status", "--porcelain") == ""


def test_one_failed_job_doesnt_stop_the_others(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(root: Path, today: dt.date) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(index, "build", broken)
    results = curator.Curator(vault, tmp_path / "gos.sqlite", tmp_path / "b").run(
        TODAY, ["index", "schema"]
    )
    assert [(r.job, r.status) for r in results] == [
        ("schema", "ok"),  # the schema report comes first, so INDEX.md lists it
        ("index", "failed"),
    ]
    assert results[1].detail == "OSError: disk full"
    with pytest.raises(ValueError, match="unknown jobs"):
        curator.Curator(vault, tmp_path / "gos.sqlite", tmp_path / "b").run(
            TODAY, ["nope"]
        )


def test_no_vault_skips_the_vault_jobs(tmp_path: Path) -> None:
    results = curator.Curator(
        tmp_path / "vault", tmp_path / "gos.sqlite", tmp_path / "b"
    ).run(TODAY)
    assert {r.job: r.status for r in results} == {
        "compact": "skipped",
        "quests": "skipped",
        "index": "skipped",
        "schema": "skipped",
        "fts": "skipped",
        "backup": "skipped",
        "git": "skipped",
    }


# --- review fixes: holds, credits, the key read, stop, leftovers ---------------------------


@pytest.fixture
def meter(tmp_path: Path) -> Iterator[UsageMeter]:
    store = Store(tmp_path / "meter.sqlite")
    yield UsageMeter(store, 100.0, (80, 100))
    store.close()


LATER = TODAY + dt.timedelta(days=30)


def test_a_period_refused_3_times_is_held_for_clayton(
    vault: Path, meter: UsageMeter
) -> None:
    daily(vault, "2026-10-26", "death: creeper\ndeath: lava\ndeath: fall\n")
    counting = FakeSummariser(["deaths: 3"], fail=True)
    for n in (1, 2, 3):
        result = curator.compact(vault, LATER, counting, meter)
        assert result.status == "failed", n
    assert len(counting.calls) == 3
    held = (vault / curator.HELD_NOTE).read_text(encoding="utf-8")
    assert "id: ib-compact-held\nt: inbox\n" in held
    assert "2026-W44\t3\tno\n" in held
    assert not schema.check_text(
        curator.HELD_NOTE, held, schema.load_types(vault / "_gos" / "schema.md")
    )
    # The next daily run makes no call: the logs wait for Clayton.
    result = curator.compact(vault, LATER, counting, meter)
    assert result.status == "skipped" and "2026-W44 held after 3" in result.detail
    assert len(counting.calls) == 3 and counting.made == 3
    assert (vault / "log" / "2026-10-26.md").exists()
    # His own run (gos vault maintain) tries again; a good summary clears the hold.
    good = FakeSummariser(["death: creeper"])
    result = curator.compact(vault, LATER, good, meter, retry_held=True)
    assert result == curator.JobResult("compact", "ok", "compacted 2026-W44")
    assert not (vault / curator.HELD_NOTE).exists()
    assert meter.store.rows("SELECT key FROM kv WHERE key LIKE 'maintenance.%'") == []


def test_spent_credits_skip_compaction(vault: Path, meter: UsageMeter) -> None:
    daily(vault, "2026-10-26", "ev: a\n")
    meter.record("deep", HAIKU, Usage(), usd=100.0)
    fake = FakeSummariser()
    result = curator.compact(vault, LATER, fake, meter)
    assert (
        result.status == "skipped" and "reached the 80% credit alert" in result.detail
    )
    assert fake.made == 0 and fake.calls == []


def test_the_key_is_read_only_when_a_period_is_due(vault: Path) -> None:
    import keyring.errors

    fake = FakeSummariser()
    assert curator.compact(vault, TODAY, fake).detail == "nothing old enough"
    assert fake.made == 0

    def locked() -> None:
        raise keyring.errors.KeyringLocked("locked")

    daily(vault, "2026-10-26", "ev: a\n")
    result = curator.compact(vault, LATER, locked)
    assert result.status == "skipped" and "can't be read (locked)" in result.detail


def test_numbers_compare_by_value() -> None:
    source = "built: 06 boilers\nrate: 2.50"
    assert checked_lines("built: 6 boilers\nrate: 2.5", source) == [
        "built: 6 boilers",
        "rate: 2.5",
    ]
    with pytest.raises(SummaryError, match=r"\['3'\]"):  # a count is computing
        checked_lines("deaths: 3", "death: a\ndeath: b\ndeath: c")


def test_a_stop_skips_the_rest(vault: Path, tmp_path: Path) -> None:
    for day in ("2026-10-19", "2026-10-26"):  # weeks 43 and 44
        daily(vault, day, "ev: a\n")
    asked = []

    def stop() -> bool:
        asked.append(1)
        return len(asked) > 2  # compact's first period runs, then the stop comes

    results = curator.Curator(
        vault, tmp_path / "gos.sqlite", tmp_path / "b", FakeSummariser()
    ).run(LATER, stop=stop)
    assert (
        results[0].job == "compact" and "stopped before the rest" in results[0].detail
    )
    assert "compacted 2026-W43" in results[0].detail
    assert [(r.status, r.detail) for r in results[1:]] == [("skipped", "stopped")] * 6
    assert (vault / "log" / "2026-10-26.md").exists()


def test_a_run_killed_after_the_summary_lists_its_sources_once(vault: Path) -> None:
    daily(vault, "2026-10-26", "ev: a\n")
    note(
        vault,
        "log/wk/2026-W44.md",
        "log",
        "log-2026-W44",
        "period: 2026-W44\nfrom: 2026-10-26.md\nev: a\n",
    )
    curator.compact(vault, LATER, FakeSummariser(["ev: a"]))
    week = (vault / "log" / "wk" / "2026-W44.md").read_text(encoding="utf-8")
    assert "from: 2026-10-26.md\n" in week


def test_a_stale_backup_tmp_is_removed(tmp_path: Path) -> None:
    db, folder = tmp_path / "gos.sqlite", tmp_path / "backups"
    make_db(db)
    folder.mkdir()
    (folder / "gos-20261001.sqlite.tmp").write_bytes(b"half")
    curator.backup(db, folder, TODAY)
    assert sorted(p.name for p in folder.iterdir()) == ["gos-20261106.sqlite"]


def test_git_never_commits_a_killed_writes_temp_file(vault: Path) -> None:
    note(vault, "plans/pl-1.md", "plan", "pl-1", "st: x\n")
    (vault / "plans" / ".pl-2.md.ab12cd34.gos-tmp").write_text("x", encoding="utf-8")
    (vault / ".INDEX.md.ab12cd34.gos-tmp").write_text("x", encoding="utf-8")
    assert curator.commit_agent_writes(vault, "GOS: agent writes")
    assert git(vault, "show", "--name-only", "--format=", "HEAD").split() == [
        "plans/pl-1.md"
    ]
    assert "*.gos-tmp" in (TEMPLATE / ".gitignore").read_text(encoding="utf-8")


def test_index_lists_the_schema_report_the_same_day(
    vault: Path, tmp_path: Path
) -> None:
    note(vault, "plans/pl-1.md", "plan", "pl-1", "Not a fact.\n")
    curator.Curator(vault, tmp_path / "gos.sqlite", tmp_path / "b").run(
        TODAY, ["schema", "index"]
    )
    assert curator.SCHEMA_REPORT in (vault / "INDEX.md").read_text(encoding="utf-8")


# --- D142 (e): compaction stops at the first credit alert -----------------------------------


def test_compaction_stops_at_the_80_pct_credit_alert(
    vault: Path, meter: UsageMeter
) -> None:
    daily(vault, "2026-10-26", "ev: a\n")
    meter.record("deep", HAIKU, Usage(), usd=79.0)
    fake = FakeSummariser(["ev: a"])
    assert curator.compact(vault, LATER, fake, meter).status == "ok"  # 79%: runs
    daily(vault, "2026-10-19", "ev: a\n")
    meter.record("deep", HAIKU, Usage(), usd=1.0)
    result = curator.compact(vault, LATER, fake, meter)
    assert result.status == "skipped"
    assert "reached the 80% credit alert" in result.detail
    assert len(fake.calls) == 1 and fake.made == 1  # no key read, no call at 80%
    assert (vault / "log" / "2026-10-19.md").exists()


def test_the_stop_is_the_first_of_credit_alerts_pct(
    vault: Path, tmp_path: Path
) -> None:
    store = Store(tmp_path / "alerts.sqlite")
    try:
        for alerts, stops in (((100, 50), True), ((60, 100), False), ((), False)):
            m = UsageMeter(store, 100.0, alerts)
            assert curator.stop_pct(m) == (min(alerts) if alerts else 80)
            daily(vault, "2026-10-26", "ev: a\n")
            if not store.rows("SELECT 1 FROM llm_usage"):
                m.record("deep", HAIKU, Usage(), usd=55.0)
            fake = FakeSummariser(["ev: a"])
            result = curator.compact(vault, LATER, fake, m)
            assert (result.status == "skipped") is stops, alerts
            (vault / "log" / "wk" / "2026-W44.md").unlink(missing_ok=True)
    finally:
        store.close()


def test_compaction_stops_between_periods_once_the_alert_is_reached(
    vault: Path, meter: UsageMeter
) -> None:
    for day in ("2026-10-19", "2026-10-26"):  # weeks 43 and 44
        daily(vault, day, "ev: a\n")

    class Spending(FakeSummariser):
        def summarise(
            self,
            period: str,
            sources: Sequence[tuple[str, str]],
            counts: Mapping[str, int],
        ) -> Summary:
            meter.record("deep", HAIKU, Usage(), usd=80.0)  # the lanes spend meanwhile
            return super().summarise(period, sources, counts)

    fake = Spending(["ev: a"])
    result = curator.compact(vault, LATER, fake, meter)
    assert [p for p, _ in fake.calls] == ["2026-W43"]
    assert result.status == "ok"
    assert result.detail.startswith("compacted 2026-W43; stopped before the rest: ")
    assert "80% credit alert" in result.detail
    assert (vault / "log" / "2026-10-26.md").exists()


def test_no_ai_mode_skips_compaction_or_stops_it_between_periods(
    vault: Path,
) -> None:
    for day in ("2026-10-19", "2026-10-26"):  # weeks 43 and 44
        daily(vault, day, "ev: a\n")

    def off() -> FakeSummariser:
        raise SummariserOff("the AI is off (paused for credits)")

    skipped = curator.compact(vault, LATER, off)
    assert skipped.status == "skipped"
    assert skipped.detail == "the AI is off (paused for credits): the logs wait"

    class TurnedOff(FakeSummariser):
        def summarise(
            self,
            period: str,
            sources: Sequence[tuple[str, str]],
            counts: Mapping[str, int],
        ) -> Summary:
            if self.calls:
                raise SummariserOff("the AI is off (you turned the AI off)")
            return super().summarise(period, sources, counts)

    fake = TurnedOff(["ev: a"])
    result = curator.compact(vault, LATER, fake, None)
    assert result.status == "ok" and [p for p, _ in fake.calls] == ["2026-W43"]
    assert result.detail.endswith(
        "stopped before the rest: the AI is off (you turned the AI off)"
    )
    assert (vault / "log" / "2026-10-26.md").exists()


# --- D142 (f): counts are computed in code ---------------------------------------------------


def test_event_counts_count_each_kind_from_the_logs() -> None:
    day1 = (
        "---\nid: log-2026-10-26\nt: log\nupd: 2026-10-26\n---\n"
        "death: creeper\nquest: q:1 done\nnote: a long day\nbuilt: ebf\n"
        "\n```tsv\ndeath: in a table\tx\n```\nNot a fact.\n"
    )
    day2 = "death: lava\ndeath: fall\nquest: q:2 done\n"
    sources = [("log/2026-10-26.md", day1), ("log/2026-10-27.md", day2)]
    assert event_counts(sources) == {"death": 3, "quest": 2, "built": 1}
    assert count_lines(event_counts(sources)) == [
        "n_built: 1",
        "n_death: 3",
        "n_quest: 2",
    ]
    assert event_counts([]) == {}


def test_event_counts_add_up_summaries_and_count_a_listed_log_once() -> None:
    week40 = "period: 2026-W40\nfrom: 2026-09-28.md\nn_death: 2\nn_quest: 1\ndeath: a\n"
    week41 = "period: 2026-W41\nfrom: 2026-10-05.md\nn_death: 1\nquest: q:9\n"
    assert event_counts(
        [("log/wk/2026-W40.md", week40), ("log/wk/2026-W41.md", week41)]
    ) == {"death": 3, "quest": 1}  # a summary's own event lines aren't counted again
    earlier = "period: 2026-W44\nfrom: 2026-10-26.md\nn_death: 1\n"
    assert event_counts(
        [
            ("log/wk/2026-W44.md", earlier),
            ("log/2026-10-26.md", "death: a\n"),  # a killed run's leftover: in from:
            ("log/2026-10-27.md", "death: b\n"),
        ]
    ) == {"death": 2}
    # A month written, then killed before its weeks were deleted: they count once, in it.
    month = "period: 2026-10\nfrom: wk/2026-W43.md, wk/2026-W44.md\nn_death: 6\n"
    assert event_counts(
        [
            ("log/mo/2026-10.md", month),
            ("log/wk/2026-W43.md", "period: 2026-W43\nn_death: 4\n"),
            ("log/wk/2026-W44.md", "period: 2026-W44\nn_death: 2\n"),
        ]
    ) == {"death": 6}
    # Only ASCII digits are a count ("²" is a digit to str.isdigit, not to int()).
    odd = "period: 2026-W45\nfrom: 2026-11-02.md\nn_death: ²\nn_quest: 3\n"
    assert event_counts([("log/wk/2026-W45.md", odd)]) == {"quest": 3}


def test_checked_lines_accept_exactly_the_given_counts() -> None:
    logs = "death: a\ndeath: b\ndeath: c\nquest: q:12"
    counts = {"death": 3, "quest": 1}
    source = source_text("2026-W44", [("log/a.md", logs)], counts)
    assert "<counts>\nn_death: 3\nn_quest: 1\n</counts>" in source
    assert checked_lines("deaths: 3 (creeper, lava, fall)", logs, counts) == [
        "deaths: 3 (creeper, lava, fall)"
    ]
    # A repeat of a count line is dropped: the curator writes them.
    assert checked_lines("n_death: 3\ndeath: a, b, c", logs, counts) == [
        "death: a, b, c"
    ]
    # The kind's own key may give its count beside the logs' numbers (q:12 is in the logs).
    assert checked_lines("death: 3 (a, b, c)\nquest: 1 done, q:12", logs, counts) == [
        "death: 3 (a, b, c)",
        "quest: 1 done, q:12",
    ]
    for answer, why in [
        ("deaths: 4", "counts that aren't the computed ones"),
        ("deaths: 12", "counts that aren't the computed ones"),  # 12 is in the logs
        ("quests: 3", "counts that aren't the computed ones"),  # the death count
        ("deaths: 3 of 12", "counts that aren't the computed ones"),
        ("built: 3 boilers", r"numbers that aren't in the logs: \['3'\]"),
        ("n_death: 4", "count lines that aren't the computed counts"),
        ("n_built: 3", "count lines that aren't the computed counts"),
        ("n_death: 3", "empty"),
    ]:
        with pytest.raises(SummaryError, match=why):
            checked_lines(answer, logs, counts)
    with pytest.raises(SummaryError, match="the cap is 118"):
        checked_lines("\n".join(["death: a"] * 119), logs, counts)
    # One kind's count is never another's, and the <counts> block isn't the logs' text.
    for answer in ("quest: 3 done", "built: 3 boilers"):
        with pytest.raises(
            SummaryError, match=r"numbers that aren't in the logs: \['3'\]"
        ):
            checked_lines(answer, source, counts)


def test_the_summary_carries_the_computed_counts(vault: Path) -> None:
    daily(vault, "2026-10-26", "death: creeper\nquest: q:1 done\nnote: x\n")
    daily(vault, "2026-10-27", "death: lava\nbuilt: ebf\n")
    fake = FakeSummariser(["death: creeper, lava", "built: ebf"])
    assert curator.compact(vault, LATER, fake, None).status == "ok"
    assert fake.counts == [{"death": 2, "quest": 1, "built": 1}]
    week = (vault / "log" / "wk" / "2026-W44.md").read_text(encoding="utf-8")
    assert week.endswith(
        "period: 2026-W44\nfrom: 2026-10-26.md, 2026-10-27.md\n"
        "n_built: 1\nn_death: 2\nn_quest: 1\ndeath: creeper, lava\nbuilt: ebf\n"
    )
    assert not schema.check_text(
        "log/wk/2026-W44.md", week, schema.load_types(vault / "_gos" / "schema.md")
    )
    # Eight weeks on, October's month adds up its weeks' counts.
    note(
        vault,
        "log/wk/2026-W43.md",
        "log",
        "log-2026-W43",
        "period: 2026-W43\nfrom: 2026-10-19.md\nn_death: 4\nnote: y\n",
    )
    month = FakeSummariser(["note: a month"])
    assert curator.compact(vault, dt.date(2026, 12, 27), month, None).status == "ok"
    assert month.counts == [{"death": 6, "quest": 1, "built": 1}]
    text = (vault / "log" / "mo" / "2026-10.md").read_text(encoding="utf-8")
    assert "from: wk/2026-W43.md, wk/2026-W44.md\n" in text
    assert "n_built: 1\nn_death: 6\nn_quest: 1\nnote: a month\n" in text


def test_n_keys_are_the_curators_counts_only(vault: Path) -> None:
    """D143: a daily log never holds an n_ key, so no agent note is mistaken for a count."""
    types = schema.load_types(vault / "_gos" / "schema.md")
    daily = "---\nid: log-2026-10-26\nt: log\nupd: 2026-10-26\n---\nn_items: 5\n"
    assert schema.check_text("log/2026-10-26.md", daily, types) == [
        (
            "key n_items: n_ keys are the curator's counts, in log/wk/ and log/mo/ only "
            "(and the session scribe's n_<kind> counts in daily logs)"
        )
    ]
    # D263: the session scribe's count of a kind it journals is a daily log's only n_ key
    scribe = daily.replace("n_items: 5", "n_quest: 5")
    assert schema.check_text("log/2026-10-26.md", scribe, types) == []
    assert schema.check_text(
        "log/2026-10-26.md", scribe.replace(": 5", ": five"), types
    )
    for rel, id_ in (("log/wk/2026-W44.md", "W44"), ("log/mo/2026-10.md", "10")):
        note = f"---\nid: log-2026-{id_}\nt: log\nupd: 2026-11-06\n---\nn_death: 2\n"
        assert schema.check_text(rel, note, types) == []


def test_haiku_summariser_refuses_a_count_it_made_up() -> None:
    messages = FakeMessages("deaths: 2")
    summariser = HaikuSummariser(SimpleNamespace(messages=messages))  # type: ignore[arg-type]
    logs = [("log/a.md", "death: a\ndeath: b\ndeath: c\n")]
    with pytest.raises(SummaryError, match=r"computed ones: \['deaths: 2'\]"):
        summariser.summarise("2026-W44", logs, {"death": 3})
    messages.text = "deaths: 3"
    assert summariser.summarise("2026-W44", logs, {"death": 3}).lines == ["deaths: 3"]


# --- D142 (g): a backup with a deadline ------------------------------------------------------


def big_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE t(x)")
        conn.executemany(
            "INSERT INTO t VALUES (?)", [(f"row {n}",) for n in range(5000)]
        )
        conn.commit()
    finally:
        conn.close()


def test_a_backup_past_its_deadline_leaves_no_file(tmp_path: Path) -> None:
    import time

    db, folder = tmp_path / "gos.sqlite", tmp_path / "backups"
    big_db(db)
    result = curator.backup(db, folder, TODAY, deadline=time.monotonic() - 1)
    assert result == curator.JobResult("backup", "skipped", "cut short: out of time")
    assert list(folder.iterdir()) == []
    result = curator.backup(db, folder, TODAY, deadline=time.monotonic() + 60)
    assert result.detail == "wrote gos-20261106.sqlite"


def test_a_backup_waits_on_a_locked_db_only_until_its_deadline(tmp_path: Path) -> None:
    """The progress handler doesn't run during SQLite's busy wait: the wait itself ends at the
    deadline, not after the usual 30 s."""
    import time

    db, folder = tmp_path / "gos.sqlite", tmp_path / "backups"
    big_db(db)
    holder = sqlite3.connect(db, isolation_level=None)
    try:
        holder.execute("BEGIN EXCLUSIVE")  # no reader gets in (rollback journal)
        start = time.monotonic()
        result = curator.backup(db, folder, TODAY, deadline=start + 0.3)
        took = time.monotonic() - start
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert result == curator.JobResult(
        "backup", "skipped", "cut short: the DB stayed locked"
    )
    assert took < 5 and list(folder.iterdir()) == []
