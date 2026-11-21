import os
import tempfile
import unittest
from unittest.mock import patch

from qtris.util.wine import proton

QTRIS_ENV = {"HOME": "/home/player", "XDG_DATA_HOME": "/home/player/.local/share"}


@patch.object(proton.LINUX_SYSTEM, "is_flatpak", return_value=False)
class TestUmuFoldersPath(unittest.TestCase):
    """Where Umu keeps its runtime when a game moves its own directories.

    Umu reads XDG_DATA_HOME (HOST_XDG_DATA_HOME under Flatpak) to decide where to
    download the runtime and Proton builds. A game that overrides it to keep its
    saves elsewhere dragged those downloads along with it.
    """

    def get_path(self, env, environ=None):
        with patch.dict(os.environ, QTRIS_ENV if environ is None else environ, clear=True):
            return proton.get_umu_folders_path(env)

    def test_untouched_environment_is_left_to_umu(self, _is_flatpak):
        self.assertEqual(self.get_path({}), "")
        self.assertEqual(self.get_path(dict(QTRIS_ENV)), "")

    def test_game_overriding_the_data_home_keeps_umu_in_place(self, _is_flatpak):
        env = dict(QTRIS_ENV, XDG_DATA_HOME="/games/witcher/data")
        self.assertEqual(self.get_path(env), "/home/player/.local/share")

    def test_game_overriding_home_keeps_umu_in_place(self, _is_flatpak):
        """Umu falls back to Path.home(), so HOME moves the downloads too."""
        env = dict(QTRIS_ENV, HOME="/games/witcher")
        self.assertEqual(self.get_path(env), "/home/player/.local/share")

    def test_falls_back_to_the_default_share_directory(self, _is_flatpak):
        environ = {"HOME": "/home/player"}
        env = {"XDG_DATA_HOME": "/games/witcher/data"}
        self.assertEqual(self.get_path(env, environ), "/home/player/.local/share")

    def test_nothing_to_point_at_means_nothing_is_set(self, _is_flatpak):
        self.assertEqual(self.get_path({"XDG_DATA_HOME": "/games/x"}, environ={}), "")

    def test_update_proton_env_sets_it_only_when_needed(self, _is_flatpak):
        with patch.dict(os.environ, QTRIS_ENV, clear=True):
            plain = dict(QTRIS_ENV)
            proton.update_proton_env("/wine", plain)
            self.assertNotIn("UMU_FOLDERS_PATH", plain)

            moved = dict(QTRIS_ENV, XDG_DATA_HOME="/games/witcher/data")
            proton.update_proton_env("/wine", moved)
            self.assertEqual(moved["UMU_FOLDERS_PATH"], "/home/player/.local/share")
            # the game still sees the directories it asked for
            self.assertEqual(moved["XDG_DATA_HOME"], "/games/witcher/data")

    def test_an_explicit_setting_is_never_overwritten(self, _is_flatpak):
        with patch.dict(os.environ, QTRIS_ENV, clear=True):
            env = dict(QTRIS_ENV, XDG_DATA_HOME="/games/x", UMU_FOLDERS_PATH="/chosen")
            proton.update_proton_env("/wine", env)
            self.assertEqual(env["UMU_FOLDERS_PATH"], "/chosen")


class TestUmuFoldersPathInFlatpak(unittest.TestCase):
    @patch.object(proton.LINUX_SYSTEM, "is_flatpak", return_value=True)
    def test_flatpak_uses_the_host_data_home(self, _is_flatpak):
        environ = {"HOME": "/home/player", "HOST_XDG_DATA_HOME": "/home/player/.local/share"}
        with patch.dict(os.environ, environ, clear=True):
            # XDG_DATA_HOME inside the sandbox is not what Umu reads there
            self.assertEqual(proton.get_umu_folders_path({"XDG_DATA_HOME": "/games/x"}), "")
            moved = {"HOST_XDG_DATA_HOME": "/games/x"}
            self.assertEqual(proton.get_umu_folders_path(moved), "/home/player/.local/share")


INSTALLED_PROTONS = {
    "GE-Proton11-7-x86_64": "/compat/GE-Proton11-7-x86_64",
    "GE-Proton10-34": "/compat/GE-Proton10-34",
    "proton-cachyos-11.0-20261015-slr-x86_64_v3": "/compat/proton-cachyos-11.0-20261015-slr-x86_64_v3",
}


@patch.object(proton.LINUX_SYSTEM, "arch", "x86_64")
@patch.object(proton, "get_proton_versions", return_value=INSTALLED_PROTONS)
class TestFindProtonVersion(unittest.TestCase):
    """GE-Proton versions are normalized to the bare release tag, while GE-Proton 11
    and later install into '<tag>-<arch>' folders."""

    def test_bare_tag_matches_an_arch_suffixed_folder(self, _versions):
        self.assertEqual(proton.find_proton_version("GE-Proton11-7"), "GE-Proton11-7-x86_64")
        self.assertTrue(proton.is_proton_version("GE-Proton11-7"))

    def test_exact_folder_name_matches(self, _versions):
        self.assertEqual(proton.find_proton_version("GE-Proton10-34"), "GE-Proton10-34")
        self.assertEqual(proton.find_proton_version("GE-Proton11-7-x86_64"), "GE-Proton11-7-x86_64")

    def test_proton_cachyos_release_matches_its_optimized_build(self, _versions):
        self.assertEqual(
            proton.find_proton_version("proton-cachyos-11.0-20261015-slr"),
            "proton-cachyos-11.0-20261015-slr-x86_64_v3",
        )

    def test_missing_versions_do_not_match(self, _versions):
        self.assertIsNone(proton.find_proton_version("GE-Proton11-8"))
        self.assertIsNone(proton.find_proton_version(""))
        self.assertIsNone(proton.find_proton_version(None))


class TestSteamCompatDir(unittest.TestCase):
    @patch.object(proton.LINUX_SYSTEM, "is_flatpak", return_value=False)
    def test_follows_xdg_data_home(self, _is_flatpak):
        with patch.dict(os.environ, {"HOME": "/home/player", "XDG_DATA_HOME": "/data"}, clear=True):
            self.assertEqual(proton.get_steam_compat_dir(), "/data/Steam/compatibilitytools.d")

    @patch.object(proton.LINUX_SYSTEM, "is_flatpak", return_value=True)
    def test_flatpak_uses_the_host_data_home(self, _is_flatpak):
        environ = {"HOME": "/home/player", "XDG_DATA_HOME": "/sandbox", "HOST_XDG_DATA_HOME": "/host"}
        with patch.dict(os.environ, environ, clear=True):
            self.assertEqual(proton.get_steam_compat_dir(), "/host/Steam/compatibilitytools.d")


class TestGeProtonLatest(unittest.TestCase):
    @patch.object(proton.LINUX_SYSTEM, "is_flatpak", return_value=False)
    def test_reads_the_release_umu_downloaded(self, _is_flatpak):
        with tempfile.TemporaryDirectory() as data_home:
            with patch.dict(os.environ, {"HOME": "/home/player", "XDG_DATA_HOME": data_home}, clear=True):
                self.assertIsNone(proton.get_ge_proton_latest_release())
                latest = proton.get_ge_proton_latest_path()
                self.assertEqual(latest, os.path.join(data_home, "umu", "compatibilitytools", "GE-Latest"))
                os.makedirs(latest)
                with open(os.path.join(latest, "version"), "w", encoding="utf-8") as version_file:
                    version_file.write("1793687165 GE-Proton11-12\n")
                self.assertEqual(proton.get_ge_proton_latest_release(), "GE-Proton11-12")
