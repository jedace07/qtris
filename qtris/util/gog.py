from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from qtris.util import system
from qtris.util.log import logger

if TYPE_CHECKING:
    from qtris.config import GameConfigDict, LaunchConfigDict


def get_gog_game_path(target_path: str, product_id: str | None = None) -> str | None:
    """Return the absolute path where a GOG game is installed"""
    gog_game_path = os.path.join(target_path, "drive_c/GOG Games/")
    if not os.path.isdir(gog_game_path):
        logger.debug("No 'GOG Games' folder in %s", target_path)
        return None
    games = os.listdir(gog_game_path)
    if product_id:
        manifest = f"goggame-{product_id}.info"
        return next(
            (
                os.path.join(gog_game_path, game)
                for game in games
                if os.path.isfile(os.path.join(gog_game_path, game, manifest))
            ),
            None,
        )
    if not games:
        logger.warning("No game found in %s", gog_game_path)
        return None
    if len(games) > 1:
        logger.warning("More than 1 game found, this is currently unsupported")
    return os.path.join(gog_game_path, games[0])


def get_gog_config(gog_game_path, product_id: str | None = None):
    """Extract runtime information such as executable paths from GOG files"""
    manifest = f"goggame-{product_id}.info" if product_id else "goggame-*.info"
    config_files = glob.glob(os.path.join(glob.escape(gog_game_path), manifest))
    if not config_files:
        logger.error("No config file found in %s", gog_game_path)
        return
    gog_config_path = config_files[0]
    with open(gog_config_path, encoding="utf-8-sig") as gog_config_file:
        gog_config = json.loads(gog_config_file.read())
    return gog_config


def get_game_config(task: dict[str, object], game_path: str) -> LaunchConfigDict | None:
    path = task.get("path")
    if not isinstance(path, str) or not path:
        return None
    if task.get("isHidden") or task.get("type") not in (None, "FileTask"):
        return None
    if task.get("category") == "document":
        return None

    root = Path(game_path).resolve()

    def resolve(path: str) -> Path | None:
        if "\0" in path:
            return None
        relative = path.replace("\\", "/").lstrip("/")
        candidate = Path(system.fix_path_case(str(root / relative)))
        return candidate if candidate.resolve().is_relative_to(root) else None

    exe = resolve(path)
    if exe is None or not exe.is_file():
        return None

    working_dir = exe.parent
    if isinstance(directory := task.get("workingDir"), str) and directory:
        candidate = resolve(directory)
        if candidate and candidate.is_dir():
            working_dir = candidate
    config: LaunchConfigDict = {"exe": str(exe), "working_dir": str(working_dir)}
    if isinstance(args := task.get("arguments"), str) and args:
        config["args"] = args
    if isinstance(name := task.get("name"), str) and name.strip():
        config["name"] = name.strip()
    return config


def convert_gog_config_to_qtris(gog_config: object, gog_game_path: str) -> GameConfigDict:
    tasks = gog_config.get("playTasks") if isinstance(gog_config, dict) else None
    if not isinstance(tasks, list):
        return {}
    configs: list[tuple[LaunchConfigDict, bool]] = []
    for task in tasks:
        if isinstance(task, dict) and (config := get_game_config(task, gog_game_path)):
            configs.append((config, bool(task.get("isPrimary"))))
    if not configs:
        return {}

    primary_index = next((index for index, (_, primary) in enumerate(configs) if primary), 0)
    primary, _ = configs.pop(primary_index)
    alternatives = [config for config, _ in configs if config != primary]
    for alternative in alternatives:
        alternative.setdefault("name", Path(alternative["exe"]).stem)
    return {**primary, "launch_configs": alternatives}


def find_gog_launch_config(install_dir: str, appid: str) -> GameConfigDict:
    if not appid or not appid.isascii() or not appid.isdecimal():
        return {}
    try:
        if directory := find_gog_config_dir(install_dir, appid):
            return convert_gog_config_to_qtris(get_gog_config(directory, appid), directory)
    except (OSError, ValueError, RuntimeError) as error:
        logger.warning("Could not read GOG launch options for %s in %s: %s", appid, install_dir, error)
    return {}


def find_gog_config_dir(install_dir: str, product_id: str | None = None) -> str | None:
    """Return the directory containing goggame-*.info files, or None.

    Windows installs place the .info file directly in the install root;
    Linux offline installs nest it under 'game/'; Linux depot installs nest
    it under '<gameName>/game/'. We check those locations and one level of
    subdirectories' 'game/' folders. We deliberately don't recurse further
    to avoid following symlink cycles inside any Wine prefix that may live
    under the install dir."""
    if not install_dir or not os.path.isdir(install_dir):
        return None

    def has_info(path: str) -> bool:
        if product_id:
            return os.path.isfile(os.path.join(path, f"goggame-{product_id}.info"))
        return os.path.isdir(path) and any(f.startswith("goggame-") and f.endswith(".info") for f in os.listdir(path))

    candidates = [install_dir, os.path.join(install_dir, "game")]
    for entry in os.listdir(install_dir):
        sub = os.path.join(install_dir, entry)
        if os.path.isdir(sub):
            candidates.extend([sub, os.path.join(sub, "game")])

    for candidate in candidates:
        if has_info(candidate):
            return candidate
    return None


def find_installed_product_ids(install_dir):
    """Return the GOG product IDs of all goggame-*.info files in the install.

    GOG writes one .info file per installed product (base game + each DLC),
    named goggame-<productId>.info. The returned set includes the base
    game's id; callers that only want DLCs should subtract it."""
    config_dir = find_gog_config_dir(install_dir)
    if not config_dir:
        return set()
    ids = set()
    for filename in os.listdir(config_dir):
        if filename.startswith("goggame-") and filename.endswith(".info"):
            product_id = filename[len("goggame-") : -len(".info")]
            if product_id.isdigit():
                ids.add(product_id)
    return ids


def apply_gog_config(installer):
    """Post-install hook: read GOG config from the install target and merge into the game script."""
    prefix = installer.wine_prefix or installer.interpreter.target_path
    product_id = str(installer.service_appid) if installer.wine_prefix and installer.service_appid else None
    if (
        "game" in installer.script
        and (gog_game_path := get_gog_game_path(prefix, product_id))
        and (gog_config := get_gog_config(gog_game_path, product_id))
    ):
        qtris_config = convert_gog_config_to_qtris(gog_config, gog_game_path)
        installer.script["game"].update(qtris_config)
