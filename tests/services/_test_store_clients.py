"""The Epic Games Store through legendary and Amazon Games through Nile: library,
installation, launch and uninstallation, against stand-ins for the two clients."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from qtris.database.services import ServiceGameCollection
from qtris.game import Game
from qtris.installer.interpreter import ScriptInterpreter
from qtris.services.amazon import AmazonService
from qtris.services.egs import EpicGamesStoreService
from qtris.util import legendary, nile, store_cli
from qtris.util.store_cli import DownloadProgress, StoreClientError
from qtris.util.test_config import setup_test_environment
from tests.store_clients import install_store_clients

setup_test_environment()


@pytest.fixture(autouse=True)
def clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_store_clients(tmp_path, monkeypatch)


def interpreter(target: Path, appid: str, delegate: Mock | None = None) -> ScriptInterpreter:
    installer = {
        "script": {"game": {"prefix": "$GAMEDIR/pfx"}, "installer": []},
        "version": "test",
        "game_slug": "test",
        "name": "test",
        "slug": "test",
        "runner": "wine",
    }
    script = ScriptInterpreter(installer)
    script.installer.service_appid = appid
    script.target_path = str(target)
    script.interpreter_ui_delegate = delegate or Mock()
    return script


def store_game(appid: str) -> Game:
    return cast(Game, SimpleNamespace(appid=appid))


def test_epic_library_comes_from_legendary() -> None:
    service = EpicGamesStoreService()
    service.wipe_game_cache()
    games = service.load()
    assert [(game.appid, game.name) for game in games] == [("Sugar", "Sugar Rush")]
    row = ServiceGameCollection.get_game("egs", "Sugar")
    assert row is not None
    details = json.loads(str(row["details"]))
    # The artwork reads Epic's catalog images from the details
    assert details["keyImages"][0]["type"] == "DieselGameBoxTall"
    assert service.get_store_url(row) == "https://store.epicgames.com/p/sugar-rush"
    service.wipe_game_cache()


def test_epic_games_install_launch_and_uninstall_through_legendary(tmp_path: Path) -> None:
    target = tmp_path / "games" / "sugar-rush"
    delegate = Mock()
    script = interpreter(target, "Sugar", delegate)
    script.legendary_setup({"app_name": "Sugar"})

    exe = target / "Binaries" / "Sugar.exe"
    assert exe.is_file()
    assert script.installer.script["game"] == {"prefix": "$GAMEDIR/pfx", "exe": str(exe)}
    reported = [call.args for call in delegate.report_progress.call_args_list]
    assert (0.25, "ETA: 00:00:03") in reported and (1.0, "12.5 MiB/s — ETA: 00:00:03") in reported

    service = EpicGamesStoreService()
    launch = service.get_store_launch(store_game("Sugar"))
    assert launch is not None
    assert launch.args == ["-nosplash", "-AUTH_PASSWORD=one-time", "-epicapp=Sugar"]
    assert launch.env == {"EOS_OVERLAY": "0"}
    assert launch.working_dir == str(target / "Binaries")

    script.legendary_setup({"app_name": "Sugar", "command": "update"})
    installed = legendary.get_installed("Sugar")
    assert installed and installed["version"] == "2"

    service.forget_installed_game("Sugar")
    assert legendary.get_installed("Sugar") is None
    # legendary forgets the game but leaves its files to Qtris
    assert exe.is_file()


def test_amazon_library_comes_from_nile_without_duplicates() -> None:
    service = AmazonService()
    service.wipe_game_cache()
    games = service.load()
    # Amazon lists Lumen twice; the lowest ID is kept, as it always was
    assert sorted((game.appid, game.name) for game in games) == [("amzn-1", "Lumen"), ("amzn-3", "Quiet Tide")]
    service.wipe_game_cache()


def test_amazon_games_install_launch_and_uninstall_through_nile(tmp_path: Path) -> None:
    target = tmp_path / "games" / "lumen"
    script = interpreter(target, "amzn-1")
    script.nile_setup({"game_id": "amzn-1"})
    assert script.installer.script["game"]["exe"] == str(target / "Lumen.exe")

    launch = AmazonService().get_store_launch(store_game("amzn-1"))
    assert launch is not None
    assert launch.args == ["-fuel"]
    assert launch.env == {"FUEL_DIR": "/nile/SDK/Legacy"}

    AmazonService().forget_installed_game("amzn-1")
    assert nile.get_installed("amzn-1") is None
    assert (target / "Lumen.exe").is_file()


def test_store_client_errors_name_the_problem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = interpreter(tmp_path / "game", "")
    with pytest.raises(Exception, match="requires an app_name"):
        script.legendary_setup({})
    (tmp_path / "runtime" / "legendary" / "legendary").unlink()
    with pytest.raises(StoreClientError, match="hasn't been downloaded yet"):
        legendary.list_games()


def test_progress_lines_from_both_clients() -> None:
    progress = DownloadProgress()
    assert progress.parse_line("[DLManager] INFO: = Progress: 12.50% (10/80), Running for 00:00:05, ETA: 00:00:35")
    assert (progress.fraction, progress.eta) == (0.125, "00:00:35")
    assert progress.parse_line("[PROGRESS] INFO: = Progress: 50.00 400/800, Running for: 00:00:05, ETA: 00:00:05")
    assert (progress.fraction, progress.eta) == (0.5, "00:00:05")
    assert progress.parse_line("[PROGRESS] INFO:  + Download\t- 3.25 MiB/s")
    assert progress.speed_mib == 3.25
    assert not progress.parse_line("[DLManager] INFO: = Downloaded: 10.00 MiB, Written: 20.00 MiB")


def test_a_crashing_client_reports_why() -> None:
    output = 'Traceback (most recent call last):\n  File "core.py", line 192\nValueError: No saved credentials\n'
    assert store_cli._get_error("legendary", output, 1) == "No saved credentials"
    assert store_cli._get_error("legendary", "nothing useful", 2) == "legendary failed (exit code 2)."


@pytest.mark.parametrize("timeout", [False, True])
def test_epic_sign_in_errors_hide_authorization_codes(monkeypatch: pytest.MonkeyPatch, timeout: bool) -> None:
    code = "private-authorization-code"

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[1:4] == ["auth", "--code", code]
        if timeout:
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 1, "", f"ERROR: {code}")

    monkeypatch.setattr(subprocess, "run", run)
    message = "Timed out waiting for legendary" if timeout else "legendary failed"
    with pytest.raises(StoreClientError, match=message) as error:
        legendary.sign_in(code)
    assert code not in str(error.value)
    assert error.value.__cause__ is None


def test_epic_games_start_offline_only_when_the_sign_in_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run_json(_client: store_cli.StoreClient, args: list[str], timeout: float = 0) -> dict[str, str]:
        calls.append(args)
        if "--offline" not in args:
            raise StoreClientError(failure)
        return {"game_executable": "Game.exe"}

    monkeypatch.setattr(store_cli.StoreClient, "run_json", run_json)
    failure = "[cli] ERROR: Login failed, cannot continue!"
    assert legendary.get_launch_parameters("Sugar") == {"game_executable": "Game.exe"}
    assert calls[-1][-1] == "--offline"
    failure = "[cli] ERROR: Game Sugar is not currently installed!"
    with pytest.raises(StoreClientError, match="not currently installed"):
        legendary.get_launch_parameters("Sugar")
