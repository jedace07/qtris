"""Play web games in Qtris's own player, each with its own browser profile."""

from __future__ import annotations

import os
import re
import sys
from gettext import gettext as _
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from gi.repository import GLib

from qtris import settings
from qtris.exceptions import GameConfigError
from qtris.runners.runner import GamePlayInfoDict, Runner
from qtris.util import datapath, resources
from qtris.util.process import Process

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qtris.config import LaunchConfigDict

# Started as a program, since the core doesn't import the frontend
PLAYER_MODULE = "qtris.qt.web_player"

_WINDOW_SIZE = re.compile(r"([1-9]\d{1,4})[xX]([1-9]\d{1,4})")
_PERSONAL_FOLDERS = (
    GLib.UserDirectory.DIRECTORY_DESKTOP,
    GLib.UserDirectory.DIRECTORY_DOCUMENTS,
    GLib.UserDirectory.DIRECTORY_DOWNLOAD,
    GLib.UserDirectory.DIRECTORY_MUSIC,
    GLib.UserDirectory.DIRECTORY_PICTURES,
    GLib.UserDirectory.DIRECTORY_PUBLIC_SHARE,
    GLib.UserDirectory.DIRECTORY_TEMPLATES,
    GLib.UserDirectory.DIRECTORY_VIDEOS,
)


def _parse_window_size(value: object) -> str:
    if match := _WINDOW_SIZE.fullmatch(str(value).strip()):
        return f"{match[1]}x{match[2]}"
    raise GameConfigError(_("Enter the window size as width x height, such as 1280x720."))


def _is_shared_folder(folder: Path) -> bool:
    """True if serving a folder would show a game the user's other files."""
    private = [str(Path.home()), settings.CONFIG_DIR, settings.DATA_DIR]
    private += [path for kind in _PERSONAL_FOLDERS if (path := GLib.get_user_special_dir(kind))]
    return any(Path(path).resolve().is_relative_to(folder) for path in private)


class web(Runner):
    human_name = _("Web")
    description = _("Plays web games, each with its own browser profile")
    platform_dict = Runner.to_platform_dict([_("Web")])
    game_options = [
        {
            "option": "main_file",
            "type": "string",
            "label": _("Full URL or HTML file path"),
            "help": _(
                "The game's web address, or the HTML file that starts it. "
                "A local game can read the files in that file's folder, so give it a folder of its own."
            ),
        },
        {
            "option": "shared_memory",
            "type": "bool",
            "label": _("Shared memory for local games"),
            "default": False,
            "advanced": True,
            "help": _(
                "Needed by local games built with threads, such as some Godot exports. "
                "It stops them embedding most other websites."
            ),
        },
    ]
    runner_options = [
        {
            "option": "window_mode",
            "type": "choice",
            "label": _("Window mode"),
            "choices": [
                (_("Windowed"), "windowed"),
                (_("Maximized"), "maximized"),
                (_("Fullscreen"), "fullscreen"),
            ],
            "default": "windowed",
            "help": _("How the game's window opens. F11 toggles fullscreen."),
        },
        {
            "option": "window_size",
            "type": "choice_with_entry",
            "label": _("Window size"),
            "choices": ["800x600", "1024x768", "1280x720", "1600x900", "1920x1080"],
            "default": "1280x720",
            "validator": _parse_window_size,
        },
        {
            "option": "open_links",
            "type": "bool",
            "label": _("Open new tabs in the game's profile"),
            "default": False,
            "help": _(
                "Links to other sites that open a new tab go to your browser. "
                "Turn this on if the game signs in through one."
            ),
        },
        {
            "option": "user_agent",
            "type": "string",
            "label": _("Custom user agent"),
            "default": "",
            "advanced": True,
        },
    ]

    @property
    def game_path(self) -> str:
        if directory := self.game_data.get("directory"):
            return os.path.expanduser(directory)
        if self.has_explicit_config and (value := self.game_config.get("main_file")):
            try:
                address = urlsplit(str(value).strip())
            except ValueError:
                return ""
            # The base class would make a folder out of a web address
            if address.scheme == "file":
                return os.path.dirname(unquote(address.path))
            if not address.scheme:
                path = os.path.expanduser(str(value).strip())
                return os.path.dirname(os.path.join(os.path.expanduser("~"), path))
        return ""

    def get_executable(self) -> str:
        return sys.executable

    def get_command(self) -> list[str]:
        # -P keeps the game's folder, the working directory, off Python's import path
        return [self.get_executable(), "-P", "-m", PLAYER_MODULE]

    def play(self) -> GamePlayInfoDict:
        return {"command": self._get_player_command(str(self.game_config.get("main_file") or ""))}

    def get_launch_config_command(self, gameplay_info: GamePlayInfoDict, launch_config: LaunchConfigDict) -> list[str]:
        if "command" in launch_config:
            return super().get_launch_config_command(gameplay_info, launch_config)
        target = str(launch_config.get("exe") or "")
        if not target:
            return list(gameplay_info["command"])
        if "://" not in target:
            target = self.resolve_config_path(target, self.get_launch_config_working_dir(launch_config))
        return self._get_player_command(target)

    def _get_player_command(self, target: str) -> list[str]:
        url = self._get_url(target.strip())
        game_id = self.game_data.get("id")
        if not game_id:
            raise GameConfigError(_("Save the game before playing it."))
        # SQLite reuses a deleted game's id, so the time it was added is part of the name
        profile = f"{game_id}-{self.game_data.get('installed_at') or 0}"
        options = self.runner_config
        icon = resources.get_icon_path(self.game_data.get("slug") or "")
        if not os.path.isfile(icon):
            icon = os.path.join(datapath.get(), "media", "default_icon.png")
        command = [
            *self.get_command(),
            f"--profile={os.path.join(settings.DATA_DIR, 'web-profiles', profile)}",
            f"--cache={os.path.join(settings.CACHE_DIR, 'web-profiles', profile)}",
            f"--name={self.game_data.get('name') or self.human_name}",
            f"--icon={icon}",
            f"--window-mode={options.get('window_mode') or 'windowed'}",
            f"--size={_parse_window_size(options.get('window_size') or '1280x720')}",
        ]
        if options.get("open_links"):
            command.append("--links-in-game")
        if self.game_config.get("shared_memory"):
            command.append("--shared-memory")
        if user_agent := options.get("user_agent"):
            command.append(f"--user-agent={user_agent}")
        return [*command, url]

    def _get_url(self, target: str) -> str:
        """Check a game's address, and return it as an http, https or file URL."""
        if not target:
            raise GameConfigError(_("Enter a URL or HTML file path for the game."))
        invalid = _("%s is not a web address or an HTML file. Web addresses start with https:// or http://.") % target
        try:
            address = urlsplit(target)
            hostname = address.hostname
        except ValueError as error:
            raise GameConfigError(invalid) from error
        if address.scheme in ("http", "https"):
            if not hostname:
                raise GameConfigError(invalid)
            return target

        suffix = ""
        if address.scheme == "file" and hostname in (None, "localhost"):
            path = unquote(address.path)
            suffix = f"?{address.query}" if address.query else ""
            suffix += f"#{address.fragment}" if address.fragment else ""
        elif not address.scheme:
            # A file name may contain ? and #
            path = os.path.expanduser(target)
            if not os.path.isabs(path):
                path = os.path.join(os.path.expanduser(self.game_data.get("directory") or "~"), path)
        else:
            raise GameConfigError(invalid)

        file = Path(path)
        if not file.is_file():
            raise GameConfigError(_("The file %s does not exist.") % file)
        file = file.resolve()
        if _is_shared_folder(file.parent):
            raise GameConfigError(
                _(
                    "Move the game into a folder of its own. "
                    "A local web game can read the files next to it, and %s holds your other files."
                )
                % file.parent
            )
        return file.as_uri() + suffix

    def filter_game_pids(self, candidate_pids: Iterable[int], game_uuid: str, game_folder: str) -> set[int]:
        # The engine's processes don't name the game's folder, only carry its UUID
        return super().filter_game_pids(candidate_pids, game_uuid, "")

    def force_stop_game(self, game_pids: Iterable[int]) -> None:
        # Signalling the engine's own processes too would lose what the game saved
        pids = set(game_pids)
        player = " ".join(self.get_command()) + " "
        players = {pid for pid in pids if (Process(pid).cmdline or "").startswith(player)}
        super().force_stop_game(players or pids)
