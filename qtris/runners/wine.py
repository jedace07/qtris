"""Proton runner, registered as 'wine' for existing games and installers."""

from __future__ import annotations

# pylint: disable=too-many-lines
import os
import shlex
from collections.abc import Callable, Iterable
from gettext import gettext as _
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

from qtris import settings
from qtris.config import QtrisConfig, is_option_visible
from qtris.database.games import get_game_by_field
from qtris.exceptions import (
    MisconfigurationError,
    MissingExecutableError,
    MissingGameExecutableError,
    SymlinkNotUsableError,
    UnspecifiedVersionError,
)
from qtris.game import Game
from qtris.runners import NonInstallableRunnerError, RunnerInstallationError
from qtris.runners.commands.wine import (  # noqa: F401 pylint: disable=unused-import
    create_prefix,
    open_wine_terminal,
    winecfg,
    wineexec,
    winekill,
    winetricks,
)
from qtris.runners.runner import RunDataDict, Runner
from qtris.util import system
from qtris.util.display import get_default_dpi, is_display_x11
from qtris.util.graphics import vkquery
from qtris.util.http import HTTPError
from qtris.util.linux import LINUX_SYSTEM
from qtris.util.log import logger
from qtris.util.process import Process
from qtris.util.strings import split_arguments
from qtris.util.wine import proton, proton_releases, rolling_proton
from qtris.util.wine.extract_icon import PEFILE_AVAILABLE, IconExtractor
from qtris.util.wine.launchers import repair_ea_launcher
from qtris.util.wine.prefix import DEFAULT_DLL_OVERRIDES, WinePrefixManager, find_prefix
from qtris.util.wine.wine import (
    GE_PROTON_LATEST,
    WINE_DEFAULT_ARCH,
    clear_wine_version_cache,
    detect_prefix_arch,
    get_default_wine_version,
    get_installed_wine_versions,
    get_overrides_env,
    get_proton_version_or_default,
    get_real_executable,
    get_runner_files_dir_for_version,
    get_wine_path_for_version,
    get_wine_version_label,
    is_prefix_directory,
    is_winewayland_available,
)

if TYPE_CHECKING:
    from qtris.delegates import Delegate, InstallUIDelegate
    from qtris.monitored_command import MonitoredCommand


def is_umu_managed_version(version: str | None) -> bool:
    """True if this Wine 'version' is actually launched through umu: a rolling build
    such as GE-Proton Latest, a Proton release or any installed Proton build, rather
    than a Wine runner build."""
    return (
        proton.is_proton_version(version)
        or proton_releases.is_proton_release(version)
        or proton_releases.get_rolling(version) is not None
    )


def is_missing_proton_release(version: str | None) -> bool:
    """Check if a pinned Proton release or a rolling build needs to be installed."""
    downloadable = proton_releases.is_proton_release(version) or proton_releases.get_rolling(version) is not None
    return downloadable and not proton.is_proton_version(version)


def _is_proton_hdr_available(_option_key: str, config: QtrisConfig) -> bool:
    version = config.runner_config.get("version")
    return bool(version and is_winewayland_available(version) and config.runner_config.get("Graphics") == "wayland")


def _is_32_bit_prefix(prefix_path: str | None) -> bool:
    return bool(prefix_path and is_prefix_directory(prefix_path) and detect_prefix_arch(prefix_path) == "win32")


def _get_version_warning(_option_key: str, config: QtrisConfig) -> str | None:
    if _is_32_bit_prefix(config.game_config.get("prefix")):
        return _("Proton is not compatible with 32-bit prefixes.")

    return None


def _get_prefix_warning(_option_key: str, config: QtrisConfig) -> str | None:
    game_config = config.game_config
    if game_config.get("prefix"):
        return None

    exe = game_config.get("exe")
    if exe and find_prefix(exe):
        return None

    return _("<b>Warning</b> Some Wine configuration options cannot be applied, if no prefix can be found.")


def _get_exe_warning(_option_key: str, config: QtrisConfig) -> str | None:
    exe = config.game_config.get("exe") or ""
    stripped_exe = exe.strip()

    if not stripped_exe:
        return _("<b>Warning</b> No executable path specified")
    if exe != stripped_exe:
        return _("<b>Warning</b> Executable path has extra whitespace at the beginning or end")
    if not os.path.isabs(exe):
        # The working dir is a bit dicey- Qtris doe snot use the prefix as a working dir,
        # but it seems like Wine does, so we'll be conservative here. Only works with an explicit
        # prefix- if we derive it from "exe" we obviosly can't use that.
        working_dir = config.game_config.get("working_dir") or config.game_config.get("prefix") or ""
        exe = os.path.join(os.path.expanduser(working_dir), os.path.expanduser(exe))

    exe = system.fix_path_case(exe)

    if not os.path.isfile(exe):
        return _("<b>Warning</b> Executable file does not exist")

    return None


def _get_wine_wayland_warning(_option_key: str, config: QtrisConfig) -> str | None:
    runner_config = config.runner_config
    if runner_config.get("Graphics") == "wayland":
        runner_version = runner_config.get("version")

        if not runner_version:
            return None

        if is_display_x11():
            return _("You cannot use winewayland driver when using an X11-based session")

        if not is_winewayland_available(runner_version):
            return _("Your Proton version does not support winewayland graphics driver")

    return None


def _get_wine_version_choices() -> list[tuple[str, str]]:
    return [(get_wine_version_label(version), version) for version in get_installed_wine_versions()]


class wine(Runner):
    description: str = _("Runs Windows games with Proton")
    human_name = _("Proton")
    platform_dict = Runner.to_platform_dict([_("Windows")])
    multiple_versions = True
    entry_point_option = "exe"

    game_options = [
        {
            "option": "exe",
            "type": "file",
            "label": _("Executable"),
            "help": _("The game's main EXE file"),
            "warning": _get_exe_warning,
        },
        {
            "option": "args",
            "type": "string",
            "label": _("Arguments"),
            "help": _("Windows command line arguments used when launching the game"),
            "validator": shlex.split,
        },
        {
            "option": "working_dir",
            "type": "directory",
            "label": _("Working directory"),
            "help": _(
                "The folder the game starts in. Relative paths in games and launch scripts can depend on it.\n"
                "By default, Qtris uses the executable's folder."
            ),
        },
        {
            "option": "prefix",
            "type": "directory",
            "label": _("Wine prefix"),
            "warning": _get_prefix_warning,
            "help": _(
                "The prefix used by Wine.\n"
                "It's a directory containing a set of files and "
                "folders making up a confined Windows environment."
            ),
        },
        {
            "option": "desktop_integration",
            "type": "bool",
            "label": _("Integrate system files in the prefix"),
            "default": False,
            "advanced": True,
            "help": _(
                "Place 'Documents', 'Pictures', and similar files in your home folder, instead of "
                "keeping them in the game's prefix. This includes some saved games."
            ),
        },
    ]

    runner_options = [
        {
            "option": "version",
            "label": _("Proton version"),
            "type": "choice",
            "choices": _get_wine_version_choices,
            "default": get_default_wine_version,
            "warning": _get_version_warning,
            "help": _(
                "The Proton build used to launch the game.\n"
                "GE-Proton Latest is kept up to date by umu and is generally recommended, "
                "but some games work better on a specific release."
            ),
        },
        {
            "option": "system_winetricks",
            "label": _("Use system winetricks"),
            "type": "bool",
            "default": False,
            "advanced": True,
            "help": _("Switch on to use /usr/bin/winetricks for winetricks."),
        },
        {
            "option": "proton_hdr",
            "section": _("Graphics"),
            "label": _("Enable HDR (Experimental)"),
            "type": "bool",
            "default": False,
            "advanced": True,
            "visible": _is_proton_hdr_available,
            "help": _(
                "Enable Proton's support for High Dynamic Range graphics. "
                "Requires Wayland selected as Graphics backend."
            ),
        },
        {
            "option": "fsr",
            "label": _("Enable fullscreen FSR upscaling"),
            "type": "bool",
            "default": False,
            "help": _(
                "Use AMD FidelityFX Super Resolution 1.0 to upscale games running in exclusive fullscreen "
                "at a lower resolution than the screen's.\n"
                "Requires a Wine or Proton build with FSR and a lower resolution in the game's settings.\n"
                "Does not work with games running in borderless window mode or that perform their own upscaling."
            ),
        },
        {
            "option": "battleye",
            "label": _("Enable BattlEye Anti-Cheat"),
            "type": "bool",
            "default": True,
            "help": _("Use the Proton BattlEye Runtime installed through Steam for supported games."),
        },
        {
            "option": "eac",
            "label": _("Enable Easy Anti-Cheat"),
            "type": "bool",
            "default": True,
            "help": _("Use the Proton EasyAntiCheat Runtime installed through Steam for supported games."),
        },
        {
            "option": "Dpi",
            "section": _("DPI"),
            "label": _("Enable DPI Scaling"),
            "type": "bool",
            "advanced": True,
            "default": False,
            "help": _(
                "Enables the Windows application's DPI scaling.\n"
                "Otherwise, the Screen Resolution option in 'Wine configuration' controls this."
            ),
        },
        {
            "option": "ExplicitDpi",
            "section": _("DPI"),
            "label": _("DPI"),
            "type": "string",
            "conditional_on": "Dpi",
            "advanced": True,
            "default": str(get_default_dpi()),
            "help": _("The DPI to be used if 'Enable DPI Scaling' is turned on."),
        },
        {
            "option": "MouseWarpOverride",
            "label": _("Mouse Warp Override"),
            "type": "choice",
            "choices": [
                (_("Enable"), "enable"),
                (_("Disable"), "disable"),
                (_("Force"), "force"),
            ],
            "default": "enable",
            "advanced": True,
            "help": _(
                "Override the default mouse pointer warping behavior\n"
                "<b>Enable</b>: (Wine default) warp the pointer when the "
                "mouse is exclusively acquired \n"
                "<b>Disable</b>: never warp the mouse pointer \n"
                "<b>Force</b>: always warp the pointer"
            ),
        },
        {
            "option": "Audio",
            "label": _("Audio driver"),
            "type": "choice",
            "advanced": True,
            "choices": [
                (_("Auto"), "auto"),
                ("ALSA", "alsa"),
                ("PulseAudio", "pulse"),
                ("OSS", "oss"),
            ],
            "default": "auto",
            "help": _(
                "Which audio backend to use.\nBy default, Wine automatically picks the right one for your system."
            ),
        },
        {
            "option": "Graphics",
            "label": _("Graphics driver"),
            "type": "choice",
            "advanced": True,
            "choices": [
                (_("Auto"), "auto"),
                ("Wayland", "wayland"),
                ("X11", "x11"),
            ],
            "default": "auto",
            "warning": _get_wine_wayland_warning,
            "help": _(
                "Which graphics backend to use.\nBy default, Wine automatically picks the right one for your system."
            ),
        },
        {
            "option": "overrides",
            "type": "mapping",
            "label": _("DLL overrides"),
            "help": _("Sets WINEDLLOVERRIDES when launching the game."),
        },
        {
            "option": "show_debug",
            "label": _("Output debugging info"),
            "type": "choice",
            "choices": [
                (_("Disabled"), "-all"),
                (_("Enabled"), ""),
                (_("Inherit from environment"), "inherit"),
                (_("Show FPS"), "+fps"),
                (_("Full (CAUTION: Will cause MASSIVE slowdown)"), "+all"),
            ],
            "default": "-all",
            "help": _("Output debugging information in the game log (might affect performance)"),
        },
        {
            "option": "ShowCrashDialog",
            "label": _("Show crash dialogs"),
            "type": "bool",
            "default": False,
            "advanced": True,
        },
        {
            "option": "autoconf_joypad",
            "type": "bool",
            "label": _("Autoconfigure joypads"),
            "advanced": True,
            "default": False,
            "help": _("Automatically disables one of Wine's detected joypad to avoid having 2 controllers detected"),
        },
    ]

    reg_prefix = "HKEY_CURRENT_USER/Software/Wine"
    reg_keys = {
        "Audio": r"%s/Drivers" % reg_prefix,
        "Graphics": r"%s/Drivers" % reg_prefix,
        "MouseWarpOverride": r"%s/DirectInput" % reg_prefix,
        "ShowCrashDialog": "MANAGED",
    }

    core_processes = (
        "services.exe",
        "winedevice.exe",
        "plugplay.exe",
        "explorer.exe",
        "rpcss.exe",
        "rundll32.exe",
        "wineboot.exe",
    )

    def __init__(self, config=None, prefix=None, working_dir=None):
        super().__init__(config)
        self._prefix = prefix
        self._working_dir = working_dir
        self.dll_overrides = DEFAULT_DLL_OVERRIDES.copy()  # we'll modify this, so we better copy it

    @property
    def context_menu_entries(self):
        """Return the contexual menu entries for wine"""
        return [
            ("wineexec", _("Run EXE inside Wine prefix"), self.run_wineexec),
            ("wineshell", _("Open Bash terminal"), self.run_wine_terminal),
            ("wineconsole", _("Open Wine console"), self.run_wineconsole),
            (None, "-", None),
            ("winecfg", _("Wine configuration"), self.run_winecfg),
            ("wine-regedit", _("Wine registry"), self.run_regedit),
            ("winecpl", _("Wine Control Panel"), self.run_winecpl),
            ("winetaskmgr", _("Wine Task Manager"), self.run_taskmgr),
            (None, "-", None),
            ("winetricks", _("Winetricks"), self.run_winetricks),
        ]

    @property
    def prefix_path(self):
        """Return the absolute path of the Wine prefix. Falls back to default WINE prefix."""
        _prefix_path = self._prefix or self.game_config.get("prefix") or os.environ.get("WINEPREFIX")
        if not _prefix_path and self.game_config.get("exe"):
            # Find prefix from game if we have one
            _prefix_path = find_prefix(self.game_exe)
        if _prefix_path:
            _prefix_path = os.path.expanduser(_prefix_path)  # just in case!
        return _prefix_path

    @property
    def game_exe(self):
        """Return the game's executable's path, which may not exist. None
        if there is no exe path defined."""
        exe = self.game_config.get("exe")
        if not exe:
            logger.error("The game doesn't have an executable")
            return None
        exe = os.path.expanduser(exe)  # just in case!
        if os.path.isabs(exe):
            return system.fix_path_case(exe)
        if not self.game_path:
            logger.warning("The game has an executable, but not a game path")
            return None
        return system.fix_path_case(os.path.join(self.game_path, exe))

    @property
    def has_working_dir(self) -> bool:
        return bool(self._get_explicit_working_dir() or super().has_working_dir)

    @property
    def working_dir(self):
        """Return the working directory to use when running the game."""
        return self._get_explicit_working_dir() or super().working_dir

    def _get_explicit_working_dir(self):
        _working_dir = self._working_dir or self.game_config.get("working_dir")
        if _working_dir:
            return os.path.expanduser(_working_dir)
        if self.game_exe:
            game_dir = os.path.dirname(self.game_exe)
            if os.path.isdir(game_dir):
                return game_dir
        return None

    @property
    def wine_arch(self) -> str:
        """Return the prefix architecture, which is always 64-bit with Proton."""
        return WINE_DEFAULT_ARCH

    def read_version_from_config(self) -> str:
        """Return the Wine version to use: the game's, else the runner's, else the
        global Wine default."""

        # We must use the config levels to avoid getting a default if the setting
        # is not set; we'll fall back to get_default_version()

        for level in [self.config.game_level, self.config.runner_level]:
            if "wine" in level:
                runner_version = level["wine"].get("version")
                if runner_version:
                    return runner_version

        return get_default_wine_version()

    def resolve_config_path(self, path, relative_to=None):
        # Resolve paths with tolerance for Windows-isms;
        # first try to fix mismatched casing, and then if that
        # finds no file or directory, try again after swapping in
        # slashes for backslashes.

        resolved = super().resolve_config_path(path, relative_to)
        resolved = system.fix_path_case(resolved)

        if not os.path.exists(resolved) and "\\" in path:
            fixed = path.replace("\\", "/")
            fixed_resolved = super().resolve_config_path(fixed, relative_to)
            fixed_resolved = system.fix_path_case(fixed_resolved)
            return fixed_resolved

        return resolved

    def get_launch_config_exe(self, launch_config):
        """Resolve launch config exe relative to game_path, like game_exe does."""
        exe = launch_config.get("exe")
        if not exe:
            return None
        exe = os.path.expanduser(exe)
        if os.path.isabs(exe):
            return system.fix_path_case(exe)
        if self.game_path:
            return system.fix_path_case(os.path.join(self.game_path, exe))
        return exe

    def get_executable(self, version: str = "", fallback: bool = True) -> str:
        """Return the wine executable of the Proton build that runs games, which umu
        runs. A version that names no Proton build, such as an old Wine build, falls
        back to GE-Proton Latest unless 'fallback' is off."""
        if not version:
            version = self.read_version_from_config()
        if not fallback or proton.is_proton_version(version) or proton_releases.get_rolling(version):
            return get_wine_path_for_version(version)

        # Proton upgrades a Wine prefix in place, and that can't be undone.
        if proton.is_wine_only_prefix(self.prefix_path):
            raise MissingExecutableError(
                _(
                    "Wine version '%s' isn't a Proton build. Qtris won't switch this game's Wine prefix to Proton "
                    "automatically; choose a Proton version for this game."
                )
                % version
            )
        return get_wine_path_for_version(GE_PROTON_LATEST)

    def get_command(self) -> list[str]:
        command = super().get_command()
        if command:
            if proton.is_proton_path(command[0]) and not proton.is_umu_path(command[0]):
                command[0] = proton.get_umu_path()

            if _is_32_bit_prefix(self.prefix_path):
                raise RuntimeError(_("Proton is not compatible with 32-bit prefixes."))

        return command

    def is_installed(self, flatpak_allowed: bool = True, version: str | None = None, fallback: bool = True) -> bool:
        """Check if a Proton version is installed. Without a version, this checks the
        configured one."""
        try:
            if version:
                # We don't care where Wine is, but only if it was found at all.
                self.get_executable(version, fallback)
                return True

            # A version that names no Proton build runs with GE-Proton Latest
            return not is_missing_proton_release(get_proton_version_or_default(self.read_version_from_config()))
        except MisconfigurationError:
            return False

    def install_dialog(self, ui_delegate: "InstallUIDelegate") -> bool:
        """Download the game's Proton build on demand."""
        self.install(ui_delegate)
        return self.is_installed()

    def is_installed_for(self, interpreter):
        try:
            version = self.get_installer_runner_version(interpreter.installer, use_default=True)
        except MisconfigurationError:
            return False
        # Installed Proton builds are ready; a rolling build or a pinned release that
        # isn't installed yet needs installing.
        return is_umu_managed_version(version) and not is_missing_proton_release(version)

    def install(
        self,
        install_ui_delegate: "InstallUIDelegate",
        version: str | None = None,
        callback: Callable[[], None] | None = None,
    ) -> None:
        version = version or get_proton_version_or_default(self.read_version_from_config())
        if rolling := proton_releases.get_rolling(version):
            self.install_rolling_proton(install_ui_delegate, rolling, callback=callback)
            return
        if version and is_missing_proton_release(version):
            self.install_proton_release(install_ui_delegate, version, callback=callback)
            return
        if is_umu_managed_version(version):
            if callback:
                callback()
            return
        raise NonInstallableRunnerError(_("Wine version '%s' isn't a Proton build. Choose a Proton version.") % version)

    def install_rolling_proton(
        self,
        install_ui_delegate: "InstallUIDelegate",
        rolling: proton_releases.RollingProton,
        callback: Callable[[], None] | None = None,
    ) -> None:
        """Install a rolling Proton build, or bring it up to the newest release."""
        try:
            rolling_proton.update(rolling, install_ui_delegate.download_install_file)
        except rolling_proton.RollingProtonError as ex:
            raise RunnerInstallationError(ex.message) from ex
        clear_wine_version_cache()
        if callback:
            callback()

    def install_proton_release(
        self, install_ui_delegate: "InstallUIDelegate", version: str, callback: Callable[[], None] | None = None
    ) -> None:
        """Download a Proton release from its upstream repository into Qtris's Proton folder."""
        try:
            urls = proton_releases.get_proton_release_urls(version)
        except (HTTPError, ValueError) as ex:
            raise RunnerInstallationError(_("Failed to retrieve %s information: %s") % (version, ex)) from ex
        if not urls:
            raise RunnerInstallationError(_("%s has no build for the %s architecture.") % (version, LINUX_SYSTEM.arch))
        archive_url, checksum_url = urls
        try:
            expected_checksum = proton_releases.get_proton_checksum(checksum_url)
        except HTTPError as ex:
            raise RunnerInstallationError(_("Failed to retrieve the checksum of %s: %s") % (version, ex)) from ex
        if not expected_checksum:
            raise RunnerInstallationError(_("The %s download is corrupt; its checksum does not match.") % version)

        filename = os.path.basename(archive_url)
        os.makedirs(settings.CACHE_DIR, exist_ok=True)
        proton_dir = proton.get_proton_dir()
        with TemporaryDirectory(dir=settings.CACHE_DIR) as download_dir:
            archive = os.path.join(download_dir, filename)
            logger.info("Downloading %s from %s", version, archive_url)
            if not install_ui_delegate.download_install_file(archive_url, archive):
                logger.info("Download canceled by the user.")
                return
            if system.get_file_checksum(archive, "sha512") != expected_checksum:
                raise RunnerInstallationError(_("The %s download is corrupt; its checksum does not match.") % version)
            logger.info("Installing %s in %s", version, proton_dir)
            executable = f"{proton_releases.get_build_name(filename)}/proton"
            self.extract(archive, proton_dir, merge_single=False, executable=executable)
        clear_wine_version_cache()
        if callback:
            callback()

    def get_installer_runner_version(
        self, installer, use_runner_config: bool = True, use_default: bool = False
    ) -> str | None:
        # If a version is specified in the script choose this one
        version = None
        if installer.script.get(installer.runner):
            version = installer.script[installer.runner].get("version")
            if version:
                version = get_proton_version_or_default(version)
        # If the installer is an extension, use the wine version from the base game
        elif installer.requires:
            db_game = installer.required_game or get_game_by_field(installer.requires, field="installer_slug")
            if not db_game:
                db_game = get_game_by_field(installer.requires, field="slug")
            if not db_game:
                raise MisconfigurationError(_("The required game '%s' could not be found.") % installer.requires)
            game = Game(db_game["id"])
            if game.config:
                version = game.config.runner_config["version"]
            else:
                version = None

        if not version and use_runner_config:
            # Try to read the version from the saved runner config for Wine.
            try:
                return get_proton_version_or_default(wine.get_runner_version_and_config()[0])
            except UnspecifiedVersionError:
                pass  # fall back to the API in this case

        if not version and use_default:
            # Fall back to Qtris' own default (GE-Proton Latest), not the API's
            # latest Wine build: the API has no notion of a default version, so asking it
            # would download whichever build was uploaded last, and nothing would use it.
            version = get_default_wine_version()
            logger.debug("No Wine version specified, using the default: %s", version)

        return version

    def adjust_installer_runner_config(self, installer_runner_config: dict[str, Any]) -> None:
        version = installer_runner_config.get("version")
        if version:
            installer_runner_config["version"] = get_proton_version_or_default(version)

    @classmethod
    def get_runner_version_and_config(cls) -> tuple[str, QtrisConfig]:
        runner_config = QtrisConfig(runner_slug="wine")
        if "wine" in runner_config.runner_level:
            config_version = runner_config.runner_level["wine"].get("version")
            if config_version:
                return config_version, runner_config

        raise UnspecifiedVersionError(_("The runner configuration does not specify a Wine version."))

    @classmethod
    def msi_exec(
        cls,
        msi_file,
        quiet=False,
        prefix=None,
        wine_path=None,
        working_dir=None,
        blocking=False,
    ):
        msi_args = "/i %s" % msi_file
        if quiet:
            msi_args += " /q"
        return wineexec(
            "msiexec",
            args=msi_args,
            prefix=prefix,
            wine_path=wine_path,
            working_dir=working_dir,
            blocking=blocking,
        )

    def _run_executable(self, executable):
        """Runs a Windows executable using this game's configuration"""
        wineexec(
            executable,
            wine_path=self.get_executable(),
            prefix=self.prefix_path,
            working_dir=self.prefix_path,
            env=self.get_env(os_env=True),
            runner=self,
        )

    def run_wineexec(self, ui_delegate: "Delegate", *args):
        """Ask the user for an arbitrary exe file to run in the game's prefix"""
        filename = ui_delegate.ask_for_file(_("Select an EXE or MSI file"), default_path=self.game_path)
        if not filename:
            return
        self.prelaunch()
        self._run_executable(filename)

    def run_wineconsole(self, *args):
        """Runs wineconsole inside wine prefix."""
        self.prelaunch()
        self._run_executable("wineconsole")

    def run_winecfg(self, *args):
        """Run winecfg in the current context"""
        self.prelaunch()
        winecfg(
            wine_path=self.get_executable(),
            prefix=self.prefix_path,
            arch=self.wine_arch,
            env=self.get_env(os_env=True),
            runner=self,
        )

    def run_regedit(self, *args) -> None:
        """Run regedit in the current context"""
        self.prelaunch()
        self._run_executable("regedit")

    def run_wine_terminal(self, *args) -> None:
        terminal = self.system_config.get("terminal_app")
        system_winetricks: bool = self.runner_config.get("system_winetricks", False)
        open_wine_terminal(
            terminal=terminal,
            wine_path=self.get_executable(),
            prefix=self.prefix_path,
            env=self.get_env(os_env=True),
            system_winetricks=system_winetricks,
        )

    def run_winetricks(self, *args):
        """Run winetricks in the current context"""
        self.prelaunch()
        system_winetricks = self.runner_config.get("system_winetricks")
        winetricks(
            "",
            prefix=self.prefix_path,
            wine_path=self.get_executable(),
            system_winetricks=system_winetricks,
            env=self.get_env(os_env=True),
            runner=self,
        )

    def run_winecpl(self, *args):
        """Execute Wine control panel."""
        self.prelaunch()
        self._run_executable("control")

    def run_taskmgr(self, *args):
        """Execute Wine task manager"""
        self.prelaunch()
        self._run_executable("taskmgr")

    def run_winekill(self, *args):
        """Runs wineserver -k."""

        winekill(
            self.prefix_path,
            arch=self.wine_arch,
            wine_path=self.get_executable(),
            env=self.get_env(),
            initial_pids=self.get_wine_executable_pids(),
        )
        return True

    def set_regedit_keys(self):
        """Reset regedit keys according to config."""
        prefix_manager = WinePrefixManager(self.prefix_path)
        # Those options are directly changed with the prefix manager and skip
        # any calls to regedit.
        managed_keys = {
            "ShowCrashDialog": prefix_manager.set_crash_dialogs,
        }
        for key, path in self.reg_keys.items():
            # An option the user can't even see, like the virtual desktop in a Proton
            # configuration, must not do anything - not even clear the keys it would
            # write, since the prefix may have its own settings there.
            if not is_option_visible(self.runner_options, key, self.config):
                logger.debug("Not applying '%s'; it is hidden for this configuration", key)
                continue
            value = self.runner_config.get(key) or "auto"
            if not value or (value == "auto" and key not in managed_keys):
                prefix_manager.clear_registry_subkeys(path, [key])
            elif key in self.runner_config:
                if key == "Graphics" and value == "wayland":
                    # Proton turns on its Wayland driver with PROTON_ENABLE_WAYLAND instead
                    continue

                if key in managed_keys:
                    # Do not pass fallback 'auto' value to managed keys
                    if value == "auto":
                        value = None
                    managed_keys[key](value)
                    continue
                # Convert numeric strings to integers so they are saved as dword
                if value.isdigit():
                    value = int(value)

                prefix_manager.set_registry_key(path, key, value)

        # We always configure the DPI, because if the user turns off DPI scaling, but it
        # had been on the only way to implement that is to save 96 DPI into the registry.
        prefix_manager.set_dpi(self.get_dpi())

    def get_dpi(self) -> int | None:
        """Return the DPI to be used by Wine; returns None to allow Wine's own
        setting to govern."""
        if not self.runner_config.get("Dpi"):
            return None
        try:
            return int(self.runner_config.get("ExplicitDpi", get_default_dpi()))
        except TypeError, ValueError:
            # Older configs may say 'auto', or be blank
            return get_default_dpi()

    def attach_log_handlers(self, monitored_command: "MonitoredCommand", game: Game) -> None:
        """Install a umu launch-status parser when the wine executable is
        actually umu — umu's runtime/Proton setup can take a long time on the
        first launch and there's no other indication of progress."""
        try:
            wine_exe = self.get_executable()
        except MissingExecutableError:
            return
        if proton.is_umu_path(wine_exe):
            monitored_command.log_handlers.append(proton.UmuLaunchStatusParser(game))

    def prelaunch(self):
        prefix_path = self.prefix_path
        if prefix_path:
            if (exe := self.game_config.get("exe")) and os.path.isabs(exe):
                repair_ea_launcher(Path(prefix_path), Path(exe))
            if os.path.islink(prefix_path) and not os.path.exists(prefix_path):
                raise SymlinkNotUsableError(
                    message=f"Link {prefix_path} couldn't be used, please check permissions or broken link.",
                    link=prefix_path,
                )
            if not system.path_exists(os.path.join(prefix_path, "user.reg")):
                logger.warning("No valid prefix detected in %s, creating one...", prefix_path)
                create_prefix(prefix_path, wine_path=self.get_executable(), arch=self.wine_arch, runner=self)

            prefix_manager = WinePrefixManager(prefix_path)
            prefix_manager.cleanup_broken_symlinks()
            if self.runner_config.get("autoconf_joypad", False):
                prefix_manager.configure_joypads()
            prefix_manager.create_user_symlinks()
            self.configure_desktop_integration(prefix_manager)
            self.set_regedit_keys()

        client_exe = self.game_config.get("client_exe")
        if client_exe:
            self._ensure_client_running(client_exe)

    def _ensure_client_running(self, client_exe, wait_time=15):
        """Launch a client application (e.g. Battle.net) and wait for it to be ready.
        Many game clients need to be fully initialized before they can accept
        commands like --exec to launch a specific game."""
        import time

        if not os.path.isabs(client_exe):
            client_exe = os.path.join(self.prefix_path, client_exe)

        if not os.path.exists(client_exe):
            logger.warning("Client executable not found: %s", client_exe)
            return

        exe_name = os.path.basename(client_exe)

        # Search for the full path so we match the umu-run/pressure-vessel
        # wrapper processes, which use Unix paths.  This also implicitly
        # scopes the check to the current prefix.
        if system.is_process_running(client_exe):
            logger.info("Client %s is already running", exe_name)
            return

        logger.info("Launching client %s", exe_name)
        wineexec(
            client_exe,
            prefix=self.prefix_path,
            wine_path=self.get_executable(),
            arch=self.wine_arch,
        )
        logger.info("Waiting %d seconds for client to be ready", wait_time)
        time.sleep(wait_time)

    def get_dll_overrides(self):
        """Return the DLLs overriden at runtime"""
        try:
            overrides = self.runner_config["overrides"]
        except KeyError:
            overrides = {}
        if not isinstance(overrides, dict):
            logger.warning("DLL overrides is not a mapping: %s", overrides)
            overrides = {}
        return overrides

    def get_env(self, os_env=False):
        """Return environment variables used by the game"""
        # Always false to runner.get_env, the default value
        # of os_env is inverted in the wine class,
        # the OS env is read later.
        env = super().get_env(os_env)
        show_debug = self.runner_config.get("show_debug", "-all")
        if show_debug != "inherit":
            env["WINEDEBUG"] = show_debug
            env["DXVK_LOG_LEVEL"] = "error"
            env["UMU_LOG"] = "1"
            if show_debug == "":
                env["DXVK_LOG_LEVEL"] = "info"
                env["UMU_LOG"] = "warning"
            elif show_debug == "+all":
                env["DXVK_LOG_LEVEL"] = "debug"
                env["UMU_LOG"] = "debug"
        env["WINEARCH"] = self.wine_arch
        wine_exe = self.get_executable()

        wine_config_version = self.read_version_from_config()
        env["WINE"] = wine_exe

        files_dir = get_runner_files_dir_for_version(wine_config_version)
        if files_dir:
            env["WINE_MONO_CACHE_DIR"] = os.path.join(files_dir, "mono")
            env["WINE_GECKO_CACHE_DIR"] = os.path.join(files_dir, "gecko")

        if self.prefix_path:
            env["WINEPREFIX"] = self.prefix_path

        if self.runner_config.get("fsr"):
            env["WINE_FULLSCREEN_FSR"] = "1"

        anti_cheat_options = {"PROTON_BATTLEYE_RUNTIME": "battleye", "PROTON_EAC_RUNTIME": "eac"}
        if any(self.runner_config.get(option) for option in anti_cheat_options.values()):
            for variable, path in proton.get_anti_cheat_runtimes().items():
                if self.runner_config.get(anti_cheat_options[variable]):
                    env[variable] = path

        # Proton brings its own DXVK and VKD3D, which need Vulkan; without it, WineD3D
        has_vulkan = LINUX_SYSTEM.is_vulkan_supported()
        if not has_vulkan:
            env["PROTON_USE_WINED3D"] = "1"

        if "PROTON_DXVK_D3D8" not in env:
            env["PROTON_DXVK_D3D8"] = "1" if has_vulkan else "0"

        if has_vulkan and "PROTON_DXVK_SAREK" not in env and not os.environ.get("QTRIS_NO_VKQUERY"):
            lib_api_version = vkquery.get_vulkan_api_version()
            devices = vkquery.get_device_info()
            if (lib_api_version and lib_api_version < vkquery.REQUIRED_VULKAN_API_VERSION) or (
                devices and devices[0].api_version < vkquery.REQUIRED_VULKAN_API_VERSION
            ):
                env["PROTON_DXVK_SAREK"] = "1"

        if self.runner_config.get("Graphics") == "wayland" and is_winewayland_available(wine_config_version):
            env["PROTON_ENABLE_WAYLAND"] = "1"

            if self.runner_config.get("proton_hdr"):
                env["PROTON_ENABLE_HDR"] = "1"

        overrides = self.get_dll_overrides()
        if overrides:
            self.dll_overrides.update(overrides)

        env["WINEDLLOVERRIDES"] = get_overrides_env(self.dll_overrides)

        return env

    def finish_env(self, env: dict[str, str], game: Game | None = None) -> None:
        super().finish_env(env, game)

        wine_exe = self.get_executable()
        game_id = env.get("UMU_ID") or proton.DEFAULT_GAMEID
        proton.update_proton_env(wine_exe, env, game_id=game_id)

    def get_run_data(self) -> RunDataDict:
        # The standalone run() path does not call finish_env() the way launching a
        # game does, so finalize the environment here too. Without a game context,
        # Proton/umu falls back to a default GAMEID.
        data = super().get_run_data()
        self.finish_env(data["env"])
        return data

    def get_wine_executable_pids(self) -> set[int]:
        """Return the pids of processes using the current Wine. Proton runs games
        inside umu's container, so they can't be tracked this way."""
        logger.debug("Tracking PIDs of Proton games is not possible at the moment")
        return set()

    def configure_desktop_integration(self, wine_prefix):
        try:
            if self.game_config.get("desktop_integration", False):
                wine_prefix.install_desktop_integration()
            else:
                wine_prefix.remove_desktop_integration()
        except Exception as ex:
            logger.exception("Failed to setup desktop integration, the prefix may not be valid: %s", ex)

    def play(self) -> dict[str, Any]:  # pylint: disable=too-many-return-statements
        game_exe = self.game_exe
        arguments: str = self.game_config.get("args", "")
        launch_info: dict = {"env": self.get_env(os_env=False)}
        if LINUX_SYSTEM.is_vulkan_supported():
            # Set this to 1 to enable access to more RAM for 32-bit applications
            launch_info["env"]["WINE_LARGE_ADDRESS_AWARE"] = "1"

        if not game_exe or not system.path_exists(game_exe):
            raise MissingGameExecutableError(filename=game_exe)

        command = self.get_command()

        game_exe, args, _working_dir = get_real_executable(game_exe, self.working_dir)
        command.append(game_exe)
        if args:
            command = command + args

        if arguments:
            for arg in split_arguments(arguments):
                command.append(arg)
        launch_info["command"] = command
        return launch_info

    def filter_game_pids(self, candidate_pids: Iterable[int], game_uuid: str, game_folder: str) -> set[int]:
        """Checks the pids given and returns a set containing only those that are part of the running game,
        identified by its UUID and directory."""

        folder_pids = set()
        gamescope_pids = set()
        has_gamescope = self.system_config.get("gamescope")

        for pid in candidate_pids:
            proc = Process(pid)
            cmdline = proc.cmdline or ""
            # pressure-vessel: This could potentially pick up PIDs not started by qtris?
            if game_folder in cmdline or "pressure-vessel" in cmdline:
                folder_pids.add(pid)
            # Include gamescope-related processes when gamescope is enabled
            if has_gamescope and (proc.name or "").startswith("gamescope"):
                gamescope_pids.add(pid)

        uuid_pids = set(pid for pid in candidate_pids if Process(pid).environ.get("QTRIS_GAME_UUID") == game_uuid)

        return (folder_pids & uuid_pids) | gamescope_pids

    def force_stop_game(self, game_pids: Iterable[int]) -> None:
        """Kill WINE with kindness, or at least with -k. This seems to leave a process
        alive for some reason, but the caller will detect this and SIGKILL it."""

        winekill(
            self.prefix_path,
            arch=self.wine_arch,
            wine_path=self.get_executable(),
            env=self.get_env(),
            initial_pids=game_pids,
        )

        # Kill non-Wine processes (like gamescope) that winekill doesn't handle
        super().force_stop_game(game_pids)

    def extract_icon(self, game_slug):
        """Extracts the 128*128 icon from EXE and saves it, if not resizes the biggest icon found.
        returns true if an icon is saved, false if not"""

        try:
            wantedsize = (128, 128)
            pathtoicon = settings.ICON_PATH + "/qtris_" + game_slug + ".png"
            exe = self.game_exe
            if not exe or os.path.exists(pathtoicon) or not PEFILE_AVAILABLE:
                return False

            extractor = IconExtractor(exe)

            icon = extractor.get_best_icon()

            if not icon.size == wantedsize:
                icon = icon.resize(wantedsize)
            icon.save(pathtoicon)
            return True
        except Exception as ex:
            logger.exception("Unable to extract icon from %s: %s", exe, ex)
            return False
