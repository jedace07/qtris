"""Steam for Linux runner"""

import os
import signal
import time
from collections.abc import Iterable
from gettext import gettext as _
from typing import Optional

from qtris.config import QtrisConfig
from qtris.exceptions import UnavailableRunnerError
from qtris.monitored_command import MonitoredCommand
from qtris.runners import NonInstallableRunnerError
from qtris.runners.runner import Runner, RunnerOptionDict
from qtris.util import host_processes, linux, system
from qtris.util.log import logger
from qtris.util.steam.appinfo import get_app_oslist
from qtris.util.steam.appmanifest import get_appmanifest_from_appid, get_path_from_appmanifest
from qtris.util.steam.config import get_compat_tools, get_default_acf, get_steam_dir, get_steamapps_dirs
from qtris.util.steam.vdfutils import to_vdf
from qtris.util.strings import split_arguments

# Time (seconds) to keep monitoring after the launcher exits while waiting for the
# game's processes to appear, before concluding the launch failed. Covers slow
# hand-off, shader pre-caching and first-launch prerequisites.
LAUNCH_GRACE_SECONDS = 120

# How long a game gets to quit after SIGTERM before it's killed
STOP_TIMEOUT_SECONDS = 5.0


def get_steam_game_pids(appid: str) -> set[int]:
    """Return the PIDs of a running Steam game's processes, wherever they run.

    Steam gives every process of a game its appid in SteamAppId and SteamGameId,
    and they keep it through Proton, the Steam Linux Runtime's containers and the
    new sandboxes the Steam Flatpak starts through the Flatpak portal, where the
    game is no longer under the process that Steam launched it with. The Steam
    client itself doesn't have them. Steam runs on the host, so from the Flatpak
    sandbox this asks the host."""
    if not appid:
        return set()
    return host_processes.find_by_environment([f"SteamAppId={appid}", f"SteamGameId={appid}"])


def get_steam_game_platform(appid: str) -> str:
    """Return the platform of the build Steam runs of a game: Windows when Steam runs it
    through Proton, because the user chose a Proton build for it or it has no Linux
    build, and Linux when it runs natively. Empty if Steam doesn't know the game."""
    if not appid:
        return ""
    # Native games can be set to run in the Steam Linux Runtime too, which they do natively
    tool = get_compat_tools().get(str(appid), "")
    if tool and not tool.casefold().startswith("steamlinuxruntime"):
        return _("Windows")
    oslist = get_app_oslist(appid)
    if "linux" in oslist:
        return _("Linux")
    if "windows" in oslist:
        return _("Windows")
    return ""


class steam(Runner):
    """Runs the Steam source's games through Steam. It isn't offered as a runner, and
    has no options of its own: Steam's launch options are set in Steam."""

    description = _("Runs Steam for Linux games")
    human_name = _("Steam")
    platform_dict = Runner.to_platform_dict([_("Linux"), _("Windows")])
    system_executable = "steam"
    flatpak_id = "com.valvesoftware.Steam"
    game_options = [
        {
            "option": "appid",
            "label": _("Application ID"),
            "type": "string",
            "help": _(
                "The application ID can be retrieved from the game's "
                "page at steampowered.com. Example: 235320 is the "
                "app ID for <i>Original War</i> in: \n"
                "http://store.steampowered.com/app/<b>235320</b>/"
            ),
        },
        {
            "option": "args",
            "type": "string",
            "label": _("Arguments"),
            "help": _("Command line arguments used when launching the game."),
        },
    ]

    def __init__(self, config: QtrisConfig | None = None) -> None:
        super().__init__(config)
        # Launch-window tracking for keep_game_alive()
        self._launch_monitor_start: Optional[float] = None
        self._steam_game_seen: bool = False

    def get_runner_options(self) -> list[RunnerOptionDict]:
        return []

    @property
    def runnable_alone(self):
        return not linux.LINUX_SYSTEM.is_flatpak()

    @property
    def appid(self):
        return self.game_config.get("appid") or ""

    def get_platform(self) -> str:
        return get_steam_game_platform(self.appid)

    @property
    def game_path(self):
        if not self.appid:
            return None
        return self.get_game_path_from_appid(self.appid)

    @property
    def steam_data_dir(self):
        """Main installation directory for Steam"""
        return get_steam_dir()

    def get_appmanifest(self):
        """Return an AppManifest instance for the game"""
        appmanifests = []
        for apps_path in get_steamapps_dirs():
            appmanifest = get_appmanifest_from_appid(apps_path, self.appid)
            if appmanifest:
                appmanifests.append(appmanifest)
        if len(appmanifests) > 1:
            logger.warning("More than one AppManifest for %s returning only 1st", self.appid)
        if appmanifests:
            return appmanifests[0]

    def get_executable(self) -> str:
        if linux.LINUX_SYSTEM.is_flatpak():
            # Fallback to xgd-open for Steam URIs in Flatpak
            return system.find_required_executable("xdg-open")
        return super().get_executable()

    def get_game_path_from_appid(self, appid):
        """Return the game directory."""
        for apps_path in get_steamapps_dirs():
            game_path = get_path_from_appmanifest(apps_path, appid)
            if game_path:
                return game_path
        logger.info("Data path for SteamApp %s not found.", appid)
        return ""

    def get_default_steamapps_path(self):
        steamapps_paths = get_steamapps_dirs()
        if steamapps_paths:
            return steamapps_paths[0]
        return ""

    @property
    def can_install(self) -> bool:
        return False

    def install(self, install_ui_delegate, version=None, callback=None):
        raise NonInstallableRunnerError(
            message=_("Steam for Linux installation is not handled by Qtris."),
            message_markup=_(
                "Steam for Linux installation is not handled by Qtris.\n"
                "Please go to "
                "<a href='http://steampowered.com'>http://steampowered.com</a>"
                " or install Steam with the package provided by your distribution."
            ),
        )

    def install_game(self, appid, generate_acf=False):
        logger.debug("Installing steam game %s", appid)
        if generate_acf:
            acf_data = get_default_acf(appid, appid)
            acf_content = to_vdf(acf_data)
            steamapps_path = self.get_default_steamapps_path()
            if not steamapps_path:
                raise UnavailableRunnerError(_("Could not find Steam path, is Steam installed?"))
            acf_path = os.path.join(steamapps_path, "appmanifest_%s.acf" % appid)
            with open(acf_path, "w", encoding="utf-8") as acf_file:
                acf_file.write(acf_content)
        system.spawn(self.get_command() + [f"steam://install/{appid}"])

    def get_run_data(self):
        return {"command": self.get_command(), "env": self.get_env()}

    def play(self):
        # Reset launch-window tracking for this run (the runner instance may be
        # reused across launches); see keep_game_alive().
        self._launch_monitor_start = None
        self._steam_game_seen = False
        game_args = self.game_config.get("args") or ""
        command = self.get_command()
        if linux.LINUX_SYSTEM.is_flatpak():
            # Steam is reached through xdg-open, which takes a single URI
            if game_args:
                command.append("steam://run/%s//%s/" % (self.appid, game_args))
            else:
                command.append("steam://rungameid/%s" % self.appid)
        elif game_args:
            command += ["-applaunch", self.appid, *split_arguments(game_args)]
        else:
            command.append("steam://rungameid/%s" % self.appid)
        return {"command": command, "env": self.get_env()}

    def force_stop_game(self, game_pids: Iterable[int]) -> None:
        """Stop a running Steam game without harming the Steam client: send SIGTERM
        to every process of the game, and SIGKILL to those left after
        STOP_TIMEOUT_SECONDS. Blocks until the game is gone."""
        super().force_stop_game(game_pids)
        if not self.appid:
            return
        pids = get_steam_game_pids(self.appid)
        if not pids:
            return
        logger.info("Stopping Steam game %s", self.appid)
        host_processes.send_signal(signal.SIGTERM, pids)
        deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.5)
            if not (pids := get_steam_game_pids(self.appid)):
                return
        logger.warning("Steam game %s didn't quit; killing it", self.appid)
        host_processes.send_signal(signal.SIGKILL, pids)

    def keep_game_alive(self, game_pids: Iterable[int], game_thread_running: bool) -> bool:
        """Keep beat() monitoring a Steam game across the out-of-process launch.

        Steam launches the game through a URI, so game_thread exits within seconds,
        and the game's processes may take a while to appear (Steam starting,
        updates, shader pre-caching). The game is alive while game_thread runs or
        any of its processes do. Before any has appeared, it's given
        LAUNCH_GRACE_SECONDS; once its processes have been seen and are gone, it
        has quit.
        """
        if not self.appid:
            return False

        running = bool(get_steam_game_pids(self.appid))
        if game_thread_running or running:
            self._steam_game_seen = self._steam_game_seen or running
            self._launch_monitor_start = self._launch_monitor_start or time.monotonic()
            return True
        if self._steam_game_seen:
            return False
        if self._launch_monitor_start is None:
            self._launch_monitor_start = time.monotonic()
        return (time.monotonic() - self._launch_monitor_start) < LAUNCH_GRACE_SECONDS

    def remove_game_data(self, app_id=None, **kwargs):
        if not self.is_installed():
            return False
        app_id = app_id or self.appid
        command = MonitoredCommand(
            self.get_command() + [f"steam://uninstall/{app_id}"],
            runner=self,
            env=self.get_env(),
        )
        command.start()
