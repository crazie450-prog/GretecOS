"""Fakes for the setup wizard's tests and its e2e harness (dashboard/e2e/serve_core.py loads this
file by path): a keyring in memory or in one JSON file, and a Prism tree with GTNH 2.9.0 Play,
Export and Lab instances shaped like the Java 17-26 Prism zip (tests/unit/test_doctor.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import keyring.backend
import keyring.errors

ANTHROPIC_KEY = "sk-ant-api03-" + "W1zardK3y" * 8  # fake, the shape of a real key
DISCORD_TOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GaBcDe." + "x" * 38  # fake, a token's shape

JAVA17_PACK = {
    "components": [
        {"uid": "org.lwjgl3", "version": "3.4.3"},
        {"uid": "net.minecraft", "version": "1.7.10"},
        {"uid": "me.eigenraven.lwjgl3ify.forgepatches", "version": "3.0.42"},
        {"uid": "net.minecraftforge", "version": "10.13.4.1614"},
        {"uid": "me.eigenraven.lwjgl3ify.launchargs", "version": "3.0.42"},
    ],
    "formatVersion": 1,
}


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.entries.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError(username)


class FileKeyring(MemoryKeyring):
    """A keyring in one JSON file, so an e2e test can see what the core stored (test only)."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._load().get(f"{service}|{username}")

    def set_password(self, service: str, username: str, password: str) -> None:
        data = self._load()
        data[f"{service}|{username}"] = password
        self.path.write_text(json.dumps(data), "utf-8")

    def delete_password(self, service: str, username: str) -> None:
        data = self._load()
        if data.pop(f"{service}|{username}", None) is None:
            raise keyring.errors.PasswordDeleteError(username)
        self.path.write_text(json.dumps(data), "utf-8")


def make_instance(instances: Path, folder: str, version: str = "2.9.0") -> Path:
    inst = instances / folder
    game = inst / ".minecraft"
    (game / "config" / "GTNewHorizons").mkdir(parents=True)
    (inst / "instance.cfg").write_text(
        f"InstanceType=OneSix\nOverrideJavaLocation=false\nname={folder}\n", "utf-8"
    )
    (inst / "mmc-pack.json").write_text(json.dumps(JAVA17_PACK), "utf-8")
    (game / "config" / "GTNewHorizons" / "dreamcraft.cfg").write_text(
        f"modules {{\n    S:ModPackVersion={version}\n}}\n", "utf-8"
    )
    mods = game / "mods"
    mods.mkdir()
    (mods / "gregtech-5.09.54.265.jar").write_bytes(b"gt")
    (mods / "lwjgl3ify-3.0.42.jar").write_bytes(b"l3")
    return inst


def make_prism(
    root: Path, names: tuple[str, ...] = ("GTNH-Play", "GTNH-Export", "GTNH-Lab")
) -> Path:
    """A portable Prism folder at ``root`` with GTNH 2.9.0 instances; returns ``root``."""
    (root / "portable.txt").parent.mkdir(parents=True, exist_ok=True)
    (root / "portable.txt").write_text("", "utf-8")
    for name in names:
        make_instance(root / "instances", name)
    return root


def link_folder(link: Path, target: Path) -> None:
    """A folder link: a junction on Windows (no privilege needed), else a symlink."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
