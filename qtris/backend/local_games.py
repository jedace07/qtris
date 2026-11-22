"""Register existing game files in the library."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from gettext import gettext as _
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from qtris import settings
from qtris.backend import events, game_maintenance, games
from qtris.database import games as games_db
from qtris.database import sql
from qtris.database.services import ServiceGameCollection
from qtris.exceptions import QtrisError
from qtris.runners.linux import linux
from qtris.util.gog import get_game_config
from qtris.util.log import logger
from qtris.util.strings import slugify
from qtris.util.yaml import write_yaml_to_file

if TYPE_CHECKING:
    from qtris.backend.models import GameInfo


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


async def register_gog_game(appid: str, directory: str, executable: str = '', *, game_id: str = '') -> GameInfo:
    """Link a GOG library entry to an existing game folder."""

    async def register() -> GameInfo:
        row = await asyncio.to_thread(_register_gog, appid, directory, executable, game_id)
        game = games.to_game_info(row)
        events.GAME_INSTALLED.fire(game)
        return game

    return await game_maintenance._run_maintenance(game_id, register)


def _register_gog(appid: str, directory: str, executable: str, game_id: str) -> games_db.DbGameDict:
    if not appid.isascii() or not appid.isdecimal():
        raise QtrisError(_('This GOG game has an invalid product ID.'))
    source = ServiceGameCollection.get_game('gog', appid)
    if source is None:
        raise QtrisError(_('Sync GOG to find this game in your library.'))

    folder = Path(directory).expanduser()
    if not folder.is_absolute() or not folder.is_dir():
        raise QtrisError(_('Choose an existing game folder.'))
    folder = folder.resolve()
    if executable:
        options = {'exe': str(Path(executable).expanduser())}
        manifest_dir = folder
    else:
        options, manifest_dir = _detect_gog_launch(folder, appid)

    path = Path(options['exe'])
    if not path.is_absolute() or not path.resolve().is_relative_to(folder) or not path.is_file():
        raise QtrisError(_('Choose a game executable inside the selected folder.'))
    runner = 'wine' if path.suffix.casefold() == '.exe' else 'linux'
    if runner == 'wine':
        options['prefix'] = str(Path(settings.DATA_DIR) / 'prefixes' / f'gog-{appid}')
    else:
        _check_executable(str(path))
    working_dir = Path(options.get('working_dir') or path.parent)
    if not working_dir.is_absolute():
        working_dir = manifest_dir / working_dir
    if not working_dir.resolve().is_relative_to(folder) or not working_dir.is_dir():
        working_dir = path.parent
    options['working_dir'] = str(working_dir)

    # Serialize the duplicate check and registration with other library writes
    with sql.DB_LOCK:
        matches = games_db.get_games(filters={'service': 'gog', 'service_id': appid})
        if any(row['installed'] for row in matches):
            raise QtrisError(_('This game is already installed.'))
        entry = next((row for row in matches if not game_id or row['id'] == game_id), None)
        if game_id and entry is None:
            raise QtrisError(_('This game is no longer in the library.'))
        if runner == 'wine':
            Path(options['prefix']).parent.mkdir(parents=True, exist_ok=True)
        name = entry['name'] if entry else str(source['name'])
        return _save_registration(
            name,
            entry,
            options,
            {
                'service': 'gog',
                'service_id': appid,
                'runner': runner,
                'platform': _('Windows') if runner == 'wine' else linux().get_platform(),
                'directory': str(folder),
                'external_files': 1,
            },
            slug=str(source['slug'] or ''),
        )


def _detect_gog_launch(folder: Path, appid: str) -> tuple[dict[str, str], Path]:
    for manifest_dir in (folder, folder / 'game'):
        manifest = manifest_dir / f'goggame-{appid}.info'
        if not manifest.is_file():
            continue
        start_script = folder / 'start.sh'
        if start_script.is_file() and os.access(start_script, os.X_OK):
            return {'exe': str(start_script)}, folder
        try:
            data = json.loads(manifest.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as error:
            raise QtrisError(_('Could not read the GOG launch info. Choose the game executable below.')) from error
        tasks = data.get('playTasks', []) if isinstance(data, dict) else []
        if not isinstance(tasks, list):
            break
        for task in tasks:
            if not isinstance(task, dict) or not task.get('isPrimary') or not isinstance(task.get('path'), str):
                continue
            options = get_game_config(
                {key: task[key] for key in ('path', 'workingDir', 'arguments') if isinstance(task.get(key), str)},
                str(manifest_dir),
            )
            if options:
                return options, manifest_dir
    raise QtrisError(_('Could not detect this game in the chosen folder. Choose its executable below.'))


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
    options = {"exe": executable}
    if arguments:
        options["args"] = arguments
    return _save_registration(
        name, entry, options, {'runner': 'linux', 'platform': linux().get_platform(), 'directory': ''}
    )


def _save_registration(
    name: str,
    entry: games_db.DbGameDict | None,
    options: dict[str, str],
    installation: games_db.DbGameDict,
    *,
    slug: str = '',
) -> games_db.DbGameDict:
    config_id = f'local-{uuid4().hex}'
    config_path = Path(settings.CONFIG_DIR) / 'games' / f'{config_id}.yml'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fields = {
        "installed": 1,
        "installed_at": int(time.time()),
        "configpath": config_id,
        "installer_slug": None,
        "parent_slug": None,
        **installation,
    }
    write_yaml_to_file({"game": options}, str(config_path))
    try:
        if entry:
            updated = sql.db_update(settings.DB_PATH, 'games', fields, {'id': entry['id']})
            if updated.rowcount != 1:
                raise QtrisError(_("This game is no longer in the library."))
            return {**entry, **fields}

        slug = slug or slugify(name)
        new_id = games_db.add_game(name=name, slug=slug, **fields)
        return {**fields, 'id': new_id, 'name': name, 'slug': slug}
    except Exception:
        try:
            config_path.unlink()
        except OSError:
            logger.exception("Could not remove unused game configuration %s", config_path)
        raise
