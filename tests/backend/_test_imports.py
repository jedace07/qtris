"""Import local ROMs without taking ownership of the source files."""

import asyncio
import zipfile
from pathlib import Path

import pytest

from qtris import settings
from qtris.backend import game_maintenance, imports
from qtris.database import games as games_db
from qtris.exceptions import QtrisError
from qtris.scanners.default_installers import DEFAULT_INSTALLERS
from qtris.scanners.roms import EXTENSION_PLATFORMS, get_rom_name, get_rom_platform
from qtris.util import path_cache
from qtris.util.test_config import setup_test_environment

setup_test_environment()


def test_rom_import_deduplicates_resolved_paths_and_preserves_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(path_cache, "GAME_PATH_CACHE_PATH", str(tmp_path / "path-cache.json"))
    path_cache.get_path_cache.cache_clear()
    (tmp_path / "games").mkdir()
    rom = tmp_path / "Qt Import Test (USA) [!].sfc"
    rom.write_bytes(b"fixture")
    alternate = tmp_path / "alternate.sfc"
    alternate.symlink_to(rom)

    async def run() -> None:
        assert (await imports.import_rom(str(rom))).imported
        rows = games_db.get_games(filters={"slug": "qt-import-test"})
        assert len(rows) == 1
        game_id = rows[0]["id"]
        try:
            duplicate = await imports.import_rom(str(alternate))
            assert not duplicate.imported and not duplicate.error
            state = await game_maintenance.get_maintenance_info(game_id)
            assert state.installed and not state.directory and state.files_unavailable_reason
            with pytest.raises(QtrisError):
                await game_maintenance.uninstall_game(game_id, state, trash_files=True)
            await game_maintenance.uninstall_game(game_id, state, remove_from_library=True)
        finally:
            games_db.delete_game(game_id)
            path_cache.get_path_cache.cache_clear()

    asyncio.run(run())
    assert rom.read_bytes() == b"fixture" and alternate.is_symlink()


def test_roms_are_recognized_by_their_extension(tmp_path: Path) -> None:
    rom = tmp_path / "Legend of Example, The (Europe) (Rev 1).gba"
    rom.write_bytes(b"")
    assert get_rom_platform(rom) == "gba"
    assert get_rom_name(rom) == "The Legend of Example"

    archive = tmp_path / "game.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("Game (Japan).nds", b"")
    assert get_rom_platform(archive) == "ds"

    shared = tmp_path / "game.iso"
    shared.write_bytes(b"")
    assert get_rom_platform(shared) is None
    result = asyncio.run(imports.import_rom(str(shared)))
    assert not result.imported and "file extension" in result.error


def test_every_recognized_platform_has_a_configuration() -> None:
    assert set(EXTENSION_PLATFORMS.values()) <= set(DEFAULT_INSTALLERS)
