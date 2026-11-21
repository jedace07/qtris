import os
import unittest
from unittest.mock import patch

from qtris.util.wine import proton

QTRIS_ENV = {"HOME": "/home/player", "XDG_DATA_HOME": "/home/player/.local/share"}


@patch.object(proton.settings, "UMU_DATA_DIR", "/qtris/runtime/umu-data")
class TestUmuFoldersPath(unittest.TestCase):
    """umu keeps its runtime and locks in Qtris's own folder.

    umu reads XDG_DATA_HOME (HOST_XDG_DATA_HOME under Flatpak) to decide where to
    download the runtime, which is outside the sandbox under Flatpak, and a game that
    overrides it to keep its saves elsewhere dragged those downloads along with it.
    """

    def test_umu_is_pointed_at_qtris_folder(self):
        for is_flatpak in (False, True):
            with (
                self.subTest(is_flatpak=is_flatpak),
                patch.object(proton.LINUX_SYSTEM, "is_flatpak", return_value=is_flatpak),
                patch.dict(os.environ, dict(QTRIS_ENV, HOST_XDG_DATA_HOME="/home/player/.local/share"), clear=True),
            ):
                env = dict(QTRIS_ENV)
                proton.update_proton_env("/wine", env)
                self.assertEqual(env["UMU_FOLDERS_PATH"], "/qtris/runtime/umu-data")

    def test_game_overriding_its_folders_keeps_umu_in_place(self):
        with patch.dict(os.environ, QTRIS_ENV, clear=True):
            moved = dict(QTRIS_ENV, XDG_DATA_HOME="/games/witcher/data", HOME="/games/witcher")
            proton.update_proton_env("/wine", moved)
            self.assertEqual(moved["UMU_FOLDERS_PATH"], "/qtris/runtime/umu-data")
            # The game still sees the folders it asked for
            self.assertEqual(moved["XDG_DATA_HOME"], "/games/witcher/data")
            self.assertEqual(moved["HOME"], "/games/witcher")

    def test_an_explicit_setting_is_never_overwritten(self):
        with patch.dict(os.environ, QTRIS_ENV, clear=True):
            env = dict(QTRIS_ENV, UMU_FOLDERS_PATH="/chosen")
            proton.update_proton_env("/wine", env)
            self.assertEqual(env["UMU_FOLDERS_PATH"], "/chosen")


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
