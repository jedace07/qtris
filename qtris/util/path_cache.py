"""Keep track of game executables' presence"""

import json
import os
import time

from qtris import settings
from qtris.database.games import get_games
from qtris.game import GAME_UPDATED, Game
from qtris.util import cache_single
from qtris.util.log import logger

GAME_PATH_CACHE_PATH = os.path.join(settings.CACHE_DIR, "game-paths.json")


def _ensure_cache_dir():
    cache_dir = os.path.dirname(GAME_PATH_CACHE_PATH)
    if cache_dir and not os.path.isdir(cache_dir):
        os.makedirs(cache_dir, exist_ok=True)


def _write_path_cache(cache):
    _ensure_cache_dir()
    temp_path = GAME_PATH_CACHE_PATH + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as cache_file:
            json.dump(cache, cache_file, indent=2)
        os.replace(temp_path, GAME_PATH_CACHE_PATH)
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def get_game_paths():
    game_paths = {}
    all_games = get_games(filters={"installed": 1})
    for db_game in all_games:
        if db_game.get("runner") in ("steam", "web"):
            continue
        game = Game(db_game["id"])
        path = game.get_path_from_config()
        if not path:
            continue
        game_paths[db_game["id"]] = path
    return game_paths


def build_path_cache(recreate=False):
    """Generate a new cache path"""
    if os.path.exists(GAME_PATH_CACHE_PATH) and not recreate:
        return
    start_time = time.time()
    _write_path_cache(get_game_paths())
    end_time = time.time()
    get_path_cache.cache_clear()
    logger.debug("Game path cache built in %0.2f seconds", end_time - start_time)


def add_to_path_cache(game):
    """Add or update the path of a game in the cache"""
    logger.debug("Adding %s to path cache", game)
    path = game.get_path_from_config()
    if not path:
        logger.warning("No path for %s", game)
        return
    current_cache = read_path_cache()
    current_cache[game.id] = path
    _write_path_cache(current_cache)
    get_path_cache.cache_clear()


@cache_single
def get_path_cache():
    """Return the contents of the path cache file; this
    dict is cached, so do not modify it."""
    return read_path_cache()


def read_path_cache():
    """Read the contents of the path cache file, and does not cache it."""
    try:
        with open(GAME_PATH_CACHE_PATH, encoding="utf-8") as cache_file:
            try:
                return json.load(cache_file)
            except json.JSONDecodeError:
                return {}
    except FileNotFoundError:
        return {}


def remove_from_path_cache(game):
    logger.debug("Removing %s from path cache", game)
    current_cache = read_path_cache()
    if game.id not in current_cache:
        logger.warning("Game %s (id=%s) not in cache path", game, game.id)
        return
    del current_cache[game.id]
    _write_path_cache(current_cache)
    get_path_cache.cache_clear()


# Keep the cache current as games change
GAME_UPDATED.register(add_to_path_cache)
