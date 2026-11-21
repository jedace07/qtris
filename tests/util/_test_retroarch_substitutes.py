"""Games of emulators that RetroArch cores replaced."""

from pathlib import Path

import pytest

from qtris import settings
from qtris.migrations.retire_standalone_emulators import migrate_config
from qtris.util.retroarch import content
from qtris.util.retroarch.content import get_content_path
from qtris.util.retroarch.substitutes import translate_game_config, translate_runner


def test_rom_runners_become_their_core() -> None:
    assert translate_game_config("ppsspp", {"main_file": "/roms/game.iso"}) == {
        "main_file": "/roms/game.iso",
        "core": "ppsspp",
    }
    assert translate_game_config("hatari", {"disk-a": "/st/a.st", "disk-b": "/st/b.st"}) == {
        "main_file": "/st/a.st",
        "core": "hatarib",
    }
    assert translate_game_config("mednafen", {"main_file": "a.pce", "machine": "pce"})["core"] == "mednafen_pce"


def test_other_runners_are_left_alone() -> None:
    assert translate_runner("wine") == "wine"
    assert translate_runner("dosbox") == "libretro"
    assert translate_runner("yuzu") == "eden"
    assert translate_game_config("wine", {"exe": "game.exe"}) == {"exe": "game.exe"}


def test_renamed_runners_keep_their_options() -> None:
    config = {"game": {"main_file": "game.nsp"}, "yuzu": {"fullscreen": False}}
    assert migrate_config("yuzu", config) == {"game": {"main_file": "game.nsp"}, "eden": {"fullscreen": False}}


def test_migration_moves_game_options_to_libretro() -> None:
    config = {"game": {"main_file": "/roms/zelda.iso", "platform": "1"}, "dolphin": {"fullscreen": True}, "system": {}}
    assert migrate_config("dolphin", config) == {
        "game": {"main_file": "/roms/zelda.iso", "core": "dolphin"},
        "system": {},
    }


def test_scummvm_core_gets_a_file_naming_the_game(tmp_path: Path) -> None:
    path = get_content_path("scummvm", {"game_id": "monkey", "path": str(tmp_path)})
    assert Path(path) == tmp_path / "monkey.scummvm"
    assert Path(path).read_text() == "monkey"


def test_dosbox_settings_and_launch_files_are_merged(tmp_path: Path) -> None:
    (tmp_path / "DOSBOX").mkdir()
    (tmp_path / "game.conf").write_text("[cpu]\ncycles=max\n")
    (tmp_path / "game_single.conf").write_text("[autoexec]\nmount C ..\n")
    config = translate_game_config(
        "dosbox",
        {
            "main_file": str(tmp_path / "game_single.conf"),
            "config_file": str(tmp_path / "game.conf"),
            "working_dir": str(tmp_path / "DOSBOX"),
        },
    )

    merged = Path(get_content_path(config["core"], config))

    assert merged == tmp_path / "DOSBOX" / ".qtris-dosbox.conf"
    assert merged.read_text().index("[cpu]") < merged.read_text().index("[autoexec]")


def test_mame_machines_run_from_a_command_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content, "GENERATED_CONTENT_DIR", tmp_path / "content")
    disk = tmp_path / "game.adf"
    disk.write_bytes(b"")
    path = Path(get_content_path("mame", {"main_file": str(disk), "machine": "aa4000", "device": "flop1"}))
    assert path.parent == tmp_path / "content"
    assert path.read_text() == f"aa4000 -flop1 {disk}\n"


def test_folder_games_load_their_entry_file(tmp_path: Path) -> None:
    (tmp_path / "code").mkdir()
    (tmp_path / "code" / "game.rpx").write_bytes(b"")
    assert get_content_path("cemu", {"main_file": str(tmp_path)}) == str(tmp_path / "code" / "game.rpx")
    (tmp_path / "rpg_rt.ldb").write_bytes(b"")
    assert get_content_path("easyrpg", {"main_file": str(tmp_path)}) == str(tmp_path / "rpg_rt.ldb")


def test_generated_content_lives_in_the_cache() -> None:
    assert content.GENERATED_CONTENT_DIR.is_relative_to(settings.CACHE_DIR)
