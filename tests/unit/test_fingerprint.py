"""Play's fingerprint and the stale flag (WO-1.4, plan 5.2 step 4, D120) on a small fake Play."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

from gregtec import config
from gregtec.ingest import fingerprint

PLAY = {
    "mods/example-1.0.jar": b"jar one",
    "mods/1.7.10/inner-2.0.jar": b"inner jar",
    "mods/pack.zip": b"zip mod",
    "mods/OLD.JAR": b"upper-case suffix",
    "mods/off-1.0.jar.disabled": b"disabled",
    "mods/readme.txt": b"not a mod",
    "mods/NotEnoughItems-2.8.171-GTNH.jar": b"nei 171",  # Export's exclude
    "mods/gregteclink-0.1.0.jar": b"link client",  # Export's exclude
    "config/gregteclink.cfg": b"client config",  # Export's exclude
    "config/GTNewHorizons/dreamcraft.cfg": b"S:ModPackVersion=2.9.0\n",
    "config/GregTech/GregTech.cfg": b"gregtech {}\n",
    "config/GregTech/Client.cfg": b"client prefs\n",  # not recipe-affecting
    "config/other.cfg": b"other\n",
    "scripts/example.zs": b"// zs",
    "scripts/sub/deep.zs": b"// deep",
    "scripts/notes.txt": b"not a script",
    "options.txt": b"pauseOnLostFocus:false\n",
    "saves/world/level.dat": b"save",
}
EXPECTED = [
    "config/GTNewHorizons/dreamcraft.cfg",
    "config/GregTech/GregTech.cfg",
    "mmc-pack.json",
    "mods/1.7.10/inner-2.0.jar",
    "mods/OLD.JAR",
    "mods/example-1.0.jar",
    "mods/pack.zip",
    "scripts/example.zs",
    "scripts/sub/deep.zs",
]


def make_play(
    root: Path, files: dict[str, bytes] = PLAY, game: str = ".minecraft"
) -> Path:
    inst = root / "instances" / "GTNH-Play"
    (inst / game).mkdir(parents=True)
    (inst / "instance.cfg").write_text("[General]\nname=GTNH-Play\n")
    (inst / "mmc-pack.json").write_text('{"components": []}\n')
    for rel, data in files.items():
        path = inst / game / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return inst


def make_cfg(tmp: Path, extra: str = "") -> config.Config:
    path = tmp / "gos.toml"
    path.write_text(
        f'[paths]\nprism_instances = "{(tmp / "instances").as_posix()}"\n'
        f'gos_home = "{(tmp / "home").as_posix()}"\n{extra}'
    )
    return config.load(path)


def test_fingerprint_lists_what_matters(tmp_path: Path) -> None:
    make_play(tmp_path)
    fp = fingerprint.compute_play(make_cfg(tmp_path))
    assert [rel for rel, _ in fp.entries] == EXPECTED
    assert fp.digest == hashlib.sha256(fingerprint.listing(fp.entries)).hexdigest()
    lines = fingerprint.listing(fp.entries).decode().splitlines()
    assert lines[0] == (
        "config/GTNewHorizons/dreamcraft.cfg\t"
        + hashlib.sha256(PLAY["config/GTNewHorizons/dreamcraft.cfg"]).hexdigest()
    )
    assert fp.counts() == "4 mods, 2 scripts, 2 configs, 1 other"


def test_same_files_same_fingerprint_whatever_the_mtimes(tmp_path: Path) -> None:
    a = make_play(tmp_path / "a")
    b = make_play(tmp_path / "b", dict(reversed(list(PLAY.items()))), game="minecraft")
    for path in (b / "minecraft").rglob("*"):
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    cfg_a, cfg_b = make_cfg(tmp_path / "a"), make_cfg(tmp_path / "b")
    one = fingerprint.compute_play(cfg_a)
    assert fingerprint.compute_play(cfg_a) == one  # repeatable (now from the cache)
    assert fingerprint.compute_play(cfg_a, use_cache=False) == one
    assert fingerprint.compute_play(cfg_b).digest == one.digest
    assert a != b


def test_export_only_files_never_make_play_stale(tmp_path: Path) -> None:
    """Export's sync excludes (its NEI and GregtecLink) are left out of Play's fingerprint."""
    inst = make_play(tmp_path)
    cfg = make_cfg(tmp_path)
    before = fingerprint.compute_play(cfg).digest
    (inst / ".minecraft/mods/NotEnoughItems-2.8.171-GTNH.jar").write_bytes(b"nei 180")
    (inst / ".minecraft/mods/gregteclink-0.2.0.jar").write_bytes(b"new link")
    (inst / ".minecraft/config/gregteclink.cfg").write_bytes(b"listener on")
    (inst / ".minecraft/config/GregTech/Client.cfg").write_bytes(b"other prefs")
    (inst / ".minecraft/options.txt").write_bytes(b"changed")
    (inst / ".minecraft/saves/world/level.dat").write_bytes(b"played")
    assert fingerprint.compute_play(cfg).digest == before


@pytest.mark.parametrize(
    ("rel", "data"),
    [
        ("mods/example-1.0.jar", b"jar one, updated"),
        ("mods/new-1.0.jar", b"added"),
        ("scripts/example.zs", b"// changed recipe"),
        ("config/GTNewHorizons/dreamcraft.cfg", b"S:ModPackVersion=2.9.1\n"),
        ("config/GregTech/GregTech.cfg", b"gregtech { changed }\n"),
    ],
)
def test_a_change_that_matters_changes_it(
    tmp_path: Path, rel: str, data: bytes
) -> None:
    inst = make_play(tmp_path)
    cfg = make_cfg(tmp_path)
    before = fingerprint.compute_play(cfg)
    (inst / ".minecraft" / rel).write_bytes(data)
    after = fingerprint.compute_play(cfg)
    assert after.digest != before.digest
    changes = fingerprint.changes(before.entries, after.entries)
    assert rel in (*changes.added, *changes.changed)


def test_disabling_or_removing_a_mod_changes_it(tmp_path: Path) -> None:
    inst = make_play(tmp_path)
    cfg = make_cfg(tmp_path)
    before = fingerprint.compute_play(cfg)
    jar = inst / ".minecraft/mods/example-1.0.jar"
    jar.rename(jar.with_name(jar.name + ".disabled"))  # Prism's disable
    after = fingerprint.compute_play(cfg)
    assert fingerprint.changes(before.entries, after.entries).removed == (
        "mods/example-1.0.jar",
    )
    (inst / "mmc-pack.json").write_text('{"components": ["forge"]}\n')
    assert fingerprint.compute_play(cfg).digest != after.digest


def test_config_whitelist_from_gos_toml(tmp_path: Path) -> None:
    files = {**PLAY, "config/bartworks.cfg": b"bw", "config/TST/a.cfg": b"tst"}
    make_play(tmp_path, files)
    cfg = make_cfg(
        tmp_path, '[fingerprint]\nconfigs = ["config/bartworks.cfg", "config/TST"]\n'
    )
    assert cfg.fingerprint_configs[: len(config.DEFAULT_FINGERPRINT_CONFIGS)] == (
        config.DEFAULT_FINGERPRINT_CONFIGS
    )
    rels = [rel for rel, _ in fingerprint.compute_play(cfg).entries]
    assert "config/bartworks.cfg" in rels and "config/TST/a.cfg" in rels


@pytest.mark.parametrize(
    "toml",
    [
        '[fingerprint]\nconfigs = ["mods/x.jar"]\n',
        '[fingerprint]\nconfigs = ["config\\\\x.cfg"]\n',
        '[fingerprint]\nconfigs = "config/x.cfg"\n',
        '[policy]\nexport_on_quit = "always"\n',
    ],
)
def test_bad_keys_are_refused(tmp_path: Path, toml: str) -> None:
    with pytest.raises(config.ConfigError):
        make_cfg(tmp_path, toml)


def test_hashes_are_cached_by_size_and_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = make_play(tmp_path)
    cfg = make_cfg(tmp_path)
    fingerprint.compute_play(cfg)
    hashed: list[str] = []
    real = fingerprint.sha256_file

    def counting(path: str | os.PathLike[str]) -> str:
        hashed.append(Path(path).name)
        return real(path)

    monkeypatch.setattr(fingerprint, "sha256_file", counting)
    fingerprint.compute_play(cfg)
    assert hashed == []
    jar = inst / ".minecraft/mods/example-1.0.jar"
    jar.write_bytes(b"jar two")  # same size, new mtime
    os.utime(jar, ns=(jar.stat().st_atime_ns, jar.stat().st_mtime_ns + 1_000_000_000))
    fingerprint.compute_play(cfg)
    assert hashed == ["example-1.0.jar"]
    (cfg.gos_home / fingerprint.CACHE).write_text("{damaged")
    hashed.clear()
    fingerprint.compute_play(cfg)
    assert len(hashed) == len(EXPECTED)


def test_play_is_only_read(tmp_path: Path) -> None:
    inst = make_play(tmp_path)
    before = {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in inst.rglob("*")
        if p.is_file()
    }
    home = make_cfg(tmp_path).gos_home
    fp = fingerprint.compute_play(make_cfg(tmp_path))
    fingerprint.check(home, fp, "test")
    after = {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in inst.rglob("*")
        if p.is_file()
    }
    assert after == before


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege on Windows"
)
def test_links_are_not_followed(tmp_path: Path) -> None:
    inst = make_play(tmp_path)
    outside = tmp_path / "outside"
    (outside / "x").mkdir(parents=True)
    (outside / "x" / "evil.jar").write_bytes(b"evil")
    (inst / ".minecraft/mods/linked").symlink_to(outside / "x")
    fp = fingerprint.compute_play(make_cfg(tmp_path))
    assert not any("evil" in rel for rel, _ in fp.entries)
    assert any("mods/linked is a link" in n for n in fp.notes)


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege on Windows"
)
def test_linked_roots_are_not_followed(tmp_path: Path) -> None:
    """mods/, scripts/ and mmc-pack.json as links are noted; a linked game or instance folder
    can't be fingerprinted (sync refuses all of them)."""
    inst = make_play(tmp_path)
    game = inst / ".minecraft"
    outside = tmp_path / "outside"
    outside.mkdir()
    for name in ("mods", "scripts"):
        (game / name).rename(outside / name)
        (game / name).symlink_to(outside / name)
    (inst / "mmc-pack.json").rename(outside / "mmc-pack.json")
    (inst / "mmc-pack.json").symlink_to(outside / "mmc-pack.json")
    fp = fingerprint.compute_play(make_cfg(tmp_path))
    assert not any(rel.startswith(("mods/", "scripts/")) for rel, _ in fp.entries)
    assert "mmc-pack.json" not in dict(fp.entries)
    for name in ("mods", "scripts", "mmc-pack.json"):
        assert f"{name} is a link or junction: not followed" in "\n".join(fp.notes)
    assert not any("mmc-pack.json is missing" in n for n in fp.notes)

    game.rename(outside / "game")
    game.symlink_to(outside / "game")
    with pytest.raises(fingerprint.FingerprintError, match="symlink or junction"):
        fingerprint.compute_play(make_cfg(tmp_path))


def test_stale_flag_follows_the_last_export(tmp_path: Path) -> None:
    inst = make_play(tmp_path)
    cfg = make_cfg(tmp_path)
    home = cfg.gos_home
    first = fingerprint.compute_play(cfg)
    status = fingerprint.check(home, first, "pre-launch")
    assert status.stale and status.export_run is None
    assert "no export is recorded yet" in status.line()

    status = fingerprint.record_export(home, "20261106T000000Z", first, first)
    assert not status.stale
    assert "recipe data matches Play (export 20261106T000000Z" in status.line()
    assert fingerprint.load_state(home)["stale"] is False
    assert (home / fingerprint.RUNS / "20261106T000000Z.tsv").read_bytes() == (
        fingerprint.listing(first.entries)
    )
    assert (
        hashlib.sha256(
            (home / fingerprint.RUNS / "20261106T000000Z.tsv").read_bytes()
        ).hexdigest()
        == first.digest
    )

    (inst / ".minecraft/mods/new-1.0.jar").write_bytes(b"new")
    status = fingerprint.check(home, fingerprint.compute_play(cfg), "pre-launch")
    assert status.stale and status.changes.added == ("mods/new-1.0.jar",)
    assert "1 added, 0 removed, 0 changed" in status.line()
    assert fingerprint.load_state(home)["stale"] is True
    assert (home / fingerprint.PLAY_LISTING).read_bytes() == fingerprint.listing(
        status.play.entries
    )


def test_old_run_listings_are_pruned(tmp_path: Path) -> None:
    make_play(tmp_path)
    cfg = make_cfg(tmp_path)
    fp = fingerprint.compute_play(cfg)
    for i in range(fingerprint.KEEP_RUNS + 5):
        fingerprint.record_export(cfg.gos_home, f"run-{i:02d}", fp, fp)
    state = fingerprint.load_state(cfg.gos_home)
    assert len(state["exports"]) == fingerprint.KEEP_RUNS
    assert state["exports"][-1]["run_id"] == f"run-{fingerprint.KEEP_RUNS + 4:02d}"
    kept = sorted(p.stem for p in (cfg.gos_home / fingerprint.RUNS).glob("*.tsv"))
    assert kept == [e["run_id"] for e in state["exports"]]


def test_bad_run_ids_and_damaged_state(tmp_path: Path) -> None:
    make_play(tmp_path)
    cfg = make_cfg(tmp_path)
    fp = fingerprint.compute_play(cfg)
    with pytest.raises(ValueError):
        fingerprint.record_export(cfg.gos_home, "../escape", fp, fp)
    state = cfg.gos_home / fingerprint.STATE
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{not json")
    assert fingerprint.load_state(cfg.gos_home)["exports"] == []
    assert fingerprint.status(cfg.gos_home, fp).stale
    assert fingerprint.run_entries(cfg.gos_home, "../escape") is None


def test_no_game_folder_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "instances" / "GTNH-Play").mkdir(parents=True)
    with pytest.raises(fingerprint.FingerprintError):
        fingerprint.compute_play(make_cfg(tmp_path))
