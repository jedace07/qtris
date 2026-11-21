"""Steam for Linux runner"""

import os
import re
import signal
import time
from collections.abc import Iterable
from gettext import gettext as _
from typing import Optional

from qtris.config import QtrisConfig
from qtris.exceptions import MissingGameExecutableError, UnavailableRunnerError
from qtris.monitored_command import MonitoredCommand
from qtris.runners import NonInstallableRunnerError
from qtris.runners.runner import Runner
from qtris.util import host_processes, linux, system
from qtris.util.log import logger
from qtris.util.steam.appmanifest import get_appmanifest_from_appid, get_path_from_appmanifest
from qtris.util.steam.config import get_default_acf, get_steam_dir, get_steamapps_dirs
from qtris.util.steam.vdfutils import to_vdf
from qtris.util.strings import split_arguments

# Default time (seconds) to keep monitoring after the launcher exits while
# waiting for Steam's per-game reaper to appear, before concluding the launch
# failed. Covers slow hand-off, shader pre-caching and first-launch
# prerequisites. Overridable per-game via the "launch_grace_seconds" option.
DEFAULT_LAUNCH_GRACE_SECONDS = 120

# How long a game gets to quit after SIGTERM before it's killed
STOP_TIMEOUT_SECONDS = 5.0


def is_reaper_for(cmdline: str, appid: str) -> bool:
    """True if a command line is Steam's reaper for this appid.

    When Steam launches a game it starts its reaper program, ".../reaper SteamLaunch
    AppId=<appid> -- ...", which exists for the game's whole lifetime (through shader
    pre-caching and play) and exits when the game does, for native, Proton and
    Flatpak Steam alike. Only the reaper itself matches, not a process that merely
    mentions it, and 440 doesn't match a reaper for 4400."""
    return re.match(r"(\S*/)?reaper SteamLaunch AppId=%s( |$)" % re.escape(appid), cmdline) is not None


def find_reapers(appid: str, processes: Iterable[host_processes.HostProcess]) -> list[int]:
    """Return the PIDs of the reapers Steam started for a game."""
    if not appid:
        return []
    return [process.pid for process in processes if is_reaper_for(process.cmdline, appid)]


def get_steam_game_pids(appid: str) -> list[int]:
    """Return the PIDs of the reapers of a running Steam game. Steam runs on the
    host, so from the Flatpak sandbox this asks the host."""
    if not appid:
        return []
    return find_reapers(appid, host_processes.list_processes())


class steam(Runner):
    description = _("Runs Steam for Linux games")
    human_name = _("Steam")
    platform_dict = Runner.to_platform_dict([_("Linux")])
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
            "help": _(
                "Command line arguments used when launching the game.\nIgnored when Steam Big Picture mode is enabled."
            ),
        },
        {
            "option": "run_without_steam",
            "label": _("DRM free mode (Do not launch Steam)"),
            "type": "bool",
            "default": False,
            "advanced": True,
            "help": _("Run the game directly without Steam, requires the game binary path to be set"),
        },
        {
            "option": "steamless_binary",
            "type": "file",
            "label": _("Game binary path"),
            "advanced": True,
            "help": _("Path to the game executable (Required by DRM free mode)"),
        },
    ]
    runner_options = [
        {
            "option": "start_in_big_picture",
            "label": _("Start Steam in Big Picture mode"),
            "type": "bool",
            "default": False,
            "help": _(
                "Launches Steam in Big Picture mode.\n"
                "Only works if Steam is not running or "
                "already running in Big Picture mode.\n"
                "Useful when playing with a Steam Controller."
            ),
        },
        {
            "option": "args",
            "type": "string",
            "label": _("Arguments"),
            "advanced": True,
            "help": _("Extra command line arguments used when launching Steam"),
        },
        {
            "option": "launch_grace_seconds",
            "type": "string",
            "label": _("Launch grace period (seconds)"),
            "advanced": True,
            "help": _(
                "How long Qtris keeps monitoring after the Steam launcher exits "
                "while waiting for the game to appear, before assuming the launch "
                "failed. Covers shader pre-caching and first-launch prerequisites. "
                "Leave blank to use the default (%s seconds)."
            )
            % DEFAULT_LAUNCH_GRACE_SECONDS,
        },
    ]

    def __init__(self, config: QtrisConfig | None = None) -> None:
        super().__init__(config)
        # Launch-window tracking for keep_game_alive()
        self._launch_monitor_start: Optional[float] = None
        self._steam_reaper_seen: bool = False

    @property
    def launch_grace_seconds(self) -> float:
        """Launch grace period, from the per-game option if set and valid,
        otherwise DEFAULT_LAUNCH_GRACE_SECONDS."""
        raw = self.runner_config.get("launch_grace_seconds")
        if raw not in (None, ""):
            try:
                value = float(raw)  # type: ignore[arg-type]
            except TypeError, ValueError:
                value = None
            if value is not None and value > 0:
                return value
            logger.warning("Invalid launch_grace_seconds %r; using default", raw)
        return DEFAULT_LAUNCH_GRACE_SECONDS

    @property
    def runs_through_steam(self) -> bool:
        """False in DRM-free mode, which runs the game's binary without Steam."""
        return not (self.game_config.get("run_without_steam") and self.game_config.get("steamless_binary"))

    @property
    def runnable_alone(self):
        return not linux.LINUX_SYSTEM.is_flatpak()

    @property
    def appid(self):
        return self.game_config.get("appid") or ""

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

    @property
    def has_working_dir(self) -> bool:
        return bool(self._get_steamless_working_dir() or super().has_working_dir)

    @property
    def working_dir(self):
        """Return the working directory to use when running the game."""
        return self._get_steamless_working_dir() or super().working_dir

    def _get_steamless_working_dir(self) -> str | None:
        """Return the working directory to use when running the game."""
        if self.game_config.get("run_without_steam"):
            steamless_binary = self.game_config.get("steamless_binary")
            if steamless_binary and os.path.isfile(steamless_binary):
                return os.path.dirname(steamless_binary)
        return None

    @property
    def launch_args(self):
        """Provide launch arguments for Steam"""
        command = self.get_command()
        if self.runner_config.get("start_in_big_picture"):
            command.append("-bigpicture")
        return command + split_arguments(self.runner_config.get("args") or "")

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
        return {"command": self.launch_args, "env": self.get_env()}

    def play(self):
        # Reset launch-window tracking for this run (the runner instance may be
        # reused across launches); see keep_game_alive().
        self._launch_monitor_start = None
        self._steam_reaper_seen = False
        game_args = self.game_config.get("args") or ""

        binary_path = self.game_config.get("steamless_binary")
        if not self.runs_through_steam:
            # Start without steam
            if not system.path_exists(binary_path):
                raise MissingGameExecutableError(filename=binary_path)
            command = [binary_path]
        else:
            # Start through steam
            if linux.LINUX_SYSTEM.is_flatpak():
                if game_args:
                    steam_uri = "steam://run/%s//%s/" % (self.appid, game_args)
                else:
                    steam_uri = "steam://rungameid/%s" % self.appid
                return {
                    "command": self.launch_args + [steam_uri],
                    "env": self.get_env(),
                }
            command = self.launch_args
            if self.runner_config.get("start_in_big_picture") or not game_args:
                command.append("steam://rungameid/%s" % self.appid)
            else:
                command.append("-applaunch")
                command.append(self.appid)

        if game_args:
            for arg in split_arguments(game_args):
                command.append(arg)

        return {
            "command": command,
            "env": self.get_env(),
        }

    def force_stop_game(self, game_pids: Iterable[int]) -> None:
        """Stop a running Steam game without harming the Steam client.

        Steam's reaper doesn't pass signals on to the game it started, so stopping
        it alone would leave the game running: this stops every process under it
        instead, killing what's left after STOP_TIMEOUT_SECONDS, and the reaper
        exits with its last child. Steam runs on the host, which the Flatpak
        sandbox reaches through flatpak-spawn. Blocks until the game is gone.
        """
        super().force_stop_game(game_pids)
        if not self.appid or not self.runs_through_steam:
            return
        processes = host_processes.list_processes()
        reapers = find_reapers(self.appid, processes)
        if not reapers:
            return
        logger.info("Stopping Steam game %s", self.appid)
        host_processes.send_signal(signal.SIGTERM, host_processes.get_descendants(reapers, processes))
        deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.5)
            processes = host_processes.list_processes()
            reapers = find_reapers(self.appid, processes)
            if not reapers:
                return
        logger.warning("Steam game %s didn't quit; killing it", self.appid)
        host_processes.send_signal(signal.SIGKILL, {*reapers, *host_processes.get_descendants(reapers, processes)})

    def keep_game_alive(self, game_pids: Iterable[int], game_thread_running: bool) -> bool:
        """Keep beat() monitoring a Steam game across the out-of-process launch.

        Steam launches the game via a non-blocking URI, so game_thread exits
        within seconds. There's then a brief window before Steam's per-game
        reaper process appears. We:

        * report alive while game_thread is still running (normal early phase);
        * report alive while the reaper for this appid is present (the whole
          game lifetime, detected via the reaper PID scan);
        * during the launch window -- after game_thread exits but before the
          reaper has ever appeared -- report alive until LAUNCH_GRACE_SECONDS
          elapses, so a slow hand-off / shader pre-cache doesn't trigger a
          premature on_game_quit(). Once the reaper has been seen and then
          disappears, the game has genuinely quit and we return False.
        """
        if not self.appid or not self.runs_through_steam:
            return False

        reaper_alive = bool(get_steam_game_pids(self.appid))

        if game_thread_running or reaper_alive:
            if reaper_alive:
                self._steam_reaper_seen = True
            self._launch_monitor_start = self._launch_monitor_start or time.monotonic()
            return True

        # game_thread has exited and no reaper is visible.
        if self._steam_reaper_seen:
            # We saw the game running and it's now gone -> genuinely quit.
            return False

        # Reaper has never appeared yet: stay alive through the launch window.
        if self._launch_monitor_start is None:
            self._launch_monitor_start = time.monotonic()
        return (time.monotonic() - self._launch_monitor_start) < self.launch_grace_seconds

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
