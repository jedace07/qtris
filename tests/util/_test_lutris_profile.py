"""Moving a Lutris profile to Qtris's folders on Qtris's first start."""

import configparser
import sqlite3
from pathlib import Path

import pytest

from qtris.util import lutris_profile


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home folder with a Lutris profile, where Qtris has none yet."""
    monkeypatch.setattr(lutris_profile, "_TEST_MODULES", ())
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    bases = {"data": tmp_path / ".local/share", "config": tmp_path / ".config", "cache": tmp_path / ".cache"}
    monkeypatch.setattr(lutris_profile, "_current_base", lambda kind: bases[kind])
    data = bases["data"] / "lutris"
    (data / "games").mkdir(parents=True)
    (data / "lutris.conf").write_text("[lutris]\nwidth = 800\nPGA_cache = x\n[services]\ngog = True\n")
    (data / "games" / "quake-1.yml").write_text(f"game:\n  exe: {data}/runners/quake/quake\n")
    with sqlite3.connect(data / "pga.db") as db:
        db.execute("create table games (id integer, directory text, playtime real)")
        db.execute("insert into games values (1, ?, 1.5)", (f"{data}/games/quake",))
    icons = bases["data"] / "icons/hicolor/128x128/apps"
    icons.mkdir(parents=True)
    (icons / "lutris_quake.png").write_bytes(b"png")
    (bases["cache"] / "lutris").mkdir(parents=True)
    (bases["cache"] / "lutris" / "lutris.log").write_text("log")
    return tmp_path


def test_lutris_profile_moves_to_qtris(home: Path) -> None:
    assert lutris_profile.adopt_lutris_profile()

    data = home / ".local/share/qtris"
    assert not (home / ".local/share/lutris").exists()
    assert (home / ".cache/qtris/lutris.log").is_file()
    assert (home / ".local/share/icons/hicolor/128x128/apps/qtris_quake.png").read_bytes() == b"png"

    config = configparser.RawConfigParser()
    config.optionxform = str  # type: ignore[assignment]
    config.read(data / "qtris.conf")
    assert not (data / "lutris.conf").exists()
    assert dict(config["qtris"]) == {"width": "800", "PGA_cache": "x"}
    assert config["services"]["gog"] == "True"

    assert (data / "games/quake-1.yml").read_text() == f"game:\n  exe: {data}/runners/quake/quake\n"
    with sqlite3.connect(data / "pga.db") as db:
        assert db.execute("select directory, playtime from games").fetchone() == (f"{data}/games/quake", 1.5)


def test_an_existing_qtris_profile_is_left_alone(home: Path) -> None:
    (home / ".local/share/qtris").mkdir()
    (home / ".config/qtris").mkdir(parents=True)
    (home / ".cache/qtris").mkdir()
    assert not lutris_profile.adopt_lutris_profile()
    assert (home / ".local/share/lutris/lutris.conf").is_file()


def test_nothing_moves_while_testing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lutris_profile, "_TEST_MODULES", ("pytest",))
    assert not lutris_profile.adopt_lutris_profile()
    assert (home / ".local/share/lutris").is_dir()
