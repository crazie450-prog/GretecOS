"""WO-9.1 on a real harness DB: ``/plan`` and ``/power`` from Discord, end to end.

A fake interaction from Clayton goes through the bot's door and its real HTTP client
(:class:`HttpCore`, the ``discord`` token) to a real core (uvicorn on a loopback port) whose
durable queue runs the real designer (:func:`gregtec.remote.routes.run_job`) on the dev
runtime's recipes. The queued reply comes at once; the result carries the routes in rank.py's
order with their ``plan:`` ids, the saved PNG diagram and BOM, and the phone-dashboard link.
The core is then restarted on the same DB: the job is neither run nor delivered again.

As the other harness tests, it runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the
server harness; locally: mod/README.md), and writes nothing outside tmp_path.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.tools.catalog import default_registry
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.api.server import bind
from gregtec.config import Config
from gregtec.db import build
from gregtec.discordbot.core_api import HttpCore
from gregtec.discordbot.handlers import Bot, Message
from gregtec.ingest import collect
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.remote.jobs import JobQueue
from gregtec.remote.routes import run_job
from gregtec.store import DB_NAME, Store

from ..unit.discordbot.fakes import CLAYTON, SETTINGS, FakeClock, FakeInteraction

REPO = Path(__file__).resolve().parents[2]
ADMIN = "admin-" + "h" * 40


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    h = tmp_path_factory.mktemp("discord-harness")
    collect.collect(game, h, 1, allow_harness=True)
    build.build(h, allow_harness=True)
    return h


class LiveCore:
    def __init__(self, home: Path) -> None:
        self.sock = bind(0)
        self.port = self.sock.getsockname()[1]
        self.store = Store(home / "data" / DB_NAME)
        deep = HandoffRecorder()
        self.core = Core(
            cfg=Config(gos_home=home, core_port=self.port),
            store=self.store,
            auth=Auth(self.store, ADMIN),
            meter=UsageMeter(self.store, 100.0, (80, 100)),
            asker=Asker(default_registry(home, deep=deep), deep, None),
        )
        self.core.jobs = JobQueue(self.store, run_job(home))
        self.core.jobs.recover()  # as server.run does before the worker starts
        self.server = uvicorn.Server(
            uvicorn.Config(
                create_app(self.core), log_config=None, access_log=False, lifespan="on"
            )
        )
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True
        )
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            assert time.monotonic() < deadline and self.thread.is_alive()
            time.sleep(0.02)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()
        self.store.close()


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        keys, "has_key", lambda name: False
    )  # no-AI mode: jobs need no model
    yield


def test_plan_and_power_from_discord_on_a_real_db(home: Path, no_key: None) -> None:
    live = LiveCore(home)
    token = live.core.auth.issue_client_token("discord")
    dms: list[Message] = []
    clock = FakeClock()

    async def dm(msg: Message) -> None:
        dms.append(msg)

    try:
        bot = Bot(SETTINGS, HttpCore(live.port, token), dm=dm, clock=clock)
        plan = FakeInteraction(CLAYTON, "command", clock)
        power = FakeInteraction(CLAYTON, "command", clock)

        async def ask() -> None:
            await bot.interaction(
                plan, "plan", {"target": "steel ingot", "amount": "2/s", "tier": "MV"}
            )
            await bot.interaction(power, "power", {"mode": "best", "tier": "LV"})

        asyncio.run(ask())
        assert "queued" in plan.texts[0] and "queued" in power.texts[0]
        assert live.core.jobs is not None
        while live.core.jobs.run_one() is not None:  # the core's worker, inline here
            pass
        assert asyncio.run(bot.deliver()) == 2
        result = plan.sent[-1][1]
        assert "route 1 of" in result.text and "(plan:pl-" in result.text, result.text
        assert "https://pc.tail1.ts.net/#/designer/pl-" in result.text
        names = [n for n, _ in result.files]
        assert (
            len(names) == 2
            and names[0].endswith("-flow.png")
            and names[1].endswith("-bom.tsv")
        )
        png, bom = (data for _, data in result.files)
        assert png.startswith(b"\x89PNG") and bom.count(b"\t") > 0
        pid = names[0].removesuffix("-flow.png")
        saved = home / "data" / "plans" / pid
        assert (saved / "flow.png").read_bytes() == png
        power_text = power.texts[-1]
        assert "option 1 of" in power_text and "(plan:pw-" in power_text, power_text
        assert "https://pc.tail1.ts.net/#/power/pw-" in power_text
        assert dms == []
    finally:
        live.stop()

    # The core restarts on the same DB: nothing is run or delivered again.
    again = LiveCore(home)
    try:
        assert again.core.jobs is not None and again.core.jobs.run_one() is None
        bot = Bot(SETTINGS, HttpCore(again.port, token), dm=dm, clock=clock)
        assert asyncio.run(bot.deliver()) == 0 and dms == []
    finally:
        again.stop()
