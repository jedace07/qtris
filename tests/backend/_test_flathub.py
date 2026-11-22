"""Sync the Flathub source without making any host package changes."""

from __future__ import annotations

import asyncio
import gzip
from io import BytesIO
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from qtris import settings
from qtris.backend import game_maintenance, installers, library
from qtris.database import games, schema, sql
from qtris.database.services import ServiceGameCollection
from qtris.exceptions import QtrisError
from qtris.game import Game
from qtris.services import SERVICES
from qtris.services.flathub import FlathubService
from qtris.util import flatpak

if TYPE_CHECKING:
    from pathlib import Path

_EMULATOR = "org.example.Emulator"
_GAME = "org.example.Game"
_CATALOG = b"""<components>
  <component><name>An Emulator</name><categories><category>Emulator</category></categories>
    <bundle type="flatpak">app/org.example.Emulator/x86_64/stable</bundle></component>
  <component><name>A Game</name><categories><category>Game</category></categories>
    <bundle type="flatpak">app/org.example.Game/x86_64/stable</bundle></component>
  <component><name>A Utility</name><categories><category>Utility</category></categories>
    <bundle type="flatpak">app/org.example.Utility/x86_64/stable</bundle></component>
</components>"""


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FlathubService:
    config_path = tmp_path / "games"
    config_path.mkdir()
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "GAME_CONFIG_DIR", str(config_path))
    monkeypatch.setitem(SERVICES, "flathub", FlathubService)
    schema.syncdb()
    response = MagicMock(raw=BytesIO(gzip.compress(_CATALOG)))
    response.__enter__.return_value = response
    monkeypatch.setattr("qtris.services.flathub.requests.get", MagicMock(return_value=response))
    return FlathubService()


def test_sync_imports_only_catalog_apps_and_reuses_the_game_record(
    source: FlathubService, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = {_EMULATOR, "org.example.Utility"}
    monkeypatch.setattr(flatpak, "get_installed_apps", MagicMock(return_value=installed))
    source.reload()

    rows = asyncio.run(library.query_games(library.LibraryQuery(service="flathub")))
    assert [(row.appid, row.installed) for row in rows] == [(_EMULATOR, True), (_GAME, False)]
    row = rows[0]
    assert row.game_id and row.external_files
    assert Game(row.game_id).runner.game_config["appid"] == _EMULATOR
    maintenance = asyncio.run(game_maintenance.get_maintenance_info(row.game_id))
    with pytest.raises(QtrisError, match="desktop app store"):
        asyncio.run(game_maintenance.uninstall_game(row.game_id, maintenance, remove_from_library=True))
    assert not source.scan_installed_apps()

    initial = games.get_game_by_field(row.game_id, "id")
    assert initial
    sql.db_update(settings.DB_PATH, "games", {"playtime": 3}, {"id": row.game_id})
    installed.remove(_EMULATOR)
    assert source.scan_installed_apps()
    assert not Game(row.game_id).is_installed
    installed.add(_EMULATOR)
    assert source.scan_installed_apps()
    current = games.get_game_by_field(row.game_id, "id")
    assert current and current["installed"] and current["playtime"] == 3
    assert current["configpath"] == initial["configpath"]


def test_install_only_opens_the_host_app_store(source: FlathubService, monkeypatch: pytest.MonkeyPatch) -> None:
    source.load()
    open_store = MagicMock()
    monkeypatch.setattr(flatpak, "open_app_store", open_store)

    asyncio.run(installers.open_external_installation("flathub", _EMULATOR))

    open_store.assert_called_once_with(_EMULATOR)
    assert len(ServiceGameCollection.get_for_service("flathub")) == 2
    assert not games.get_games(filters={"service": "flathub"})


def test_failed_host_scan_does_not_mark_apps_uninstalled(
    source: FlathubService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flatpak, "get_installed_apps", MagicMock(return_value={_EMULATOR}))
    source.reload()

    monkeypatch.setattr(flatpak, "get_installed_apps", MagicMock(side_effect=flatpak.FlatpakError("Host unavailable")))
    with pytest.raises(flatpak.FlatpakError, match="Host unavailable"):
        source.scan_installed_apps()

    row = games.get_game_for_service("flathub", _EMULATOR)
    assert row and row["installed"]
