"""The web player's command line, which the web runner builds."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

WINDOW_MODES = ("windowed", "maximized", "fullscreen")


@dataclass(frozen=True)
class PlayerOptions:
    url: str
    name: str
    icon: str
    profile_dir: Path
    cache_dir: Path
    width: int
    height: int
    window_mode: str
    links_in_game: bool
    shared_memory: bool
    user_agent: str


def _size(value: str) -> tuple[int, int]:
    width, height = value.split("x")
    return int(width), int(height)


def parse_options(arguments: Sequence[str]) -> PlayerOptions:
    parser = argparse.ArgumentParser(prog="qtris.qt.web_player", description=__doc__)
    parser.add_argument("url", help="an http, https or file URL")
    parser.add_argument("--profile", type=Path, required=True, help="where the game's site data is kept")
    parser.add_argument("--cache", type=Path, required=True, help="where the game's cache is kept")
    parser.add_argument("--name", default="Qtris")
    parser.add_argument("--icon", default="")
    parser.add_argument("--size", type=_size, default=(1280, 720), help="the window's WIDTHxHEIGHT")
    parser.add_argument("--window-mode", choices=WINDOW_MODES, default="windowed")
    parser.add_argument("--links-in-game", action="store_true")
    parser.add_argument("--shared-memory", action="store_true")
    parser.add_argument("--user-agent", default="")
    values = parser.parse_args(arguments)
    return PlayerOptions(
        url=values.url,
        name=values.name,
        icon=values.icon,
        profile_dir=values.profile,
        cache_dir=values.cache,
        width=values.size[0],
        height=values.size[1],
        window_mode=values.window_mode,
        links_in_game=values.links_in_game,
        shared_memory=values.shared_memory,
        user_agent=values.user_agent,
    )
