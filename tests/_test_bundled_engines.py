"""Store DOS and ScummVM games, which run the Windows engine they bundle through Proton."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from qtris import settings
from qtris.config import QtrisConfig
from qtris.database import games
from qtris.installer.interpreter import ScriptInterpreter
from qtris.runners.wine import wine
from qtris.services.gog import GOGService
from qtris.services.zoom import ZoomService
from qtris.util import extract
from qtris.util.test_config import setup_test_environment

if TYPE_CHECKING:
    from collections.abc import Iterator

setup_test_environment()

# The playTasks of GOG's builds of Akalabeth and Beneath a Steel Sky
DOSBOX_ARGS = '-conf "..\\dosboxAkalabeth.conf" -conf "..\\dosboxAkalabeth_single.conf" -noconsole -c "exit"'
DOSBOX_TASK = {"path": "DOSBOX\\DOSBox.exe", "arguments": DOSBOX_ARGS, "workingDir": "DOSBOX"}
SCUMMVM_TASK = {"path": "ScummVM\\scummvm.exe", "arguments": '-c "..\\beneath.ini" beneath', "workingDir": "ScummVM"}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config" / "games").mkdir(parents=True)
    yield tmp_path
    for game in games.get_games(filters={"slug": "bundled-engine-test"}):
        games.delete_game(game["id"])


def put(path: Path, data: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return path


def write_gog_info(folder: Path, task: dict[str, str]) -> None:
    info = {"playTasks": [{**task, "category": "game", "isPrimary": True, "type": "FileTask"}]}
    put(folder / "goggame-123.info", json.dumps(info))


def make_interpreter(plan: dict[str, Any], target: Path) -> ScriptInterpreter:
    interpreter = ScriptInterpreter(plan | {"name": "Bundled Engine Test", "game_slug": "bundled-engine-test"})
    interpreter.target_path = str(target)
    return interpreter


def launch(interpreter: ScriptInterpreter, monkeypatch: pytest.MonkeyPatch) -> tuple[wine, list[str]]:
    """Save the installation and return what its saved configuration launches."""
    game_id, _config = interpreter.installer.save()
    row = games.get_game_by_field(game_id, "id")
    assert row is not None and row["runner"] == "wine"
    runner = wine(QtrisConfig(runner_slug="wine", game_config_id=row["configpath"]))
    monkeypatch.setattr(runner, "get_command", lambda: ["umu-run"])
    monkeypatch.setattr(runner, "get_env", lambda **_options: {})
    return runner, runner.play()["command"]


def gog_plan(kind: str) -> dict[str, Any]:
    service = GOGService()
    game = {"name": "Bundled Engine Test", "appid": "123", "slug": "bundled-engine-test", "details": "{}"}
    plan = service._generate_depot_installer("bundled-engine-test", "wine", game)
    if kind == "offline":
        plan["script"] = service._offline_download_script("wine")
    return plan


@pytest.mark.parametrize(
    ("task", "exe", "arguments", "setting"),
    [
        (
            DOSBOX_TASK,
            "DOSBOX/DOSBox.exe",
            [
                "-conf",
                "..\\dosboxAkalabeth.conf",
                "-conf",
                "..\\dosboxAkalabeth_single.conf",
                "-noconsole",
                "-c",
                "exit",
            ],
            "dosboxAkalabeth_single.conf",
        ),
        (SCUMMVM_TASK, "ScummVM/scummvm.exe", ["-c", "..\\beneath.ini", "beneath"], "beneath.ini"),
    ],
)
def test_gog_depot_games_launch_their_engine_with_its_settings(
    home: Path, monkeypatch: pytest.MonkeyPatch, task: dict[str, str], exe: str, arguments: list[str], setting: str
) -> None:
    target = home / "install"
    folder = target / "Some Game"
    put(folder / exe)
    write_gog_info(folder, task)
    put(folder / "gog-support/123/app" / setting, "settings")
    interpreter = make_interpreter(gog_plan("depot"), target)

    interpreter._apply_gogdl_post_install(str(folder), "windows", "123")
    runner, command = launch(interpreter, monkeypatch)

    assert command == ["umu-run", str(folder / exe), *arguments]
    assert runner.working_dir == str((folder / exe).parent)
    assert runner.prefix_path == str(target / "pfx")
    # Where the arguments point from the working folder
    assert (Path(runner.working_dir).parent / setting).read_text() == "settings"


def test_gog_settings_already_in_place_are_kept(home: Path) -> None:
    folder = home / "game"
    put(folder / "SKY.CFG", "the user's")
    put(folder / "gog-support/123/app/sky.cfg", "GOG's")
    put(folder / "gog-support/123/app/set_config/options.ini", "nested")
    interpreter = make_interpreter(gog_plan("depot"), home)

    interpreter._place_gog_support_files(str(folder))

    assert (folder / "SKY.CFG").read_text() == "the user's"
    assert not (folder / "sky.cfg").exists()
    assert (folder / "set_config/options.ini").read_text() == "nested"


def test_other_gog_depot_games_are_left_as_they_are(home: Path) -> None:
    folder = home / "game"
    put(folder / "Game.exe")
    write_gog_info(folder, {"path": "Game.exe"})
    put(folder / "gog-support/123/app/config.ini")
    interpreter = make_interpreter(gog_plan("depot"), home)

    interpreter._apply_gogdl_post_install(str(folder), "windows", "123")

    assert not (folder / "config.ini").exists()


@pytest.mark.parametrize(("task", "exe"), [(DOSBOX_TASK, "DOSBOX/DOSBox.exe"), (SCUMMVM_TASK, "ScummVM/scummvm.exe")])
def test_gog_offline_installers_extract_to_the_same_launch(
    home: Path, monkeypatch: pytest.MonkeyPatch, task: dict[str, str], exe: str
) -> None:
    target = home / "install"
    interpreter = make_interpreter(gog_plan("offline"), target)
    interpreter.game_files["goginstaller"] = str(home / "setup.exe")
    monkeypatch.setattr(extract, "get_innoextract_list", lambda _file: [exe])

    def unpack(_file_id: str) -> None:
        put(target / exe)
        write_gog_info(target, task)

    monkeypatch.setattr(interpreter, "_extract_innosetup", unpack)

    interpreter.autosetup_gog_game("goginstaller")
    runner, command = launch(interpreter, monkeypatch)

    assert command[1] == str(target / exe)
    assert command[-1] == ("exit" if task is DOSBOX_TASK else "beneath")
    assert runner.working_dir == str((target / exe).parent)
    assert runner.prefix_path == str(target / "pfx")


@pytest.mark.parametrize(
    ("files", "exe", "arguments"),
    [
        (
            {"Launch Game.bat": '@echo off\n.\\DOSBOX\\dosbox.exe -conf .\\game.conf -conf ".\\my game.conf"\n'},
            "DOSBOX/dosbox.exe",
            ["-conf", "./game.conf", "-conf", "./my game.conf"],
        ),
        (
            {"game_single.conf": "", "game.conf": ""},
            "DOSBOX/dosbox.exe",
            ["-conf", "game.conf", "-conf", "game_single.conf"],
        ),
        ({}, "ScummVM/scummvm.exe", ["--path=Data", "--auto-detect"]),
    ],
)
def test_zoom_installers_launch_the_engine_they_bundle(
    home: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str], exe: str, arguments: list[str]
) -> None:
    target = home / "install"
    game = {"name": "Bundled Engine Test", "appid": "123", "slug": "bundled-engine-test", "details": "{}"}
    interpreter = make_interpreter(ZoomService()._generate_installer("wine", game), target)
    interpreter.game_files["zoominstaller"] = str(home / "setup.exe")
    monkeypatch.setattr(extract, "get_innoextract_list", lambda _file: [exe])

    def unpack(_file_id: str) -> None:
        put(target / exe)
        for name, text in files.items():
            put(target / name, text)

    monkeypatch.setattr(interpreter, "_extract_innosetup", unpack)

    interpreter.autosetup_zoom_platform("zoominstaller")
    runner, command = launch(interpreter, monkeypatch)

    assert command == ["umu-run", str(target / exe), *arguments]
    assert runner.working_dir == str(target)
    assert runner.prefix_path == str(target / "pfx")


def test_zoom_linux_archive_uses_its_launch_script(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = home / "installed"
    game = {"name": "Example", "appid": "123", "slug": "example", "details": '{"operating_systems": ["linux"]}'}
    interpreter = make_interpreter(ZoomService().generate_installer(game), target)
    launch_script = target / "Example" / "start.sh"

    def unpack(spec: dict[str, str]) -> None:
        assert spec == {"file": "zoominstaller", "dst": "$GAMEDIR"}
        put(launch_script, "#!/bin/sh\n")

    monkeypatch.setattr(interpreter, "extract", unpack)
    interpreter.autosetup_zoom_platform("zoominstaller")

    assert interpreter.installer.script["game"] == {
        "exe": str(launch_script),
        "working_dir": str(launch_script.parent),
    }
    assert launch_script.stat().st_mode & 0o100
