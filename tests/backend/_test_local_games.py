"""Exercise native game registration with real files and library data."""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from qtris import settings
from qtris.backend import events, game_actions, game_maintenance, local_games
from qtris.backend.models import GameInfo
from qtris.database import categories
from qtris.database import games as games_db
from qtris.delegates import CommandLineUIDelegate
from qtris.exceptions import QtrisError
from qtris.game import Game
from qtris.services.base import StoreLaunch
from qtris.util.test_config import setup_test_environment
from qtris.util.yaml import write_yaml_to_file
from tests.conftest import FakeMainLoop

setup_test_environment()


@pytest.fixture
def executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path / "config"))
    game = tmp_path / "Game folder" / "play game"
    game.parent.mkdir()
    game.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@"\n', encoding="utf-8")
    game.chmod(0o700)
    return game


def test_add_game_saves_launch_options_without_owning_files(executable: Path, fake_main_loop: FakeMainLoop) -> None:
    installed: list[GameInfo] = []
    registration = events.GAME_INSTALLED.register(installed.append)
    game_ids: list[str] = []

    async def run() -> None:
        game = await local_games.register_linux_game(str(executable), " Example Game ", '"two words" --flag')
        game_ids.append(game.id)
        duplicate = await local_games.register_linux_game(str(executable), game.name)
        game_ids.append(duplicate.id)
        assert game.id != duplicate.id
        assert (game.name, game.installed, game.runner, game.directory) == ("Example Game", True, "linux", None)
        launchable = Game(game.id)
        assert launchable.game_config_id != Game(duplicate.id).game_config_id
        gameplay = launchable.get_gameplay_info(CommandLineUIDelegate(None))
        result = subprocess.run(
            gameplay["command"], cwd=gameplay["working_dir"], check=True, capture_output=True, text=True, timeout=5
        )
        assert result.stdout.splitlines() == [str(executable.parent), "two words", "--flag"]
        assert await game_actions.get_installation_directory(game.id) == str(executable.parent)
        maintenance = await game_maintenance.get_maintenance_info(game.id)
        assert maintenance.installed and maintenance.files_unavailable_reason
        fake_main_loop.run_pending()
        assert installed == [game, duplicate]

    try:
        asyncio.run(run())
    finally:
        registration.unregister()
        for game_id in game_ids:
            games_db.delete_game(game_id)


def test_a_stores_launch_parameters_join_the_command(executable: Path, tmp_path: Path) -> None:
    game_ids: list[str] = []

    async def run() -> None:
        game = await local_games.register_linux_game(str(executable), "Store Game", "--own")
        game_ids.append(game.id)
        store_launch = StoreLaunch(
            args=["-AUTH_PASSWORD=one-time"], env={"FUEL_DIR": "/sdk"}, working_dir=str(tmp_path)
        )
        gameplay = Game(game.id).get_gameplay_info(CommandLineUIDelegate(None), store_launch)
        assert gameplay["working_dir"] == str(tmp_path)
        assert gameplay["env"]["FUEL_DIR"] == "/sdk"
        result = subprocess.run(
            gameplay["command"], cwd=executable.parent, check=True, capture_output=True, text=True, timeout=5
        )
        assert result.stdout.splitlines() == [str(executable.parent), "--own", "-AUTH_PASSWORD=one-time"]

    try:
        asyncio.run(run())
    finally:
        for game_id in game_ids:
            games_db.delete_game(game_id)


def test_link_preserves_library_identity_and_categories(executable: Path) -> None:
    game_id = games_db.add_game(name="Existing Game", installed=0, playtime=2.5, directory="/old/game/directory")
    category_id = categories.add_category("local-game-registration-test")
    try:
        categories.add_game_to_category(game_id, category_id)
        game = asyncio.run(local_games.register_linux_game(str(executable), "", game_id=game_id))
        assert (game.id, game.name, game.playtime, game.directory) == (game_id, "Existing Game", 2.5, None)
        assert game.installed
        assert categories.get_categories_in_game(game_id) == ["local-game-registration-test"]
        with pytest.raises(QtrisError, match="cannot be linked"):
            asyncio.run(local_games.register_linux_game(str(executable), "", game_id=game_id))
    finally:
        categories.remove_category_from_game(game_id, category_id)
        categories.remove_category(category_id)
        games_db.delete_game(game_id)


def test_reject_invalid_executables_and_store_entries(executable: Path, tmp_path: Path) -> None:
    executable.chmod(0o600)
    with pytest.raises(QtrisError, match="not executable"):
        asyncio.run(local_games.register_linux_game(str(executable), "Invalid Game"))
    with pytest.raises(QtrisError, match="absolute path"):
        asyncio.run(local_games.register_linux_game("./relative-launcher", "Invalid Game"))

    executable.chmod(0o700)
    game_id = games_db.add_game(name="Store Game", installed=0, service="steam", runner="steam")
    try:
        with pytest.raises(QtrisError, match="cannot be linked"):
            asyncio.run(local_games.register_linux_game(str(executable), "", game_id=game_id))
        assert not Game(game_id).is_installed
        assert not (tmp_path / "config" / "games").exists()
    finally:
        games_db.delete_game(game_id)


def test_database_failure_removes_new_config(executable: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_to_add(**_fields: object) -> str:
        raise OSError("database failure")

    monkeypatch.setattr(games_db, "add_game", fail_to_add)
    with pytest.raises(OSError, match="database failure"):
        asyncio.run(local_games.register_linux_game(str(executable), "Test Game"))
    assert not list((Path(settings.CONFIG_DIR) / "games").iterdir())
    assert executable.exists()


def test_existing_gog_game_launches_the_selected_manifest_option(
    executable: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "prefix"
    folder = prefix / "drive_c" / "GOG Games" / "Existing Game"
    default_exe = folder / "Bin" / "Start.exe"
    vulkan_exe = folder / "Bin" / "Vulkan.exe"
    vulkan_data = folder / "Vulkan Data"
    vulkan_data.mkdir(parents=True)
    default_exe.parent.mkdir()
    for path in (default_exe, vulkan_exe):
        path.touch()
    tasks = [
        {"isPrimary": True, "path": r"bin\start.exe", "name": "Standard"},
        {"path": r"\bin\vulkan.exe", "workingDir": "vulkan data", "arguments": '--mode "Vulkan 1"', "name": "Vulkan"},
        {"path": "bin/Vulkan.exe", "isHidden": True, "name": "Hidden"},
        {"path": "bin/Vulkan.exe", "category": "launcher", "name": "Launcher"},
        {"path": "bin/Vulkan.exe", "category": "document", "name": "Manual"},
        {"path": str(executable), "name": "Outside install"},
        {"path": "missing.exe", "name": "Missing"},
    ]
    (folder / "goggame-42.info").write_text(json.dumps({"playTasks": tasks}), encoding="utf-8")
    config_dir = Path(settings.CONFIG_DIR) / "games"
    config_dir.mkdir(parents=True)
    write_yaml_to_file({"game": {"exe": str(default_exe), "prefix": str(prefix)}}, str(config_dir / "gog-existing.yml"))
    game_id = games_db.add_game(
        name="Existing GOG Game",
        service="gog",
        service_id="42",
        runner="wine",
        directory=str(prefix),
        configpath="gog-existing",
        installed=1,
    )
    try:
        game = Game(game_id)
        monkeypatch.setattr(game.runner, "get_command", lambda: ["umu-run"])
        monkeypatch.setattr(game.runner, "play", lambda: {"command": ["umu-run", str(default_exe)]})
        name, alternatives = game.get_launch_options()
        assert name == "Standard"
        assert [option["name"] for option in alternatives] == ["Vulkan", "Launcher"]
        gameplay = game.get_gameplay_info(CommandLineUIDelegate("Vulkan"))
        assert gameplay["command"] == ["umu-run", str(vulkan_exe), "--mode", "Vulkan 1"]
        assert gameplay["working_dir"] == str(vulkan_data)
    finally:
        games_db.delete_game(game_id)


def test_gog_import_uses_the_linux_start_script_in_a_nested_install(tmp_path: Path) -> None:
    folder = tmp_path / "Existing Game"
    game_data = folder / "game"
    game_data.mkdir(parents=True)
    start = folder / "start.sh"
    start.write_text("#!/bin/sh\n", encoding="utf-8")
    start.chmod(0o755)
    (game_data / "game").touch()
    (game_data / "goggame-42.info").write_text(
        json.dumps({"playTasks": [{"isPrimary": True, "path": "game", "arguments": "--from-manifest"}]}),
        encoding="utf-8",
    )

    options = local_games._detect_gog_launch(tmp_path, "42")

    assert options["exe"] == str(start) and options["working_dir"] == str(folder)
    assert "args" not in options
