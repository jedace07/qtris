"""Register games already present on disk without taking ownership of their files."""

import asyncio

from qtris.game import GAME_INSTALLED, GAME_UPDATED, Game
from qtris.scanners import playtron


async def import_playtron_games() -> int:
    game_ids = await asyncio.to_thread(playtron.scan_all_libraries)
    for game_id in game_ids:
        game = Game(game_id)
        GAME_INSTALLED.fire(game)
        GAME_UPDATED.fire(game)
    return len(game_ids)
