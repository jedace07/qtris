"""Turning runners on and off, and what a new profile starts with."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from qtris import runners as runner_registry
from qtris import settings
from qtris.backend import runners
from qtris.runners import InvalidRunnerError, runner
from qtris.util.test_config import setup_test_environment
from qtris.util.wine import proton

setup_test_environment()


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    values: dict[tuple[str, str], str] = {}

    def read_bool_setting(key: str, default: bool = False, section: str = "qtris") -> bool:
        value = values.get((section, key))
        return default if value is None else value == "True"

    def write_setting(key: str, value: Any, section: str = "qtris") -> None:
        values[(section, key)] = str(value)

    monkeypatch.setattr(settings, "read_bool_setting", read_bool_setting)
    monkeypatch.setattr(settings, "write_setting", write_setting)
    return values


def test_a_new_profile_has_linux_and_proton_turned_on(saved: dict[tuple[str, str], str]) -> None:
    enabled = asyncio.run(runners.list_enabled_runners())
    assert [(item.name, item.human_name) for item in enabled] == [("linux", "Linux"), ("wine", "Proton")]


def test_steam_is_not_offered_as_a_runner(saved: dict[tuple[str, str], str]) -> None:
    # A profile that turned the Steam runner off before it became internal
    saved[("runners", "steam")] = "False"
    assert "steam" not in asyncio.run(runners.list_runners())
    assert "steam" not in {entry.name for entry in asyncio.run(runners.list_runner_catalog())}
    assert "steam" not in {item.name for item in asyncio.run(runners.list_enabled_runners())}
    # The Steam source's games still run through it
    assert runner_registry.is_runner_enabled("steam") and runner_registry.is_valid_runner_name("steam")
    with pytest.raises(InvalidRunnerError):
        asyncio.run(runners.set_runner_enabled("steam", True))


def test_runners_can_be_turned_on_and_off(saved: dict[tuple[str, str], str], monkeypatch: pytest.MonkeyPatch) -> None:
    installs: list[str] = []

    async def install_runner(name: str, version: str | None = None, **_kwargs: Any) -> None:
        installs.append(name)

    monkeypatch.setattr(runners, "install_runner", install_runner)
    monkeypatch.setattr(runner.Runner, "can_install", property(lambda self: True))
    monkeypatch.setattr(runner.Runner, "is_installed", lambda self, flatpak_allowed=True: self.name == "web")

    async def change() -> None:
        await runners.set_runner_enabled("web", True)
        await runners.set_runner_enabled("pico8", True)
        await runners.set_runner_enabled("linux", False)

    asyncio.run(change())
    # Only the runner that wasn't installed yet is installed
    assert installs == ["pico8"]
    assert saved[("runners", "linux")] == "False"
    enabled = asyncio.run(runners.list_enabled_runners())
    assert [item.name for item in enabled] == ["pico8", "wine", "web"]

    with pytest.raises(InvalidRunnerError):
        asyncio.run(runners.set_runner_enabled("no-such-runner", True))


def test_the_catalog_says_which_runners_are_on(saved: dict[tuple[str, str], str]) -> None:
    catalog = {entry.name: entry for entry in asyncio.run(runners.list_runner_catalog())}
    assert {name for name, entry in catalog.items() if entry.enabled} == {"linux", "wine"}
    assert catalog["wine"].label == "Proton"
    # The runner downloads Proton builds when a game needs them
    assert catalog["wine"].installed and not catalog["wine"].can_install


def test_protonplus_builds_are_not_qtris_to_list_or_remove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proton_dir, compat_dir = tmp_path / "proton", tmp_path / "compatibilitytools.d"
    for folder in (compat_dir / "GE-Proton11-12", compat_dir / "GE-Proton11-11"):
        folder.mkdir(parents=True)
        (folder / "proton").touch()
    (compat_dir / "GE-Proton11-12" / proton.PROTONPLUS_MARKER).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(proton.LINUX_SYSTEM, "arch", "x86_64")
    monkeypatch.setattr(proton, "get_proton_dir", lambda: str(proton_dir))
    monkeypatch.setattr(proton, "get_steam_compat_dir", lambda: str(compat_dir))
    monkeypatch.setattr(proton, "get_proton_versions", dict)

    assert runners._managed_proton_path("GE-Proton11-12") is None
    assert runners._managed_proton_path("GE-Proton11-11") == compat_dir / "GE-Proton11-11"
    builds = asyncio.run(runners.list_proton_builds("GE-Proton"))
    assert [(build.version, build.installed, build.can_remove) for build in builds] == [("GE-Proton11-11", True, True)]
