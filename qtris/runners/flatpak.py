"""Launch an app from the Flathub source."""

from __future__ import annotations

from gettext import gettext as _

from qtris.exceptions import GameConfigError
from qtris.runners.runner import GamePlayInfoDict, Runner
from qtris.util import flatpak as host_flatpak
from qtris.util.strings import split_arguments


class flatpak(Runner):
    human_name = _("Flatpak")
    description = _("Launches apps installed from Flathub")
    platform_dict = Runner.to_platform_dict([_("Linux")])
    game_options = [
        {"option": "appid", "type": "string", "label": _("Application ID")},
        {"option": "args", "type": "string", "label": _("Arguments")},
    ]

    @property
    def appid(self) -> str | None:
        appid = self.game_config.get("appid")
        return appid if isinstance(appid, str) and host_flatpak.is_app_id(appid) else None

    @property
    def can_install(self) -> bool:
        return False

    def is_installed(self, flatpak_allowed: bool = True) -> bool:
        return flatpak_allowed and host_flatpak.is_app_installed(self.appid)

    def get_command(self) -> list[str]:
        if not (appid := self.appid):
            raise GameConfigError(_("A valid Flatpak application ID is required."))
        return host_flatpak.get_run_command(appid)

    def prepare_game_command(self, command: list[str], env: dict[str, str]) -> list[str]:
        appid = self.appid
        return host_flatpak.add_environment(command, appid, env) if appid else command

    def play(self) -> GamePlayInfoDict:
        args = split_arguments(self.game_config.get("args") or "")
        return {"command": [*self.get_command(), *args]}
