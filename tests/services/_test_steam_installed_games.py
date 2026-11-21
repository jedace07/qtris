from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from unittest import TestCase
from unittest.mock import PropertyMock, patch

from qtris import settings
from qtris.config import write_game_config
from qtris.database import categories, games, schema, sql
from qtris.database.services import ServiceGameCollection
from qtris.game import Game
from qtris.services.steam import SteamGame, SteamService
from qtris.util.steam.appmanifest import APP_STATE_FLAGS

APPID = "12345"
# A game the Steam Web API leaves out of the library
UNLISTED_APPID = "3545060"
FULLY_INSTALLED = 1 << (APP_STATE_FLAGS.index("Fully Installed") - 1)
# With playtime, so the dedup at the end of add_installed_games() keeps the entry
PLAYTIME = 1.0
APPMANIFEST = dedent(
    """\
    "AppState"
    {
        "appid"       "%s"
        "name"        "Test Game"
        "StateFlags"  "%s"
    }
    """
)


class TestImportInstalledSteamGames(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        for name, value in {"DB_PATH": str(self.directory / "pga.db"), "CONFIG_DIR": str(self.directory)}.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        schema.syncdb()
        (self.directory / "games").mkdir()
        self.steamapps = self.directory / "steamapps"
        self.steamapps.mkdir()
        SteamGame.new_from_steam_game({"appid": APPID, "name": "Test Game"}).save()
        for patcher in (
            patch.object(
                SteamService, "steamapps_paths", new_callable=PropertyMock, return_value=[str(self.steamapps)]
            ),
            # Don't touch the real desktop and Steam shortcuts when uninstalling
            patch("qtris.game.remove_steam_shortcut"),
            patch("qtris.game.xdgshortcuts.remove_launcher"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("qtris.artwork.sync_artwork")
        self.sync_media = patcher.start()
        self.addCleanup(patcher.stop)
        # What Steam says about the game: it has only a Windows build
        patcher = patch("qtris.runners.steam.get_app_oslist", return_value=("windows",))
        self.get_app_oslist = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("qtris.runners.steam.get_compat_tools", return_value={})
        self.get_compat_tools = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("qtris.services.steam.get_app_type", return_value="game")
        self.get_app_type = patcher.start()
        self.addCleanup(patcher.stop)

    def set_installed_in_steam(self, installed, appid=APPID):
        manifest = self.steamapps / ("appmanifest_%s.acf" % appid)
        if installed:
            manifest.write_text(APPMANIFEST % (appid, FULLY_INSTALLED), encoding="utf-8")
        else:
            manifest.unlink()

    def get_imported_games(self, appid=APPID):
        return games.get_games(filters={"service": "steam", "service_id": appid})

    def test_installed_game_missing_from_the_web_api_is_imported(self):
        # Mods such as Team Fortress 2 Classified are left out of the owned games
        self.set_installed_in_steam(True, UNLISTED_APPID)
        SteamService().add_installed_games()

        self.get_app_type.assert_any_call(UNLISTED_APPID)
        service_game = ServiceGameCollection.get_game("steam", UNLISTED_APPID)
        assert service_game is not None
        self.assertEqual(service_game["name"], "Test Game")
        imported = self.get_imported_games(UNLISTED_APPID)
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["installed"], 1)
        self.assertEqual(imported[0]["runner"], "steam")

    def test_installed_tools_are_not_imported(self):
        self.get_app_type.return_value = "tool"
        self.set_installed_in_steam(True, UNLISTED_APPID)
        SteamService().add_installed_games()

        self.assertIsNone(ServiceGameCollection.get_game("steam", UNLISTED_APPID))
        self.assertEqual(self.get_imported_games(UNLISTED_APPID), [])

    def test_apps_steam_does_not_know_are_not_imported(self):
        self.get_app_type.return_value = ""
        self.set_installed_in_steam(True, UNLISTED_APPID)
        SteamService().add_installed_games()
        self.assertEqual(self.get_imported_games(UNLISTED_APPID), [])

    def test_installed_game_is_imported_once(self):
        self.set_installed_in_steam(True)
        service = SteamService()
        service.add_installed_games()
        service.add_installed_games()

        imported = self.get_imported_games()
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["installed"], 1)
        self.assertEqual(Game(imported[0]["id"]).config.game_level["game"]["appid"], APPID)

    def test_game_reinstalled_in_steam_is_installed_again(self):
        service = SteamService()
        self.set_installed_in_steam(True)
        service.add_installed_games()
        self.set_installed_in_steam(False)
        service.add_installed_games()
        self.assertEqual(self.get_imported_games()[0]["installed"], 0)

        self.set_installed_in_steam(True)
        service.add_installed_games()

        imported = self.get_imported_games()
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["installed"], 1)
        self.assertEqual(imported[0]["runner"], "steam")
        game = Game(imported[0]["id"])
        self.assertTrue(game.is_installed)
        self.assertEqual(game.config.game_level["game"]["appid"], APPID)

    def test_reinstall_keeps_what_was_changed_in_qtris(self):
        service = SteamService()
        self.set_installed_in_steam(True)
        service.add_installed_games()
        self.set_installed_in_steam(False)
        service.add_installed_games()
        game_id = self.get_imported_games()[0]["id"]
        sql.db_update(
            settings.DB_PATH,
            "games",
            {"name": "Renamed Game", "slug": "renamed-game", "installed_at": None},
            {"id": game_id},
        )
        categories.add_game_to_category(game_id, categories.add_category("Shooters"))

        self.set_installed_in_steam(True)
        service.add_installed_games()

        game = games.get_game_by_field(game_id, "id")
        self.assertEqual(game["installed"], 1)
        self.assertEqual(game["name"], "Renamed Game")
        self.assertEqual(game["slug"], "renamed-game")
        self.assertIsNotNone(game["installed_at"])
        self.assertEqual(categories.get_categories_in_game(game_id), ["Shooters"])
        self.assertEqual(self.sync_media.call_args.args[0], ["renamed-game"])

    def test_uninstalled_duplicate_is_left_alone_when_another_entry_is_installed(self):
        entry = {"name": "Test Game", "installer_slug": "steam-%s" % APPID, "service": "steam", "service_id": APPID}
        uninstalled_id = games.add_game(slug="test-game", installed=0, **entry)
        configpath = write_game_config("steam-%s" % APPID, {"game": {"appid": APPID}})
        installed_id = games.add_game(
            slug="test-game-2", installed=1, runner="steam", configpath=configpath, playtime=PLAYTIME, **entry
        )
        self.set_installed_in_steam(True)

        SteamService().add_installed_games()

        uninstalled = games.get_game_by_field(uninstalled_id, "id")
        self.assertIsNotNone(uninstalled)
        self.assertEqual(uninstalled["installed"], 0)
        self.assertEqual(games.get_game_by_field(installed_id, "id")["installed"], 1)

    def test_games_that_run_through_proton_are_windows_games(self):
        self.set_installed_in_steam(True)
        SteamService().add_installed_games()
        self.assertEqual(self.get_imported_games()[0]["platform"], "Windows")

    def test_platform_follows_the_compatibility_tool_chosen_in_steam(self):
        self.get_app_oslist.return_value = ("windows", "linux")
        self.set_installed_in_steam(True)
        service = SteamService()
        service.add_installed_games()
        self.assertEqual(self.get_imported_games()[0]["platform"], "Linux")

        self.get_compat_tools.return_value = {APPID: "proton_experimental"}
        service.add_installed_games()
        self.assertEqual(self.get_imported_games()[0]["platform"], "Windows")

    def test_platform_is_kept_when_steam_does_not_know_the_game(self):
        self.set_installed_in_steam(True)
        service = SteamService()
        service.add_installed_games()
        self.get_app_oslist.return_value = ()
        service.add_installed_games()
        self.assertEqual(self.get_imported_games()[0]["platform"], "Windows")
