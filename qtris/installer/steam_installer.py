"""Collection of installer files"""

import asyncio
from gettext import gettext as _
from pathlib import Path

from qtris.config import QtrisConfig
from qtris.installer.errors import ScriptingError
from qtris.runners import steam
from qtris.util.notifications import NotificationSource
from qtris.util.steam.appmanifest import get_appmanifest_from_appid
from qtris.util.steam.config import get_steamapps_dirs


class SteamInstaller:
    """Handles installation of Steam games"""

    def __init__(self, steam_uri: str, file_id: str):
        """
        Params:
            steam_uri: Colon separated game info containing:
                    - $STEAM
                    - The Steam appid
                    - The relative path of files to retrieve
            file_id: The qtris installer internal id for the game files
        """
        self.game_installed = NotificationSource()
        self.game_state_changed = NotificationSource()
        self.steam_poll = None
        self.prev_states = []  # Previous states for the Steam installer
        self.state = ""
        self.install_start_time = None
        self.steam_uri = steam_uri
        self.cancelled = False
        self._runner = None

        self.file_id = file_id
        try:
            _steam, appid, path = self.steam_uri.split(":", 2)
        except ValueError as err:
            raise ScriptingError(_("Malformed steam path: %s") % self.steam_uri) from err

        self.appid = appid
        self.path = path
        self.platform = "linux"
        self.runner = steam.steam()

    @property
    def steam_rel_path(self) -> str:
        """Return the relative path for data files"""
        _steam_rel_path = self.path.strip()
        if _steam_rel_path == "/":
            _steam_rel_path = "."
        return _steam_rel_path

    async def wait_for_data(self) -> tuple[str, str]:
        """Ask Steam to install the game and wait for its root and requested data path."""
        if not (self.appid.isascii() and self.appid.isdecimal()):
            raise ScriptingError(_("Invalid Steam app ID: %s") % self.appid)
        root = await asyncio.to_thread(self._find_installed_root)
        if root is None:
            await asyncio.to_thread(self._request_install)
            while root is None:
                await asyncio.sleep(2)
                root = await asyncio.to_thread(self._find_installed_root)
        data_path = (root / self.steam_rel_path).resolve()
        if not data_path.is_relative_to(root) or not data_path.exists():
            raise ScriptingError(_("The requested files were not found in the Steam installation."))
        return str(root), str(data_path)

    def _request_install(self) -> None:
        self.runner.config = QtrisConfig(runner_slug=self.runner.name)
        self.runner.install_game(self.appid)

    def _find_installed_root(self) -> Path | None:
        for steamapps in get_steamapps_dirs():
            try:
                manifest = get_appmanifest_from_appid(steamapps, self.appid)
                if manifest and manifest.is_installed() and (path := manifest.get_install_path()):
                    return Path(path).resolve()
            except OSError, ValueError:
                # Steam may be replacing the manifest or library while this runs
                continue
        return None
