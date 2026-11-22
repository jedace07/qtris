"""Launch host Flatpaks and hand off installs to the desktop."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from qtris.runners import get_user_runner_names
from qtris.runners.flatpak import flatpak as FlatpakRunner
from qtris.runners.runner import Runner
from qtris.util import flatpak


@pytest.fixture
def host_flatpak(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Pretend Flatpak is on the system, with the returned set of apps installed."""
    installed: set[str] = set()
    monkeypatch.setattr(flatpak, "get_executable", lambda: "/usr/bin/flatpak")
    monkeypatch.setattr(flatpak, "is_app_installed", lambda appid: appid in installed)
    return installed


APP_ID = "org.example.Player"


class AppRunner(Runner):
    """A runner with a Flathub build, as Steam's is."""

    human_name = "Player"
    flatpak_id = APP_ID


def make_runner(game_file: Path) -> AppRunner:
    runner = AppRunner()
    runner._config = MagicMock(game_config={"main_file": str(game_file)}, runner_config={}, system_config={})
    runner.has_explicit_config = True
    return runner


def test_flatpak_runner_launches_its_flatpak_with_the_game_folder_shared(
    tmp_path: Path, host_flatpak: set[str]
) -> None:
    host_flatpak.add(APP_ID)
    game = tmp_path / "games" / "game.bin"
    game.parent.mkdir()
    game.write_bytes(b"")

    command = make_runner(game).get_command()

    assert command == ["/usr/bin/flatpak", "run", f"--filesystem={game.parent}:rw", APP_ID]


def test_flatpak_runner_passes_the_game_environment_to_the_app(tmp_path: Path, host_flatpak: set[str]) -> None:
    host_flatpak.add(APP_ID)
    game = tmp_path / "game.bin"
    game.write_bytes(b"")
    runner = make_runner(game)
    env = {"DRI_PRIME": "1", "VK_ICD_FILENAMES": "/usr/share/vulkan/icd.d/nvidia.json", "LC_ALL": "C"}

    command = runner.prepare_game_command(runner.get_command(), env)

    app = command.index(APP_ID)
    assert command[app - 2 : app] == ["--env=DRI_PRIME=1", "--env=LC_ALL=C"]
    # Driver paths from outside the app's sandbox would break it
    assert "VK_ICD_FILENAMES" not in env


def test_missing_flatpak_names_the_flathub_app(tmp_path: Path, host_flatpak: set[str]) -> None:
    runner = make_runner(tmp_path / "game.bin")
    assert not runner.is_installed()
    with pytest.raises(Exception, match=re.escape(APP_ID)):
        runner.get_command()


@pytest.mark.parametrize("sandbox", [False, True])
def test_app_store_uses_the_hosts_xdg_handler(sandbox: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    run = MagicMock()
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(os.path, "exists", lambda path: sandbox and path == "/.flatpak-info")
    monkeypatch.setattr(flatpak, "get_executable", lambda: "/usr/bin/flatpak-spawn")

    flatpak.open_app_store(APP_ID)

    prefix = ["/usr/bin/flatpak-spawn", "--host"] if sandbox else []
    assert run.call_count == 1
    assert run.call_args.args[0] == [*prefix, "xdg-open", f"appstream://{APP_ID}"]


def test_missing_app_store_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=OSError))
    with pytest.raises(flatpak.FlatpakError, match="host's app store"):
        flatpak.open_app_store(APP_ID)


def test_hidden_runner_uses_the_catalog_app_id(host_flatpak: set[str]) -> None:
    appid = APP_ID
    runner = FlatpakRunner()
    runner._config = MagicMock(game_config={"appid": appid, "args": '--example "two words"'})
    assert "flatpak" not in get_user_runner_names()
    assert not runner.is_installed()
    host_flatpak.add(appid)
    assert runner.is_installed()
    assert runner.play()["command"] == ["/usr/bin/flatpak", "run", appid, "--example", "two words"]


def test_flatpak_spawn_reaches_the_hosts_flatpak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flatpak, "get_executable", lambda: "/usr/bin/flatpak-spawn")
    assert flatpak.get_run_command(APP_ID) == [
        "/usr/bin/flatpak-spawn",
        "--host",
        "flatpak",
        "run",
        APP_ID,
    ]


def test_system_folders_are_not_shared(tmp_path: Path) -> None:
    game = tmp_path / "game.bin"
    game.write_bytes(b"")
    assert flatpak.get_shareable_paths(["/", "/usr/share", str(game), str(tmp_path), "relative", "/missing"]) == [
        str(tmp_path)
    ]
