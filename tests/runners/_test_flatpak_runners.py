"""Launch Flatpaks and hand off missing emulator installs to the desktop."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from qtris.delegates import InstallUIDelegate
from qtris.runners import RunnerInstallationError, get_user_runner_names
from qtris.runners.eden import eden
from qtris.runners.flatpak import flatpak as FlatpakRunner
from qtris.runners.json import load_json_runners
from qtris.util import flatpak


@pytest.fixture
def host_flatpak(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Pretend Flatpak is on the system, with the returned set of apps installed."""
    installed: set[str] = set()
    monkeypatch.setattr(flatpak, "get_executable", lambda: "/usr/bin/flatpak")
    monkeypatch.setattr(flatpak, "is_app_installed", lambda appid: appid in installed)
    return installed


def make_eden(rom: Path) -> eden:
    runner = eden()
    runner._config = MagicMock(game_config={"main_file": str(rom)}, runner_config={}, system_config={})
    runner.has_explicit_config = True
    return runner


def test_flatpak_runner_launches_its_flatpak_with_the_game_folder_shared(
    tmp_path: Path, host_flatpak: set[str]
) -> None:
    host_flatpak.add("dev.eden_emu.eden")
    rom = tmp_path / "games" / "game.nsp"
    rom.parent.mkdir()
    rom.write_bytes(b"")

    command = make_eden(rom).play()["command"]

    assert command[:4] == ["/usr/bin/flatpak", "run", f"--filesystem={rom.parent}:rw", "dev.eden_emu.eden"]
    assert command[-2:] == ["-g", str(rom)]


def test_flatpak_runner_passes_the_game_environment_to_the_app(tmp_path: Path, host_flatpak: set[str]) -> None:
    host_flatpak.add("dev.eden_emu.eden")
    rom = tmp_path / "game.nsp"
    rom.write_bytes(b"")
    runner = make_eden(rom)
    env = {"DRI_PRIME": "1", "VK_ICD_FILENAMES": "/usr/share/vulkan/icd.d/nvidia.json", "LC_ALL": "C"}

    command = runner.prepare_game_command(runner.play()["command"], env)

    app = command.index("dev.eden_emu.eden")
    assert command[app - 2 : app] == ["--env=DRI_PRIME=1", "--env=LC_ALL=C"]
    # Driver paths from outside the app's sandbox would break it
    assert "VK_ICD_FILENAMES" not in env


def test_missing_flatpak_names_the_flathub_app(tmp_path: Path, host_flatpak: set[str]) -> None:
    runner = make_eden(tmp_path / "game.nsp")
    assert not runner.is_installed()
    assert runner.can_install
    with pytest.raises(Exception, match=re.escape("dev.eden_emu.eden")):
        runner.get_command()


def test_legacy_runner_hands_off_install_without_reporting_success(monkeypatch: pytest.MonkeyPatch) -> None:
    open_store = MagicMock()
    monkeypatch.setattr(flatpak, "open_app_store", open_store)
    monkeypatch.setattr(flatpak, "is_installed", lambda: True)
    callback = MagicMock()
    ruffle = load_json_runners()["ruffle"]()

    with pytest.raises(RunnerInstallationError, match="Finish installing"):
        ruffle.install(InstallUIDelegate(), callback=callback)

    open_store.assert_called_once_with("rs.ruffle.Ruffle")
    callback.assert_not_called()


@pytest.mark.parametrize("sandbox", [False, True])
def test_app_store_uses_the_hosts_xdg_handler(sandbox: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    run = MagicMock()
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(os.path, "exists", lambda path: sandbox and path == "/.flatpak-info")
    monkeypatch.setattr(flatpak, "get_executable", lambda: "/usr/bin/flatpak-spawn")

    flatpak.open_app_store("rs.ruffle.Ruffle")

    prefix = ["/usr/bin/flatpak-spawn", "--host"] if sandbox else []
    assert run.call_count == 1
    assert run.call_args.args[0] == [*prefix, "xdg-open", "appstream://rs.ruffle.Ruffle"]


def test_missing_app_store_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=OSError))
    with pytest.raises(flatpak.FlatpakError, match="host's app store"):
        flatpak.open_app_store("rs.ruffle.Ruffle")


def test_hidden_runner_uses_the_catalog_app_id(host_flatpak: set[str]) -> None:
    appid = "rs.ruffle.Ruffle"
    runner = FlatpakRunner()
    runner._config = MagicMock(game_config={"appid": appid, "args": '--example "two words"'})
    assert {"flatpak", "eden", "ruffle"}.isdisjoint(get_user_runner_names())
    assert not runner.is_installed()
    host_flatpak.add(appid)
    assert runner.is_installed()
    assert runner.play()["command"] == ["/usr/bin/flatpak", "run", appid, "--example", "two words"]


def test_flatpak_spawn_reaches_the_hosts_flatpak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flatpak, "get_executable", lambda: "/usr/bin/flatpak-spawn")
    assert flatpak.get_run_command("org.libretro.RetroArch") == [
        "/usr/bin/flatpak-spawn",
        "--host",
        "flatpak",
        "run",
        "org.libretro.RetroArch",
    ]


def test_system_folders_are_not_shared(tmp_path: Path) -> None:
    game = tmp_path / "game.rom"
    game.write_bytes(b"")
    assert flatpak.get_shareable_paths(["/", "/usr/share", str(game), str(tmp_path), "relative", "/missing"]) == [
        str(tmp_path)
    ]
