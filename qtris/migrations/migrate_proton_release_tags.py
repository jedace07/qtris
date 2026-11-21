"""Name GE-Proton releases by their release tags, as the Proton version picker does.

GE-Proton 11 and later install into '<tag>-<arch>' folders, and the Wine version
picker used to offer those folder names; it now offers the bare release tag. Other
versions are left alone: one that isn't Proton falls back to GE-Proton Latest when the
game runs, except in a Wine prefix Proton has never touched."""

import os

from qtris import settings
from qtris.database.games import get_games_by_runner
from qtris.util.log import logger
from qtris.util.wine.proton import get_proton_release_tag
from qtris.util.yaml import read_yaml_from_file, write_yaml_to_file


def migrate() -> None:
    """Run migration"""
    config_paths = [os.path.join(settings.CONFIG_DIR, "runners/wine.yml")]
    try:
        for db_game in get_games_by_runner("wine"):
            config_paths.append(os.path.join(settings.CONFIG_DIR, "games/%s.yml" % db_game.get("configpath")))
    except Exception as ex:
        logger.exception("Failed to list the Wine games to rename their Proton versions: %s", ex)

    for config_path in config_paths:
        try:
            if not os.path.isfile(config_path):
                continue
            config = read_yaml_from_file(config_path)
            wine = config.get("wine")
            if not isinstance(wine, dict) or not isinstance(version := wine.get("version"), str):
                continue
            tag = get_proton_release_tag(version)
            if tag and tag != version:
                wine["version"] = tag
                write_yaml_to_file(config, filepath=config_path)
        except Exception as ex:
            logger.exception("Failed to rename the Proton version in '%s': %s", config_path, ex)
