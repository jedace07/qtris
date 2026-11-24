"""Preferences: game sources, storage paths, the Steam account and system info."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from qtris import settings
from qtris.backend import preferences
from qtris.exceptions import QtrisError
from qtris.util.test_config import setup_test_environment
from qtris.util.yaml import read_yaml_from_file

setup_test_environment()


def test_issue_report_exports_description_and_system_information(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preferences, "gather_system_info", lambda: {"distribution": "Test Linux"})
    report = tmp_path / "report.json"
    asyncio.run(preferences.save_issue_report(str(report), "The screen went blank"))
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "comment": "The screen went blank",
        "system": {"distribution": "Test Linux"},
    }


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    values: dict[tuple[str, str], str] = {}

    def read_setting(key: str, default: str = "", section: str = "qtris") -> str:
        return values.get((section, key), default)

    def read_bool_setting(key: str, default: bool = False, section: str = "qtris") -> bool:
        value = values.get((section, key))
        return default if value is None else value == "True"

    def write_setting(key: str, value: Any, section: str = "qtris") -> None:
        values[(section, key)] = str(value)

    monkeypatch.setattr(settings, "read_setting", read_setting)
    monkeypatch.setattr(settings, "read_bool_setting", read_bool_setting)
    monkeypatch.setattr(settings, "write_setting", write_setting)
    return values


def test_sources_can_be_turned_on_and_hidden_from_the_games_view(saved: dict[tuple[str, str], str]) -> None:
    async def run() -> None:
        sources = await preferences.list_sources()
        gog = next(source for source in sources if source.id == "gog")
        assert not gog.enabled and gog.in_games_view and gog.name == "GOG"

        await preferences.set_source_in_games_view("gog", False)
        await preferences.set_source_enabled("gog", True)
        gog = next(source for source in await preferences.list_sources() if source.id == "gog")
        # Turning a source on or off shows its games in the Games view again
        assert gog.enabled and gog.in_games_view

        await preferences.set_source_in_games_view("gog", False)
        assert not next(source for source in await preferences.list_sources() if source.id == "gog").in_games_view
        with pytest.raises(QtrisError):
            await preferences.set_source_enabled("no-such-store", True)

    asyncio.run(run())


def test_storage_paths_are_saved_where_qtris_reads_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))

    async def run() -> None:
        paths = await preferences.set_game_path(" /games ")
        assert paths.game_path == "/games"
        assert paths.prefix_path == str(Path.home() / "Games" / "Prefixes")
        prefix = tmp_path / "Windows games"
        paths = await preferences.set_prefix_path(f" {prefix} ")
        assert paths.prefix_path == str(prefix) and paths.game_path == "/games"
        assert read_yaml_from_file(str(tmp_path / "system.yml"))["system"] == {
            "game_path": "/games",
            "prefix_path": str(prefix),
        }
        with pytest.raises(QtrisError, match="absolute path"):
            await preferences.set_prefix_path("relative")
        assert (await preferences.set_prefix_path("")).prefix_path == str(Path.home() / "Games" / "Prefixes")
        paths = await preferences.set_game_path("")
        assert paths.game_path.endswith("Games")

    asyncio.run(run())


def test_steam_accounts_and_system_info(saved: dict[tuple[str, str], str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        preferences,
        "get_steam_users",
        lambda: [{"steamid64": "1", "PersonaName": "Main"}, {"steamid64": "2"}],
    )

    async def run() -> None:
        await preferences.set_steam_account("2")
        accounts = await preferences.list_steam_accounts()
        assert [(account.name, account.active) for account in accounts] == [("Main", False), ("#2", True)]
        sections = await preferences.get_system_info()
        assert sections[0].title == "Features" and ("In Flatpak", "NO") in sections[0].items
        assert preferences.system_info_text(sections).startswith("[Features]\nVulkan support: ")

    asyncio.run(run())
