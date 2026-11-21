"""Register existing native Linux games without taking ownership of their files."""

import asyncio
import os
import stat
import time
from gettext import gettext as _
from pathlib import Path
from uuid import uuid4

from qtris import settings
from qtris.backend import events, game_maintenance, games
from qtris.backend.models import GameInfo
from qtris.database import games as games_db
from qtris.database import sql
from qtris.exceptions import QtrisError
from qtris.runners.linux import linux
from qtris.util.log import logger
from qtris.util.strings import slugify
from qtris.util.yaml import write_yaml_to_file


def can_link_linux_game(installed: bool, runner: str | None, service: str | None) -> bool:
    """Test whether a library entry can be linked to an existing Linux executable."""
    return not installed and runner in (None, "", "linux") and service in (None, "", "lutris")


async def register_linux_game(executable: str, name: str, arguments: str = "", *, game_id: str = "") -> GameInfo:
    """Add a native executable as a new game or link it to an uninstalled entry."""

    async def register() -> GameInfo:
        row = await asyncio.to_thread(_register, executable, name, arguments, game_id)
        game = games.to_game_info(row)
        events.GAME_INSTALLED.fire(game)
        return game

    return await game_maintenance._run_maintenance(game_id, register)


def _check_executable(executable: str) -> str:
    path = Path(executable).expanduser()
    if not path.is_absolute():
        raise QtrisError(_("Choose an executable using its absolute path."))
    try:
        mode = path.stat().st_mode
    except OSError as error:
        raise QtrisError(_("The executable could not be opened: %s") % path) from error
    if not stat.S_ISREG(mode) or not mode & stat.S_IXUSR or not os.access(path, os.X_OK):
        raise QtrisError(_("The file is not executable: %s") % path)
    return str(path)


def _register(executable: str, name: str, arguments: str, game_id: str) -> games_db.DbGameDict:
    entry = games_db.get_game_by_field(game_id, "id") if game_id else None
    if game_id and entry is None:
        raise QtrisError(_("This game is no longer in the library."))
    if entry and not can_link_linux_game(bool(entry.get("installed")), entry.get("runner"), entry.get("service")):
        raise QtrisError(_("This library entry cannot be linked to a Linux executable."))
    name = entry["name"] if entry else name.strip()
    if not name:
        raise QtrisError(_("Enter a name for the game."))
    executable = _check_executable(executable)
    config_id = f"local-{uuid4().hex}"
    config_path = Path(settings.CONFIG_DIR) / "games" / f"{config_id}.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    options = {"exe": executable}
    if arguments:
        options["args"] = arguments
    fields = {
        "installed": 1,
        "installed_at": int(time.time()),
        "runner": "linux",
        "platform": linux().get_platform(),
        "configpath": config_id,
        "directory": "",
        "installer_slug": None,
        "parent_slug": None,
    }
    write_yaml_to_file({"game": options}, str(config_path))
    try:
        if entry:
            updated = sql.db_update(settings.DB_PATH, "games", fields, {"id": game_id})
            if updated.rowcount != 1:
                raise QtrisError(_("This game is no longer in the library."))
            return {**entry, **fields}

        slug = slugify(name)
        new_id = games_db.add_game(name=name, slug=slug, **fields)
        return {**fields, "id": new_id, "name": name, "slug": slug}
    except Exception:
        try:
            config_path.unlink()
        except OSError:
            logger.exception("Could not remove unused game configuration %s", config_path)
        raise
