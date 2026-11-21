"""Set service ID for Steam games"""

from qtris import settings
from qtris.database import sql
from qtris.database.games import get_games


def migrate():
    """Run migration"""
    for game in get_games():
        if not game.get("steamid"):
            continue
        if game["runner"] and game["runner"] != "steam":
            continue
        print("Migrating Steam game %s" % game["name"])
        sql.db_update(
            settings.DB_PATH, "games", {"service": "steam", "service_id": game["steamid"]}, {"id": game["id"]}
        )
