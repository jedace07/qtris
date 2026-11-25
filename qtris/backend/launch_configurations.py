"""Translate launch configuration choices into data a frontend can display."""

from __future__ import annotations

from dataclasses import dataclass
from gettext import gettext as _
from typing import TYPE_CHECKING

from qtris.backend.games import game_info_from
from qtris.backend.models import GameInfo
from qtris.delegates import LaunchUIDelegate
from qtris.exceptions import QtrisError
from qtris.game import Game

if TYPE_CHECKING:
    from qtris.config import GameConfigDict, LaunchConfigDict


@dataclass(frozen=True)
class LaunchChoice:
    index: int
    remember: bool


def configuration_names(configs: list["LaunchConfigDict"], primary_name: str = "") -> tuple[str, ...]:
    """Return the primary configuration label followed by the named alternatives."""
    return (
        primary_name or _("Default"),
        *(str(config.get("name") or _("Unnamed configuration")) for config in configs),
    )


def preferred_index(configs: list["LaunchConfigDict"], name: str | None, index: int | None) -> int | None:
    """Resolve a saved selection, keeping its index when duplicate names exist."""
    if index == 0 or name == Game.PRIMARY_LAUNCH_CONFIG_NAME:
        return 0
    if isinstance(index, int) and 0 < index <= len(configs) and configs[index - 1].get("name") == name:
        return index
    if name:
        return next((i for i, config in enumerate(configs, 1) if config.get("name") == name), None)
    return None


def set_preferred_index(options: GameConfigDict, configs: list[LaunchConfigDict], index: int | None) -> None:
    if index is None:
        options.pop("preferred_launch_config_index", None)
        options.pop("preferred_launch_config_name", None)
        return
    if not 0 <= index <= len(configs):
        raise QtrisError(_("Invalid launch configuration."))
    options["preferred_launch_config_index"] = index
    options["preferred_launch_config_name"] = (
        configs[index - 1].get("name") if index else Game.PRIMARY_LAUNCH_CONFIG_NAME
    )


class BackendLaunchUIDelegate(LaunchUIDelegate):
    """Adapt the core's launch selection hook for frontends that consume plain data."""

    def __init__(self, *, choose_again: bool = False) -> None:
        self._choose_again = choose_again

    def choose_launch_configuration(
        self, game: GameInfo, names: tuple[str, ...], current_index: int | None
    ) -> LaunchChoice | None:
        """Ask the frontend to choose an index, or return None on cancellation."""
        raise NotImplementedError

    def select_game_launch_config(self, game: Game) -> "LaunchConfigDict | None":
        config = game.config
        if not config:
            return {}
        options = config.game_level.get("game", {})
        primary_name, configs = game.get_launch_options()
        if not configs:
            return {}
        index = preferred_index(
            configs, options.get("preferred_launch_config_name"), options.get("preferred_launch_config_index")
        )
        if index is None or self._choose_again:
            choice = self.choose_launch_configuration(
                game_info_from(game), configuration_names(configs, primary_name), index
            )
            if choice is None:
                return None
            index = choice.index
            if not 0 <= index <= len(configs):
                raise QtrisError(_("Invalid launch configuration."))
            set_preferred_index(options, configs, index if choice.remember else None)
            config.save()
        return configs[index - 1] if index else {}
