"""Amazon Games through Nile, the client Heroic uses.

Nile keeps the sign-in, the library and the installed games in Qtris's
configuration folder, and gives the command and the Amazon Games SDK settings a
game starts with.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from qtris import settings
from qtris.util.credentials import protect_config_directory
from qtris.util.store_cli import DownloadProgress, StoreClient, StoreClientError

# Nile adds "nile" to the folder it's given
CONFIG_PATH = os.path.join(settings.CONFIG_DIR, "nile")
CLIENT = StoreClient("nile", "NILE_CONFIG_PATH", settings.CONFIG_DIR)


@dataclass(frozen=True)
class SignInRequest:
    """A device registration waiting for the user to sign in with the browser."""

    url: str
    client_id: str
    code_verifier: str
    serial: str


def _read_config(name: str) -> Any:
    try:
        with open(os.path.join(CONFIG_PATH, f"{name}.json"), encoding="utf-8") as config_file:
            return json.load(config_file)
    except OSError, ValueError:
        return None


def _write_config(name: str, data: Any) -> None:
    path = os.path.join(CONFIG_PATH, f"{name}.json")
    staged = path + ".tmp"
    with open(staged, "w", encoding="utf-8") as config_file:
        json.dump(data, config_file)
    os.replace(staged, path)


def is_signed_in() -> bool:
    user = _read_config("current_user")
    if not isinstance(user, dict) or not user.get("user_id"):
        return False
    protect_config_directory(CONFIG_PATH)
    return True


def begin_sign_in() -> SignInRequest:
    protect_config_directory(CONFIG_PATH)
    data = CLIENT.run_json(["auth", "--login", "--non-interactive"], timeout=60)
    try:
        return SignInRequest(data["url"], data["client_id"], data["code_verifier"], data["serial"])
    except (KeyError, TypeError) as ex:
        raise StoreClientError("Nile did not start a sign-in.") from ex


def finish_sign_in(request: SignInRequest, authorization_code: str) -> None:
    # Nile prints the tokens it gets, so its output is never logged
    CLIENT.run(
        [
            "register",
            "--code",
            authorization_code,
            "--client-id",
            request.client_id,
            "--code-verifier",
            request.code_verifier,
            "--serial",
            request.serial,
        ],
        timeout=120,
        sensitive=True,
    )
    if not is_signed_in():
        raise StoreClientError("Amazon did not register this device.")


def sign_out() -> None:
    if is_signed_in():
        CLIENT.run(["auth", "--logout"])


def list_games(refresh: bool = True) -> list[dict[str, Any]]:
    """Return the games of the library, as Amazon describes them."""
    if refresh:
        CLIENT.run(["library", "sync"], timeout=900)
    games = _read_config("library") or []
    return [game for game in games if isinstance(game, dict) and game.get("id")]


def get_installed(game_id: str) -> dict[str, Any] | None:
    installed = _read_config("installed") or []
    return next((game for game in installed if isinstance(game, dict) and game.get("id") == game_id), None)


def install(
    game_id: str, path: str, command: str, progress_callback: Callable[[DownloadProgress], None]
) -> dict[str, Any]:
    """Download a game into path ("download"), bring it up to date ("update") or fix
    its files ("repair"); return what Nile knows of the installed game."""
    if command == "download":
        args = ["install", game_id, "--path", path]
    elif command == "update":
        args = ["update", game_id]
    elif command == "repair":
        args = ["verify", game_id]
    else:
        raise ValueError(f"Unknown Nile command {command}")
    CLIENT.download(args, progress_callback)
    installed = get_installed(game_id)
    if not installed:
        raise StoreClientError("Nile did not install %s." % game_id)
    return installed


def get_launch_parameters(game_id: str) -> dict[str, Any]:
    """Return the game's command and the environment the Amazon Games SDK needs."""
    return CLIENT.run_json(["launch", game_id, "--json", "--no-wine"], timeout=60)


def forget(game_id: str) -> None:
    """Remove a game from Nile's installed games, leaving its files to Qtris. Nile's
    own uninstall deletes the files for good, while Qtris moves them to the Trash,
    or keeps them."""
    installed = _read_config("installed") or []
    remaining = [game for game in installed if not (isinstance(game, dict) and game.get("id") == game_id)]
    if len(remaining) != len(installed):
        _write_config("installed", remaining)
    try:
        os.remove(os.path.join(CONFIG_PATH, "manifests", f"{game_id}.raw"))
    except FileNotFoundError:
        pass
