import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from qtris.util.wine import proton, wine

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


def _install_build(folder, marker: str = "") -> None:
    folder.mkdir(parents=True)
    (folder / "proton").touch()
    if marker:
        (folder / marker).write_text("{}", encoding="utf-8")


def _write_manifest(steamapps, appid: int, name: str, installdir: str) -> None:
    (steamapps / f"appmanifest_{appid}.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"%s"\n\t"installdir"\t\t"%s"\n}\n' % (appid, name, installdir),
        encoding="utf-8",
    )


class TestSteamProtonBuilds(unittest.TestCase):
    """Steam's Proton builds are shown by the names Steam gives them, and the builds
    ProtonPlus manages in Steam's compatibilitytools.d are left to it."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        steamapps, compat = root / "steamapps", root / "compatibilitytools.d"
        _install_build(steamapps / "common" / "Proton 9.0 (Beta)")
        _write_manifest(steamapps, 2805730, "Proton 9.0", "Proton 9.0 (Beta)")
        _install_build(steamapps / "common" / "Proton 8.0")
        _write_manifest(steamapps, 2348590, "Proton 8.0", "Proton 8.0")
        _write_manifest(steamapps, 440, "Team Fortress 2", "Team Fortress 2")
        _install_build(compat / "Proton-GE Latest", proton.PROTONPLUS_MARKER)
        _install_build(compat / "GE-Proton11-12")
        self.compat = compat
        locations = (str(root / "qtris"), str(steamapps / "common"), str(compat))
        for patcher in (
            patch.object(proton, "get_umu_path", return_value="/umu/umu-run"),
            patch.object(proton, "get_proton_dir", return_value=str(root / "qtris")),
            patch.object(proton, "_iter_proton_locations", side_effect=lambda: iter(locations)),
            patch.object(proton, "get_steamapps_dirs", return_value=[str(steamapps)]),
            # Steam's menu names Proton 9.0, but its app cache doesn't hold Proton 8.0
            patch.object(proton, "get_compat_tool_names", return_value={2805730: "Proton 9.0-4"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.clear_caches()
        self.addCleanup(self.clear_caches)

    @staticmethod
    def clear_caches():
        proton.get_proton_versions.cache_clear()
        proton.get_steam_proton_labels.cache_clear()

    def test_protonplus_builds_are_left_out(self):
        self.assertEqual(set(proton.get_proton_versions()), {"Proton 9.0 (Beta)", "Proton 8.0", "GE-Proton11-12"})
        self.assertTrue(proton.is_protonplus_build(self.compat / "Proton-GE Latest"))
        self.assertFalse(proton.is_protonplus_build(self.compat / "GE-Proton11-12"))

    def test_steam_builds_have_the_names_steam_shows(self):
        self.assertEqual(proton.get_steam_proton_label("Proton 9.0 (Beta)"), "Proton 9.0-4")
        self.assertEqual(proton.get_steam_proton_label("Proton 8.0"), "Proton 8.0")
        self.assertIsNone(proton.get_steam_proton_label("GE-Proton11-12"))
        self.assertIsNone(proton.get_steam_proton_label("Team Fortress 2"))

    def test_wine_version_labels_use_steam_names(self):
        self.assertEqual(wine.get_wine_version_label("Proton 9.0 (Beta)"), "Proton 9.0-4")
        self.assertEqual(wine.get_wine_version_label("GE-Proton11-12"), "GE-Proton11-12")
        self.assertEqual(wine.get_wine_version_label(wine.GE_PROTON_LATEST), "GE-Proton Latest")
