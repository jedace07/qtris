"""Find and select install scripts for library games without running them."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from qtris import settings
from qtris.backend import installers, services
from qtris.database import games as games_db
from qtris.exceptions import QtrisError
from qtris.installer import commands
from qtris.installer.interpreter import ScriptInterpreter
from qtris.runners.wine import wine
from qtris.services import gog
from qtris.services.ubisoft import UbisoftConnectService
from qtris.util import game_finder, magic
from qtris.util.test_config import setup_test_environment

setup_test_environment()

SLUG = "backend-installers-test"


def make_script(version: str, **script: Any) -> dict[str, Any]:
    return {
        "name": "Installer Test",
        "game_slug": SLUG,
        "slug": f"{SLUG}-{version.lower()}",
        "version": version,
        "runner": "linux",
        "description": f"  The {version} version  ",
        "notes": "Bring your own files",
        "credits": "",
        "script": {
            "game": {"exe": "$GAMEDIR/game.sh"},
            "files": [{"setup": "N/A:Select the game"}],
            "installer": [{"extract": {"file": "setup", "dst": "$GAMEDIR"}}],
            **script,
        },
    }


@pytest.fixture
def scripts() -> list[dict[str, Any]]:
    return [
        make_script("Native"),
        {"name": "Broken", "version": "Missing fields"},
        make_script("Extra", files=[{"one": "a", "two": "b"}]),
        {**make_script("Flatpak"), "runner": "flatpak"},
        make_script("Requires", requires="backend-installers-base-game"),
    ]


def test_catalog_lists_usable_plans_as_plain_choices(scripts: list[dict[str, Any]]) -> None:
    catalog = installers._catalog_from_scripts(scripts)

    assert catalog.game_name == "Installer Test"
    assert catalog.game_slug == SLUG
    assert [choice.version for choice in catalog.choices] == ["Native", "Requires"]
    native = catalog.choices[0]
    assert native.runner == "linux"
    assert native.runner_name == "Linux"
    assert native.description == "The Native version"
    assert native.notes == "Bring your own files"
    assert native.credits == ""
    asyncio.run(installers.close_catalog(catalog.id))


def test_unknown_sources_and_empty_catalogs_are_user_errors() -> None:
    with pytest.raises(QtrisError, match="source is unavailable"):
        asyncio.run(installers.find_store_installers("unknown-store", "42"))
    with pytest.raises(QtrisError, match="No installer available"):
        installers._catalog_from_scripts([])


def test_selecting_checks_the_script_without_running_it(scripts: list[dict[str, Any]]) -> None:
    async def run() -> None:
        catalog = installers._catalog_from_scripts(scripts)
        selection = await installers.select_installer(catalog.id, catalog.choices[0].id)

        assert selection.catalog_id == catalog.id
        assert selection.version == "Native"
        assert selection.creates_game_folder
        assert selection.default_directory.endswith(os.sep + SLUG)
        assert not os.path.exists(selection.default_directory)
        assert selection.runners_to_install == ()
        assert selection.required_game_slug is None
        # Selecting again starts from the script as it was found
        assert await installers.select_installer(catalog.id, catalog.choices[0].id) == selection
        await installers.close_catalog(catalog.id)

    asyncio.run(run())
    assert "requires" not in scripts[0]["script"]


def test_selection_reports_a_missing_required_game(scripts: list[dict[str, Any]]) -> None:
    async def run() -> None:
        catalog = installers._catalog_from_scripts(scripts)
        selection = await installers.select_installer(catalog.id, catalog.choices[1].id)

        assert selection.required_game_slug == "backend-installers-base-game"
        assert selection.default_directory == ""
        await installers.close_catalog(catalog.id)

    asyncio.run(run())


def test_closed_and_evicted_catalogs_are_forgotten(scripts: list[dict[str, Any]]) -> None:
    async def run() -> None:
        first = installers._catalog_from_scripts(scripts)
        await installers.close_catalog(first.id)
        with pytest.raises(QtrisError, match="no longer available"):
            await installers.select_installer(first.id, "0")

        oldest = installers._catalog_from_scripts(scripts)
        newer = [installers._catalog_from_scripts(scripts) for _ in range(installers._CATALOG_LIMIT)]
        with pytest.raises(QtrisError, match="no longer available"):
            await installers.select_installer(oldest.id, "0")
        for catalog in newer:
            await installers.close_catalog(catalog.id)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("has_gogdl", "builds_status", "builds", "offline_platforms"),
    [
        (True, 200, [{"generation": 1}], ()),
        (True, 200, [], ("wine",)),
        (True, 404, [], ("wine",)),
        (True, 503, [], ()),
        (False, 200, [], ("linux", "wine")),
    ],
)
def test_gog_catalog_has_one_download_per_platform(
    monkeypatch: pytest.MonkeyPatch,
    has_gogdl: bool,
    builds_status: int,
    builds: list[dict[str, int]],
    offline_platforms: tuple[str, ...],
) -> None:
    details = {"slug": "gog-download-test", "worksOn": {"Windows": True, "Mac": True, "Linux": True}}
    game = {"name": "GOG Download Test", "appid": "1234567", "details": json.dumps(details)}
    monkeypatch.setattr(installers.ServiceGameCollection, "get_game", lambda *_args: game)
    monkeypatch.setattr(gog.GOGService, "is_authenticated", lambda self: True)
    monkeypatch.setattr(gog, "is_gogdl_available", lambda: has_gogdl)
    build_lookups: list[str] = []

    def request_builds(url: str, *, timeout: int) -> SimpleNamespace:
        assert has_gogdl
        assert url.endswith("/1234567/os/windows/builds?generation=2")
        build_lookups.append(url)
        if builds_status != 200:
            raise gog.HTTPError("Build lookup failed", code=builds_status)
        return SimpleNamespace(get=lambda: SimpleNamespace(json={"items": builds}))

    monkeypatch.setattr(gog, "Request", request_builds)

    async def run() -> None:
        catalog = await installers.find_store_installers("gog", "1234567")
        try:
            assert [choice.version for choice in catalog.choices] == ["Linux download", "Windows download"]
            assert [choice.runner for choice in catalog.choices] == ["linux", "wine"]
            for choice in catalog.choices:
                selection = await installers.select_installer(catalog.id, choice.id)
                assert selection.creates_game_folder
            assert not build_lookups
            for choice in catalog.choices:
                interpreter, _kind = await installers.build_interpreter(
                    catalog.id, choice.id, ScriptInterpreter.InterpreterUIDelegate()
                )
                script = interpreter.installer.script
                if choice.runner in offline_platforms:
                    assert interpreter.installer.script_files[0].id == "goginstaller"
                    platform = "linux" if choice.runner == "linux" else "windows"
                    other_platform = "windows" if platform == "linux" else "linux"
                    downloads = {"installers": [{"os": other_platform, "language": "en"}]}
                    service = interpreter.installer.service
                    assert isinstance(service, gog.GOGService)
                    assert service._get_installer_links(interpreter.installer, downloads) == []
                else:
                    assert "gogdl_setup" in script["installer"][0]
            assert len(build_lookups) == int(has_gogdl)
        finally:
            await installers.close_catalog(catalog.id)

    asyncio.run(run())


def test_store_content_targets_the_selected_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from qtris.installer import InstallationKind

    slug = "shared-install-slug"
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    wrong = games_db.add_game(name="Other", slug=slug, installed=1, directory=str(first))
    expected = games_db.add_game(
        name="GOG game", slug=slug, installed=1, directory=str(second), service="gog", service_id="4780", runner="linux"
    )
    script = {**make_script("Patch", extends=slug), "game_slug": slug}
    monkeypatch.setattr(gog.GOGService, "is_authenticated", lambda self: True)
    monkeypatch.setattr(gog.GOGService, "get_update_installers", lambda self, row: [script])

    async def run() -> None:
        catalog = await installers.find_store_installers("gog", "4780", str(expected), InstallationKind.UPDATE)
        try:
            selection = await installers.select_installer(catalog.id, "0")
            assert not selection.creates_game_folder and selection.default_directory == str(second)
            interpreter, kind = await installers.build_interpreter(
                catalog.id, "0", ScriptInterpreter.InterpreterUIDelegate()
            )
            assert kind == InstallationKind.UPDATE
            assert interpreter.installer.save() == (str(expected), None)
        finally:
            await installers.close_catalog(catalog.id)

    try:
        asyncio.run(run())
    finally:
        games_db.delete_game(str(wrong))
        games_db.delete_game(str(expected))


def test_store_plan_uses_the_preferred_launcher_as_its_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher_slug = UbisoftConnectService.client_installer
    ids = [
        str(
            games_db.add_game(
                name=name,
                slug=name,
                installer_slug=launcher_slug,
                installed=1,
                runner="wine",
                directory=str(tmp_path / name),
                configpath=name,
            )
        )
        for name in ("first-launcher", "preferred-launcher")
    ]
    saved: dict[str, str] = {}
    monkeypatch.setattr(settings, "write_setting", lambda key, value, section: saved.update({key: value}))
    monkeypatch.setattr(settings, "read_setting", lambda key, default="", section="qtris": saved.get(key, default))
    monkeypatch.setattr(UbisoftConnectService, "is_authenticated", lambda self: True)
    monkeypatch.setattr(installers.ServiceGameCollection, "get_game", lambda *_args: {"appid": "test"})
    monkeypatch.setattr(
        UbisoftConnectService, "generate_installers", lambda self, row: [make_script("Store", requires=launcher_slug)]
    )

    async def run() -> None:
        with pytest.raises(QtrisError, match="no longer available"):
            await services.set_source_launcher("ubisoft", "no-such-launcher")
        await services.set_source_launcher("ubisoft", ids[1])
        assert (await services.list_source_launchers("ubisoft"))[0].id == ids[1]
        catalog = await installers.find_store_installers("ubisoft", "test")
        try:
            selection = await installers.select_installer(catalog.id, "0")
            assert selection.default_directory == str(tmp_path / "preferred-launcher")
            await services.set_source_launcher("ubisoft", ids[0])
            with pytest.raises(QtrisError, match="launcher changed"):
                await installers.select_installer(catalog.id, "0")
        finally:
            await installers.close_catalog(catalog.id)

    try:
        asyncio.run(run())
    finally:
        for game_id in ids:
            games_db.delete_game(game_id)


@pytest.mark.parametrize(("windows_version", "locale"), [("win10", ""), ("win7", "ja_JP.utf8")])
def test_windows_setup_preserves_a_literal_executable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows_version: str, locale: str
) -> None:
    executable = tmp_path / "Setup $GAMEDIR.exe"
    executable.touch()
    prefix_root = tmp_path / "prefixes"
    monkeypatch.setattr(settings, "INSTALLER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(installers, "get_prefix_root", lambda: prefix_root)

    async def run() -> None:
        catalog = await installers.prepare_windows_setup("Test Game", str(executable), windows_version, locale)
        try:
            selection = await installers.select_installer(catalog.id, "0")
            assert selection.default_directory == str(prefix_root / "test-game")
            interpreter, _ = await installers.build_interpreter(
                catalog.id, "0", ScriptInterpreter.InterpreterUIDelegate()
            )
            assert interpreter.installer.runner == "wine"
            assert interpreter.installer.creates_game_folder
            assert interpreter.target_path == selection.default_directory and not prefix_root.exists()
            assert interpreter._substitute("$SETUP_EXE") == str(executable)
            tasks = [step["task"] for step in interpreter.installer.script["installer"]]
            assert tasks[-1] == {"name": "wineexec", "executable": "$SETUP_EXE", "arch": "win64"}
            assert interpreter.script_env == ({"LC_ALL": locale} if locale else {})
            if windows_version == "win10":
                assert len(tasks) == 1
            else:
                assert tasks[0] == {"name": "winetricks", "app": windows_version, "arch": "win64"}
        finally:
            await installers.close_catalog(catalog.id)

    asyncio.run(run())


def test_windows_setup_uses_the_chosen_prefix_for_tasks_and_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = tmp_path / "Setup.exe"
    setup.touch()
    prefix = tmp_path / "Prefix $GAMEDIR"
    drive_c = prefix / "drive_c"
    drive_c.mkdir(parents=True)
    (prefix / "system.reg").write_text("#arch=win64\n", encoding="utf-8")
    other_game = drive_c / "Long name for another game.exe"
    other_game.touch()
    game_exe = drive_c / "Game.exe"
    task_arguments: dict[str, Any] = {}

    def execute_task(**kwargs: Any) -> None:
        task_arguments.update(kwargs)

    monkeypatch.setattr(commands, "import_task", lambda *_args: execute_task)
    monkeypatch.setattr(ScriptInterpreter, "get_wine_path", lambda _self: "/bin/wine")
    monkeypatch.setattr(magic, "from_file", lambda *_args: "PE32+ executable (GUI) x86-64")
    monkeypatch.setattr(
        game_finder, "stat", lambda filename: SimpleNamespace(st_ctime_ns=1 if filename == str(game_exe) else 0)
    )

    async def run() -> None:
        catalog = await installers.prepare_windows_setup("Test Game", str(setup))
        try:
            selection = await installers.select_installer(catalog.id, "0")
            assert selection.can_choose_wine_prefix
            interpreter, _ = await installers.build_interpreter(
                catalog.id, "0", ScriptInterpreter.InterpreterUIDelegate(), wine_prefix=str(prefix)
            )
            interpreter.target_path = str(tmp_path / "Install folder")
            interpreter.installer.prefix_install_started_at = 1
            game_exe.touch()
            interpreter.task({"name": "wineexec", "executable": "$SETUP_EXE", "prefix": "$GAMEDIR"})
            assert task_arguments["prefix"] == str(prefix) and task_arguments["executable"] == str(setup)
            launch = interpreter.installer.get_game_config()["game"]
            assert launch["prefix"] == str(prefix) and launch["exe"] == str(game_exe)
        finally:
            await installers.close_catalog(catalog.id)

    asyncio.run(run())


def test_generated_windows_store_installer_saves_the_default_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game = {"name": "Game", "appid": "8675309"}
    source = gog.GOGService()
    catalog = installers._catalog_from_scripts(
        [source._generate_depot_installer("game", "wine", game)], installers._Origin(source, game["appid"])
    )
    prefix_root = tmp_path / "prefixes"
    monkeypatch.setattr(installers, "get_prefix_root", lambda: prefix_root)
    monkeypatch.setattr(gog.GOGService, "is_authenticated", lambda _self: True)
    monkeypatch.setattr(gog.GOGService, "resolve_download_installer", lambda _self, script: script)
    initialized: list[str] = []
    monkeypatch.setattr(wine, "initialize_prefix", lambda _runner, prefix, *, version: initialized.append(prefix))

    async def run() -> None:
        try:
            selection = await installers.select_installer(catalog.id, "0")
            assert selection.can_choose_wine_prefix and not prefix_root.exists()
            interpreter, _ = await installers.build_interpreter(
                catalog.id, "0", ScriptInterpreter.InterpreterUIDelegate()
            )
            assert interpreter.target_path == selection.default_directory
            prefix = prefix_root / "game"
            assert interpreter.installer.get_game_config()["game"]["prefix"] == str(prefix)
            assert prefix.is_dir()
            interpreter._initialize_wine_prefix()
            assert initialized == [str(prefix)]
            (prefix / "system.reg").touch()
            interpreter.cleanup()
            assert prefix.is_dir()
            (prefix / "system.reg").unlink()
            interpreter.cleanup()
            assert not prefix.exists()
        finally:
            await installers.close_catalog(catalog.id)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("source_id", "slug", "exe", "args"),
    [
        (
            "battlenet",
            "battlenet",
            "Program Files (x86)/Battle.net/Battle.net.exe",
            [r"--installpath=C:\Program Files (x86)\Battle.net", "--lang=enUS"],
        ),
        ("ea_app", "ea-app", "Program Files/Electronic Arts/EA Desktop/EA Desktop/EALauncher.exe", ["/S"]),
        ("ubisoft", "ubisoft-connect", "Program Files (x86)/Ubisoft/Ubisoft Game Launcher/UbisoftConnect.exe", ["/S"]),
    ],
)
def test_store_launcher_setup_saves_a_launchable_ge_proton_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_id: str, slug: str, exe: str, args: list[str]
) -> None:
    from qtris.config import QtrisConfig
    from qtris.installer.errors import ScriptingError
    from qtris.services.base import BaseService
    from qtris.util.strings import split_arguments
    from qtris.util.wine.wine import GE_PROTON_LATEST

    config_dir = tmp_path / "config"
    (config_dir / "games").mkdir(parents=True)
    monkeypatch.setattr(settings, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(BaseService, "list_launchers", lambda self: [])
    monkeypatch.setattr(ScriptInterpreter, "get_wine_path", lambda self: "/test/bin/wine")
    task_arguments: dict[str, Any] = {}
    monkeypatch.setattr(commands, "import_task", lambda *_args: lambda **kwargs: task_arguments.update(kwargs))
    target = tmp_path / slug
    expected = target / "drive_c" / exe
    setup = tmp_path / "vendor setup.exe"
    setup.touch()

    async def run() -> None:
        catalog = await installers.prepare_store_launcher(source_id)
        try:
            selection = await installers.select_installer(catalog.id, "0")
            assert selection.creates_game_folder and not selection.can_choose_wine_prefix
            assert Path(selection.default_directory).name == slug
            interpreter, _ = await installers.build_interpreter(
                catalog.id, "0", ScriptInterpreter.InterpreterUIDelegate()
            )
            assert interpreter.installer.script_files[0].url.startswith("https://")
            interpreter.target_path = str(target)
            interpreter.game_files = {"setup": str(setup)}
            commands = interpreter.installer.script["installer"]
            interpreter.task(dict(commands[0]["task"]))
            assert split_arguments(task_arguments["args"]) == args
            expected_env = {"PROTON_ENABLE_WAYLAND": "0"}
            if source_id == "battlenet":
                expected_env["WINE_SIMULATE_WRITECOPY"] = "1"
            assert task_arguments["env"] == expected_env
            assert task_arguments["prefix"] == str(target) and task_arguments["executable"] == str(setup)
            runner = task_arguments["runner"]
            assert runner.runner_config["version"] == GE_PROTON_LATEST and runner.runner_config["Graphics"] == "x11"

            with pytest.raises(ScriptingError, match="launcher was not installed"):
                interpreter.finish_launcher_setup(commands[1]["finish_launcher_setup"])
            expected.parent.mkdir(parents=True)
            expected.touch()
            interpreter.finish_launcher_setup(commands[1]["finish_launcher_setup"])
            game_id, config = interpreter.installer.save()
            try:
                assert config["game"]["exe"] == str(expected)
                assert config["game"]["prefix"] == str(target)
                assert config["system"]["env"] == expected_env
                row = games_db.get_game_by_field(game_id, "id")
                assert row and row["installer_slug"] == slug
                saved = QtrisConfig(runner_slug="wine", game_config_id=row["configpath"])
                assert saved.runner_config["version"] == GE_PROTON_LATEST
            finally:
                games_db.delete_game(game_id)
        finally:
            await installers.close_catalog(catalog.id)

    asyncio.run(run())
