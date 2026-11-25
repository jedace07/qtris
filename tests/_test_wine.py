import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from qtris.delegates import InstallUIDelegate
from qtris.runners import wine
from qtris.util.test_config import setup_test_environment
from qtris.util.wine.dll_manager import DLLManager

setup_test_environment()

GE_LATEST_PATH = "/proton/GE-Proton Latest"
GE_LATEST_WINE = GE_LATEST_PATH + "/files/bin/wine"


class TestDllOverrides(TestCase):
    def test_env_format(self):
        overrides = {
            "d3dcompiler_43": "native,builtin",
            "d3dcompiler_47": "native,builtin",
            "dnsapi": " builtin",
            "dwrite": " disabled",
            "winemenubuilder": "disabled",
            "rasapi32": " native",
        }
        env_string = wine.get_overrides_env(overrides)
        self.assertEqual(env_string, "d3dcompiler_43,d3dcompiler_47=n,b;dnsapi=b;rasapi32=n;dwrite,winemenubuilder=")


class TestDllManager(TestCase):
    def test_numeric_version_is_normalized(self):
        manager = DLLManager(version=2.6)
        self.assertEqual(manager.version, "2.6")


class TestProtonGraphics(TestCase):
    """Proton brings its own DXVK and VKD3D; Qtris only falls back to WineD3D without Vulkan."""

    def get_env(self, vulkan: bool) -> dict[str, str]:
        runner = wine.wine()
        runner.system_config["env"] = {}
        with (
            patch.object(wine.LINUX_SYSTEM, "is_vulkan_supported", return_value=vulkan),
            patch.object(runner, "get_executable", return_value=GE_LATEST_WINE),
            patch.object(runner, "read_version_from_config", return_value=wine.GE_PROTON_LATEST),
        ):
            return runner.get_env()

    def test_wined3d_only_without_vulkan(self):
        self.assertNotIn("PROTON_USE_WINED3D", self.get_env(vulkan=True))
        self.assertEqual(self.get_env(vulkan=False)["PROTON_USE_WINED3D"], "1")

    def test_proton_leaves_shader_cache_to_driver(self) -> None:
        env = self.get_env(vulkan=False)

        self.assertNotIn("__GL_SHADER_DISK_CACHE", env)
        self.assertNotIn("__GL_SHADER_DISK_CACHE_PATH", env)

    def test_no_dxvk_or_vkd3d_options(self):
        option_names = {option["option"] for option in wine.wine().runner_options}
        self.assertFalse(option_names & {"dxvk", "dxvk_version", "vkd3d", "vkd3d_version", "dxvk_nvapi", "d3d_extras"})

    def test_ge_proton_latest_runs_from_qtris_folder(self):
        env = self.get_env(vulkan=True)
        self.assertEqual(env["WINE"], GE_LATEST_WINE)
        self.assertNotIn("PROTONPATH", env)
        wine.proton.update_proton_env(env["WINE"], env)
        self.assertEqual(env["PROTONPATH"], GE_LATEST_PATH)


class TestProtonOnly(TestCase):
    """The Wine runner runs Proton only: no system Wine, custom executables or
    options that only plain Wine builds honored."""

    def test_no_options_for_plain_wine(self):
        runner = wine.wine()
        option_names = {option["option"] for option in runner.runner_options + runner.game_options}
        self.assertFalse(option_names & {"custom_wine_path", "arch", "Desktop", "WineDesktop"})

    @patch.object(wine.proton.LINUX_SYSTEM, "arch", "x86_64")
    @patch.object(wine.proton, "get_proton_versions", return_value={"Proton 11.0": "/steam/Proton 11.0"})
    def test_version_choices_are_proton_builds(self, _versions):
        wine.get_installed_wine_versions.cache_clear()
        self.addCleanup(wine.get_installed_wine_versions.cache_clear)
        self.assertEqual(
            wine._get_wine_version_choices(),
            [
                ("GE-Proton Latest", wine.GE_PROTON_LATEST),
                ("Proton-CachyOS Latest", "proton-cachyos-latest"),
                ("Proton Wineland Latest", "proton-wineland-latest"),
                ("Proton 11.0", "Proton 11.0"),
            ],
        )

    @patch.object(wine.proton, "get_proton_versions", return_value={})
    def test_a_game_on_a_missing_rolling_build_needs_it_installed(self, _versions):
        runner = wine.wine()
        with patch.object(runner, "read_version_from_config", return_value="proton-cachyos-latest"):
            self.assertFalse(runner.is_installed())
            delegate = InstallUIDelegate()
            with patch.object(runner, "install_rolling_proton") as install_rolling_proton:
                runner.install(delegate)
        install_rolling_proton.assert_called_once()
        self.assertEqual(install_rolling_proton.call_args.args[1].version, "proton-cachyos-latest")

    @patch.object(wine.proton, "get_proton_versions", return_value={})
    def test_ge_proton_latest_is_installed_like_the_other_rolling_builds(self, _versions):
        runner = wine.wine()
        # A version that isn't Proton runs with GE-Proton Latest, so that's what gets installed
        for version in (wine.GE_PROTON_LATEST, "wine-ge-8-26-x86_64"):
            with self.subTest(version=version), patch.object(runner, "read_version_from_config", return_value=version):
                self.assertFalse(runner.is_installed())
                with patch.object(runner, "install_rolling_proton") as install_rolling_proton:
                    runner.install(InstallUIDelegate())
                self.assertEqual(install_rolling_proton.call_args.args[1].version, wine.GE_PROTON_LATEST)

    def test_umu_without_a_proton_build_gets_ge_proton_latest(self):
        with tempfile.TemporaryDirectory() as proton_dir:
            with patch.object(wine.proton, "get_proton_dir", return_value=proton_dir):
                env = {}
                wine.proton.update_proton_env("/umu/umu-run", env)
                # umu downloads its own copy when Qtris hasn't installed GE-Proton Latest
                self.assertEqual(env["PROTONPATH"], "GE-Latest")
                latest = os.path.join(proton_dir, "GE-Proton Latest")
                os.makedirs(latest)
                open(os.path.join(latest, "proton"), "w", encoding="utf-8").close()
                env = {}
                wine.proton.update_proton_env("/umu/umu-run", env)
                self.assertEqual(env["PROTONPATH"], latest)


class TestInstallerRunnerVersion(TestCase):
    """Use GE-Proton Latest when an installer does not name a Wine build."""

    def get_installer(self, script):
        return SimpleNamespace(runner="wine", script=script, requires=None)

    @patch.object(wine.wine, "get_runner_version_and_config", side_effect=wine.UnspecifiedVersionError("unset"))
    def test_unspecified_version_uses_default(self, _get_config):
        runner = wine.wine()
        version = runner.get_installer_runner_version(self.get_installer({}), use_default=True)
        self.assertEqual(version, wine.get_default_wine_version())

    @patch.object(wine.wine, "get_runner_version_and_config", side_effect=wine.UnspecifiedVersionError("unset"))
    def test_unspecified_version_needs_ge_proton_latest(self, _get_config):
        runner = wine.wine()
        interpreter = SimpleNamespace(installer=self.get_installer({}))
        for versions, installed in (({}, False), ({wine.GE_PROTON_LATEST: GE_LATEST_PATH}, True)):
            with patch.object(wine.proton, "get_proton_versions", return_value=versions):
                self.assertEqual(runner.is_installed_for(interpreter), installed)

    def test_script_wine_build_uses_default(self):
        runner = wine.wine()
        installer = self.get_installer({"wine": {"version": "wine-ge-8-26-x86_64"}})
        self.assertEqual(runner.get_installer_runner_version(installer, use_default=True), wine.GE_PROTON_LATEST)

    @patch.object(
        wine.proton, "get_proton_versions", return_value={"GE-Proton11-7-x86_64": "/compat/GE-Proton11-7-x86_64"}
    )
    def test_pinned_ge_proton_tag_is_kept(self, _versions):
        runner = wine.wine()
        installer = self.get_installer({"wine": {"version": "GE-Proton11-7"}})
        self.assertEqual(runner.get_installer_runner_version(installer, use_default=True), "GE-Proton11-7")


class TestPinnedGeProton(TestCase):
    """A pinned GE-Proton release comes from GitHub into Qtris's Proton folder, since
    the Lutris.net API doesn't serve them."""

    def get_interpreter(self, version):
        return SimpleNamespace(
            installer=SimpleNamespace(runner="wine", script={"wine": {"version": version}}, requires=None)
        )

    @patch.object(
        wine.proton, "get_proton_versions", return_value={"GE-Proton11-7-x86_64": "/compat/GE-Proton11-7-x86_64"}
    )
    def test_installed_release_needs_no_install(self, _versions):
        self.assertTrue(wine.wine().is_installed_for(self.get_interpreter("GE-Proton11-7")))

    @patch.object(wine.proton, "get_proton_versions", return_value={})
    def test_missing_release_needs_an_install(self, _versions):
        self.assertFalse(wine.wine().is_installed_for(self.get_interpreter("GE-Proton11-7")))

    @patch.object(wine.proton, "get_proton_versions", return_value={})
    def test_missing_release_is_downloaded_from_github(self, _versions):
        runner = wine.wine()
        delegate = InstallUIDelegate()
        with patch.object(runner, "install_proton_release") as install_proton_release:
            runner.install(delegate, version="GE-Proton11-7")
        install_proton_release.assert_called_once_with(delegate, "GE-Proton11-7", callback=None)

    @patch.object(wine.proton, "get_proton_dir")
    @patch.object(wine.proton_releases, "get_proton_checksum")
    @patch.object(
        wine.proton_releases,
        "get_proton_release_urls",
        return_value=("https://dl/GE-Proton11-7-x86_64.tar.gz", "https://dl/GE-Proton11-7-x86_64.sha512sum"),
    )
    def test_download_is_verified_and_extracted_to_the_proton_dir(self, _urls, get_checksum, get_proton_dir):
        with tempfile.TemporaryDirectory() as temp_dir:
            proton_dir = os.path.join(temp_dir, "proton")
            get_proton_dir.return_value = proton_dir
            payload = b"GE-Proton build"
            get_checksum.return_value = hashlib.sha512(payload).hexdigest()

            def download(_url, destination):
                with open(destination, "wb") as archive:
                    archive.write(payload)
                return True

            delegate = InstallUIDelegate()
            delegate.download_install_file = download
            runner = wine.wine()
            with (
                patch.object(wine.settings, "CACHE_DIR", temp_dir),
                patch.object(runner, "extract") as extract,
            ):
                runner.install_proton_release(delegate, "GE-Proton11-7")
            archive, destination = extract.call_args.args
            self.assertEqual(Path(archive).name, "GE-Proton11-7-x86_64.tar.gz")
            self.assertEqual(destination, proton_dir)
            self.assertEqual(
                extract.call_args.kwargs, {"merge_single": False, "executable": "GE-Proton11-7-x86_64/proton"}
            )

    @patch.object(wine.proton_releases, "get_proton_checksum", return_value="0" * 128)
    @patch.object(
        wine.proton_releases,
        "get_proton_release_urls",
        return_value=("https://dl/GE-Proton11-7-x86_64.tar.gz", "https://dl/GE-Proton11-7-x86_64.sha512sum"),
    )
    def test_corrupt_download_is_rejected(self, _urls, _checksum):
        with tempfile.TemporaryDirectory() as temp_dir:

            def download(_url, destination):
                with open(destination, "wb") as archive:
                    archive.write(b"truncated")
                return True

            runner = wine.wine()
            with (
                patch.object(wine.settings, "CACHE_DIR", temp_dir),
                patch.object(runner, "extract") as extract,
                self.assertRaises(wine.RunnerInstallationError),
            ):
                delegate = InstallUIDelegate()
                delegate.download_install_file = download
                runner.install_proton_release(delegate, "GE-Proton11-7")
            extract.assert_not_called()
            self.assertEqual(os.listdir(temp_dir), [])

    @patch.object(wine.proton_releases, "get_proton_checksum")
    @patch.object(
        wine.proton_releases,
        "get_proton_release_urls",
        return_value=(
            "https://dl/proton-cachyos-11.0-20261015-slr-x86_64_v3.tar.xz",
            "https://dl/proton-cachyos-11.0-20261015-slr-x86_64_v3.sha512sum",
        ),
    )
    def test_proton_cachyos_unpacks_from_its_xz_archive(self, _urls, get_checksum):
        with tempfile.TemporaryDirectory() as temp_dir:
            get_checksum.return_value = hashlib.sha512(b"build").hexdigest()

            def download(_url, destination):
                with open(destination, "wb") as archive:
                    archive.write(b"build")
                return True

            delegate = InstallUIDelegate()
            delegate.download_install_file = download
            runner = wine.wine()
            with (
                patch.object(wine.settings, "CACHE_DIR", temp_dir),
                patch.object(wine.proton, "get_proton_dir", return_value=temp_dir),
                patch.object(runner, "extract") as extract,
            ):
                runner.install_proton_release(delegate, "proton-cachyos-11.0-20261015-slr")
            self.assertEqual(
                extract.call_args.kwargs["executable"], "proton-cachyos-11.0-20261015-slr-x86_64_v3/proton"
            )

    @patch.object(wine.proton_releases, "get_proton_release_urls", return_value=None)
    def test_release_without_a_build_fails_cleanly(self, _urls):
        with self.assertRaises(wine.RunnerInstallationError):
            wine.wine().install_proton_release(InstallUIDelegate(), "GE-Proton11-7")


class TestMissingWineVersion(TestCase):
    """A missing Wine version falls back to GE-Proton Latest, which would upgrade a
    plain Wine prefix to Proton for good."""

    def setUp(self):
        self.game_config = {}
        self.game_version = "wine-test-missing-1"
        for patcher in (
            patch("qtris.config.read_yaml_from_file", side_effect=self.read_yaml),
            patch.dict(os.environ, {"WINEPREFIX": ""}),
            patch.object(wine.proton, "get_proton_versions", return_value={wine.GE_PROTON_LATEST: GE_LATEST_PATH}),
            patch.object(wine.proton, "get_proton_wine_path", return_value=GE_LATEST_WINE),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def read_yaml(self, path):
        if path.endswith("game.yml"):
            if not self.game_version:
                return {"game": self.game_config}
            return {"game": self.game_config, "wine": {"version": self.game_version}}
        if path.endswith("wine.yml"):
            return {"wine": {"version": "wine-test-missing-1"}}
        return {}

    def make_prefix(self, *files):
        prefix = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, prefix)
        for name in ("user.reg",) + files:
            open(os.path.join(prefix, name), "w", encoding="utf-8").close()
        self.game_config = {"prefix": prefix}

    def get_runner(self):
        return wine.wine(wine.QtrisConfig(runner_slug="wine", game_config_id="game"))

    def test_wine_prefix_is_not_switched_to_proton(self):
        self.make_prefix()
        with self.assertRaises(wine.MissingExecutableError) as error:
            self.get_runner().get_executable()
        self.assertIn("wine-test-missing-1", str(error.exception))

    def test_wine_prefix_inheriting_a_missing_runner_version_is_not_switched(self):
        self.game_version = None
        self.make_prefix()
        with self.assertRaises(wine.MissingExecutableError):
            self.get_runner().get_executable()

    def test_wine_terminal_on_a_wine_prefix_is_not_opened(self):
        self.make_prefix()
        runner = self.get_runner()
        with (
            patch.object(runner, "get_env", return_value={}),
            patch.object(wine, "open_wine_terminal") as open_wine_terminal,
            self.assertRaises(wine.MissingExecutableError),
        ):
            runner.run_wine_terminal()
        open_wine_terminal.assert_not_called()

    def test_proton_prefix_falls_back_to_the_default(self):
        for marker in ("tracked_files", "version"):
            with self.subTest(marker=marker):
                self.make_prefix(marker)
                self.assertEqual(self.get_runner().get_executable(), GE_LATEST_WINE)

    def test_game_set_to_ge_proton_falls_back(self):
        self.game_version = wine.GE_PROTON_LATEST
        self.make_prefix()
        self.assertEqual(self.get_runner().get_executable(), GE_LATEST_WINE)

    def test_new_prefix_falls_back_to_the_default(self):
        parent = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, parent)
        self.game_config = {"prefix": os.path.join(parent, "prefix")}
        self.assertEqual(self.get_runner().get_executable(), GE_LATEST_WINE)

    def test_installed_proton_version_is_used(self):
        self.game_version = "GE-Proton11-7"
        self.make_prefix()
        wine_path = "/compat/GE-Proton11-7-x86_64/files/bin/wine"
        with (
            patch.object(wine.proton, "get_proton_versions", return_value={"GE-Proton11-7-x86_64": "/compat"}),
            patch.object(wine.proton, "get_proton_wine_path", return_value=wine_path),
        ):
            self.assertEqual(self.get_runner().get_executable(), wine_path)


class TestStoppingWineGames(TestCase):
    """Stopping signals everything the launch started rather than running wineboot -k
    through umu, which checks for runtime updates and starts its container first."""

    def test_stop_pids_include_wine_and_umu_processes(self):
        game_folder = "/games/example"
        processes = {
            1: SimpleNamespace(cmdline=f"{game_folder}/game.exe", name="game.exe", environ={"QTRIS_GAME_UUID": "a"}),
            2: SimpleNamespace(
                cmdline="/proton/files/bin/wineserver", name="wineserver", environ={"QTRIS_GAME_UUID": "a"}
            ),
            3: SimpleNamespace(cmdline="pv-adverb --fd 9", name="pv-adverb", environ={"QTRIS_GAME_UUID": "a"}),
            4: SimpleNamespace(
                cmdline="/proton/files/bin/wineserver", name="wineserver", environ={"QTRIS_GAME_UUID": "b"}
            ),
            5: SimpleNamespace(cmdline=f"{game_folder}/game.exe", name="game.exe", environ={}),
        }
        runner = wine.wine()
        with patch.object(wine, "Process", side_effect=processes.__getitem__):
            self.assertEqual(runner.filter_game_pids(processes, "a", game_folder), {1})
            self.assertEqual(runner.filter_stop_pids(processes, "a", game_folder), {1, 2, 3})

    def test_stopping_runs_no_wine_command(self):
        runner = wine.wine()
        with (
            patch.object(wine, "winekill") as winekill,
            patch("qtris.runners.runner.kill_processes") as kill_processes,
        ):
            runner.force_stop_game({1, 2, 3})
        winekill.assert_not_called()
        kill_processes.assert_called_once()
        self.assertEqual(set(kill_processes.call_args.args[1]), {1, 2, 3})
