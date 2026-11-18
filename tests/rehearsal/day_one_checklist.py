"""The day-one checklist (plan 10, G11) and the Lab scenarios (plan 9, L1-L10) as the rehearsal
reports them (WO-11.3, D336).

Each plan-10 line is split into the things it asks for. Each thing is checked **here** by named
rehearsal steps, or is **PC only** (it needs the real game, mic, Discord, Tailscale, a paid
eval or Clayton's own judgement): those name the runbook step in docs/runbooks/fake_day_one.md
that checks them. A step's result is ``green``, ``red`` or ``not run`` (with why).

- an item checked here is red when any of its steps is red, green when all are green, else
  ``not run``;
- a line is **red** when any item is red, **green** when every item is checked here and green,
  and **PC only** when every item checked here is green and the rest are PC only (the report
  lists them); a line with an item not run here says so.

The rehearsal is green when no line is red and nothing checked here was left not run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

GREEN, RED, NOT_RUN, PC = "green", "red", "not run", "PC only"


@dataclass(frozen=True)
class Item:
    text: str
    steps: tuple[str, ...] = ()  # empty: PC only
    runbook: str = ""  # the source runbook (docs/runbooks/...)

    @property
    def here(self) -> bool:
        return bool(self.steps)


@dataclass(frozen=True)
class Line:
    id: str
    text: str
    items: tuple[Item, ...]


def here(text: str, *steps: str, runbook: str = "") -> Item:
    return Item(text, steps, runbook)


def pc(text: str, runbook: str) -> Item:
    return Item(text, (), runbook)


CHECKLIST: tuple[Line, ...] = (
    Line(
        "C1",
        "Addons on 2.9-compatible releases; Play built and its fingerprint matches; the "
        "pack-update playbook passed its dry run",
        (
            pc("all 4 addons on 2.9-compatible releases", "addon_watcher.md"),
            pc(
                "Play built (WO-0.7); its fingerprint matches the export",
                "phase1_pc_checks.md",
            ),
            here(
                "pack-update playbook: a simulated 2.9.x walked end to end (L10)",
                "suite_pack_update",
                runbook="pack_update.md",
            ),
            pc("the Lab dry run of a 2.9.x update (L10)", "pack_update.md"),
        ),
    ),
    Line(
        "C2",
        "Designer: G3 lines; pins and ranking; planner exports; plan -> vault -> ledger; "
        "batch; My base feeds the boundary",
        (
            here("the G3 benchmark lines pass", "suite_g3", runbook="designer.md"),
            here(
                "pins and the deterministic ranking (rank.py order, pin survives re-solve)",
                "design_pin_rank",
                runbook="designer.md",
            ),
            here(
                "a planner export is written",
                "design_export",
                runbook="plan_outputs.md",
            ),
            pc("the export opens in gtnhplanner.com", "plan_outputs.md"),
            here(
                "plan -> vault -> ledger (accepted from the dashboard API)",
                "design_accept",
                runbook="designer.md",
            ),
            here(
                "batch mode (a quantity within a time)",
                "design_batch",
                runbook="designer.md",
            ),
            here(
                "My base fills from GregtecLink's listener (the boundary's input)",
                "my_base",
                runbook="machine_events.md",
            ),
        ),
    ),
    Line(
        "C3",
        "Power: the 4 power benchmarks (G4); 4 modes reviewed; ledger and storage sizing",
        (
            here(
                "the power benchmarks pass (G4; naquadah MkI: fusion not sized, D176/D328)",
                "suite_g4",
                runbook="power_objectives.md",
            ),
            here(
                "a power design accepted into the ledger, storage sized",
                "power_design",
                runbook="power_objectives.md",
            ),
            pc("the 4 modes reviewed at LV/HV/IV", "power_objectives.md"),
        ),
    ),
    Line(
        "C4",
        "Voice: wake word; talk key; entity accuracy; latency; pronunciation and filter; "
        "personas",
        (
            pc("wake word: <= 1 false wake/h, >= 38/40 hits", "wake.md"),
            here(
                "the wake path: trigger -> question -> spoken answer",
                "voice_wake",
                runbook="wake.md",
            ),
            here(
                "the talk key backup: hold -> question -> spoken answer",
                "voice_talk",
                runbook="voice_client.md",
            ),
            pc("entity accuracy >= 95% (50 utterances)", "voice_benchmarks.md"),
            pc("latency per PM14", "voice_benchmarks.md"),
            pc("pronunciation and the speech filter, heard", "tts.md"),
            here(
                "personas switch (gt-veteran default)",
                "persona_switch",
                runbook="voice_client.md",
            ),
        ),
    ),
    Line(
        "C5",
        "Q&A: recipe, quest, wiki, screen, hover and ambiguous-item evals; no-AI mode (L9)",
        (
            here(
                "a recipe question on the fast lane, cited",
                "ask_recipe",
                runbook="fast_lane.md",
            ),
            here(
                "a quest question on the fast lane",
                "ask_quest",
                runbook="quest_guide.md",
            ),
            here(
                "a design request handed to the deep lane",
                "ask_deep",
                runbook="deep_lane.md",
            ),
            pc("the evals pass (gos eval, paid)", "fast_lane.md"),
            here(
                "no-AI mode: key revoked -> lookups answer, AI offline; key restored -> back (L9)",
                "l9_key",
                runbook="fast_lane.md",
            ),
        ),
    ),
    Line(
        "C6",
        "Context: watchers stable 2 h; listener and census agree; journal and advisor; notices",
        (
            here(
                "watchers stable with the game writing, no save blocked (time-compressed soak)",
                "soak",
                runbook="watchers.md",
            ),
            pc("watchers stable for 2 h in the real Lab", "watchers.md"),
            here(
                "game events from a replayed log and the listener reach the core",
                "game_events",
                runbook="machine_events.md",
            ),
            pc("listener and census agree in the Lab (L3)", "machine_events.md"),
            here("the day's journal is written", "journal", runbook="event_store.md"),
            pc(
                "advisor and default notices sensible in lab-progress (L2)",
                "advisor.md",
            ),
        ),
    ),
    Line(
        "C7",
        "Crops: 5 guides reviewed; one followed; Crop Manager and Breeder; the station",
        (
            here(
                "a crop guide's next step answered",
                "crop_step",
                runbook="crop_guide.md",
            ),
            pc(
                "the 5 target guides reviewed; one followed in lab-progress",
                "crop_guide.md",
            ),
            pc("the breeding station bred a target unattended", "breeding_station.md"),
        ),
    ),
    Line(
        "C8",
        "OC bridge: config tweak, Lua agent, M1, confirmed actions; AFK alerts (L4)",
        (
            pc("config tweak, Lua agent and M1 mailbox in lab-ops", "oc_agent.md"),
            here(
                "confirmed actions: read-back, one pending, sent once after confirm",
                "oc_action",
                runbook="oc_agent.md",
            ),
            here(
                "an unconfirmed action expires at 60 s",
                "oc_expiry",
                runbook="oc_agent.md",
            ),
            here(
                "an alert reaches Discord while away",
                "alert_discord",
                runbook="alert_routing.md",
            ),
            pc("AFK alerts with the game unfocused, 1 h telemetry (L4)", "oc_agent.md"),
        ),
    ),
    Line(
        "C9",
        "Discord and phone: commands, alerts, credit and offline DMs; phone over Tailscale (L8)",
        (
            here(
                "Discord commands answered, others' ignored",
                "discord_commands",
                runbook="discord_bot.md",
            ),
            here(
                "the phone's tailnet name serves the dashboard; others refused",
                "phone",
                runbook="phone_dashboard.md",
            ),
            pc(
                "commands, alerts and DMs on his phone; Tailscale on cellular (L8)",
                "phone_dashboard.md",
            ),
        ),
    ),
    Line(
        "C10",
        "Build: footprints; 3D guide; >= 2 themes; a Claude-designed shell pasted in the Lab",
        (
            here(
                "a build guide opens from its footprint",
                "build_guide",
                runbook="build_guide_lab.md",
            ),
            pc(">= 2 themes and a shell pasted in the Lab (L6)", "shells_lab.md"),
        ),
    ),
    Line(
        "C11",
        "Ops: supervisor, login start, Play-linked voice and watchers; backups restored; "
        "isolation; costs",
        (
            here(
                "the supervisor runs every process; voice and watchers start with Lab",
                "processes_up",
                "play_linked",
                runbook="core_service.md",
            ),
            pc("login start", "core_service.md"),
            here(
                "a crash of each process is recovered",
                "crash_core",
                "crash_watchers",
                "crash_voice",
                "crash_discord",
                "suite_supervisor_crash",
                runbook="ops.md",
            ),
            here(
                "the DB and vault backup restores and checks",
                "backup_restore",
                runbook="ops.md",
            ),
            pc("an off-instance world backup restored once", "ops.md"),
            pc("Claude Code isolation re-tested", "phase1_pc_checks.md"),
            here("the cost view's caps", "costs", runbook="ops.md"),
            here(
                "Play's instance untouched by the whole session",
                "play_untouched",
                runbook="ops.md",
            ),
        ),
    ),
    Line(
        "C12",
        "A 2-hour fake day one in the Lab with everything on",
        (
            here(
                "the automated rehearsal (this run)",
                "soak",
                "shutdown",
                runbook="fake_day_one.md",
            ),
            pc("the 2-hour Lab rehearsal", "fake_day_one.md"),
        ),
    ),
)

# Plan 9's Lab scenarios: the steps that cover each here, and whether the real run is the PC's.
SCENARIOS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("L1", "projector builds and captures", ()),
    ("L2", "progress states: frontier, tier, advisor", ("ask_quest",)),
    (
        "L3",
        "30 min survival session: event recall, listener ids, journal",
        ("game_events", "journal"),
    ),
    (
        "L4",
        "OC telemetry, confirmed craft and pause, alerts to Discord",
        ("oc_action", "alert_discord"),
    ),
    ("L5", "voice in game: latency and false wakes", ("voice_talk", "voice_wake")),
    (
        "L6",
        "a themed shell pasted; a machine built from the 3D guide",
        ("build_guide",),
    ),
    ("L7", "performance with everything on", ()),
    ("L8", "phone over Tailscale on cellular", ("phone",)),
    ("L9", "API key revoked mid-session, then restored", ("l9_key",)),
    ("L10", "simulated 2.9.x update through the playbook", ("suite_pack_update",)),
)

ALL_STEPS = sorted(
    {s for line in CHECKLIST for item in line.items for s in item.steps}
    | {s for _, _, steps in SCENARIOS for s in steps}
)


@dataclass(frozen=True)
class Result:
    status: str  # GREEN, RED or NOT_RUN
    detail: str = ""


def item_status(item: Item, results: Mapping[str, Result]) -> str:
    if not item.here:
        return PC
    found = [
        results.get(s, Result(NOT_RUN, "no such step ran")).status for s in item.steps
    ]
    if RED in found:
        return RED
    return GREEN if all(f == GREEN for f in found) else NOT_RUN


def line_status(line: Line, results: Mapping[str, Result]) -> str:
    found = [item_status(i, results) for i in line.items]
    if RED in found:
        return RED
    if NOT_RUN in found:
        return NOT_RUN
    return PC if PC in found else GREEN


def green(results: Mapping[str, Result]) -> bool:
    """No line red and every item checked here run and green."""
    return all(
        item_status(i, results) in (GREEN, PC) for line in CHECKLIST for i in line.items
    )


def report(results: Mapping[str, Result], *, title: str = "Fake day one") -> str:
    """The report: each plan-10 line with its status, each item under it, then L1-L10 and the
    steps that weren't green."""
    out = [f"# {title}: plan 10 checklist", ""]
    for line in CHECKLIST:
        out.append(f"{line.id} [{line_status(line, results)}] {line.text}")
        for item in line.items:
            status = item_status(item, results)
            where = ", ".join(item.steps) if item.here else f"runbook {item.runbook}"
            out.append(f"    - [{status}] {item.text} ({where})")
    out += ["", "Lab scenarios (plan 9):"]
    for sid, text, steps in SCENARIOS:
        if not steps:
            out.append(f"{sid} [{PC}] {text}")
            continue
        found = [results.get(s, Result(NOT_RUN)).status for s in steps]
        status = (
            RED
            if RED in found
            else GREEN
            if all(f == GREEN for f in found)
            else NOT_RUN
        )
        out.append(f"{sid} [{status} here; real run: {PC}] {text}")
    bad = {k: v for k, v in results.items() if v.status != GREEN}
    if bad:
        out += ["", "Steps not green:"]
        out += [f"  {k}: {v.status}: {v.detail}" for k, v in sorted(bad.items())]
    out += ["", f"Rehearsal: {'GREEN' if green(results) else 'NOT GREEN'}"]
    return "\n".join(out) + "\n"


def unknown_steps(results: Sequence[str]) -> list[str]:
    """Result names no checklist item or scenario uses (a typo in the driver)."""
    return sorted(set(results) - set(ALL_STEPS))
