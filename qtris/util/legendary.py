"""The Epic Games Store through Legendary.

legendary keeps the sign-in, the library's metadata and the installed games in
Qtris's configuration folder, and gives the launch parameters, with a fresh
sign-in for the game, each time a game starts.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from gettext import gettext as _
from typing import Any

from qtris import settings
from qtris.util.credentials import protect_config_directory
from qtris.util.log import logger
from qtris.util.store_cli import DownloadProgress, StoreClient, StoreClientError

CONFIG_PATH = os.path.join(settings.CONFIG_DIR, "legendary")
CLIENT = StoreClient("legendary", "LEGENDARY_CONFIG_PATH", CONFIG_PATH)
LOGIN_URL = "https://legendary.gl/epiclogin"


def is_signed_in() -> bool:
    if not os.path.isfile(os.path.join(CONFIG_PATH, "user.json")):
        return False
    protect_config_directory(CONFIG_PATH)
    return True


def check_available() -> None:
    """Raise StoreClientError if legendary hasn't been downloaded."""
    _binary = CLIENT.binary  # Finding the binary is the check


def sign_in(authorization_code: str) -> None:
    protect_config_directory(CONFIG_PATH)
    CLIENT.run(["auth", "--code", authorization_code, "--disable-webview"], sensitive=True)
    # Legendary exits successfully even when Epic refuses the code
    if not is_signed_in():
        raise StoreClientError(_("legendary could not sign in to Epic Games. Try again."))


def sign_out() -> None:
    if is_signed_in():
        CLIENT.run(["auth", "--delete"])


def list_games() -> list[dict[str, Any]]:
    """Return the installable games of the library, with their catalog metadata."""
    games = CLIENT.run_json(["list", "--json"], timeout=900)
    return [game for game in games if isinstance(game, dict) and game.get("app_name")]


def list_installed() -> list[dict[str, Any]]:
    installed = CLIENT.run_json(["list-installed", "--json"])
    return [game for game in installed if isinstance(game, dict) and game.get("app_name")]


def get_installed(app_name: str) -> dict[str, Any] | None:
    return next((game for game in list_installed() if game["app_name"] == app_name), None)


def install(
    app_name: str, path: str, command: str, progress_callback: Callable[[DownloadProgress], None]
) -> dict[str, Any]:
    """Download a game into path ("download"), bring it up to date ("update") or fix
    its files ("repair"); return what legendary knows of the installed game."""
    if command == "download":
        args = ["-y", "install", app_name, "--game-folder", path, "--skip-sdl", "--skip-dlcs"]
    elif command == "update":
        args = ["-y", "install", app_name, "--update-only", "--skip-sdl", "--skip-dlcs"]
    elif command == "repair":
        args = ["-y", "repair", app_name, "--skip-sdl"]
    else:
        raise ValueError(f"Unknown legendary command {command}")
    CLIENT.download(args, progress_callback)
    installed = get_installed(app_name)
    if not installed:
        raise StoreClientError("legendary did not install %s." % app_name)
    return installed


def get_launch_parameters(app_name: str) -> dict[str, Any]:
    """Return how to start a game, signing in to Epic for it. Games that can run
    offline still start when Epic can't be reached."""
    args = ["launch", app_name, "--json", "--no-wine", "--skip-version-check"]
    try:
        return CLIENT.run_json(args, timeout=60)
    except StoreClientError as ex:
        if "Login failed" not in ex.message:
            raise
        logger.warning("Could not sign in to Epic for %s, launching it offline: %s", app_name, ex)
        return CLIENT.run_json([*args, "--offline"], timeout=60)


def forget(app_name: str) -> None:
    """Remove a game from legendary's installed games, leaving its files to Qtris."""
    if get_installed(app_name):
        CLIENT.run(["-y", "uninstall", app_name, "--keep-files", "--skip-uninstaller"])
