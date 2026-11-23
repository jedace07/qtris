"""The backend's game calls return plain data about the library."""

import asyncio
import dataclasses

import pytest

from qtris import settings
from qtris.backend import game_actions, game_maintenance, games, preferences, runners, services
from qtris.backend.models import GameInfo
from qtris.database import games as games_db
from qtris.database import sql
from qtris.database.services import ServiceGameCollection
from qtris.game import Game
from qtris.services.flathub import FlathubService
from qtris.services.steam import SteamService
from qtris.util.test_config import setup_test_environment

setup_test_environment()


@pytest.fixture
def library():
    """Two games in the test library, one of them installed from a service."""
    game_ids = [
        games_db.add_game(
            name="Backend Installed",
            slug="backend-installed",
            runner="linux",
            installed=1,
            service="steam",
            service_id="4242",
            playtime=1.5,
        ),
        games_db.add_game(name="Backend Not Installed", slug="backend-not-installed", runner="wine", installed=0),
    ]
    yield [str(game_id) for game_id in game_ids]
    for game_id in game_ids:
        games_db.delete_game(game_id)


def test_games_come_as_plain_data(library):
    installed_id, _other_id = library
    game = asyncio.run(games.get_game(installed_id))

    assert isinstance(game, GameInfo)
    assert (game.name, game.slug, game.runner, game.installed, game.playtime) == (
        "Backend Installed",
        "backend-installed",
        "linux",
        True,
        1.5,
    )
    # Plain data all the way down, so it can cross a process boundary
    for value in dataclasses.asdict(game).values():
        assert value is None or isinstance(value, (str, int, float, bool))


def test_installed_games_can_be_listed_alone(library):
    installed_id, other_id = library
    all_ids = {game.id for game in asyncio.run(games.list_games())}
    installed_ids = {game.id for game in asyncio.run(games.list_games(installed_only=True))}

    assert {installed_id, other_id} <= all_ids
    assert installed_id in installed_ids
    assert other_id not in installed_ids


def test_recent_installed_games_exclude_hidden_games(library: list[str]) -> None:
    played_id, uninstalled_id = library
    games_db.update_existing(id=played_id, lastplayed=300, installed_at=100)
    games_db.update_existing(id=uninstalled_id, lastplayed=500)
    new_id = str(games_db.add_game(name="Recently Installed", slug="recently-installed", runner="linux", installed=1))
    games_db.update_existing(id=new_id, lastplayed=None, installed_at=400)
    try:
        assert games.list_recent_installed_games(1) == [(new_id, "Recently Installed")]
        assert games.list_recent_installed_games(10) == [
            (new_id, "Recently Installed"),
            (played_id, "Backend Installed"),
        ]
        asyncio.run(game_actions.set_hidden(new_id, True))
        assert games.list_recent_installed_games(10) == [(played_id, "Backend Installed")]
    finally:
        asyncio.run(game_actions.set_hidden(new_id, False))
        games_db.delete_game(new_id)


def test_games_are_found_by_id_or_slug(library):
    installed_id, _other_id = library
    by_id = asyncio.run(games.find_game(installed_id))
    by_slug = asyncio.run(games.find_game("backend-installed"))
    assert by_id and by_id.slug == "backend-installed"
    assert by_slug and by_slug.id == installed_id
    assert asyncio.run(games.find_game("no-such-game")) is None
    assert [game.slug for game in asyncio.run(games.find_games_by_slug("backend-not"))] == ["backend-not-installed"]


def test_service_games_know_whether_they_are_installed(library, monkeypatch):
    monkeypatch.setitem(services.SERVICES, "steam", SteamService)
    service_games = [
        {"id": 1, "service": "steam", "appid": "4242", "name": "Installed", "slug": "a", "details": "{}"},
        {"id": 2, "service": "steam", "appid": "7", "name": "Not installed", "slug": "b", "details": None},
    ]
    monkeypatch.setattr(ServiceGameCollection, "get_for_service", classmethod(lambda _cls, _service: service_games))

    listed = asyncio.run(services.list_service_games("steam"))
    assert [(game.appid, game.installed) for game in listed] == [("4242", True), ("7", False)]
    assert [game.appid for game in asyncio.run(services.list_service_games("steam", installed_only=True))] == ["4242"]


@pytest.mark.parametrize("source", ["humblebundle", "gamejolt", "dlsite"])
def test_removed_sources_hide_catalog_rows_but_keep_installed_games(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_game = {"id": 1, "service": source, "appid": "123", "name": "Retired", "slug": "retired"}
    monkeypatch.setattr(ServiceGameCollection, "get_service_games", classmethod(lambda _cls: [catalog_game]))
    game_id = games_db.add_game(
        name="Retired", slug="retired", runner="wine", installed=1, service=source, service_id="123"
    )

    try:
        assert source not in services.SERVICES
        assert asyncio.run(services.list_service_games(source)) == []
        assert asyncio.run(services.list_service_games()) == []
        installed = asyncio.run(games.get_game(str(game_id)))
        assert installed is not None and installed.installed and installed.runner == "wine"
        assert Game(game_id).get_service() is None
    finally:
        games_db.delete_game(game_id)


def test_flathub_is_a_source_but_not_a_visible_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(services.SERVICES, "flathub", FlathubService)
    game_id = str(
        games_db.add_game(
            name="Previous Flatpak Game",
            slug="previous-flatpak-game",
            runner="flatpak",
            service="flathub",
            service_id="org.example.Game",
            configpath="previous-flatpak-game",
            installed=1,
        )
    )
    service_id = sql.db_insert(
        settings.DB_PATH,
        "service_games",
        {"service": "flathub", "appid": "org.example.Game", "name": "Previous Flatpak Game", "slug": "flatpak-test"},
    )
    try:
        assert "flatpak" not in asyncio.run(runners.list_runners())
        assert "flathub" in {source.id for source in asyncio.run(preferences.list_sources())}
        assert [game.appid for game in asyncio.run(services.list_service_games("flathub"))] == ["org.example.Game"]
        assert "flathub" in {game.service for game in asyncio.run(services.list_service_games())}
        assert asyncio.run(games.get_game(game_id)) is not None
        info = asyncio.run(game_maintenance.get_maintenance_info(game_id))
        assert info.files_unavailable_reason == "This installation is managed outside Qtris."
        assert Game(game_id).check_launchable()
    finally:
        sql.db_delete(settings.DB_PATH, "service_games", "id", service_id)
        games_db.delete_game(game_id)
