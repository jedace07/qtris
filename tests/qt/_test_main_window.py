"""Render the Qt Quick library from seeded backend data."""

import asyncio
import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Q_ARG, QCoreApplication, QEvent, QMetaObject, QObject, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QImage, QWindow
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWebEngineQuick import QQuickWebEngineProfile

from qtris import settings
from qtris.backend import (
    configuration,
    events,
    game_actions,
    game_maintenance,
    games,
    installations,
    installers,
    media,
    preferences,
    runners,
    runtime_updates,
    services,
    shortcuts,
    store_login,
)
from qtris.backend.launch_configurations import LaunchChoice
from qtris.backend.models import ArtworkSettings, ServiceInfo, SourceSetting, StoragePaths
from qtris.database import categories as categories_db
from qtris.database import games as games_db
from qtris.database import sql
from qtris.exceptions import QtrisError
from qtris.game import LOG_BUFFERS, Game
from qtris.installer import interpreter
from qtris.qt import store_sources
from qtris.qt.main_window import MainWindow
from qtris.runners.wine import wine
from qtris.services.base import QtrisBanner, QtrisCoverart
from qtris.services.gog import GOGService
from qtris.util.test_config import setup_test_environment
from qtris.util.web_profiles import profile_directory
from qtris.util.yaml import read_yaml_from_file, write_yaml_to_file
from tests.conftest import FakeMainLoop

setup_test_environment()

PREFIX = "qt-window-test-"


@pytest.fixture
def library() -> Iterator[list[str]]:
    ids = [
        str(games_db.add_game(name=PREFIX + "Installed", slug=PREFIX + "installed", runner="linux", installed=1)),
        str(games_db.add_game(name=PREFIX + "Uninstalled", slug=PREFIX + "uninstalled", runner="linux", installed=0)),
    ]
    category_id = categories_db.add_category(PREFIX + "Category")
    categories_db.add_game_to_category(ids[0], category_id)
    yield ids
    for game_id in ids:
        for category in categories_db.get_categories_in_game(game_id):
            if row := categories_db.get_category_by_name(category):
                categories_db.remove_category_from_game(game_id, int(row["id"]))
        games_db.delete_game(game_id)
    categories_db.remove_category(category_id)


async def settle(window: MainWindow) -> None:
    await window.load_task
    for task in (window.controller._sidebar_task, window.controller._refresh_task):
        if task:
            await task


def listed(window: MainWindow) -> list[str]:
    model = window.controller.model
    names = []
    for row in range(model.rowCount()):
        game = model.game_at(row)
        assert game is not None
        if game.name.startswith(PREFIX):
            names.append(game.name)
    return names


def test_the_window_follows_sidebar_search_and_filter(qt_application: QGuiApplication, library: list[str]) -> None:
    games_db.update_existing(id=library[0], platform="Linux")
    games_db.update_existing(id=library[1], platform="Windows")

    async def run() -> None:
        window = MainWindow()
        controller = window.controller
        try:
            controller.set_installed_only(False)
            await settle(window)
            assert window.window.title() == "Qtris"
            assert listed(window) == [PREFIX + "Installed", PREFIX + "Uninstalled"]
            assert controller.sidebar.find_row({"platform": "Linux"}) == -1
            assert controller.sidebar.find_row({"platform": "Windows"}) == -1

            controller.set_search("platform:linux")
            controller.refresh()
            await settle(window)
            assert listed(window) == [PREFIX + "Installed"]

            controller.set_search("uninstalled")
            controller.refresh()
            await settle(window)
            assert listed(window) == [PREFIX + "Uninstalled"]

            controller.set_search("")
            row = controller.sidebar.find_row({"category": PREFIX + "Category"})
            assert row >= 0
            controller.select_view(row)
            await settle(window)
            assert listed(window) == [PREFIX + "Installed"]

            linux_row = controller.sidebar.find_row({"runner": "linux"})
            assert linux_row >= 0
            controller.select_view(linux_row)
            await settle(window)
            assert listed(window) == [PREFIX + "Installed"]

            controller.select_view(controller.sidebar.find_row({"category": "all"}))
            controller.set_installed_only(True)
            await settle(window)
            assert listed(window) == [PREFIX + "Installed"]
        finally:
            window.shutdown()

    asyncio.run(run())


async def wait_for_qml(application: QGuiApplication, ready: Callable[[], bool]) -> None:
    for _ in range(100):
        application.processEvents()
        if ready():
            return
        await asyncio.sleep(0.02)
    pytest.fail("Qt Quick did not reach the expected state")


def test_quit_shortcut_requests_application_shutdown(qt_application: QGuiApplication) -> None:
    async def run() -> None:
        window = MainWindow()
        try:
            await settle(window)
            window.show()
            await wait_for_qml(qt_application, window.window.isExposed)
            quit_requested = QSignalSpy(window.engine.quit)
            QTest.keyClick(window.window, Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
            await wait_for_qml(qt_application, lambda: quit_requested.count() == 1)
        finally:
            window.shutdown()

    asyncio.run(run())


def test_show_preserves_the_maximized_window(qt_application: QGuiApplication) -> None:
    async def run() -> None:
        window = MainWindow()
        try:
            await settle(window)
            window.window.showMaximized()
            window.show()
            assert window.window.visibility() == QWindow.Visibility.Maximized

            window.minimize()
            assert window.window.visibility() == QWindow.Visibility.Minimized
            window.show()
            assert window.window.visibility() == QWindow.Visibility.Maximized

            window.window.close()
            window.show()
            assert window.window.visibility() == QWindow.Visibility.Maximized
        finally:
            window.shutdown()

    asyncio.run(run())


def test_grid_reloads_artwork_and_keeps_selection_across_layouts(
    qt_application: QGuiApplication,
    library: list[str],
    fake_main_loop: FakeMainLoop,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SHOW_MEDIA", True)
    read_setting = settings.read_setting
    monkeypatch.setattr(
        settings,
        "read_setting",
        lambda key, default="", section="qtris": (
            "covers" if key == "qt_library_layout" else read_setting(key, default, section)
        ),
    )
    monkeypatch.setattr(settings, "write_setting", lambda *_args: None)
    monkeypatch.setattr(QtrisCoverart, "dest_path", str(tmp_path / "covers"))
    monkeypatch.setattr(QtrisBanner, "dest_path", str(tmp_path / "banners"))
    cover = QtrisCoverart().get_possible_media_paths(PREFIX + "installed")[1].path
    banner = QtrisBanner().get_possible_media_paths(PREFIX + "installed")[1].path

    def save_image(path: str, width: int, height: int, color: Qt.GlobalColor) -> None:
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(color)
        assert image.save(path)

    save_image(cover, 144, 192, Qt.GlobalColor.red)
    save_image(banner, 208, 96, Qt.GlobalColor.green)

    async def run() -> None:
        window = MainWindow()
        window.show()
        controller = window.controller
        try:
            controller.set_installed_only(False)
            await settle(window)
            row = controller.model.find_row(library[0])
            assert row >= 0
            controller.select_game(row)

            grid = window.window.findChild(QQuickItem, "libraryGrid")
            assert grid is not None

            def artwork_color() -> QColor:
                image = window.window.grabWindow()
                point = grid.mapToScene(QPointF(grid.property("cellWidth") * (row + 0.5), 45))
                return image.pixelColor((point * image.devicePixelRatio()).toPoint())

            await wait_for_qml(qt_application, lambda: artwork_color() == QColor(Qt.GlobalColor.red))
            assert controller.property("canPlay")
            controller.set_layout("banners")
            await wait_for_qml(qt_application, lambda: artwork_color() == QColor(Qt.GlobalColor.green))

            save_image(banner, 208, 96, Qt.GlobalColor.blue)
            events.ARTWORK_CHANGED.fire()
            fake_main_loop.run_pending()
            await wait_for_qml(qt_application, lambda: artwork_color() == QColor(Qt.GlobalColor.blue))

            controller.set_layout("list")
            assert window.window.findChild(QObject, "libraryList") is not None
            assert controller.property("selectedGameId") == library[0]
            assert controller.property("canPlay")
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_dialog_answers_a_launch_question(qt_application: QGuiApplication, library: list[str]) -> None:
    async def run() -> None:
        window = MainWindow()
        window.show()
        try:
            await settle(window)

            def answer() -> None:
                prompt = window.window.findChild(QObject, "qtrisInput")
                if prompt is not None and prompt.property("visible"):
                    seen_prompt.append(True)
                    QTest.keyClick(window.window, Qt.Key.Key_Escape)

            seen_prompt: list[bool] = []
            deadline = QTimer(window)
            deadline.setSingleShot(True)
            deadline.timeout.connect(lambda: window.prompts.answer(True, "timeout"))

            QTimer.singleShot(50, answer)
            deadline.start(1000)
            assert window.prompts.ask("question", "Launch", "Proceed?") is None
            deadline.stop()
            assert seen_prompt
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_actions_update_favorites_and_categories(
    qt_application: QGuiApplication, library: list[str], fake_main_loop: FakeMainLoop
) -> None:
    async def run() -> None:
        window = MainWindow()
        window.show()
        controller = window.controller
        target = library[0]
        category_name = PREFIX + "Arcade"
        try:
            await settle(window)
            controller.select_game(controller.model.find_row(target))
            button = window.window.findChild(QQuickItem, "libraryGameActions")
            menu = window.window.findChild(QObject, "libraryGameMenu")
            assert button is not None and menu is not None
            qt_application.processEvents()
            point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()

            async def open_menu() -> None:
                QTest.mouseClick(window.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
                await wait_for_qml(
                    qt_application, lambda: menu.property("visible") and controller.property("organizationReady")
                )

            await open_menu()
            favorite = window.window.findChild(QObject, "toggleFavorite")
            assert favorite is not None and QMetaObject.invokeMethod(favorite, "triggered")
            await asyncio.gather(*controller._tasks)
            assert (await game_actions.get_organization(target)).favorite
            assert QMetaObject.invokeMethod(menu, "close")

            await open_menu()
            edit = window.window.findChild(QObject, "editGameCategories")
            assert edit is not None and QMetaObject.invokeMethod(edit, "triggered")
            category_field = window.window.findChild(QObject, "newGameCategory")
            assert category_field is not None
            category_field.setProperty("text", "  " + category_name + "  ")
            assert QMetaObject.invokeMethod(category_field, "accepted")
            await asyncio.gather(*controller._tasks)
            assert category_name in categories_db.get_categories_in_game(target)

            fake_main_loop.run_pending()
            fake_main_loop.run_pending()
            await settle(window)
            assert controller.sidebar.find_row({"category": category_name}) >= 0
        finally:
            window.shutdown()
            if category := categories_db.get_category_by_name(category_name):
                categories_db.remove_category(int(category["id"]))

    asyncio.run(run())


async def open_game_action(window: MainWindow, application: QGuiApplication, game_id: str, action: str) -> None:
    controller = window.controller
    controller.select_game(controller.model.find_row(game_id))
    button = window.window.findChild(QQuickItem, "libraryGameActions")
    menu = window.window.findChild(QObject, "libraryGameMenu")
    assert button is not None and menu is not None
    application.processEvents()
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    QTest.mouseClick(window.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    await wait_for_qml(application, lambda: bool(menu.property("visible")))
    item = window.window.findChild(QObject, action)
    assert item is not None and item.property("enabled")
    assert QMetaObject.invokeMethod(item, "triggered")
    assert QMetaObject.invokeMethod(menu, "close")


def find_item(root: QQuickItem, name: str) -> QQuickItem | None:
    for child in root.childItems():
        if child.objectName() == name:
            return child
        if found := find_item(child, name):
            return found
    return None


@pytest.fixture
def store_appid(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    appid = "111824"
    row_id = sql.db_insert(
        settings.DB_PATH,
        "service_games",
        {
            "service": "gog",
            "appid": appid,
            "slug": PREFIX + "uninstalled",
            "name": PREFIX + "Uninstalled",
            "details": "{}",
        },
    )
    monkeypatch.setattr(GOGService, "is_authenticated", lambda self: True)
    monkeypatch.setattr(GOGService, "has_extras", False)
    yield appid
    sql.db_delete(settings.DB_PATH, "service_games", "id", row_id)
    for row in games_db.get_games(filters={"service": "gog", "service_id": appid}):
        games_db.delete_game(row["id"])


def test_qml_opens_install_options_with_the_default_selected(
    qt_application: QGuiApplication, library: list[str], store_appid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_slug = PREFIX + "base"

    def script(name: str, slug: str, version: str, **extra: object) -> dict[str, object]:
        return {
            "name": name,
            "game_slug": slug,
            "slug": f"{slug}-{version.lower()}",
            "version": version,
            "runner": "linux",
            "description": f"{version} description",
            "notes": "",
            "credits": "",
            "script": {
                "game": {"exe": "$GAMEDIR/game.sh"},
                "files": [{"setup": "N/A:Select the setup"}],
                "installer": [{"extract": {"file": "setup", "dst": "$GAMEDIR"}}],
                **extra,
            },
        }

    games_db.update_existing(id=library[1], service="gog", service_id=store_appid)
    choices = [
        script(PREFIX + "Uninstalled", PREFIX + "uninstalled", "Native"),
        script(PREFIX + "Uninstalled", PREFIX + "uninstalled", "Expansion", requires=base_slug),
    ]
    monkeypatch.setattr(GOGService, "generate_installers", lambda self, row: choices)

    async def run() -> None:
        window = MainWindow()
        window.show()
        controller = window.controller
        dialog_controller = controller._installers
        try:
            controller.set_installed_only(False)
            await settle(window)
            controller.select_game(controller.model.find_row(library[0]))
            assert not controller.property("canInstall")
            await open_game_action(window, qt_application, library[1], "installGame")

            dialog = window.window.findChild(QObject, "installerDialog")
            options = window.window.findChild(QQuickItem, "installerOptions")
            assert dialog is not None and options is not None
            await wait_for_qml(qt_application, lambda: bool(dialog_controller.property("selected")))
            assert dialog.property("title") == "Install " + PREFIX + "Uninstalled"
            assert options.property("count") == 2 and options.property("currentIndex") == 0

            directory = window.window.findChild(QObject, "installerDirectory")
            assert directory is not None and directory.property("visible")
            target = str(directory.property("text"))
            assert target.endswith("/" + PREFIX + "uninstalled") and not Path(target).exists()
            assert dialog_controller.property("requiredGame") == ""

            assert QMetaObject.invokeMethod(options, "activated", Q_ARG(int, 1))
            await wait_for_qml(qt_application, lambda: dialog_controller.property("requiredGame") == base_slug)
            assert options.property("currentIndex") == 1
            start = window.window.findChild(QObject, "installerStart")
            assert start is not None and not start.property("visible")

            catalog = dialog_controller._catalog
            assert catalog is not None and catalog.id in installers._catalogs
            assert QMetaObject.invokeMethod(dialog, "close")
            await wait_for_qml(qt_application, lambda: catalog.id not in installers._catalogs)
            assert dialog_controller.property("choices") == []
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_installs_a_synced_store_game_without_a_library_entry(
    qt_application: QGuiApplication,
    store_appid: str,
    fake_main_loop: FakeMainLoop,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config" / "games").mkdir(parents=True)
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "INSTALLER_CACHE_DIR", str(tmp_path / "installer-cache"))
    monkeypatch.setattr(settings, "write_setting", lambda *_args: None)
    monkeypatch.setattr(interpreter, "fetch_game_artwork", lambda slug: 0)
    setup = tmp_path / "setup.txt"
    setup.write_text("game data", encoding="utf-8")
    target = tmp_path / "Games" / "qt-installed"
    prefix = tmp_path / "Windows environments" / "Chosen $GAMEDIR prefix"
    monkeypatch.setattr(wine, "is_installed_for", lambda *_args: True)
    script = {
        "name": PREFIX + "Uninstalled",
        "game_slug": PREFIX + "uninstalled",
        "slug": PREFIX + "uninstalled-local",
        "version": "Local",
        "runner": "wine",
        "script": {
            "game": {"exe": "$GAMEDIR/setup.txt"},
            "files": [{"setup": "N/A:Select the game data"}],
            "installer": [{"copy": {"src": "setup", "dst": "$GAMEDIR"}}],
        },
    }
    monkeypatch.setattr(GOGService, "generate_installers", lambda self, row: [script])
    monkeypatch.setattr(GOGService, "has_extras", True)

    def unexpected_bonus_request(_service: GOGService, _product_id: str) -> None:
        pytest.fail("Bonus downloads should be opt-in")

    monkeypatch.setattr(GOGService, "get_extras", unexpected_bonus_request)

    async def run() -> None:
        window = MainWindow()
        window.show()
        controller = window.controller
        progress = controller._installers.installation_controller
        try:
            controller.set_installed_only(False)
            await settle(window)
            source_row = controller.sidebar.find_row({"service": "gog"})
            assert source_row >= 0
            controller.select_view(source_row)
            await settle(window)
            game = controller.model.game_at(controller.model.find_row(store_appid))
            assert game is not None and game.game_id is None
            await open_game_action(window, qt_application, store_appid, "installGame")
            await wait_for_qml(qt_application, lambda: bool(controller._installers.property("selected")))
            options = window.window.findChild(QQuickItem, "installerOptions")
            assert options is not None and not options.property("visible")
            controller._installers.set_directory(str(target))
            prefix_field = window.window.findChild(QObject, "installationWinePrefix")
            assert prefix_field is not None and prefix_field.property("visible")
            assert prefix_field.setProperty("text", str(prefix))

            def reached(stage: str) -> Callable[[], bool]:
                def check() -> bool:
                    fake_main_loop.run_pending()
                    assert progress.property("stage") != "failed", progress.property("status")
                    return progress.property("stage") == stage

                return check

            start = window.window.findChild(QObject, "installerStart")
            assert start is not None and start.property("enabled") and QMetaObject.invokeMethod(start, "clicked")
            await wait_for_qml(qt_application, reached("files"))

            page = window.window.findChild(QQuickItem, "installationPage")
            assert page is not None
            await wait_for_qml(qt_application, lambda: find_item(page, "filePath_setup") is not None)
            path = find_item(page, "filePath_setup")
            assert path is not None
            path.setProperty("text", str(setup))
            assert QMetaObject.invokeMethod(path, "editingFinished")
            await wait_for_qml(qt_application, lambda: fake_main_loop.run_pending() or progress.property("filesReady"))
            files = window.window.findChild(QObject, "startInstallationFiles")
            assert files is not None and QMetaObject.invokeMethod(files, "clicked")

            await wait_for_qml(qt_application, reached("finished"))
            launch = window.window.findChild(QObject, "installerLaunch")
            assert launch is not None and launch.property("visible")

            def installed_in_library() -> bool:
                fake_main_loop.run_pending()
                model = controller.model
                game = model.game_at(model.find_row(store_appid))
                return bool(game and game.installed)

            await wait_for_qml(qt_application, installed_in_library)
            installed = games_db.get_game_for_service("gog", store_appid)
            assert installed is not None
            config = Game(installed["id"]).config
            assert config is not None and config.game_level["game"]["prefix"] == str(prefix)
            assert prefix.parent.is_dir()
            close = window.window.findChild(QObject, "installerClose")
            assert close is not None and QMetaObject.invokeMethod(close, "clicked")
            await wait_for_qml(qt_application, lambda: not progress.property("active"))
            await wait_for_qml(qt_application, lambda: not installations._installations)
        finally:
            window.shutdown()

    asyncio.run(run())
    assert (target / "setup.txt").read_text(encoding="utf-8") == "game data"


@pytest.mark.parametrize("use_manifest", [True, False])
def test_qml_links_gog_game_files_in_place(
    qt_application: QGuiApplication,
    library: list[str],
    store_appid: str,
    fake_main_loop: FakeMainLoop,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_manifest: bool,
) -> None:
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    if use_manifest:
        details = json.dumps({"slug": PREFIX + "uninstalled", "worksOn": {"Windows": True}})
        sql.db_update(settings.DB_PATH, "service_games", {"details": details}, {"service": "gog", "appid": store_appid})
    else:
        monkeypatch.setattr(GOGService, "is_authenticated", lambda self: False)
        games_db.update_existing(id=library[1], service="gog", service_id=store_appid, playtime=1.5)
    folder = tmp_path / "Other drive" / "Existing game"
    executable = folder / "Bin" / "Play.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    manifest = folder / f"goggame-{store_appid}.info"
    existing_prefix = tmp_path / "Existing prefix"
    registry = existing_prefix / "system.reg"
    metadata = json.dumps(
        {
            "playTasks": [
                {"isPrimary": True, "path": r"bin\play.exe", "workingDir": "bin", "arguments": "--windowed"},
            ],
        }
    )
    if use_manifest:
        manifest.write_text(metadata, encoding="utf-8")
        (existing_prefix / "drive_c").mkdir(parents=True)
        registry.write_text("#arch=win64\n", encoding="utf-8")

    async def run() -> None:
        window = MainWindow()
        window.show()
        controller = window.controller
        try:
            controller.set_installed_only(False)
            await settle(window)
            view_id = library[1]
            if use_manifest:
                source_row = controller.sidebar.find_row({"service": "gog"})
                assert source_row >= 0
                controller.select_view(source_row)
                await settle(window)
                view_id = store_appid
                game = controller.model.game_at(controller.model.find_row(view_id))
                assert game is not None and game.game_id is None
            await open_game_action(window, qt_application, view_id, "installGame")
            if use_manifest:
                await wait_for_qml(qt_application, lambda: bool(controller._installers.property("selected")))
            else:
                await wait_for_qml(qt_application, lambda: "Sign in" in str(controller._installers.property("error")))
            dialog = window.window.findChild(QObject, "installerDialog")
            assert dialog is not None
            locate = window.window.findChild(QObject, "useExistingGame")
            assert locate is not None and locate.property("visible") and QMetaObject.invokeMethod(locate, "clicked")
            folder_field = window.window.findChild(QObject, "existingGameFolder")
            exe_field = window.window.findChild(QObject, "existingGameExecutable")
            link = window.window.findChild(QObject, "importExistingGame")
            assert folder_field is not None and exe_field is not None and link is not None
            assert folder_field.property("visible") and link.property("visible") and not link.property("enabled")
            folder_field.setProperty("text", str(folder))
            prefix_field = window.window.findChild(QObject, "existingWinePrefix")
            assert prefix_field is not None and prefix_field.property("visible")
            if use_manifest:
                assert prefix_field.setProperty("text", str(existing_prefix))
            if not use_manifest:
                assert QMetaObject.invokeMethod(link, "clicked")
                error = window.window.findChild(QObject, "existingGameError")
                assert error is not None
                await wait_for_qml(qt_application, lambda: "Could not detect" in str(error.property("text")))
                exe_field.setProperty("text", str(executable))
            assert link.property("enabled") and QMetaObject.invokeMethod(link, "clicked")
            await wait_for_qml(qt_application, lambda: not dialog.property("visible"))

            row = games_db.get_game_for_service("gog", store_appid)
            assert row is not None
            game_id = row["id"]
            assert row["runner"] == "wine" and row["directory"] == str(folder)
            if not use_manifest:
                assert game_id == library[1] and row["playtime"] == 1.5
            config = Game(game_id).config
            assert config is not None
            options = config.game_level["game"]
            assert options["exe"] == str(executable) and options["working_dir"] == str(executable.parent)
            assert options.get("args") == ("--windowed" if use_manifest else None)
            prefix = existing_prefix if use_manifest else tmp_path / "data" / "prefixes" / f"gog-{store_appid}"
            assert options["prefix"] == str(prefix) and prefix.parent.is_dir()
            assert await game_actions.get_installation_directory(game_id) == str(folder)
            maintenance = await game_maintenance.get_maintenance_info(game_id)
            with pytest.raises(QtrisError, match="managed outside Qtris"):
                await game_maintenance.uninstall_game(game_id, maintenance, trash_files=True)

            fake_main_loop.run_pending()
            model = controller.model

            def installed_in_library() -> bool:
                game = model.game_at(model.find_row(view_id))
                return bool(game and game.installed)

            await wait_for_qml(qt_application, installed_in_library)
            await open_game_action(window, qt_application, view_id, "uninstallGame")
            unlink = window.window.findChild(QObject, "confirmGameUninstall")
            trash = window.window.findChild(QObject, "trashGameFiles")
            assert unlink is not None and trash is not None
            await wait_for_qml(qt_application, lambda: bool(unlink.property("enabled")))
            assert unlink.property("text") == "Unlink" and not trash.property("visible")
            assert QMetaObject.invokeMethod(unlink, "clicked")
            await wait_for_qml(qt_application, lambda: not Game(game_id).is_installed)
            assert executable.read_bytes() == b"MZ"
            if use_manifest:
                assert manifest.read_text(encoding="utf-8") == metadata
                assert registry.read_text(encoding="utf-8") == "#arch=win64\n"
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_configures_a_game_and_the_global_options(
    qt_application: QGuiApplication,
    library: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "config"
    (config_dir / "games").mkdir(parents=True)
    monkeypatch.setattr(settings, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(settings, "write_setting", lambda *_args: None)
    monkeypatch.setattr(configuration, "fetch_game_artwork", lambda slug: 0)
    write_yaml_to_file({"game": {"exe": "/games/run.sh"}}, str(config_dir / "games" / "qt-config.yml"))
    sql.db_update(settings.DB_PATH, "games", {"configpath": "qt-config"}, {"id": library[0]})

    async def run() -> None:
        window = MainWindow()
        window.show()
        controller = window.controller
        config = controller._configuration
        try:
            await settle(window)
            await open_game_action(window, qt_application, library[0], "configureGame")
            dialog = window.window.findChild(QObject, "configDialog")
            options = window.window.findChild(QQuickItem, "configOptions")
            assert dialog is not None and options is not None
            await wait_for_qml(qt_application, lambda: bool(config.property("ready")))
            assert config.property("pages") == ["Game info", "Game options", "Platform options", "System options"]
            assert dialog.property("title") == "Configure " + PREFIX + "Installed"
            assert config.property("infoPage") and config.property("game")["runner"] == "linux"
            config.select_page(1)

            await wait_for_qml(qt_application, lambda: find_item(options, "configOption_args") is not None)
            args = find_item(options, "configOption_args")
            assert args is not None
            config.set_value("game", "args", "--fullscreen")
            await wait_for_qml(qt_application, lambda: bool(config.property("modified")) and args.property("setHere"))
            assert args.property("optionValue") == "--fullscreen"

            config.select_page(2)
            choice_name = "optionChoice_steam_linux_runtime"
            await wait_for_qml(qt_application, lambda: find_item(options, choice_name) is not None)
            choice = find_item(options, choice_name)
            assert choice is not None and choice.property("count") == 5
            assert choice.property("currentValue") == "none"
            choice.setProperty("currentIndex", 4)
            assert QMetaObject.invokeMethod(choice, "activated", Q_ARG(int, 4))
            await wait_for_qml(
                qt_application,
                lambda: bool(
                    (reset := find_item(options, "resetOption_steam_linux_runtime")) and reset.property("visible")
                ),
            )
            assert choice.property("currentValue") == "steamrt4"

            save = window.window.findChild(QObject, "configSave")
            assert save is not None and save.property("enabled") and QMetaObject.invokeMethod(save, "clicked")
            await wait_for_qml(qt_application, lambda: not config.property("ready"))
            row = games_db.get_game_by_field(library[0], "id")
            assert row is not None
            saved = read_yaml_from_file(str(config_dir / "games" / f"{row['configpath']}.yml"))
            assert saved["game"] == {"exe": "/games/run.sh", "args": "--fullscreen"}
            assert saved["linux"] == {"steam_linux_runtime": "steamrt4"}

            action = window.window.findChild(QObject, "globalOptions")
            assert action is not None and QMetaObject.invokeMethod(action, "triggered")
            await wait_for_qml(qt_application, lambda: config.property("pages") == ["System options"])
            config.set_search("no option has this text")
            assert config.property("emptyText") == "No options match 'no option has this text'"

            runner = find_item(window.window.contentItem(), "sidebarRunner_linux")
            assert runner is not None
            config.open_runner("linux", "Linux")
            await wait_for_qml(
                qt_application, lambda: config.property("pages") == ["Platform options", "System options"]
            )
            assert config.property("title") == "Configure Linux"

            add = window.window.findChild(QObject, "addGame")
            assert add is not None and QMetaObject.invokeMethod(add, "triggered")
            await wait_for_qml(qt_application, lambda: bool(config.property("isNewGame")))
            assert config.property("pages") == ["Game info", "System options"]
            # The earlier dialogs' fields linger until their deferred deletion
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            name = window.window.findChild(QObject, "gameInfoName")
            assert name is not None
            name.setProperty("text", PREFIX + "Hand Added")
            assert QMetaObject.invokeMethod(name, "editingFinished")
            config.set_runner("linux")
            await wait_for_qml(qt_application, lambda: len(config.property("pages")) == 4)
            config.set_value("game", "exe", "/games/hand.sh")
            await wait_for_qml(qt_application, lambda: config.property("game")["name"] == PREFIX + "Hand Added")
            save = window.window.findChild(QObject, "configSave")
            assert save is not None and QMetaObject.invokeMethod(save, "clicked")
            await wait_for_qml(qt_application, lambda: not config.property("ready"))
            added = games_db.get_game_by_field(PREFIX + "Hand Added", "name")
            assert added is not None and added["runner"] == "linux"
            games_db.delete_game(added["id"])
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_saves_a_steamgriddb_key_then_fetches_artwork(
    qt_application: QGuiApplication, library: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: list[str] = []

    async def get_artwork_settings() -> ArtworkSettings:
        return ArtworkSettings(bool(saved), False, "https://example.com/key")

    async def set_steamgriddb_api_key(api_key: str) -> ArtworkSettings:
        saved.append(api_key)
        return await get_artwork_settings()

    async def update_artwork() -> int:
        return 2

    monkeypatch.setattr(media, "get_artwork_settings", get_artwork_settings)
    monkeypatch.setattr(media, "set_steamgriddb_api_key", set_steamgriddb_api_key)
    monkeypatch.setattr(media, "update_artwork", update_artwork)

    async def run() -> None:
        window = MainWindow()
        window.show()
        try:
            await settle(window)
            action = window.window.findChild(QObject, "artworkSources")
            assert action is not None and QMetaObject.invokeMethod(action, "triggered")
            await wait_for_qml(qt_application, lambda: window.window.findChild(QObject, "steamGridDbKey") is not None)
            key = window.window.findChild(QObject, "steamGridDbKey")
            save = window.window.findChild(QObject, "saveSteamGridDbKey")
            assert key is not None and save is not None and not save.property("enabled")
            key.setProperty("text", "my-key")
            assert save.property("enabled") and QMetaObject.invokeMethod(save, "clicked")
            await wait_for_qml(
                qt_application, lambda: window.controller.property("statusText") == "Downloaded 2 images"
            )
            assert saved == ["my-key"]
            dialog = window.window.findChild(QObject, "artworkSettingsDialog")
            assert dialog is None or not dialog.property("visible")
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_preferences_change_sources_storage_and_check_the_runtime(
    qt_application: QGuiApplication, library: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {"gog": SourceSetting("gog", "GOG", "", False, True)}
    game_paths: list[str] = []

    async def list_sources() -> tuple[SourceSetting, ...]:
        return tuple(sources.values())

    async def set_source_enabled(source_id: str, enabled: bool) -> None:
        sources[source_id] = SourceSetting(source_id, "GOG", "", enabled, True)

    async def get_storage_paths() -> StoragePaths:
        return StoragePaths(game_paths[-1] if game_paths else "/games", "", "", True)

    async def set_game_path(path: str) -> StoragePaths:
        game_paths.append(path)
        return await get_storage_paths()

    async def update_runtime(force: bool = False) -> tuple[str, ...]:
        return ("dxvk",) if force else ()

    async def nothing() -> tuple[()]:
        return ()

    monkeypatch.setattr(preferences, "list_sources", list_sources)
    monkeypatch.setattr(preferences, "set_source_enabled", set_source_enabled)
    monkeypatch.setattr(preferences, "get_storage_paths", get_storage_paths)
    monkeypatch.setattr(preferences, "set_game_path", set_game_path)
    monkeypatch.setattr(preferences, "list_steam_accounts", nothing)
    monkeypatch.setattr(preferences, "get_system_info", nothing)
    monkeypatch.setattr(runtime_updates, "update_runtime", update_runtime)

    async def run() -> None:
        window = MainWindow()
        window.show()
        prefs = window.controller._preferences
        refreshed: list[bool] = []
        prefs.sourcesChanged.connect(lambda: refreshed.append(True))
        try:
            await settle(window)
            action = window.window.findChild(QObject, "openPreferences")
            assert action is not None and QMetaObject.invokeMethod(action, "triggered")
            await wait_for_qml(qt_application, lambda: len(prefs.property("sources")) == 1)

            dialog = window.window.findChild(QObject, "preferencesDialog")
            assert dialog is not None
            content = dialog.property("contentItem")
            await wait_for_qml(qt_application, lambda: find_item(content, "sourceSwitch_gog") is not None)
            switch = find_item(content, "sourceSwitch_gog")
            assert switch is not None and not switch.property("checked")
            switch.setProperty("checked", True)
            assert QMetaObject.invokeMethod(switch, "toggled")
            await wait_for_qml(qt_application, lambda: prefs.property("sources")[0]["enabled"] and bool(refreshed))

            await wait_for_qml(qt_application, lambda: find_item(content, "storagePath_game") is not None)
            path = find_item(content, "storagePath_game")
            assert path is not None and path.property("text") == "/games"
            path.setProperty("text", "/mnt/games")
            assert QMetaObject.invokeMethod(path, "editingFinished")
            await wait_for_qml(qt_application, lambda: prefs.property("storage")["gamePath"] == "/mnt/games")

            check = window.window.findChild(QObject, "checkRuntimeUpdates")
            assert check is not None and QMetaObject.invokeMethod(check, "clicked")
            runtime = window.controller.runtime_updates_controller
            await wait_for_qml(qt_application, lambda: runtime.property("statusText") == "Updated dxvk.")
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_manages_ge_proton_from_preferences(
    qt_application: QGuiApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = "GE-Proton11-7"
    installed = False

    families: list[str] = []

    async def list_builds(family: str, available: tuple[str, ...]) -> tuple[runners.ProtonBuild, ...]:
        return tuple(runners.ProtonBuild(tag, installed, installed) for tag in available)

    cachyos_newest = "proton-cachyos-11.0-20261015-slr"
    cachyos_release = "proton-cachyos-11.0-20261014-slr"

    async def list_releases(family: str) -> tuple[str, ...]:
        families.append(family)
        return (version,) if family == "GE-Proton" else (cachyos_newest,)

    async def get_latest_proton(family: str) -> runners.LatestProton:
        if family == "GE-Proton":
            return runners.LatestProton("ge-proton", "GE-Proton Latest", "GE-Proton11-12", "GE-Proton11-12")
        label = "Proton-CachyOS " + cachyos_release.removeprefix("proton-cachyos-").removesuffix("-slr")
        return runners.LatestProton("proton-cachyos-latest", "Proton-CachyOS Latest", cachyos_release, label)

    async def install_runner(name: str, tag: str, *, interaction: runners.RunnerInstallInteraction) -> None:
        nonlocal installed, cachyos_release
        interaction.progress("Downloading…", 0.5)
        if tag == "proton-cachyos-latest":
            cachyos_release = cachyos_newest
            return
        assert (name, tag) == ("wine", version)
        installed = True

    async def uninstall_runner(name: str, tag: str) -> None:
        nonlocal installed
        assert (name, tag) == ("wine", version) and installed
        installed = False

    monkeypatch.setattr(runners, "list_proton_builds", list_builds)
    monkeypatch.setattr(runners, "list_proton_releases", list_releases)
    monkeypatch.setattr(runners, "get_latest_proton", get_latest_proton)
    monkeypatch.setattr(runners, "install_runner", install_runner)
    monkeypatch.setattr(runners, "uninstall_runner", uninstall_runner)

    async def run() -> None:
        window = MainWindow()
        window.show()
        try:
            await settle(window)
            action = window.window.findChild(QObject, "openPreferences")
            assert action is not None and QMetaObject.invokeMethod(action, "triggered")
            pages = window.window.findChild(QObject, "preferencesPages")
            assert pages is not None and pages.setProperty("currentIndex", 6)
            dialog = window.window.findChild(QObject, "preferencesDialog")
            assert dialog is not None
            content = dialog.property("contentItem")

            async def click(name: str) -> None:
                await wait_for_qml(
                    qt_application,
                    lambda: bool(
                        (button := find_item(content, name))
                        and button.property("visible")
                        and button.property("enabled")
                    ),
                )
                button = find_item(content, name)
                assert button is not None and QMetaObject.invokeMethod(button, "clicked")

            manager = window.controller._runner_manager
            await wait_for_qml(qt_application, lambda: bool(find_item(content, "runnerConfigure_web")))
            assert [entry["label"] for entry in manager.property("entries")] == ["Linux", "Windows", "Web"]
            config = window.controller._configuration

            def opened_config_dialog() -> QObject | None:
                return next(
                    (item for item in window.window.findChildren(QObject, "configDialog") if item.property("opened")),
                    None,
                )

            for platform, label in (("linux", "Linux"), ("wine", "Windows"), ("web", "Web")):
                assert find_item(content, f"runnerSwitch_{platform}") is None
                await click(f"runnerConfigure_{platform}")
                await wait_for_qml(qt_application, lambda: bool(config.property("ready")))
                assert config.property("title") == f"Configure {label}"
                assert config.property("pages") == ["Platform options", "System options"]
                await wait_for_qml(qt_application, lambda: opened_config_dialog() is not None)
                config_dialog = opened_config_dialog()
                assert config_dialog is not None and QMetaObject.invokeMethod(config_dialog, "reject")
                await wait_for_qml(qt_application, lambda: not config.property("ready"))

            await click("runnerVersions_wine")
            await click(f"runnerInstall_{version}")
            latest = window.controller._runner_manager.property("entries")[0]
            assert latest["label"] == "GE-Proton Latest" and "GE-Proton11-12" in latest["description"]
            assert latest["canRemove"] and not latest["canInstall"]
            await click(f"runnerRemove_{version}")
            prompt = window.window.findChild(QObject, "qtrisInput")
            assert prompt is not None
            await wait_for_qml(qt_application, lambda: bool(prompt.property("visible")))
            assert QMetaObject.invokeMethod(prompt, "reject")
            await wait_for_qml(qt_application, lambda: not window.controller._runner_manager.property("busy"))
            assert installed

            await click(f"runnerRemove_{version}")
            await wait_for_qml(qt_application, lambda: bool(prompt.property("visible")))
            assert QMetaObject.invokeMethod(prompt, "accept")
            await wait_for_qml(qt_application, lambda: not installed)

            await click("protonFamily_1")
            await wait_for_qml(qt_application, lambda: families[-1:] == ["Proton-CachyOS"] and not manager.busy)
            assert manager.property("protonFamily") == 1
            rolling = manager.property("entries")[0]
            assert rolling["label"] == "Proton-CachyOS Latest" and rolling["canUpdate"] and rolling["canRemove"]
            await click("runnerUpdate_proton-cachyos-latest")
            await wait_for_qml(qt_application, lambda: not manager.busy and cachyos_release == cachyos_newest)
            rolling = manager.property("entries")[0]
            assert not rolling["canUpdate"] and "11.0-20261015" in rolling["description"]
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_signs_in_via_the_host_browser_and_syncs(
    qt_application: QGuiApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = "https://auth.gog.com/auth"
    redirect = "https://embed.gog.com/on_login_success?code=fixture"
    actions: list[str] = []
    signed_in = False

    async def list_sources() -> list[ServiceInfo]:
        return [ServiceInfo("gog", "GOG", True)]

    async def status(source_id: str) -> services.SourceStatus:
        assert source_id == "gog"
        return services.SourceStatus("GOG", True, signed_in, True)

    async def begin(source_id: str, *, use_api_key: bool = False) -> store_login.BrowserLogin:
        assert source_id == "gog"
        return store_login.BrowserLogin("request", "GOG", target, "redirect")

    async def finish(request_id: str, value: str, cookie_file: str = "") -> None:
        nonlocal signed_in
        assert (request_id, value) == ("request", redirect)
        signed_in = True

    async def sync(source_id: str) -> None:
        assert source_id == "gog" and signed_in
        actions.append("sync")

    async def disconnect(source_id: str) -> None:
        nonlocal signed_in
        assert source_id == "gog"
        signed_in = False

    class Desktop:
        @staticmethod
        def openUrl(url: QUrl) -> bool:  # noqa: N802
            actions.append(url.toString())
            return True

    monkeypatch.setattr(services, "list_services", list_sources)
    monkeypatch.setattr(services, "get_source_status", status)
    monkeypatch.setattr(services, "sync_source", sync)
    monkeypatch.setattr(services, "disconnect_source", disconnect)
    monkeypatch.setattr(store_login, "begin", begin)
    monkeypatch.setattr(store_login, "finish", finish)
    monkeypatch.setattr(store_sources, "QDesktopServices", Desktop)

    async def run() -> None:
        window = MainWindow()
        window.show()
        try:
            await settle(window)
            window.controller.select_view(window.controller.sidebar.find_row({"service": "gog"}))

            async def click(name: str) -> None:
                await wait_for_qml(
                    qt_application,
                    lambda: bool(
                        (button := window.window.findChild(QObject, name))
                        and button.property("visible")
                        and button.property("enabled")
                    ),
                )
                button = window.window.findChild(QObject, name)
                assert button is not None and QMetaObject.invokeMethod(button, "clicked")

            await click("storeSignIn")
            await click("openStoreBrowser")
            response = window.window.findChild(QObject, "storeLoginResponse")
            assert response is not None and response.setProperty("text", redirect)
            await click("finishStoreLogin")
            await wait_for_qml(qt_application, lambda: actions == [target, "sync"])
            await click("storeSync")
            await wait_for_qml(qt_application, lambda: actions == [target, "sync", "sync"])
            await click("storeDisconnect")
            await wait_for_qml(qt_application, lambda: not signed_in)
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_signs_in_through_the_web_view_with_its_cookies(
    qt_application: QGuiApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect = "https://embed.gog.com/on_login_success?origin=client&code=fixture"
    finished: list[tuple[str, tuple[store_login.WebCookie, ...]]] = []
    signed_in = False

    async def list_sources() -> list[ServiceInfo]:
        return [ServiceInfo("gog", "GOG", True)]

    async def status(source_id: str) -> services.SourceStatus:
        return services.SourceStatus("GOG", True, signed_in, True)

    async def begin(source_id: str, *, use_api_key: bool = False) -> store_login.BrowserLogin:
        # A blank page keeps the web view off the network
        return store_login.BrowserLogin("request", "GOG", "about:blank", "web")

    async def cancel(request_id: str) -> None:
        assert request_id == "request"

    def is_web_response(request_id: str, url: str) -> bool:
        assert request_id == "request"
        return url == redirect

    async def finish(request_id: str, value: str, cookies: tuple[store_login.WebCookie, ...] = ()) -> None:
        nonlocal signed_in
        finished.append((value, tuple(cookies)))
        signed_in = True

    async def sync(source_id: str) -> None:
        pass

    class Desktop:
        @staticmethod
        def openUrl(url: QUrl) -> bool:  # noqa: N802
            raise AssertionError("A web sign-in must not open the host browser")

    monkeypatch.setattr(services, "list_services", list_sources)
    monkeypatch.setattr(services, "get_source_status", status)
    monkeypatch.setattr(services, "sync_source", sync)
    monkeypatch.setattr(store_login, "begin", begin)
    monkeypatch.setattr(store_login, "cancel", cancel)
    monkeypatch.setattr(store_login, "is_web_response", is_web_response)
    monkeypatch.setattr(store_login, "finish", finish)
    monkeypatch.setattr(store_sources, "QDesktopServices", Desktop)

    async def run() -> None:
        window = MainWindow()
        window.show()
        try:
            await settle(window)
            window.controller.select_view(window.controller.sidebar.find_row({"service": "gog"}))
            root = window.window
            assert root is not None
            await wait_for_qml(
                qt_application,
                lambda: bool((button := root.findChild(QObject, "storeSignIn")) and button.property("visible")),
            )
            button = root.findChild(QObject, "storeSignIn")
            assert button is not None and QMetaObject.invokeMethod(button, "clicked")

            def sign_in_view() -> QObject | None:
                sign_in = root.findChild(QObject, "storeWebLoginWindow")
                return sign_in.findChild(QQuickItem, "storeWebLoginView") if sign_in else None

            def window_closed() -> bool:
                # The Loader deletes the window later, which processEvents() alone doesn't do
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                return root.findChild(QObject, "storeWebLoginWindow") is None

            await wait_for_qml(qt_application, lambda: sign_in_view() is not None)
            first_view = sign_in_view()
            assert first_view is not None
            first_profile = first_view.property("profile")
            assert isinstance(first_profile, QQuickWebEngineProfile)
            assert first_profile.isOffTheRecord()
            assert first_profile is not QQuickWebEngineProfile.defaultProfile()
            sign_in = root.findChild(QWindow, "storeWebLoginWindow")
            assert sign_in is not None and sign_in.close()
            await wait_for_qml(qt_application, window_closed)
            assert QMetaObject.invokeMethod(button, "clicked")

            await wait_for_qml(qt_application, lambda: sign_in_view() is not None)
            view = sign_in_view()
            assert view is not None
            profile = view.property("profile")
            assert isinstance(profile, QQuickWebEngineProfile)
            assert profile.isOffTheRecord()
            assert profile is not first_profile and profile is not QQuickWebEngineProfile.defaultProfile()
            assert "QtWebEngine" not in profile.httpUserAgent()
            cookie = QNetworkCookie(b"gog-al", b"session")
            cookie.setDomain(".gog.com")
            cookie.setSecure(True)
            profile.cookieStore().setCookie(cookie, QUrl("https://www.gog.com/"))
            await wait_for_qml(qt_application, lambda: bool(window.controller._store_sources._web_cookies))

            # Only the final page ends the sign-in
            assert view.setProperty("url", QUrl("about:blank#not-yet"))
            await wait_for_qml(qt_application, lambda: view.property("url") == QUrl("about:blank#not-yet"))
            assert not finished
            window.controller._store_sources.web_url_changed(QUrl(redirect))
            await wait_for_qml(qt_application, lambda: bool(finished) and not window.controller._store_sources.busy)
            assert finished == [(redirect, (store_login.WebCookie(".gog.com", "gog-al", "session", secure=True),))]

            await wait_for_qml(qt_application, window_closed)
            assert window.controller._store_sources.login == {}
        finally:
            window.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("ending", ["clipboard", "manual", "reject", "cancel", "shutdown"])
def test_qml_signs_in_to_epic_in_the_host_browser(
    qt_application: QGuiApplication, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    opened: list[str] = []
    cancelled: list[str] = []
    synced: list[str] = []
    submitted: list[str] = []
    signed_in = False
    login_url = "https://legendary.gl/epiclogin"

    async def list_sources() -> list[ServiceInfo]:
        return [ServiceInfo("egs", "Epic Games Store", True)]

    async def status(source_id: str) -> services.SourceStatus:
        return services.SourceStatus("Epic Games Store", True, signed_in, True)

    async def begin(source_id: str, *, use_api_key: bool = False) -> store_login.BrowserLogin:
        return store_login.BrowserLogin("request", "Epic Games Store", login_url, "epic_code")

    async def finish(request_id: str, value: str = "", cookies: tuple[store_login.WebCookie, ...] = ()) -> None:
        nonlocal signed_in
        assert request_id == "request"
        submitted.append(value)
        if value == "expired-code":
            raise QtrisError("legendary refused.")
        assert value == "epic-code"
        signed_in = True

    async def cancel(request_id: str) -> None:
        cancelled.append(request_id)

    async def sync(source_id: str) -> None:
        synced.append(source_id)

    class Desktop:
        @staticmethod
        def openUrl(url: QUrl) -> bool:  # noqa: N802
            opened.append(url.toString())
            return True

    monkeypatch.setattr(services, "list_services", list_sources)
    monkeypatch.setattr(services, "get_source_status", status)
    monkeypatch.setattr(services, "sync_source", sync)
    monkeypatch.setattr(store_login, "begin", begin)
    monkeypatch.setattr(store_login, "finish", finish)
    monkeypatch.setattr(store_login, "cancel", cancel)
    monkeypatch.setattr(store_sources, "QDesktopServices", Desktop)

    async def run() -> None:
        clipboard = qt_application.clipboard()
        clipboard.setText('{"authorizationCode": "old-code"}')
        window = MainWindow()
        window.show()
        sources = window.controller._store_sources

        def find(name: str) -> QObject | None:
            return window.window.findChild(QObject, name)

        def shown(name: str) -> bool:
            item = find(name)
            return bool(item and item.property("visible"))

        async def click(name: str) -> None:
            await wait_for_qml(qt_application, lambda: shown(name))
            button = find(name)
            assert button is not None and QMetaObject.invokeMethod(button, "clicked")

        try:
            await settle(window)
            window.controller.select_view(window.controller.sidebar.find_row({"service": "egs"}))
            await click("storeSignIn")
            await wait_for_qml(
                qt_application, lambda: bool(opened) and shown("storeLoginResponse") and not sources.busy
            )
            assert opened == [login_url]
            assert find("storeWebLoginWindow") is None

            # Ignore the clipboard's existing response and unrelated copied text
            clipboard.setText('{"authorizationCode":"old-code"}')
            clipboard.setText("unrelated-text")
            assert not sources.busy and not submitted

            if ending in ("cancel", "shutdown"):
                if ending == "shutdown":
                    window.shutdown()
                else:
                    dialog = find("storeLoginDialog")
                    assert dialog is not None and QMetaObject.invokeMethod(dialog, "close")
                await wait_for_qml(qt_application, lambda: cancelled == ["request"])
                clipboard.setText('{"authorizationCode":"epic-code"}')
                assert not sources.busy and not submitted
                return
            if ending == "reject":
                clipboard.setText('{"authorizationCode":"expired-code"}')
                await wait_for_qml(qt_application, lambda: sources.error == "legendary refused." and not sources.busy)
                clipboard.setText('{"authorizationCode": "expired-code"}')
                assert not sources.busy and submitted == ["expired-code"]
                await click("openStoreBrowser")
                await wait_for_qml(qt_application, lambda: len(opened) == 2)
                assert opened == [login_url, login_url]
            if ending == "manual":
                response = find("storeLoginResponse")
                assert response is not None
                response.setProperty("text", "epic-code")
                await click("finishStoreLogin")
            else:
                clipboard.setText('{"authorizationCode":"epic-code"}')
            await wait_for_qml(qt_application, lambda: synced == ["egs"] and sources.login == {})
        finally:
            if ending != "shutdown":
                window.shutdown()
            clipboard.clear()

    asyncio.run(run())


def test_qml_links_a_local_linux_game(
    qt_application: QGuiApplication,
    library: list[str],
    fake_main_loop: FakeMainLoop,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "play game"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path / "config"))

    async def run() -> None:
        window = MainWindow()
        window.show()
        try:
            window.controller.set_installed_only(False)
            await settle(window)
            assert window.window.findChild(QObject, "addGame") is not None
            assert window.window.findChild(QObject, "addLocalGame") is None
            game_id = library[1]
            await open_game_action(window, qt_application, game_id, "locateLocalGame")

            dialog = window.window.findChild(QObject, "localGameDialog")
            assert dialog is not None
            path = dialog.findChild(QObject, "localGameExecutable")
            arguments = dialog.findChild(QObject, "localGameArguments")
            confirm = dialog.findChild(QObject, "confirmLocalGame")
            assert path is not None and arguments is not None and confirm is not None
            await wait_for_qml(qt_application, lambda: bool(dialog.property("opened")))
            assert not confirm.property("enabled")
            path.setProperty("text", str(executable))
            arguments.setProperty("text", "--windowed")
            assert confirm.property("enabled") and QMetaObject.invokeMethod(confirm, "clicked")
            await wait_for_qml(qt_application, lambda: not dialog.property("visible"))
            fake_main_loop.run_pending()

            def installed_in_library() -> bool:
                model = window.controller.model
                game = model.game_at(model.find_row(game_id))
                return bool(game and game.installed)

            await wait_for_qml(qt_application, installed_in_library)
            config = Game(game_id).config
            assert config is not None and config.game_level["game"] == {"exe": str(executable), "args": "--windowed"}
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_move_uninstall_and_remove_from_library(
    qt_application: QGuiApplication,
    library: list[str],
    fake_main_loop: FakeMainLoop,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = library[0]
    source = tmp_path / "Installed Game"
    destination = tmp_path / "Target Folder"
    source.mkdir()
    destination.mkdir()
    (source / "play.sh").touch()
    config = tmp_path / "games" / "qt-maintenance.yml"
    config.parent.mkdir()
    config.write_text("game:\n  exe: play.sh\n", encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    sql.db_update(settings.DB_PATH, "games", {"directory": str(source), "configpath": "qt-maintenance"}, {"id": target})

    async def run() -> None:
        window = MainWindow()
        window.show()
        try:
            window.controller.set_installed_only(False)
            await settle(window)
            await open_game_action(window, qt_application, target, "moveGame")
            confirm = window.window.findChild(QObject, "confirmGameMove")
            assert confirm is not None
            upkeep = window.controller._maintenance
            await wait_for_qml(qt_application, lambda: bool(upkeep.property("ready")))
            assert not confirm.property("enabled")
            upkeep.choose_destination(QUrl.fromLocalFile(str(destination)))
            assert upkeep.property("moveTarget") == str(destination / source.name)
            assert confirm.property("enabled") and QMetaObject.invokeMethod(confirm, "clicked")
            await wait_for_qml(qt_application, lambda: not bool(upkeep.property("working")))
            assert (destination / source.name / "play.sh").exists() and not source.exists()
            assert not upkeep.property("ready")

            await open_game_action(window, qt_application, target, "uninstallGame")
            uninstall = window.window.findChild(QObject, "confirmGameUninstall")
            trash = window.window.findChild(QObject, "trashGameFiles")
            remove = window.window.findChild(QObject, "removeGameEntry")
            assert uninstall is not None and trash is not None and remove is not None
            await wait_for_qml(qt_application, lambda: bool(uninstall.property("enabled")))
            assert trash.property("enabled") and not trash.property("checked") and not remove.property("checked")
            assert QMetaObject.invokeMethod(uninstall, "clicked")
            await wait_for_qml(qt_application, lambda: not bool(upkeep.property("working")))
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert (destination / source.name / "play.sh").exists()
            assert not config.exists() and not Game(target).is_installed

            for _ in range(3):
                fake_main_loop.run_pending()

            def entry_is_uninstalled() -> bool:
                controller = window.controller
                game = controller.model.game_at(controller.model.find_row(target))
                return bool(game and not game.installed)

            await wait_for_qml(qt_application, entry_is_uninstalled)
            await open_game_action(window, qt_application, target, "uninstallGame")
            remove_entry = window.window.findChild(QObject, "confirmGameUninstall")
            assert remove_entry is not None
            await wait_for_qml(qt_application, lambda: bool(remove_entry.property("enabled")))
            assert remove_entry.property("text") == "Remove"
            assert QMetaObject.invokeMethod(remove_entry, "clicked")
            await wait_for_qml(qt_application, lambda: not bool(upkeep.property("working")))
            for _ in range(3):
                fake_main_loop.run_pending()
            await wait_for_qml(qt_application, lambda: window.controller.model.find_row(target) < 0)
            assert (destination / source.name / "play.sh").exists()
        finally:
            window.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("trash_profile", [False, True])
def test_qml_web_profile_trash_is_opt_in_when_removing_a_game(
    qt_application: QGuiApplication,
    library: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    trash_profile: bool,
) -> None:
    target = library[0]
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    sql.db_update(settings.DB_PATH, "games", {"runner": "web", "configpath": "qt-web"}, {"id": target})
    profile = profile_directory(settings.DATA_DIR, target, Game(target).installed_at)
    profile.mkdir(parents=True)

    def trash(path: str, done: Callable[[], None], _failed: Callable[[Exception], None]) -> None:
        assert Path(path) == profile
        shutil.move(path, tmp_path / "trash")
        done()

    monkeypatch.setattr(game_maintenance.system, "remove_folder", trash)

    async def run() -> None:
        window = MainWindow()
        window.show()
        try:
            await settle(window)
            await open_game_action(window, qt_application, target, "uninstallGame")
            root = window.window
            assert root is not None
            confirm = root.findChild(QObject, "confirmGameUninstall")
            remove = root.findChild(QObject, "removeGameEntry")
            browser_data = root.findChild(QObject, "trashWebGameProfile")
            assert confirm is not None and remove is not None and browser_data is not None
            await wait_for_qml(qt_application, lambda: bool(confirm.property("enabled")))
            assert not browser_data.property("visible")
            assert remove.setProperty("checked", True)
            assert browser_data.property("visible") and not browser_data.property("checked")
            assert browser_data.setProperty("checked", trash_profile)
            assert QMetaObject.invokeMethod(confirm, "clicked")
            await wait_for_qml(qt_application, lambda: not window.controller._maintenance.is_working)
            assert games_db.get_game_by_field(target, "id") is None
            assert profile.is_dir() is not trash_profile
            assert (tmp_path / "trash").is_dir() is trash_profile
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_log_tracks_output_and_resets_on_relaunch(
    qt_application: QGuiApplication, library: list[str], fake_main_loop: FakeMainLoop
) -> None:
    async def run() -> None:
        window = MainWindow()
        window.show()
        target = library[0]
        buffer = Game(target).log_buffer
        try:
            await settle(window)
            buffer.append("<startup>\n")
            await open_game_action(window, qt_application, target, "showGameLogs")
            text = window.window.findChild(QObject, "gameLogText")
            assert text is not None
            await wait_for_qml(qt_application, lambda: text.property("text") == "<startup>\n")

            buffer.append("next line\n")
            for _ in range(3):
                fake_main_loop.run_pending()
            await wait_for_qml(qt_application, lambda: text.property("text") == "<startup>\nnext line\n")

            buffer.clear()
            buffer.append("second launch\n")
            for _ in range(3):
                fake_main_loop.run_pending()
            await wait_for_qml(qt_application, lambda: text.property("text") == "second launch\n")
        finally:
            window.shutdown()
            LOG_BUFFERS.pop(target, None)

    asyncio.run(run())


def test_runner_install_progress_goes_away_once_the_game_launches(
    qt_application: QGuiApplication, library: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: list[str] = []

    async def prepare_game_runner(game_id: str, interaction: runners.RunnerInstallInteraction) -> bool:
        interaction.progress("Downloading…", 0.5)
        interaction.progress("Verifying and installing…", None)
        return True

    async def launch_game(game_id: str, _delegate: object) -> bool:
        launched.append(game_id)
        return False

    monkeypatch.setattr(runners, "prepare_game_runner", prepare_game_runner)
    monkeypatch.setattr(games, "launch_game", launch_game)

    async def run() -> None:
        window = MainWindow()
        window.show()
        try:
            await settle(window)
            controller = window.controller
            shown: list[str] = []
            controller.statusChanged.connect(lambda: shown.append(controller.property("statusText")))
            controller.launch(library[0], False)
            await wait_for_qml(qt_application, lambda: bool(launched) and not controller.property("statusText"))
            assert "Verifying and installing…" in shown
        finally:
            window.shutdown()

    asyncio.run(run())


def test_qml_launch_configuration_uses_selected_index(qt_application: QGuiApplication) -> None:
    async def run() -> None:
        window = MainWindow()
        window.show()
        try:
            await settle(window)

            def answer() -> None:
                dialog = window.window.findChild(QObject, "launchConfigurationDialog")
                choice = window.window.findChild(QObject, "launchConfigurationChoice")
                remember = window.window.findChild(QObject, "rememberLaunchConfiguration")
                if dialog is not None and choice is not None and remember is not None and choice.property("count") == 2:
                    choice.setProperty("currentIndex", 1)
                    remember.setProperty("checked", True)
                    QMetaObject.invokeMethod(dialog, "accept")

            deadline = QTimer(window, singleShot=True, interval=1000)
            deadline.timeout.connect(window.prompts.dismiss)
            QTimer.singleShot(50, answer)
            deadline.start()
            assert window.prompts.choose_configuration("Launch", ("Primary", "Alternative"), None) == LaunchChoice(
                1, True
            )
            deadline.stop()
        finally:
            window.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("kind", "label"), [("menu", "Application Menu"), ("steam_big_picture", "Steam Big Picture (Standalone)")]
)
def test_qml_shortcuts_submit_the_destination_and_configuration(
    qt_application: QGuiApplication,
    library: list[str],
    monkeypatch: pytest.MonkeyPatch,
    kind: shortcuts.ShortcutKind,
    label: str,
) -> None:
    changes: list[tuple[str, bool, str]] = []

    async def get_shortcuts(game_id: str) -> shortcuts.GameShortcuts:
        assert game_id == library[0]
        exists = bool(changes and changes[-1][1])
        return shortcuts.GameShortcuts(
            (shortcuts.ShortcutTarget(kind, label, exists),),
            ("Primary", "Windowed"),
            ("", "Windowed"),
        )

    async def set_shortcut(game_id: str, kind: str, present: bool, configuration: str) -> shortcuts.GameShortcuts:
        changes.append((kind, present, configuration))
        return await get_shortcuts(game_id)

    monkeypatch.setattr(shortcuts, "get_shortcuts", get_shortcuts)
    monkeypatch.setattr(shortcuts, "set_shortcut", set_shortcut)

    async def run() -> None:
        window = MainWindow()
        window.show()
        try:
            await settle(window)
            await open_game_action(window, qt_application, library[0], "manageGameShortcuts")
            choice = window.window.findChild(QObject, "shortcutConfigurationChoice")
            assert choice is not None
            await wait_for_qml(qt_application, lambda: choice.property("count") == 2)
            choice.setProperty("currentIndex", 1)

            def shortcut_button(item: QQuickItem | None) -> QQuickItem | None:
                if item is None or item.objectName() == f"{kind}Shortcut":
                    return item
                return next((match for child in item.childItems() if (match := shortcut_button(child))), None)

            def find_button() -> QQuickItem | None:
                return shortcut_button(window.window.contentItem())

            button = find_button()
            assert button is not None and button.property("text") == "Create"
            assert QMetaObject.invokeMethod(button, "clicked")

            def removal_available() -> bool:
                remove = find_button()
                return bool(remove and remove.property("text") == "Remove" and remove.property("enabled"))

            await wait_for_qml(qt_application, removal_available)
            remove = find_button()
            assert remove is not None and QMetaObject.invokeMethod(remove, "clicked")
            await wait_for_qml(qt_application, lambda: len(changes) == 2)
            assert changes == [(kind, True, "Windowed"), (kind, False, "")]
        finally:
            window.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("use_api_key", [False, True])
def test_qml_itchio_accepts_browser_callback_or_api_key(
    qt_application: QGuiApplication, monkeypatch: pytest.MonkeyPatch, use_api_key: bool
) -> None:
    browser_url = "https://itch.io/user/oauth?client_id=fixture"
    key_url = "https://itch.io/user/settings/api-keys"
    opened: list[str] = []
    cancelled: list[str] = []
    submitted: list[tuple[str, str]] = []
    synced: list[str] = []
    callback: asyncio.Future[str]

    async def list_sources() -> list[ServiceInfo]:
        return [ServiceInfo("itchio", "itch.io", True)]

    async def status(source_id: str) -> services.SourceStatus:
        return services.SourceStatus("itch.io", True, bool(submitted), True)

    async def begin(source_id: str, *, use_api_key: bool = False) -> store_login.BrowserLogin:
        if use_api_key:
            return store_login.BrowserLogin("key", "itch.io", key_url, "api_key")
        return store_login.BrowserLogin("browser", "itch.io", browser_url, "itch_oauth")

    async def finish(request_id: str, value: str = "", cookies: tuple[store_login.WebCookie, ...] = ()) -> None:
        submitted.append((request_id, value))

    async def wait_for_browser(request_id: str) -> str:
        assert request_id == "browser"
        return await callback

    async def cancel(request_id: str) -> None:
        cancelled.append(request_id)
        callback.cancel()

    async def sync(source_id: str) -> None:
        synced.append(source_id)

    class Desktop:
        @staticmethod
        def openUrl(url: QUrl) -> bool:  # noqa: N802
            opened.append(url.toString())
            return True

    monkeypatch.setattr(services, "list_services", list_sources)
    monkeypatch.setattr(services, "get_source_status", status)
    monkeypatch.setattr(services, "sync_source", sync)
    monkeypatch.setattr(store_login, "begin", begin)
    monkeypatch.setattr(store_login, "finish", finish)
    monkeypatch.setattr(store_login, "wait_for_itchio", wait_for_browser)
    monkeypatch.setattr(store_login, "cancel", cancel)
    monkeypatch.setattr(store_sources, "QDesktopServices", Desktop)

    async def run() -> None:
        nonlocal callback
        callback = asyncio.get_running_loop().create_future()
        window = MainWindow()
        window.show()
        root = window.window
        assert root is not None

        async def visible(name: str) -> QObject:
            await wait_for_qml(
                qt_application, lambda: bool((item := root.findChild(QObject, name)) and item.property("visible"))
            )
            item = root.findChild(QObject, name)
            assert item is not None
            return item

        try:
            await settle(window)
            window.controller.select_view(window.controller.sidebar.find_row({"service": "itchio"}))
            assert QMetaObject.invokeMethod(await visible("storeSignIn"), "clicked")
            fallback = await visible("useItchIoApiKey")
            assert opened == [browser_url]
            response = root.findChild(QObject, "storeLoginResponse")
            assert response is not None and not response.property("visible")

            if use_api_key:
                assert QMetaObject.invokeMethod(fallback, "clicked")
                await visible("storeLoginResponse")
                assert response.setProperty("text", "manual-key")
                assert QMetaObject.invokeMethod(await visible("finishStoreLogin"), "clicked")
                expected = [("key", "manual-key")]
            else:
                callback.set_result("oauth-code")
                expected = [("browser", "oauth-code")]

            await wait_for_qml(qt_application, lambda: synced == ["itchio"])
            assert submitted == expected
            assert cancelled == (["browser"] if use_api_key else [])
        finally:
            window.shutdown()

    asyncio.run(run())
