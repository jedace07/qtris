"""Turning runners on and off, and what a new profile starts with."""

import asyncio
from typing import Any

import pytest

from qtris import settings
from qtris.backend import runners
from qtris.runners import InvalidRunnerError, runner
from qtris.util.test_config import setup_test_environment

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


def test_a_new_profile_has_steam_linux_and_wine_turned_on(saved: dict[tuple[str, str], str]) -> None:
    enabled = asyncio.run(runners.list_enabled_runners())
    assert [item.name for item in enabled] == ["linux", "steam", "wine"]


def test_runners_can_be_turned_on_and_off(saved: dict[tuple[str, str], str], monkeypatch: pytest.MonkeyPatch) -> None:
    installs: list[str] = []

    async def install_runner(name: str, version: str | None = None, **_kwargs: Any) -> None:
        installs.append(name)

    monkeypatch.setattr(runners, "install_runner", install_runner)
    monkeypatch.setattr(runner.Runner, "can_install", property(lambda self: True))
    monkeypatch.setattr(runner.Runner, "is_installed", lambda self, flatpak_allowed=True: self.name == "web")

    async def change() -> None:
        await runners.set_runner_enabled("web", True)
        await runners.set_runner_enabled("eden", True)
        await runners.set_runner_enabled("steam", False)

    asyncio.run(change())
    # Only the runner that wasn't installed yet is installed
    assert installs == ["eden"]
    assert saved[("runners", "steam")] == "False"
    enabled = asyncio.run(runners.list_enabled_runners())
    assert [item.name for item in enabled] == ["eden", "linux", "web", "wine"]

    with pytest.raises(InvalidRunnerError):
        asyncio.run(runners.set_runner_enabled("no-such-runner", True))


def test_the_catalog_says_which_runners_are_on(saved: dict[tuple[str, str], str]) -> None:
    catalog = {entry.name: entry for entry in asyncio.run(runners.list_runner_catalog())}
    assert {name for name, entry in catalog.items() if entry.enabled} == {"linux", "steam", "wine"}
    # Proton comes when a game needs it, so Wine is always ready
    assert catalog["wine"].installed and not catalog["wine"].can_install
