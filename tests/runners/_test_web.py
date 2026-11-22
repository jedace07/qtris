"""The web runner: what it hands the player, and how it tracks and stops it."""

from __future__ import annotations

import signal
import sys
from typing import TYPE_CHECKING, Any

import pytest

from qtris import runners
from qtris.exceptions import GameConfigError
from qtris.qt.web_player.options import parse_options
from qtris.runners import runner, web

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(web.settings, "DATA_DIR", str(home / ".local/share/qtris"))
    monkeypatch.setattr(web.settings, "CONFIG_DIR", str(home / ".local/share/qtris"))
    monkeypatch.setattr(web.settings, "CACHE_DIR", str(home / ".cache/qtris"))
    monkeypatch.setattr(
        web.GLib,
        "get_user_special_dir",
        lambda kind: str(home / "Downloads") if kind == web.GLib.UserDirectory.DIRECTORY_DOWNLOAD else None,
    )
    return home


def make_runner(main_file: str = "", data: dict[str, Any] | None = None, **options: Any) -> web.web:
    instance = web.web()
    config = instance.config
    config.game_config.update({"main_file": main_file})
    config.runner_config.update({"window_mode": "windowed", "window_size": "1280x720"} | options)
    instance.has_explicit_config = True
    instance.game_data = {"id": 4, "installed_at": 1000, "name": "A game", "slug": "a-game"} | (data or {})
    return instance


def make_page(folder: Path, name: str = "index.html") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("<!doctype html>", encoding="utf-8")
    return folder / name


def test_web_is_built_in() -> None:
    assert "web" in runners.get_user_runner_names()
    assert web.web().is_installed()


def test_the_player_gets_the_configuration(home: Path) -> None:
    game = make_runner(
        "https://example.com/game?level=2#start",
        {"name": "--no-sandbox"},
        window_mode="fullscreen",
        window_size="800X600",
        open_links=True,
        user_agent="Test agent",
    )
    game.game_config["shared_memory"] = True
    command = game.play()["command"]
    assert command[:4] == [sys.executable, "-P", "-m", web.PLAYER_MODULE]
    options = parse_options(command[4:])
    assert options.url == "https://example.com/game?level=2#start"
    assert options.name == "--no-sandbox"
    assert (options.width, options.height, options.window_mode) == (800, 600, "fullscreen")
    assert options.links_in_game and options.shared_memory and options.user_agent == "Test agent"


def test_each_game_has_its_own_profile(home: Path) -> None:
    def profile(**data: Any) -> tuple[Path, Path]:
        options = parse_options(make_runner("https://example.com/", data).play()["command"][4:])
        return options.profile_dir, options.cache_dir

    original = profile()
    assert original[0] == home / ".local/share/qtris/web-profiles/4-1000"
    assert original[1] == home / ".cache/qtris/web-profiles/4-1000"
    # Renaming keeps the saves
    assert profile(name="Renamed", slug="renamed") == original
    assert profile(id=5) != original
    # A new game that reuses a deleted one's id doesn't get its saves
    assert profile(installed_at=2000) != original


@pytest.mark.parametrize(
    "address",
    [
        "",
        "ftp://example.com/game",
        "javascript:alert(1)",
        "data:text/html,game",
        "steam://run/10",
        "https://",
        "http://[broken",
        "file://server/share/index.html",
        "example.com/game",
    ],
)
def test_other_addresses_are_refused(home: Path, address: str) -> None:
    with pytest.raises(GameConfigError):
        make_runner(address).play()


def test_local_files_become_file_urls(home: Path) -> None:
    page = make_page(home / "Games" / "My game #1", "start?.html")
    assert make_runner(str(page)).play()["command"][-1] == page.as_uri()
    assert make_runner("~/Games/My game #1/start?.html").play()["command"][-1] == page.as_uri()
    assert make_runner(page.as_uri() + "?level=2#top").play()["command"][-1] == page.as_uri() + "?level=2#top"
    relative = make_runner("start?.html", {"directory": str(page.parent)})
    assert relative.play()["command"][-1] == page.as_uri()


@pytest.mark.parametrize("folder", [".", "Downloads", ".."])
def test_a_game_cannot_share_a_folder_with_personal_files(home: Path, folder: str) -> None:
    page = make_page(home / folder)
    with pytest.raises(GameConfigError, match="folder of its own"):
        make_runner(str(page)).play()


def test_a_folder_inside_a_personal_one_is_fine(home: Path) -> None:
    page = make_page(home / "Downloads" / "game")
    assert make_runner(str(page)).play()["command"][-1] == page.as_uri()


def test_the_working_directory_is_never_made_from_an_address(home: Path) -> None:
    assert make_runner("https://example.com/play/game").working_dir.rstrip("/") == str(home)
    page = make_page(home / "Games" / "game")
    assert make_runner(str(page)).game_path == str(page.parent)
    assert make_runner(page.as_uri()).game_path == str(page.parent)
    assert make_runner("game/index.html").game_path == str(home / "game")


def test_a_launch_configuration_can_open_another_page(home: Path) -> None:
    folder = home / "Games" / "game"
    game = make_runner(str(make_page(folder)))
    editor = make_page(folder, "editor.html")
    info = game.play()
    assert game.get_launch_config_command(info, {"exe": "editor.html"})[-1] == editor.as_uri()
    assert game.get_launch_config_command(info, {"exe": "https://example.com/"})[-1] == "https://example.com/"
    assert game.get_launch_config_command(info, {"name": "Same"}) == info["command"]


@pytest.mark.parametrize("size", ["big", "0x600", "800x", "800*600"])
def test_bad_window_sizes_are_refused(home: Path, size: str) -> None:
    with pytest.raises(GameConfigError):
        make_runner("https://example.com/", window_size=size).play()


def test_stop_signals_only_the_player(monkeypatch: pytest.MonkeyPatch) -> None:
    game = web.web()
    player = " ".join(game.get_command())
    commands = {
        1: f"{sys.executable} qtris-wrapper Game 0 0 {player} https://example.com/",
        2: f"{player} https://example.com/",
        3: "/app/QtWebEngineProcess --type=renderer",
    }
    monkeypatch.setattr(web, "Process", lambda pid: type("Fake", (), {"cmdline": commands.get(pid)})())
    signalled: list[tuple[int, set[int]]] = []

    def kill(sig: int, pids: Iterable[int]) -> None:
        signalled.append((sig, set(pids)))

    monkeypatch.setattr(runner, "kill_processes", kill)
    game.force_stop_game([1, 2, 3])
    # With no player left, whatever remains is stopped
    game.force_stop_game([1, 3])
    assert signalled == [(signal.SIGTERM, {2}), (signal.SIGTERM, {1, 3})]
