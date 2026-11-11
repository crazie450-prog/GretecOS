"""Shared fixtures for the setup wizard's tests (WO-2.9): a keyring in memory, a fake Prism tree,
and a wizard service over a fresh GOS_HOME (no gos.toml yet)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest

from gregtec.config import Config
from gregtec.llm.fake import ScriptedClient, Turn
from gregtec.setup.devices import FakeDevices
from gregtec.setup.service import SetupService

from .fakes import MemoryKeyring, make_prism


@pytest.fixture
def ring() -> Iterator[MemoryKeyring]:
    previous = keyring.get_keyring()
    fake = MemoryKeyring()
    keyring.set_keyring(fake)
    yield fake
    keyring.set_keyring(previous)


@pytest.fixture
def prism(tmp_path: Path) -> Path:
    return make_prism(tmp_path / "PrismLauncher")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "GregtecOS"


@pytest.fixture
def clients() -> list[ScriptedClient]:
    return []


@pytest.fixture
def svc(home: Path, ring: MemoryKeyring, clients: list[ScriptedClient]) -> SetupService:
    def factory(key: str) -> ScriptedClient:
        client = ScriptedClient(turns=[Turn(text="ok")])
        client.key = key  # type: ignore[attr-defined]
        clients.append(client)
        return client

    return SetupService(
        home / "config" / "gos.toml",
        Config(gos_home=home),
        provider=FakeDevices(),
        client_factory=factory,
    )


def path_values(prism: Path, home: Path, app: Path) -> dict[str, str]:
    return {
        "prism_root": str(prism),
        "prism_instances": str(prism / "instances"),
        "play": "GTNH-Play",
        "export": "GTNH-Export",
        "lab": "GTNH-Lab",
        "gos_app": str(app),
        "gos_home": str(home),
    }
